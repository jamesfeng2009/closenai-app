/**
 * M5 演示档案契约测试（P1-1 · 架构方案 §二 M5）。
 *
 * 演示档案唯一的致命伤是"文案说的数字跟曲线对不上"——它会被印进商店截图
 * （护城河 §6.1：截图第一张必须是 Pattern 断言卡），也会被审核员逐字读。
 * 所以这里不测渲染，只测三件事：
 * 1. 数字自洽：断言卡每个参数都能由 mood 序列独立反算出来；
 * 2. 结构隔离：演示态是游客态，任何 api/service 调用都会 401，源码里就不许出现；
 * 3. 文案齐备：15 语言键齐、占位符不超出屏幕实传参数、且每个键真被引用。
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  DEMO_PERSONA_KEY, DEMO_WINDOW_DAYS, buildDemoArchive, isScrollViewAtEnd,
} from '../src/config/demoPersona';
import resources from '../src/i18n/locales';

const LANGUAGES = Object.keys(resources);
const TRANSLATIONS = Object.fromEntries(
  LANGUAGES.map((lng) => [lng, (resources as any)[lng].translation]),
);

/** 屏幕实传参数。传错名字 i18next 不报错，而是把 {{x}} 连花括号原样打给用户。 */
const ALLOWED_PLACEHOLDERS = new Set(['name', 'n', 'value', 'weeks', 'delta', 'refs']);

/** demo 命名空间的完整键集（多一个算冗余文案，少一个是白屏）。 */
const DEMO_KEYS = [
  'personaName', 'badge', 'back', 'title', 'subtitle',
  'overviewTitle', 'statCheckins', 'statMoodAvg', 'statStreak',
  'chartTitle', 'moodScale',
  'patternTitle', 'patternFriday', 'patternHoldIn', 'patternWeekend', 'patternBasis',
  'timelineTitle', 'timeline.win', 'timeline.low', 'timeline.start', 'dayNo',
  'memoryTitle', 'memory.conflict1', 'memory.conflict2', 'memory.conflict3',
  'memory.win', 'memory.value',
  'disclaimer', 'cta', 'scrollHint',
];

const FRIDAY = 5;

/** 7 个锚点覆盖一周的每一种起始星期：留白/周五落点会随锚点整体平移。 */
const ANCHORS = [
  '2026-09-25', '2026-09-26', '2026-09-27', '2026-09-28',
  '2026-09-29', '2026-09-30', '2026-10-01',
];

function flatten(obj: any, prefix = ''): Record<string, string> {
  return Object.entries(obj).reduce<Record<string, string>>((acc, [k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') acc[key] = v;
    else if (v && typeof v === 'object') Object.assign(acc, flatten(v, key));
    return acc;
  }, {});
}

function weekdayOf(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

function dayNoOf(iso: string): string {
  return iso.slice(-2);
}

function nextDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + 1));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/** 独立反算「窗口末尾起连续几个周五低落」，不复用被测模块的实现。 */
function expectedLowFridayWeeks(archive: ReturnType<typeof buildDemoArchive>): number {
  let weeks = 0;
  for (let i = archive.days.length - 1; i >= 0; i -= 1) {
    const d = archive.days[i];
    if (weekdayOf(d.date) !== FRIDAY) continue;
    if (d.mood >= 1 && d.mood <= 2) weeks += 1;
    else break;
  }
  return weeks;
}

const archives = ANCHORS.map((today) => ({ today, archive: buildDemoArchive(today) }));

describe('演示档案：数字与曲线自洽', () => {
  it.each(ANCHORS)('%s：窗口 30 天、日期连续、末日就是锚点', (today) => {
    const { days } = buildDemoArchive(today);
    expect(days).toHaveLength(DEMO_WINDOW_DAYS);
    expect(days.map((d) => d.dayNo)).toEqual(Array.from({ length: 30 }, (_, i) => i + 1));
    expect(days[days.length - 1].date).toBe(today);
    expect(days.slice(1).every((d, i) => nextDate(days[i].date) === d.date)).toBe(true);
    expect(days.every((d) => weekdayOf(d.date) === d.weekday)).toBe(true);
  });

  it.each(ANCHORS)('%s：4 天留白且绝不吃掉周五', (today) => {
    const { days } = buildDemoArchive(today);
    const misses = days.filter((d) => d.mood === 0);
    expect(misses).toHaveLength(4);
    expect(misses.filter((d) => d.weekday === FRIDAY)).toEqual([]);
    expect(days.filter((d) => d.mood > 0)).toHaveLength(26);
  });

  /** 主打断言是"周五全周最低"，曲线必须先成立，卡片上的 weeks 才有意义。 */
  it.each(ANCHORS)('%s：每个有数据的周五都 ≤2，且窗口内至少 4 个周五', (today) => {
    const { days } = buildDemoArchive(today);
    const fridays = days.filter((d) => d.weekday === FRIDAY);
    expect(fridays.length).toBeGreaterThanOrEqual(4);
    expect(fridays.filter((d) => d.mood > 0 && d.mood > 2)).toEqual([]);
  });

  it.each(ANCHORS)('%s：三条 Pattern 齐备，id 固定', (today) => {
    const { patterns } = buildDemoArchive(today);
    expect(patterns.map((p) => p.id)).toEqual(['friday_dip', 'hold_it_in', 'weekend_lift']);
    expect(patterns.map((p) => p.textKey)).toEqual([
      'demo.patternFriday', 'demo.patternHoldIn', 'demo.patternWeekend',
    ]);
  });

  it.each(ANCHORS)('%s：weeks 由曲线独立反算得出，且依据全是低落周五', (today) => {
    const archive = buildDemoArchive(today);
    const byDayNo = new Map(archive.days.map((d) => [d.dayNo, d]));
    const friday = archive.patterns.find((p) => p.id === 'friday_dip')!;
    expect(friday.params.weeks).toBe(expectedLowFridayWeeks(archive));
    expect(Number(friday.params.weeks)).toBeGreaterThanOrEqual(3);
    expect(friday.evidence.every((n) => {
      const d = byDayNo.get(n)!;
      return weekdayOf(d.date) === FRIDAY && d.mood >= 1 && d.mood <= 2;
    })).toBe(true);
  });

  /** 「写了算了、第二天更低」——evidence 里每一天都必须真的次日更低。 */
  it.each(ANCHORS)('%s：hold_in 的每个样本日都满足「当天 ≥3 且次日严格更低」', (today) => {
    const archive = buildDemoArchive(today);
    const byDate = new Map(archive.days.map((d) => [d.date, d]));
    const hold = archive.patterns.find((p) => p.id === 'hold_it_in')!;
    expect(hold.evidence.length).toBeGreaterThanOrEqual(2);
    expect(hold.params.n).toBe(hold.evidence.length);
    expect(Object.keys(hold.params)).toEqual(['n']);
    for (const n of hold.evidence) {
      const day = archive.days.find((d) => d.dayNo === n)!;
      const next = byDate.get(nextDate(day.date));
      expect(day.mood).toBeGreaterThanOrEqual(3);
      expect(next).toBeDefined();
      expect(next!.mood).toBeGreaterThan(0);
      expect(next!.mood).toBeLessThan(day.mood);
    }
    // dayNo 连续窗口里，样本日彼此至少隔 5 天，避免"同一件事被数三遍"
    const sorted = [...hold.evidence].sort((a, b) => a - b);
    expect(sorted.slice(1).every((n, i) => n - sorted[i] >= 5)).toBe(true);
  });

  it.each(ANCHORS)('%s：weekend_lift 的 delta 等于两个均值之差且为正', (today) => {
    const archive = buildDemoArchive(today);
    const lift = archive.patterns.find((p) => p.id === 'weekend_lift')!;
    const expected = round1(archive.stats.weekendAvg - archive.stats.weekdayAvg);
    expect(expected).toBeGreaterThan(0);
    expect(lift.params.delta).toBe(expected.toFixed(1));
    expect(lift.evidence.every((n) => {
      const d = archive.days.find((x) => x.dayNo === n)!;
      return d.mood > 0 && (d.weekday === 0 || d.weekday === 6);
    })).toBe(true);
  });

  it.each(ANCHORS)('%s：stats 全部由序列重算得出', (today) => {
    const { days, stats } = buildDemoArchive(today);
    const checked = days.filter((d) => d.mood > 0);
    expect(stats.days).toBe(30);
    expect(stats.checkins).toBe(checked.length);
    expect(stats.moodAvg).toBe(round1(checked.reduce((s, d) => s + d.mood, 0) / checked.length));
    expect(stats.bestMood).toBe(Math.max(...checked.map((d) => d.mood)));
    let run = 0;
    let streak = 0;
    for (const d of days) {
      run = d.mood > 0 ? run + 1 : 0;
      streak = Math.max(streak, run);
    }
    expect(stats.streak).toBe(streak);
  });

  it.each(ANCHORS)('%s：5 条原话日期互不重复，conflict 与 hold_in 同批', (today) => {
    const archive = buildDemoArchive(today);
    expect(archive.memories).toHaveLength(5);
    expect(archive.memories.map((m) => m.dayNo).sort((a, b) => a - b))
      .toEqual([...new Set(archive.memories.map((m) => m.dayNo))].sort((a, b) => a - b));
    expect(archive.memories.map((m) => m.tag).sort()).toEqual(
      ['conflict', 'conflict', 'conflict', 'value', 'win'],
    );
    const hold = archive.patterns.find((p) => p.id === 'hold_it_in')!;
    expect(archive.memories.filter((m) => m.tag === 'conflict').map((m) => m.dayNo))
      .toEqual(hold.evidence);
    // 高光日不许和「算了」撞同一天，也不许是第 1 天（价值观那条占第 1 天）
    const win = archive.memories.find((m) => m.tag === 'win')!;
    expect(hold.evidence).not.toContain(win.dayNo);
    expect(win.dayNo).not.toBe(1);
  });

  it.each(ANCHORS)('%s：2 条时间线卡片落在窗口内且不重复', (today) => {
    const archive = buildDemoArchive(today);
    expect(archive.timeline).toHaveLength(2);
    const dayNos = archive.timeline.map((t) => t.dayNo);
    expect(new Set(dayNos).size).toBe(2);
    expect(dayNos.every((n) => n >= 1 && n <= DEMO_WINDOW_DAYS)).toBe(true);
    expect(archive.timeline.map((t) => t.textKey).every((k) => k.startsWith('demo.timeline.')))
      .toBe(true);
  });

  it('同一锚点两次构建完全相等（演示数据不许有随机味）', () => {
    expect(buildDemoArchive('2026-09-29')).toEqual(buildDemoArchive('2026-09-29'));
  });

  it('参数里没有 NaN / undefined（i18next 会把它印成字面量）', () => {
    for (const { archive } of archives) {
      for (const p of archive.patterns) {
        for (const [k, v] of Object.entries(p.params)) {
          expect(`${k}=${String(v)}`).not.toMatch(/undefined|NaN/);
        }
      }
    }
  });

  it('人物名走 i18n 键，不写死中文', () => {
    expect(buildDemoArchive('2026-09-29').personaKey).toBe(DEMO_PERSONA_KEY);
    expect(DEMO_PERSONA_KEY).toBe('demo.personaName');
  });
});

describe('看完率触底判定', () => {
  const ev = (viewport: number, y: number, content: number) =>
    isScrollViewAtEnd({
      layoutMeasurement: { height: viewport },
      contentOffset: { y },
      contentSize: { height: content },
    });

  it('半程不算看完', () => {
    expect(ev(800, 0, 3000)).toBe(false);
    expect(ev(800, 1200, 3000)).toBe(false);
  });

  /** 内容不足一屏（短文案 / 大屏）也必须算看完，否则这条指标恒 0。 */
  it('一页放得下时直接算看完', () => {
    expect(ev(800, 0, 700)).toBe(true);
    expect(ev(800, 0, 800)).toBe(true);
  });

  /** 32px 容差：差一点点够不着底也要算看完（亚像素/回弹）。 */
  it('贴底与容差边界', () => {
    expect(ev(800, 2200, 3000)).toBe(true);
    expect(ev(800, 2168, 3000)).toBe(true); // 3000 - 800 - 32
    expect(ev(800, 2167, 3000)).toBe(false);
  });

  it('屏幕越矮越难触底（iPhone SE 上仍要能触发）', () => {
    expect(ev(400, 2600, 3000)).toBe(true);
    expect(ev(400, 2500, 3000)).toBe(false);
  });
});

describe('演示档案：游客态隔离', () => {
  const root = path.join(__dirname, '..');
  const read = (p: string) => fs.readFileSync(path.join(root, p), 'utf8');
  /** 注释里写着"不 import api""不跳 Messages"——断言只看代码，否则守卫会被自己的文档骗过。 */
  const codeOnly = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const importsOf = (src: string) => [...src.matchAll(/\bfrom\s+'([^']+)'/g)].map((m) => m[1]);

  const dataSrc = codeOnly(read('src/config/demoPersona.ts'));
  const screenSrc = codeOnly(read('src/screens/DemoArchiveScreen.tsx'));

  /**
   * 架构方案 §二 M5 要求 is_demo 字段隔离；引导阶段是游客、无 token，
   * 调任何接口都必 401，所以这里用"源码里不可能发请求"替代数据库标记。
   * 这条断言是那道偏离的守门人：谁往演示数据里加一个 api 调用，它就红。
   */
  it('数据层零依赖：不 import 任何东西（连 react 都不需要）', () => {
    expect(importsOf(dataSrc)).toEqual([]);
    expect(dataSrc).not.toMatch(/AsyncStorage|fetch\(|axios|XMLHttpRequest/);
  });

  /** 屏幕只准 import 视图依赖 + analytics（事件口径要求），本地只准碰 colors 与数据层。 */
  it('展示层除 analytics 外不发请求，也没有任何路由跳转能力', () => {
    const local = Array.from(new Set(importsOf(screenSrc).filter((s) => s.startsWith('..'))));
    expect(local.sort()).toEqual(
      ['../config/demoPersona', '../services/analytics', '../theme/colors'].sort(),
    );
    expect(screenSrc).not.toMatch(/\bnavigate\(|\bnavigation\b|useNavigation/);
    expect(screenSrc).not.toMatch(/MessagesScreen|ReflectScreen|ChatScreen/);
  });

  it('入口挂在 Onboarding Step 1，CTA 只回引导、不 finish（保住初读卡生成率）', () => {
    const onboarding = read('src/screens/OnboardingScreen.tsx');
    expect(onboarding).toMatch(/onboarding\.demoEntry/);
    expect(onboarding).toMatch(/step === 1 && showDemo/);
    expect(onboarding).toMatch(/<DemoArchiveScreen onClose=\{\(\) => setShowDemo\(false\)\} \/>/);
    // 演示页的关闭不许直接 finish()：那会跳过 Step 3，初读卡就没了
    expect(onboarding).not.toMatch(/<DemoArchiveScreen[^>]*onClose=\{\(\) => finish/);
  });
});

/**
 * 三处「实现与原文档不一致」的落点，各配一条结构闸（tasks.md B3 偏离 1 / 5 / 6）。
 *
 * 这三条都很容易被"顺手改回去"破坏：把数据搬去 src/data、把小 A 换成打包 JSON、
 * 或往屏里再塞一类内容。闸只认文件位与源码结构，不认注释。
 */
describe('M5 偏离面：文件位 / 生成方式 / 三段结构', () => {
  const root = path.join(__dirname, '..');
  const exists = (p: string) => fs.existsSync(path.join(root, p));
  const read = (p: string) => fs.readFileSync(path.join(root, p), 'utf8');
  const dataSrc = read('src/config/demoPersona.ts');
  const screenSrc = read('src/screens/DemoArchiveScreen.tsx');

  /** 偏离 1：§二 M5 原文写 `src/data/demoArchive.ts`，实落 `src/config/`（纯模块位）。 */
  it('数据层住 src/config/demoPersona.ts，src/data/ 与 .tsx 都不许出现', () => {
    expect(exists('src/config/demoPersona.ts')).toBe(true);
    expect(exists('src/config/demoPersona.tsx')).toBe(false);
    expect(exists('src/data/demoArchive.ts')).toBe(false);
    expect(exists('src/data')).toBe(false);
    // .tsx 里放纯数据就无法在 node 环境断言（jest testMatch 只收 .test.ts）
    expect(screenSrc).toContain("from '../config/demoPersona'");
  });

  /** 偏离 5：P1-1 第 4 条的「本地打包 assets/demo_profile.json」→ 改为函数生成。 */
  it('没有打包 JSON 资产，数字一律由 buildDemoArchive 现算', () => {
    expect(exists('assets/demo_profile.json')).toBe(false);
    expect(exists('assets')).toBe(false);
    expect(dataSrc).not.toMatch(/demo_profile|\.json|require\(|JSON\.parse|fs\.readFile/);
    // 三类内容（Pattern / 原话 / 时间线）同源于一次 days 生成：换锚点就整体平移
    const archive = buildDemoArchive('2026-09-29');
    const dayNos = new Set(archive.days.map((d) => d.dayNo));
    expect(archive.patterns.every((p) => p.evidence.every((n) => dayNos.has(n)))).toBe(true);
    expect(archive.memories.every((m) => dayNos.has(m.dayNo))).toBe(true);
    expect(archive.timeline.every((t) => dayNos.has(t.dayNo))).toBe(true);
  });

  /** 偏离 6：P1-1 第 2 条列了「1 条 AI 断言示例」→ 没另造第五类内容。 */
  it('屏内只有概览 / Pattern / 时间线+原话四类卡片，没有第五类断言示例', () => {
    const titles = [...screenSrc.matchAll(/<Text style=\{styles\.cardTitle\}>.*?\{t\('demo\.(\w+)'\)/g)]
      .map((m) => m[1]);
    expect(titles).toEqual(['overviewTitle', 'patternTitle', 'timelineTitle', 'memoryTitle']);
    expect(DEMO_KEYS.filter((k) => /assertion|example/i.test(k))).toEqual([]);
  });

  /** 偏离 6 的正面：断言示例这张卡的形态就是「Pattern + 依据行」，每条都得有依据。 */
  it('每条 Pattern 都渲染 demo.patternBasis 依据行，且依据非空', () => {
    expect(screenSrc).toMatch(
      /const basis = \(pattern: DemoPattern\) =>\s*\n\s*t\('demo\.patternBasis', \{ refs: pattern\.evidence\.join\(' · '\) \}\);/,
    );
    // 依据行只在 Pattern 卡的 map 里渲染一次，且渲染的就是当条 pattern 自己的 evidence
    const map = screenSrc.slice(
      screenSrc.indexOf('{archive.patterns.map('),
      screenSrc.indexOf('{archive.memories.map('),
    );
    expect([...map.matchAll(/basis\(p\)/g)]).toHaveLength(1);
    expect(map).toContain('<Text style={styles.patternBasis}>{basis(p)}</Text>');
    for (const { archive } of archives) {
      expect(archive.patterns.every((p) => p.evidence.length > 0)).toBe(true);
    }
  });
});

describe('演示档案：15 语言文案', () => {
  it('15 个语言全部在册', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)('%s：demo 键集与清单逐字相等且非空', (lng) => {
    const flat = flatten(TRANSLATIONS[lng].demo ?? {}, 'demo');
    expect(Object.keys(flat).sort()).toEqual(DEMO_KEYS.map((k) => `demo.${k}`).sort());
    expect(Object.entries(flat).filter(([, v]) => !v.trim())).toEqual([]);
  });

  it.each(LANGUAGES)('%s：onboarding.demoEntry 已就位', (lng) => {
    const value = TRANSLATIONS[lng].onboarding?.demoEntry;
    expect(typeof value === 'string' && value.length > 0).toBe(true);
  });

  it.each(LANGUAGES)('%s：demo 占位符不超出屏幕实传参数', (lng) => {
    const flat = flatten(TRANSLATIONS[lng].demo ?? {}, 'demo');
    const offenders: string[] = [];
    for (const [key, value] of Object.entries(flat)) {
      for (const m of value.matchAll(/\{\{(\w+)\}\}/g)) {
        if (!ALLOWED_PLACEHOLDERS.has(m[1])) offenders.push(`${key} → {{${m[1]}}}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  /** 屏幕只传 name / refs，数字全部来自 buildDemoArchive 的参数对象。 */
  it.each(LANGUAGES)('%s：带参数的键恰好只用了它需要的参数', (lng) => {
    const flat = flatten(TRANSLATIONS[lng].demo ?? {}, 'demo');
    const EXPECTED: Record<string, string[]> = {
      'demo.title': ['name'],
      'demo.statCheckins': ['n'],
      'demo.statMoodAvg': ['value'],
      'demo.statStreak': ['n'],
      'demo.patternFriday': ['weeks'],
      'demo.patternHoldIn': ['n'],
      'demo.patternWeekend': ['delta'],
      'demo.patternBasis': ['refs'],
      'demo.dayNo': ['n'],
    };
    for (const [key, params] of Object.entries(EXPECTED)) {
      const used = [...flat[key].matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
      expect(used).toEqual([...params].sort());
    }
    // 其余键不许有占位符（屏幕不会给它们传参，{{x}} 会原样印在屏幕上）
    const others = Object.entries(flat).filter(([k]) => !Object.keys(EXPECTED).includes(k));
    expect(others.filter(([, v]) => /\{\{/.test(v)).map(([k]) => k)).toEqual([]);
  });

  /** 键名打错一个字母就是白屏：每个 demo.* 键都必须真被引用。 */
  it('每个 demo 键都被屏幕或数据层引用（无死文案、无笔误键名）', () => {
    const root = path.join(__dirname, '..');
    const src = [
      fs.readFileSync(path.join(root, 'src/screens/DemoArchiveScreen.tsx'), 'utf8'),
      fs.readFileSync(path.join(root, 'src/config/demoPersona.ts'), 'utf8'),
    ].join('\n');
    const missing = DEMO_KEYS.filter((key) => !src.includes(`demo.${key}`));
    expect(missing).toEqual([]);
  });

  /** 数据层产出的 textKey 必须在每个语言里都存在，否则屏幕上直接显示键名。 */
  it.each(LANGUAGES)('%s：数据层给出的 textKey 全部有译文', (lng) => {
    const flat = flatten(TRANSLATIONS[lng].demo ?? {}, 'demo');
    const archive = buildDemoArchive('2026-09-29');
    const keys = [
      ...archive.patterns.map((p) => p.textKey),
      ...archive.memories.map((m) => m.textKey),
      ...archive.timeline.map((t) => t.textKey),
      DEMO_PERSONA_KEY,
    ];
    expect(keys.filter((k) => !flat[k])).toEqual([]);
  });

  /** 断言卡的依据行用窗口日序，不许把 ISO 日期直接甩给用户。 */
  it.each(ANCHORS)('%s：日序参数没有把年份/横杠带进文案', (today) => {
    const archive = buildDemoArchive(today);
    const refs = archive.patterns.map((p) => p.evidence.join(' · '));
    expect(refs.every((r) => /^\d+( · \d+)*$/.test(r))).toBe(true);
    expect(dayNoOf(archive.days[0].date)).toMatch(/^\d{2}$/);
  });
});
