/**
 * 年度报告卡（M4 year_in_review）前端入口 · #64。
 *
 * tasks.md:147 登记的缺口是「后端能力已实装、前端零入口」，所以这里守四件事：
 * ① 取数走的是后端那条端点，language 按当前 UI 语言透传（与 report/card 同口径）；
 * ② 卡片载荷的字段面与后端 `_build_year_in_review` 逐字对表（跨语言改名会静默丢字段）；
 * ③ 分享埋点用 year_review 这个 source，且**不带 Pro 门控**（自定决策 ①：当引流免费卡）；
 * ④ 15 语言都有这两个键，且真的被源码引用（漏引用＝死条目，漏语言＝静默回英文）。
 */
import * as fs from 'fs';
import * as path from 'path';

// funnel.ts 会顺链拖进 analytics → api → react-native（node 环境跑不动），
// 与 funnel.test.ts 同一口径：只 mock 依赖链，断言仍打在真实源码上。
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { getItem: jest.fn(async () => null), setItem: jest.fn(async () => undefined) },
}));
jest.mock('../src/services/analytics', () => ({ track: jest.fn() }));

import resources from '../src/i18n/locales';
import { FUNNEL_SOURCES } from '../src/services/funnel';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const apiSrc = read('src/services/api.ts');
const screenSrc = read('src/screens/InsightsScreen.tsx');
const typesSrc = read('src/types/index.ts');
const backendSrc = read('../backend/services/share_service.py');

const LANGUAGES = Object.keys(resources).filter((l) => l !== 'en');

/** 取 TS interface 体里的字段名（`name:` 或 `"name":`）。 */
function tsInterfaceFields(src: string, name: string): string[] {
  const start = src.indexOf(`export interface ${name} {`);
  expect(start).toBeGreaterThan(-1);
  const body = src.slice(start, src.indexOf('\n}', start));
  return [...body.matchAll(/^\s{2}(?:")?([a-z_0-9]+)"?:/gm)].map((m) => m[1]);
}

/** 取 Python dict 字面量的顶层键（`"key":`），从 _build_year_in_review 的 return 起。 */
function pyReturnKeys(src: string, funcName: string): string[] {
  const start = src.indexOf(`def ${funcName}(`);
  expect(start).toBeGreaterThan(-1);
  const ret = src.indexOf('return {', start);
  expect(ret).toBeGreaterThan(-1);
  const body = src.slice(ret, src.indexOf('\n        }', ret));
  return [...body.matchAll(/^            "([a-z_0-9]+)":/gm)].map((m) => m[1]);
}

describe('① 取数：端点与语言透传', () => {
  it('getYearInReview 打的是后端那条 GET /share/year-in-review', () => {
    expect(apiSrc).toContain('`/share/year-in-review?language=${langParam()}${suffix}`');
  });

  it('language 取当前 UI 语言（与 personality-card / report 同一个 langParam）', () => {
    const fn = apiSrc.slice(apiSrc.indexOf('export async function getYearInReview'));
    expect(fn.slice(0, 600)).toContain('langParam()');
  });

  it('year 是可选透传：不传就交给后端定默认年，前端不自己算年', () => {
    const fn = apiSrc.slice(apiSrc.indexOf('export async function getYearInReview'));
    expect(fn).toContain('year?: number');
    expect(fn).toContain('`&year=${year}`');
    expect(fn).not.toMatch(/new Date\(\)\.getFullYear\(\)/);
  });

  it('后端那条端点确实是 GET /share/year-in-review 且 language 默认 en', () => {
    const router = read('../backend/routers/share.py');
    expect(router).toContain('@router.get("/share/year-in-review")');
    expect(router).toContain('language: str = Query("en"');
  });
});

/** 取 Python 在 return 之外补挂的顶层键（`data["share_url"] = …` 这类）。 */
function pyAssignedKeys(src: string, funcName: string): string[] {
  const start = src.indexOf(`async def ${funcName}(`);
  expect(start).toBeGreaterThan(-1);
  const body = src.slice(start, src.indexOf('\n    def ', start));
  return [...body.matchAll(/^        data\[["']([a-z_0-9]+)["']\] =/gm)].map((m) => m[1]);
}

describe('② 载荷字段面与后端逐字对表', () => {
  it('YearInReviewCard 的顶层键 == _build_year_in_review 的 return 键 + 端点补的那两个', () => {
    const ts = tsInterfaceFields(typesSrc, 'YearInReviewCard').sort();
    const py = [
      ...pyReturnKeys(backendSrc, '_build_year_in_review'),
      ...pyAssignedKeys(backendSrc, 'export_year_in_review'),
    ].sort();
    // 两侧都真的解析出了键：解析器空转的话 toEqual([]) 也会绿，所以先拍掉这种假绿
    expect(ts.length).toBeGreaterThanOrEqual(12);
    expect(py.length).toBeGreaterThanOrEqual(12);
    expect(ts).toEqual(py);
    // 补挂的两个键就是分享用的那两个，别哪天又往 return 之外偷偷加第三个
    expect(pyAssignedKeys(backendSrc, 'export_year_in_review').sort()).toEqual(
      ['share_text', 'share_url']
    );
  });

  it('分享用的两个字段（share_url / share_text）在两端都声明', () => {
    const ts = tsInterfaceFields(typesSrc, 'YearInReviewCard');
    for (const key of ['share_url', 'share_text', 'period_label', 'in_progress']) {
      expect(ts).toContain(key);
    }
  });
});

describe('③ 入口与埋点：免费卡、按 action 判完成', () => {
  it('InsightsScreen 里年报入口存在且接上 onShareYearReview', () => {
    expect(screenSrc).toContain('onShareYearReview');
    expect(screenSrc).toContain('await getYearInReview()');
    expect(screenSrc).toContain("t('insights.shareYearReview')");
  });

  it('source 用的是枚举里的 year_review（防各屏自造字符串）', () => {
    expect([...FUNNEL_SOURCES]).toContain('year_review');
    expect(screenSrc).toContain("trackFunnelShare('share_tapped', 'year_review')");
    expect(screenSrc).toContain("trackFunnelShare('share_completed', 'year_review')");
  });

  it('不做 Pro 门控：年报路径既不查 tier 也不弹付费墙（自定决策 ①）', () => {
    const start = screenSrc.indexOf('const onShareYearReview');
    expect(start).toBeGreaterThan(-1);
    const body = screenSrc.slice(start, screenSrc.indexOf('\n  };', start));
    expect(body).not.toContain('isPro');
    expect(body).not.toContain('showProAlert');
    expect(body).not.toContain('Paywall');
    expect(body).not.toContain('40302');
  });

  it('整年零记录按空态提示（40402），与请求失败文案分开', () => {
    const start = screenSrc.indexOf('const onShareYearReview');
    const body = screenSrc.slice(start, screenSrc.indexOf('\n  };', start));
    expect(body).toContain('CODE_RESOURCE_NOT_FOUND');
    expect(body).toContain("t('insights.yearReviewEmpty')");
    expect(screenSrc).toMatch(/const CODE_RESOURCE_NOT_FOUND = 40402/);
  });

  it('分享文案直接取后端组装好的 share_text，前端不自己拼数字', () => {
    const start = screenSrc.indexOf('const onShareYearReview');
    const body = screenSrc.slice(start, screenSrc.indexOf('\n  };', start));
    expect(body).toContain('Share.share({ message: card.share_text })');
  });
});

describe('④ 15 语言键齐备且被引用', () => {
  const KEYS = ['insights.shareYearReview', 'insights.yearReviewEmpty'];

  it.each(KEYS)('%s：en + 14 个语言都有，值非空且不与 en 逐字同形', (key) => {
    const [ns, leaf] = key.split('.');
    const en = (resources as any).en.translation[ns][leaf];
    expect(typeof en).toBe('string');
    for (const lng of LANGUAGES) {
      const value = (resources as any)[lng].translation[ns][leaf];
      expect(typeof value).toBe('string');
      expect(value.trim()).not.toBe('');
      expect(value).not.toBe(en);
    }
  });

  it('两个键都真的被源码引用（不被引用就是死条目）', () => {
    expect(screenSrc).toContain("t('insights.shareYearReview')");
    expect(screenSrc).toContain("t('insights.yearReviewEmpty')");
  });

  it.each(LANGUAGES)('%s：文案里不含未替换的花括号占位符', (lng) => {
    for (const key of KEYS) {
      const [ns, leaf] = key.split('.');
      expect((resources as any)[lng].translation[ns][leaf]).not.toMatch(/\{\{|\}\}/);
    }
  });
});
