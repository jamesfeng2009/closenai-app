/**
 * 首页每日卡与睡前复盘的取数判定闸（C4 / C2 / C1）。
 *
 * 这些判断的失效方式都是「改一行条件就静默错卡」：把已读的主题卡再端上来、
 * 周年标题写成 1 years ago、半程退出的复盘从第 1 步重跑覆盖用户上一句原话。
 * 它们全都不会报错，所以只能靠断言钉住——逻辑一律住在 src/config/dailyCards.ts，
 * 因为 jest 跑在 node 环境下渲染不了 RN 组件（jest.config.js 的 testEnvironment）。
 */
import {
  ARCHIVE_CARD_LIMIT, ARCHIVE_TITLE_KEY, archiveCards, archiveShareMessage,
  BEDTIME_CARD_START_MINUTES, isCrisisResponse, nextPhase, pickThemeCard,
  resumePhase, showBedtimeCard, weekCells, weekSlot, WEEK_SLOTS,
} from '../src/config/dailyCards';
import type {
  BedtimeRecord, BedtimeStepResponse, CurriculumDayCard, OnThisDayCandidate,
} from '../src/types';

const cand = (over: Partial<OnThisDayCandidate> = {}): OnThisDayCandidate => ({
  memory_id: 'm1',
  content: '今天把拖了三周的报告交了',
  years_ago: 1,
  created_at: '2024-10-14T12:00:00Z',
  ...over,
});

const day = (over: Partial<CurriculumDayCard> = {}): CurriculumDayCard => ({
  id: 'e1',
  date: '2026-10-14',
  day_no: 15,
  revealed: true,
  read: false,
  memory_refs: [],
  title: '把注意力放回自己身上',
  assertion: '你这周有两次主动收尾了对话。',
  question: null,
  ...over,
});

const rec = (steps: BedtimeRecord['steps'], completed = false): BedtimeRecord => ({
  id: 'r1',
  completed,
  content: '',
  steps,
  created_at: '2026-10-14T22:10:00Z',
});

describe('记忆考古卡 · 首页每日主位（C4）', () => {
  it('保持服务端年份升序，不自己重排', () => {
    const out = archiveCards([
      cand({ memory_id: 'a', years_ago: 1 }),
      cand({ memory_id: 'b', years_ago: 2 }),
      cand({ memory_id: 'c', years_ago: 3 }),
    ]);
    expect(out.map((c) => c.memory_id)).toEqual(['a', 'b', 'c']);
  });

  it(`首页只端 ${ARCHIVE_CARD_LIMIT} 张：服务端每年给 5 条、近 3 年最多 15 条，首页不是列表页`, () => {
    const many = Array.from({ length: 10 }, (_, i) => cand({ memory_id: `m${i}` }));
    expect(archiveCards(many)).toHaveLength(ARCHIVE_CARD_LIMIT);
  });

  it('空原话与缺 id 的候选丢弃——卡片标题下不能有空白引用块', () => {
    expect(archiveCards([cand({ content: '   ' }), cand({ content: '' }), cand({ memory_id: '' })])).toEqual([]);
    expect(archiveCards([cand({ content: '  ' }), cand()])).toHaveLength(1);
  });

  it('周年标题走 CLDR 复数键，不在前端拼「1 年 / N 年」两套串', () => {
    expect(ARCHIVE_TITLE_KEY).toBe('home.archiveTitle');
  });

  it('分享文本＝原话 + 深链，空行分隔；任一段为空就不留空行', () => {
    expect(archiveShareMessage('原话', 'https://closen.app/r/abc')).toBe('原话\n\nhttps://closen.app/r/abc');
    expect(archiveShareMessage('  原话  ', '  链接 ')).toBe('原话\n\n链接');
    expect(archiveShareMessage('原话', '')).toBe('原话');
    expect(archiveShareMessage('', '')).toBe('');
  });
});

describe('本周主题卡 · 首页次位（C2）', () => {
  it('取已揭晓未读里日期最大的一张（revealed 已由服务端按用户时区判过）', () => {
    const out = pickThemeCard([
      day({ id: 'd1', date: '2026-10-12' }),
      day({ id: 'd2', date: '2026-10-14' }),
      day({ id: 'd3', date: '2026-10-13' }),
    ]);
    expect(out?.id).toBe('d2');
  });

  it('已读的不算候选——全周读完就隐藏卡片，不端旧卡骚扰', () => {
    expect(pickThemeCard([day({ read: true }), day({ id: 'x', read: true })])).toBeNull();
  });

  it('未揭晓的未来日不算候选，即使 id 存在也不许把明天推成今天', () => {
    expect(pickThemeCard([day({ id: 'future', revealed: false, date: '2026-10-15' })])).toBeNull();
  });

  it('缺 id 的行丢弃（后端不会给，给了也是脏数据，不能渲染出无键卡片）', () => {
    expect(pickThemeCard([day({ id: '' })])).toBeNull();
  });

  it('weekSlot 用后端同一份 day_no 编码（ISO 周序*7 + 周内序），取模还原 0–6', () => {
    expect(weekSlot(14)).toBe(0);
    expect(weekSlot(15)).toBe(1);
    expect(weekSlot(20)).toBe(6);
    expect(weekSlot(21)).toBe(0);
    expect(weekSlot(0)).toBe(0);
  });

  it('weekCells 恒为 7 格：未生成的天补 future，不是少一格', () => {
    const cells = weekCells([day({ day_no: 14, read: true }), day({ day_no: 15 })]);
    expect(cells).toHaveLength(WEEK_SLOTS);
    expect(cells[0]).toBe('read');
    expect(cells[1]).toBe('unread');
    expect(cells.filter((c) => c === 'future')).toHaveLength(5);
  });

  it('未揭晓的格子是 future 而不是 unread（进度条不许显示还没到的今天）', () => {
    expect(weekCells([day({ day_no: 15, revealed: false })])[1]).toBe('future');
  });

  it('同槽位取日期较新的那条（与 get_week 的后写覆盖一致）', () => {
    const cells = weekCells([
      day({ day_no: 15, date: '2026-10-14', read: true }),
      day({ day_no: 22, date: '2026-10-21' }),
    ]);
    expect(cells[1]).toBe('unread');
  });
});

describe('睡前复盘 · 晚间兜底卡与三步流程（C1 / UI Screen 26）', () => {
  it('窗口起点＝本地 22:00（UI 原型规范 22:00–23:30），21:59 不出卡', () => {
    expect(BEDTIME_CARD_START_MINUTES).toBe(22 * 60);
    expect(showBedtimeCard(BEDTIME_CARD_START_MINUTES, false)).toBe(true);
    expect(showBedtimeCard(BEDTIME_CARD_START_MINUTES - 1, false)).toBe(false);
    expect(showBedtimeCard(23 * 60 + 59, false)).toBe(true);
  });

  it('今日已完成即整卡消失——晚间重复打扰的闸门就是完成态本身', () => {
    expect(showBedtimeCard(BEDTIME_CARD_START_MINUTES, true)).toBe(false);
  });

  it('危机分支只在 intercepted===true 时判定，缺该键一律按正常出参', () => {
    expect(isCrisisResponse({ intercepted: true, crisis: { kind: 'crisis', reply: 'r', hotline: 'h', advice_blocked: true } })).toBe(true);
    expect(isCrisisResponse({ step: 1, skipped: false, digest: {} as never, record: rec({}) })).toBe(false);
    expect(isCrisisResponse({ intercepted: false } as unknown as BedtimeStepResponse)).toBe(false);
  });

  it('半程恢复按已落库的最深一步续接：重发 step1 会覆盖用户上一句原话', () => {
    expect(resumePhase(null)).toBe('step1');
    expect(resumePhase(rec({}))).toBe('step1');
    expect(resumePhase(rec({ step1: { text: '句子', skipped: false } }))).toBe('step2');
    expect(resumePhase(rec({ step1: { text: null, skipped: true }, step2: { content: '值得记下的', memory_refs: [] } }))).toBe('step3');
    expect(resumePhase(rec({ step3: { content: '留给明天' } }, true))).toBe('done');
    expect(resumePhase(rec({}, true))).toBe('done');
  });

  it('跳过第 1 步也算已提交（skipped 行仍会让流程推进到第 2 步）', () => {
    expect(resumePhase(rec({ step1: { text: null, skipped: true } }))).toBe('step2');
  });

  it('提交成功后逐级推进：1→2、2→3、3→done', () => {
    expect(nextPhase('step1', { step: 1, skipped: false, digest: {} as never, record: rec({}) })).toBe('step2');
    expect(nextPhase('step2', { step: 2, content: 'c', memory_refs: [], record: rec({}) })).toBe('step3');
    expect(nextPhase('step3', { step: 3, content: 'c', completed: true, record: rec({}) })).toBe('done');
  });

  it('危机拦截时阶段原地不动，不许把转介当 step1 成功推进', () => {
    const crisis: BedtimeStepResponse = {
      intercepted: true,
      crisis: { kind: 'self_harm', reply: '先停一下', hotline: '988', advice_blocked: false },
    };
    expect(nextPhase('step1', crisis)).toBe('step1');
    expect(nextPhase('step2', crisis)).toBe('step2');
  });
});
