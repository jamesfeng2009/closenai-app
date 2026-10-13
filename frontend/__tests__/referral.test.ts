/**
 * B4 · M4 分享卡体系前端契约测试。
 *
 * 1. 深链解析：只认品牌域、只认合法码（App 唤起路径的唯一入口）。
 * 2. 待消费推荐的存/取/清：注册可能失败，所以读不删。
 * 3. 与后端码表同步：两侧字符集不一致的后果是静默丢归因，没人报错。
 * 4. #38 归因链路配置：落地页/AASA/重写规则三处同源，API 域与品牌域走环境变量注入。
 */
import * as fs from 'fs';
import * as path from 'path';

jest.mock('@react-native-async-storage/async-storage', () => {
  const store = new Map<string, string>();
  return {
    __esModule: true,
    default: {
      getItem: jest.fn(async (key: string) => store.get(key) ?? null),
      setItem: jest.fn(async (key: string, value: string) => { store.set(key, value); }),
      removeItem: jest.fn(async (key: string) => { store.delete(key); }),
    },
  };
});

import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  clearPendingReferral,
  PENDING_REFERRAL_KEY,
  parseReferralFromUrl,
  peekPendingReferral,
  savePendingReferral,
} from '../src/services/referral';

describe('parseReferralFromUrl：路径段与查询参数', () => {
  it('认 Universal Links 的 /r/{code}', () => {
    expect(parseReferralFromUrl('https://closenai.app/r/ABCD2345')).toBe('ABCD2345');
  });

  it('认分享链接的 ?ref={code}', () => {
    expect(parseReferralFromUrl('https://closenai.app/?ref=ABCD2345')).toBe('ABCD2345');
    expect(parseReferralFromUrl('https://www.closenai.app/download?utm=x&ref=ABCD2345')).toBe('ABCD2345');
  });

  it('小写码归一为大写', () => {
    expect(parseReferralFromUrl('https://closenai.app/r/abcd2345')).toBe('ABCD2345');
  });

  it('子域放行，后缀伪装的域不放行', () => {
    expect(parseReferralFromUrl('https://r.closenai.app/r/ABCD2345')).toBe('ABCD2345');
    expect(parseReferralFromUrl('https://closenai.app.evil.com/r/ABCD2345')).toBeNull();
    expect(parseReferralFromUrl('https://notclosenai.app/r/ABCD2345')).toBeNull();
  });

  it('无协议的裸串不放行（唤起 URL 必带 scheme）', () => {
    expect(parseReferralFromUrl('closenai.app/r/ABCD2345')).toBeNull();
  });

  it.each([
    ['空/缺失', null],
    ['空串', ''],
    ['长度不足', 'https://closenai.app/r/ABCD234'],
    ['长度超长', 'https://closenai.app/r/ABCD23456'],
    ['含被排除的易混字符', 'https://closenai.app/r/ABCD1OIL'],
    ['含非码字符', 'https://closenai.app/r/ABCD234%'],
    ['尾巴为空', 'https://closenai.app/r/'],
    ['无关路径且无 ref', 'https://closenai.app/download'],
  ])('非法输入返回 null：%s', (_label, url) => {
    expect(parseReferralFromUrl(url as string | null)).toBeNull();
  });

  it('任意域名蹭 /r/ 不产生归因（否则别人能强绑自己的码）', () => {
    expect(parseReferralFromUrl('https://evil.example/r/ABCD2345')).toBeNull();
  });
});

describe('pending referral 暂存（跨冷启动）', () => {
  it('save → peek → clear 全流程', async () => {
    await savePendingReferral('ABCD2345');
    expect(await peekPendingReferral()).toBe('ABCD2345');
    await clearPendingReferral();
    expect(await peekPendingReferral()).toBeNull();
  });

  it('peek 是读而不是删：注册失败后来源必须还在', async () => {
    await savePendingReferral('ABCD2345');
    await peekPendingReferral();
    expect(await peekPendingReferral()).toBe('ABCD2345');
    expect(AsyncStorage.removeItem).not.toHaveBeenCalled();
  });

  it('后到的链接覆盖前一个：以用户最新一次来源为准', async () => {
    await savePendingReferral('AAAA2222');
    await savePendingReferral('BBBB3333');
    expect(await peekPendingReferral()).toBe('BBBB3333');
  });

  it('存储里被写进脏值时 peek 返回 null，不把脏码带上注册请求', async () => {
    await AsyncStorage.setItem(PENDING_REFERRAL_KEY, 'hack;ref=1');
    expect(await peekPendingReferral()).toBeNull();
  });
});

describe('与后端码表同步', () => {
  it('字符集逐字相等：前端放行、后端判非法 = 静默丢归因', () => {
    const py = backendSrc().match(/REFERRAL_ALPHABET = "([A-Z2-9]+)"/)?.[1];
    expect(py).toBeDefined();
    expect(tsAlphabet()).toBe(py);
  });

  it('码长一致：两侧都写死 8', () => {
    expect(backendSrc()).toMatch(/REFERRAL_CODE_LEN = 8/);
    expect(tsAlphabet()).toBeTruthy();
    expect(referralSrc()).toMatch(/\{8\}\$\//);
  });
});

describe('落地页与三处码表同源（#38）', () => {
  // 28a9fc3 起站点是 Astro 工程：这些产物住在 `site/public/`（构建时整目录拷进
  // `site/dist/`，dist 已 gitignore），**对外 URL 一字不变**（`/r/index.html`、
  // `/_redirects`、`/.well-known/apple-app-site-association`）。所以断言读的是
  // public 下的源文件——真源在 git 里的那一份，不是本地产物。
  const sitePublic = (...parts: string[]) =>
    fs.readFileSync(path.join(__dirname, '..', '..', 'site', 'public', ...parts), 'utf8');
  const landingSrc = () => sitePublic('r', 'index.html');

  it('落地页的码字符集与前后端逐字相等：不一致就在页面上显示一个后端根本不存在的码', () => {
    // 落地页里唯一一条 {8} 字符类就是推荐码校验（JS 正则源码带 \/ 转义，按字符类取更稳）
    const landingClass = landingSrc().match(/\[([A-Z2-9]+)\]\{8\}/)?.[1];
    expect(landingClass).toBeDefined();
    expect(landingClass).toBe(tsAlphabet());
    expect(backendSrc().match(/REFERRAL_ALPHABET = "([A-Z2-9]+)"/)?.[1]).toBe(landingClass);
  });

  it('落地页只重写 /r/*，且 AASA 的接管路径与之完全一致（否则点了链接开了网页）', () => {
    const redirects = sitePublic('_redirects');
    const aasa = sitePublic('.well-known', 'apple-app-site-association');
    const rules = redirects
      .split('\n')
      .filter((line) => line.trim() && !line.trim().startsWith('#'))
      .join('\n');
    expect(rules).toMatch(/^\/r\/\*\s+\/r\/index\.html\s+200$/m);
    expect(rules).not.toMatch(/\s30[12]\b/); // 3xx 会让 Apple 判定多一层不确定性
    expect(JSON.parse(aasa).applinks.details[0].components[0]['/']).toBe('/r/*');
  });

  it('AASA 的 Team ID 由构建期脚本注入，缺环境变量就非零退出而不是静默发布占位串', () => {
    const script = fs.readFileSync(
      path.join(__dirname, '..', '..', 'site', 'scripts', 'build-aasa.mjs'),
      'utf8'
    );
    expect(script).toMatch(/APPLE_TEAM_ID/);
    expect(script).toMatch(/process\.exit\(/);
  });
});

describe('归因链路的环境注入（#38：不再有写死的 localhost 与写死的域）', () => {
  const apiSrc = () =>
    fs.readFileSync(path.join(__dirname, '..', 'src', 'services', 'api.ts'), 'utf8');

  it('API_BASE 先读 EXPO_PUBLIC_API_BASE，缺省才回落本机开发地址', () => {
    expect(apiSrc()).toContain("const API_BASE = (process.env.EXPO_PUBLIC_API_BASE || '')");
    // 回落值必须仍是 localhost：生产漏配时报错可见，而不是静默打到某个默认域
    expect(apiSrc()).toContain("DEV_API_BASE;\n");
    expect(apiSrc()).toContain("'http://localhost:8000/api/v1'");
  });

  it('注入值去掉尾部斜杠：拼出 /api/v1//auth 时后端 404 很难归因', () => {
    expect(apiSrc()).toContain(".replace(/\\/+$/, '')");
  });

  it('品牌域可被 EXPO_PUBLIC_SITE_HOST 覆盖，但默认仍是 closenai.app', () => {
    expect(referralSrc()).toMatch(/process\.env\.EXPO_PUBLIC_SITE_HOST \|\| 'closenai\.app'/);
  });

  it('域判定逻辑不因注入而放宽：仍然只认精确域或子域', () => {
    expect(referralSrc()).toMatch(/host === SITE_HOST \|\| host\.endsWith\(`\.\$\{SITE_HOST\}`\)/);
  });

  it('匿名公开端点 /share/resolve 在后端挂了限流依赖', () => {
    const router = fs.readFileSync(
      path.join(__dirname, '..', '..', 'backend', 'routers', 'share.py'),
      'utf8'
    );
    const start = router.indexOf('@router.get("/share/resolve');
    const end = router.indexOf('\nasync def', start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start); // indexOf 失配会返回 -1，切片会退化成整段尾部
    expect(router.slice(start, end)).toMatch(/dependencies=\[Depends\(guard_resolve_rate\)\]/);
  });
});
/** referral.ts 里那条正则的字符类（常量未导出，只能按文本对表）。 */
function referralSrc(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'src', 'services', 'referral.ts'), 'utf8');
}

function backendSrc(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', '..', 'backend', 'core', 'referral.py'),
    'utf8'
  );
}

function tsAlphabet(): string {
  return referralSrc().match(/\/\^(\[.*?\])\{8\}\$\//)?.[1]?.slice(1, -1) ?? '';
}
