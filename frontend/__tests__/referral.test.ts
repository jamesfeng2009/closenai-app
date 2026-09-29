/**
 * B4 · M4 分享卡体系前端契约测试。
 *
 * 1. 深链解析：只认品牌域、只认合法码（App 唤起路径的唯一入口）。
 * 2. 待消费推荐的存/取/清：注册可能失败，所以读不删。
 * 3. 与后端码表同步：两侧字符集不一致的后果是静默丢归因，没人报错。
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
  const backendSrc = () =>
    fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'core', 'referral.py'), 'utf8');

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

/** referral.ts 里那条正则的字符类（常量未导出，只能按文本对表）。 */
function referralSrc(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'src', 'services', 'referral.ts'), 'utf8');
}

function tsAlphabet(): string {
  return referralSrc().match(/\/\^(\[.*?\])\{8\}\$\//)?.[1]?.slice(1, -1) ?? '';
}
