/**
 * KG-27 情绪统计可见性（纯前端）。
 *
 * 台账的三条验收在这里各自有闸：
 * ① 7/30 视图可达且不产生第二个时间真源——折线窗与摘要窗共用同一个 `moodRange`，
 *    折线数据源仍是 `/growth/timeline` 的 days；
 * ② 入口可达——Explore 与 Profile 各有一条进 Insights 情绪卡的直接入口；
 * ③ 跨年/时区边界有测试——窗口截取与热力矩阵都不碰本地时间语义。
 *
 * 另外钉住这一条修的原缺陷：卡片曾经整块挂在 `timeline?.days?.length` 上，
 * 新用户与拉取失败的人看到的是一片空白。现在三态（失败/空/有数据）各说一句人话。
 */
import * as fs from 'fs';
import * as path from 'path';

import resources from '../src/i18n/locales';
import { hasMoodInWindow, recentDays, toHeatMatrix, type ChartDay } from '../src/components/charts/transforms';

const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');

/** 只扫代码不扫注释：这里要判的正是「实现里不许有本地时间运算」，而说明文字里会提到 Date。 */
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

const day = (date: string, value: number): ChartDay => ({ date, value });

/** 跨年那一周：周日→周六，正好横跨 2025/2026。 */
const CROSS_YEAR: ChartDay[] = [
  day('2025-12-28', 3), day('2025-12-29', 4), day('2025-12-30', 2),
  day('2025-12-31', 5), day('2026-01-01', 1), day('2026-01-02', 4), day('2026-01-03', 3),
];

describe('① 窗口截取：跨年一天不吞', () => {
  it('乱序输入也先排升序再截尾（后端不保证窗口方向）', () => {
    const shuffled = [CROSS_YEAR[5], CROSS_YEAR[0], CROSS_YEAR[3], CROSS_YEAR[1], CROSS_YEAR[6]];
    const got = recentDays(shuffled, 3);
    expect(got.map((d) => d.date)).toEqual(['2025-12-31', '2026-01-02', '2026-01-03']);
  });

  it('跨年截到近 5 天：拿「一年中的第几天」排序会在这里翻车', () => {
    const got = recentDays(CROSS_YEAR, 5).map((d) => d.date);
    expect(got).toEqual([
      '2025-12-30', '2025-12-31', '2026-01-01', '2026-01-02', '2026-01-03',
    ]);
    // 反向证人：日序数排序（12 月≈362 > 1 月≈0）会把最新的一天排到窗口最前
    const dayOfYear = (d: ChartDay) =>
      Math.floor(
        (Date.parse(`${d.date}T00:00:00Z`) - Date.parse(`${d.date.slice(0, 4)}-01-01T00:00:00Z`)) / 86400000,
      );
    const byDoy = [...CROSS_YEAR].sort((a, b) => dayOfYear(a) - dayOfYear(b)).slice(-5).map((d) => d.date);
    expect(byDoy).not.toEqual(got);
    expect(byDoy.indexOf('2026-01-03')).toBeLessThan(byDoy.indexOf('2025-12-28'));
  });

  it('n 大于总天数时原样给回，不报错也不补空', () => {
    expect(recentDays(CROSS_YEAR, 90).length).toBe(CROSS_YEAR.length);
    expect(recentDays([], 7)).toEqual([]);
  });

  it('截取只做字符串排序：实现里不许出现 Date/时区换算（跨年与跨时区的根）', () => {
    const src = codeOnly(read('src/components/charts/transforms.ts'));
    const body = /export function recentDays\([\s\S]*?\n\}/.exec(src);
    expect(body).not.toBeNull();
    expect(body![0]).not.toMatch(/new Date|Date\.|getTime|toLocale|getDay\(\)/);
    expect(body![0]).toContain('localeCompare');
  });
});

describe('① 热力矩阵：跨年那一周落在哪一格是算得出的', () => {
  /**
   * 手工推出的期望（行=周一起始，0=周一…6=周日）：
   * 2025-12-28 是周日，正好是跨年那一周的第一格，所以它前面补 6 个 null；
   * 之后 12-29（周一）到 2026-01-03（周六）连着排进第二列——年界在列内，不在列间。
   *
   * 为什么这一条同时是时区闸：实现里若把 `T00:00:00Z` 换成不带 Z 的本地解析，
   * 本机（Asia/Shanghai，UTC+8）拿到的 UTC 日会往前挪一天，行号全体错位，这条立刻红。
   * 运行时改 `process.env.TZ` 在 jest 沙箱里不生效（实测设成 Pacific/Kiritimati 后
   * getHours 仍是 8），所以跨时区交付按「结构性判据 + 这条手算格子」+ 文档限制三件一起，
   * 见 docs/growth/mood-stats-audit-2026-11.md。
   */
  it('跨年 7 天各归其位，一天不吞也不挪行', () => {
    const matrix = toHeatMatrix(recentDays(CROSS_YEAR, 7));
    expect(matrix.map((row) => row.map((c) => c?.date ?? '·'))).toEqual([
      ['·', '2025-12-29'],
      ['·', '2025-12-30'],
      ['·', '2025-12-31'],
      ['·', '2026-01-01'],
      ['·', '2026-01-02'],
      ['·', '2026-01-03'],
      // 周日那列到 01-03 就停：矩阵不补尾部的空格（补了会让人以为那天也没记）
      ['2025-12-28'],
    ]);
    // 值也带上：12-31 是 5、01-01 是 1——跨年不重置
    expect(matrix[2][1]?.value).toBe(5);
    expect(matrix[3][1]?.value).toBe(1);
  });

  it('矩阵按 UTC 对齐（getUTCDay/toISOString），不是本地 getDay', () => {
    const src = codeOnly(read('src/components/charts/transforms.ts'));
    const body = /export function toHeatMatrix\([\s\S]*?\n\}/.exec(src);
    expect(body).not.toBeNull();
    expect(body![0]).toContain('getUTCDay');
    expect(body![0]).toContain('toISOString');
    expect(body![0]).not.toMatch(/\.getDay\(\)/);
    // 日期串一律带 Z 解析；少了这个 Z，本机 +8 就会把那天挪进前一格
    expect(body![0]).toContain('T00:00:00Z');
  });
});

describe('① 空态判据与折线断线判据同一条尺', () => {
  it('全 0 = 没记过；value=0 不算「情绪为零」', () => {
    expect(hasMoodInWindow([day('2026-01-01', 0), day('2026-01-02', 0)])).toBe(false);
    expect(hasMoodInWindow([])).toBe(false);
    expect(hasMoodInWindow([day('2026-01-01', 0), day('2026-01-02', 1)])).toBe(true);
  });

  it('阈值取自折线用的同一个 VALUE_MIN，不在两处各写一遍 1', () => {
    const src = codeOnly(read('src/components/charts/transforms.ts'));
    const body = /export function hasMoodInWindow\(days: ChartDay\[\]\): boolean \{\s*return ([^\n]+)\n?\}/.exec(src);
    expect(body).not.toBeNull();
    expect(body![1]).toContain('VALUE_MIN');
    expect(body![1]).not.toMatch(/>=\s*1\b/);
  });
});

describe('② 卡片常驻 + 三态分开说', () => {
  const insights = read('src/screens/InsightsScreen.tsx');

  it('旧的那个死条件已经不在了（卡片不再挂在 days 非空上）', () => {
    expect(codeOnly(insights)).not.toContain('{!!timeline?.days?.length && (');
  });

  it('三态齐备：拉取失败 / 一条没记 / 有数据', () => {
    expect(insights).toContain("{!timeline ? (");
    expect(insights).toContain('t(\'insights.moodLoadFailedTitle\')');
    expect(insights).toContain('t(\'insights.moodRetry\')');
    expect(insights).toContain(') : !anyMoodData ? (');
    expect(insights).toContain("t('insights.moodEmptyTitle')");
    expect(insights).toContain("t('insights.moodEmptyCta')");
    // 有数据态才画折线，且整年有记录、当前窗为空时另给一句窗口提示
    expect(insights).toContain("t('insights.moodWindowEmpty7')");
    expect(insights).toContain("t('insights.moodWindowEmpty30')");
  });

  it('失败态不说成「你还没签到」，也不给分享年报的空头承诺', () => {
    const from = insights.indexOf('{!timeline ? (');
    const to = insights.indexOf(') : !anyMoodData ? (');
    expect(from).toBeGreaterThanOrEqual(0);
    expect(to).toBeGreaterThan(from);
    const failed = insights.slice(from, to);
    expect(failed).toContain('moodLoadFailedTitle');
    expect(failed).toContain('moodRetry');
    // 失败态不说「还没有记录，去签到」那句（样式名可以复用，文案键不行）
    expect(failed).not.toContain("t('insights.moodEmptyCta')");
    // 年报两个出口整体挂在 anyMoodData 上（空态与失败态都不出现）。
    // 取 lastIndexOf：`{anyMoodData && (` 在热力图那枚切换按钮上先出现过一次。
    const at = insights.lastIndexOf('{anyMoodData && (');
    expect(at).toBeGreaterThan(to);
    const yearRow = insights.slice(at, at + 1800);
    expect(yearRow).toContain('onShareYearReview');
    expect(yearRow).toContain('onExportYearReviewPdf');
    expect(yearRow).not.toContain('moodEmptyCta');
  });

  it('空态不套模糊门：门后没有内容可解锁', () => {
    const from = insights.indexOf(') : !anyMoodData ? (');
    const to = insights.indexOf(') : (', from);
    expect(to).toBeGreaterThan(from);
    const empty = insights.slice(from, to);
    expect(empty).toContain('moodEmptyCta');
    expect(empty).not.toContain('InsightBlurGate');
  });

  it('签到入口指向 Reflect（签到面板与反思同一 tab），游客指向 Auth', () => {
    const go = /const goCheckin = \(\) => \{([\s\S]*?)\n  \};/.exec(codeOnly(insights));
    expect(go).not.toBeNull();
    expect(go![1]).toContain("navigation.navigate('Auth')");
    expect(go![1]).toContain("navigation.navigate('Tabs', { screen: 'Reflect' })");
  });

  it('7/30 切换是两枚可点按钮，不是一句说明文字', () => {
    expect(insights).toContain('MOOD_RANGE_OPTIONS.map((range) =>');
    expect(insights).toContain('onPress={() => setMoodRange(range)}');
    expect(insights).toContain("t('insights.moodRange7')");
    expect(insights).toContain("t('insights.moodRange30')");
    expect(insights).toContain('accessibilityState={{ selected: moodRange === range }}');
  });
});

describe('② 入口：Explore 与 Profile 都能一步进情绪卡', () => {
  const navigateCall = "navigation.navigate('Insights', { initialSection: 'mood' })";

  it('两个入口都在位', () => {
    expect(read('src/screens/ExploreScreen.tsx')).toContain(navigateCall);
    expect(read('src/screens/ProfileScreen.tsx')).toContain(navigateCall);
  });

  it('路由表声明了这个参数（否则 initialSection 是运行时才见的野参数）', () => {
    const app = read('App.tsx');
    expect(app).toContain("Insights: { initialSection?: 'mood' } | undefined;");
  });

  it('InsightsScreen 真会滚过去，且只滚一次', () => {
    const insights = read('src/screens/InsightsScreen.tsx');
    expect(insights).toContain("=== 'mood'");
    expect(insights).toContain('onLayout={onMoodCardLayout}');
    expect(insights).toContain('scrollRef.current?.scrollTo');
    expect(insights).toContain('if (!scrollToMood || moodScrolled.current) return;');
  });
});

describe('文案：15 语言齐、不含诊断承诺', () => {
  type Tree = Record<string, unknown>;
  const flatten = (obj: Tree, prefix = ''): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === 'object') Object.assign(out, flatten(v as Tree, key));
      else out[key] = String(v);
    }
    return out;
  };

  const KEYS = [
    'insights.moodRange7', 'insights.moodRange30',
    'insights.moodEmptyTitle', 'insights.moodEmptyDesc', 'insights.moodEmptyCta',
    'insights.moodLoadFailedTitle', 'insights.moodRetry',
    'insights.moodWindowEmpty7', 'insights.moodWindowEmpty30', 'insights.timelineTitle',
    'explore.moodStats', 'explore.moodStatsDesc', 'profile.moodStats',
  ];

  const langs = Object.keys(resources);
  const tables: Record<string, Record<string, string>> = Object.fromEntries(
    langs.map((l) => [l, flatten((resources as any)[l].translation as Tree)])
  );

  it('15 个语言文件都参与（守卫没扫成空集）', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(KEYS)('%s：15 语言都有值', (key) => {
    expect(langs.filter((l) => !tables[l][key])).toEqual([]);
  });

  it('天数切换的标签在各语言里都带着那个数字（不许翻成「本周/本月」丢了窗口口径）', () => {
    for (const lang of langs) {
      expect(tables[lang]['insights.moodRange7']).toContain('7');
      expect(tables[lang]['insights.moodRange30']).toContain('30');
    }
  });

  it('不含诊断/治疗类词（台账：不把情绪统计包装成诊断）', () => {
    const banned = /diagnos|treat|therapy|诊断|治疗|諮詢|咨询/i;
    const offenders: string[] = [];
    for (const lang of langs) {
      for (const key of KEYS) {
        if (banned.test(tables[lang][key])) offenders.push(`${lang}.${key}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
