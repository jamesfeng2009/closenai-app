/**
 * T1-04 · 付费交付「最后一跳」的客户端上报闸门（2026-10-18）。
 *
 * 为什么后端三条闸盖不到这里：`backend/services/delivery_service.py` 能看到
 * 收据校验、档案生成、PDF 请求，但 `exportPdf()` 里 `Sharing.isAvailableAsync()`
 * 为假时抛的 `PdfUnavailableError` 全程不出设备——「付了钱、后端把文件送出去了、
 * 用户手里还是没有东西」这一段只有客户端知道。工作单 §T1-04 步骤 2 因此要求
 * 成/败各补一条 `archive_share_result`。
 *
 * 手法沿用本仓接线闸（见 `dataExportWiring.test.ts` 头注释）：屏在 node 环境渲染不了，
 * 所以读源码文本 + 切片；白名单这种零依赖的字面量集合直接从三端源码解析出来比。
 *
 * 反向断言（「屏里没有 e.message」）一律限定在 `pdfFailureReason` 函数体与两个上报调用
 * 点上，不做整文件扫——`onBuyArchive` 那处 `e.message` 是给 Alert 看的文案（第 296 行），
 * 不上网、不越界；整文件禁 `.message` 会把合法文案也打红，反而逼人绕过闸门。
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');
const countIn = (s: string, re: RegExp) => (s.match(re) ?? []).length;

const SCREEN = 'src/screens/PaywallScreen.tsx';
const FRONT_ANALYTICS = 'src/services/analytics.ts';
const BACK_ANALYTICS = 'backend/services/analytics_service.py';
const GO_ANALYTICS = 'go-core/internal/domain/analytics.go';
const API = 'src/services/api.ts';

/** 从 `const onExportArchivePdf` 起切到该箭头函数收尾（`\n  };`）。 */
function handlerBody(src: string): string {
  const at = src.indexOf('const onExportArchivePdf');
  expect(at).toBeGreaterThan(-1);
  const end = src.indexOf('\n  };', at);
  expect(end).toBeGreaterThan(at);
  return src.slice(at, end);
}

/** 从 `function pdfFailureReason` 起切到函数收尾（`\n}`，模块级函数不缩进）。 */
function reasonFnBody(src: string): string {
  const at = src.indexOf('function pdfFailureReason');
  expect(at).toBeGreaterThan(-1);
  const end = src.indexOf('\n}', at);
  expect(end).toBeGreaterThan(at);
  return src.slice(at, end);
}

/** 三端属性白名单：各自的字面量集合，解析不出就直接红（否则后面的⊆是空跑）。 */
function quotedList(src: string, re: RegExp): string[] {
  const m = re.exec(src);
  expect(m).not.toBeNull();
  return [...m![1].matchAll(/["']([a-z_]+)["']/g)].map((x) => x[1]);
}
const frontWhitelist = () => new Set(
  quotedList(read(FRONT_ANALYTICS), /const ALLOWED_PROPERTY_KEYS = new Set\(\[([\s\S]*?)\]\)/),
);
const backendWhitelist = () => new Set(
  quotedList(readRepo(BACK_ANALYTICS), /ALLOWED_PROPERTY_KEYS = frozenset\(\s*\{([\s\S]*?)\}\s*\)/),
);
const goWhitelist = () => new Set(
  quotedList(readRepo(GO_ANALYTICS), /var AllowedPropertyKeys = map\[string\]bool\{([\s\S]*?)\n\}/),
);

/** 两个上报调用里实际用到的属性键（值不要，键才决定会不会被剔空）。
 *
 * 这里**不**断言调用点数量：那条判据属于 ① 的「恰好两个调用点」。写在切片函数里会让
 * 一次源码形状破坏把整个文件炸成 describe 期异常（`Tests: 0 total`），受害用例的名字
 * 就再也印不出来——变异证人 P1/P5 第一次就跑成了那种「红了但打不中靶」。
 */
function usedPropertyKeys(body: string): string[] {
  const keys = new Set<string>();
  for (const call of [...body.matchAll(/track\('archive_share_result', \{([\s\S]*?)\}\)/g)]) {
    for (const m of call[1].matchAll(/(\w+):/g)) keys.add(m[1]);
  }
  return [...keys].sort();
}

describe('① 交付最后一跳有两条上报，且各自只在真正发生时打', () => {
  const handler = () => handlerBody(read(SCREEN));

  it('屏里从 services/analytics 引到了 track（只有 import 没有调用就是假绿）', () => {
    const src = read(SCREEN);
    expect(countIn(src, /import \{ track \} from '\.\.\/services\/analytics';/g)).toBe(1);
    expect(countIn(src, /\btrack\(/g)).toBeGreaterThanOrEqual(2);
  });

  it('archive_share_result 恰好两个调用点：成功一个、失败一个', () => {
    expect(countIn(handler(), /track\('archive_share_result'/g)).toBe(2);
  });

  it('成功上报排在 exportPdf 之后（过早等于把「面板没接住」说成 delivered）', () => {
    const okBranch = handler().slice(0, handler().indexOf('} catch'));
    expect(countIn(okBranch, /track\('archive_share_result'/g)).toBe(1);
    expect(okBranch).toContain('await exportPdf(bytes, lifeArchivePdfFile(scope))');
    expect(okBranch.indexOf('await exportPdf')).toBeLessThan(
      okBranch.indexOf("track('archive_share_result'"),
    );
    expect(okBranch).toContain("result: 'ok'");
    expect(okBranch).not.toContain('reason');
  });

  it('失败上报带 failed + 机器码，且先上报再弹窗（Alert 是阻塞式原生面板）', () => {
    const src = handler();
    const failBranch = src.slice(src.indexOf('} catch'));
    expect(countIn(failBranch, /track\('archive_share_result'/g)).toBe(1);
    expect(failBranch).toContain("result: 'failed'");
    expect(failBranch).toContain('reason: pdfFailureReason(e)');
    expect(failBranch.indexOf('track(')).toBeLessThan(failBranch.indexOf('Alert.alert('));
  });

  it('未owned / 忙时的静默 return 不算一次交付尝试（否则分母被点击噪声灌满）', () => {
    const src = handler();
    const guard = src.slice(0, src.indexOf('setArchivePdfScope(scope)'));
    expect(guard).toContain('if (anyBusy || !isArchiveOwned(scope)) return;');
    expect(countIn(guard, /track\(/g)).toBe(0);
    expect(guard.length).toBeGreaterThan(0);
  });
});

describe('② reason 只能是机器码，永远不是给人看的那句话', () => {
  const fn = () => reasonFnBody(read(SCREEN));

  it('三个分支的返回值逐字钉死，多一个少一个都红', () => {
    const returns = [...fn().matchAll(/return (.*);/g)].map((m) => m[1].trim());
    expect(returns.sort()).toEqual([
      "'device_share_unavailable'",
      "'network_or_unknown'",
      '`api_${e.code}`',
    ]);
  });

  it('码的语法本身是枚举形的：小写下划线，或 api_ + 数字码', () => {
    for (const r of [...fn().matchAll(/return (.*);/g)].map((m) => m[1].trim())) {
      expect(r).toMatch(/^(?:'[a-z][a-z_]*'|`api_\$\{e\.code\}`)$/);
    }
  });

  it('函数体里没有任何自由文本出口（message / stringify / String()）', () => {
    const src = fn();
    expect(src).not.toMatch(/\.message/);
    expect(src).not.toMatch(/JSON\.stringify/);
    expect(src).not.toMatch(/\bString\(/);
    // 正向证人：不是空函数——上面三条之所以有意义，是因为这里确实读得到分支
    expect(countIn(src, /return /g)).toBe(3);
    expect(countIn(src, /instanceof/g)).toBe(2);
  });

  it('api_ 那一支插的是数字码，不是错误文案：ApiError.code 声明为 number', () => {
    const api = read(API);
    expect(api).toMatch(/export class ApiError extends Error \{\s*code: number;/);
    expect(fn()).toContain('e instanceof ApiError');
  });
});

describe('③ 上报用到的键，三端白名单都认（漂移 = 后端匿名化时整条剔空）', () => {
  const keys = () => usedPropertyKeys(handlerBody(read(SCREEN)));

  it('三端白名单都解析得到且非空（否则下面的⊆对空集恒真）', () => {
    for (const set of [frontWhitelist(), backendWhitelist(), goWhitelist()]) {
      expect(set.size).toBeGreaterThan(10);
    }
  });

  it('用到的键恰好是 feature/action/reason/result 四个', () => {
    expect(keys()).toEqual(['action', 'feature', 'reason', 'result']);
  });

  it.each(['front', 'backend', 'go'] as const)('%s 白名单放行全部用到的键', (side) => {
    const set = { front: frontWhitelist, backend: backendWhitelist, go: goWhitelist }[side]();
    for (const key of keys()) expect(set.has(key)).toBe(true);
  });

  it('三端白名单键集完全一致（前端有后端没有 = 静默丢属性）', () => {
    const f = [...frontWhitelist()].sort();
    const b = [...backendWhitelist()].sort();
    const g = [...goWhitelist()].sort();
    expect(b).toEqual(f);
    expect(g).toEqual(f);
  });
});

describe('④ 后端侧的对应物仍在册（客户端补的是最后一跳，不是唯一一跳）', () => {
  const delivery = readRepo('backend/services/delivery_service.py');

  it('delivery_service 里七个事件枚举仍在，客户端事件不冒充后端事件名', () => {
    expect(countIn(delivery, /^    [A-Z_]+ = "/gm)).toBe(7);
    expect(delivery).not.toContain('archive_share_result');
    expect(read(SCREEN)).not.toContain('archive_generated');
  });

  it('客户端这条走的是同一条 /analytics/batch 中继（不新造出口）', () => {
    expect(read(FRONT_ANALYTICS)).toContain('postAnalyticsBatch');
    expect(read(API)).toMatch(/export (?:async )?function postAnalyticsBatch/);
  });
});
