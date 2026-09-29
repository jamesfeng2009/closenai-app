/**
 * B2 第一周剧本前端契约测试（PRD §5.19.3 · 架构方案 §二 M2）。
 *
 * 断言三类会被真实操作暴露的硬失败：
 * 1. 二次打开报告页面变空态（acknowledge 把 sent 推成 seen，页面却只认 sent）；
 * 2. 时间线混进没有文案键的事件（level_reward 的 day_no 也是 7），界面直接显示键名；
 * 3. 任一语言缺 journey / reflect.memoryRefs 键，或占位符与屏幕实传参数漂移
 *    （i18next 缺参时把 {{n}} 原样打给用户）。
 */
import {
  getAckPending, getReport, getTimeline, JOURNEY_TIMELINE_KINDS,
} from '../src/config/journey';
import resources from '../src/i18n/locales';
import type { JourneyEvent, JourneyEventPayload } from '../src/types';

const LANGUAGES = Object.keys(resources);
const TRANSLATIONS = Object.fromEntries(
  LANGUAGES.map((lng) => [lng, (resources as any)[lng].translation]),
);

/** 屏幕实传参数；{{count}} 不在其列（i18next 见到 count 会转去查 key_one/key_other）。 */
const ALLOWED_PLACEHOLDERS = new Set(['n', 'date', 'arrow']);

const TOP_LEVEL_KEYS = [
  'title', 'subtitle', 'reportTitle', 'cta', 'empty',
  'homeEntryTitle', 'homeEntry', 'source',
  'statEntries', 'statCheckins', 'evidenceNote', 'timelineTitle',
];
const STATUSES = ['pending', 'sent', 'seen', 'skipped'];

function flatten(obj: any, prefix = ''): Record<string, string> {
  return Object.entries(obj).reduce<Record<string, string>>((acc, [k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') acc[key] = v;
    else if (v && typeof v === 'object') Object.assign(acc, flatten(v, key));
    return acc;
  }, {});
}

function journeyFlat(lng: string): Record<string, string> {
  return flatten(TRANSLATIONS[lng].journey ?? {}, 'journey');
}

function event(partial: Partial<JourneyEvent>): JourneyEvent {
  const payload: JourneyEventPayload = { kind: '', day_no: 0 };
  return {
    id: partial.id ?? 'e1',
    day_no: 0,
    event_kind: 'day7_report',
    status: 'sent',
    scheduled_at: '2026-08-12T12:00:00Z',
    acknowledged: false,
    payload,
    memory_refs: [],
    ...partial,
  };
}

describe('journey 15 语言文案齐备', () => {
  it('15 个语言全部在册', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)('%s：journey 顶层键齐备且非空', (lng) => {
    const flat = journeyFlat(lng);
    expect(TOP_LEVEL_KEYS.filter((key) => !flat[`journey.${key}`])).toEqual([]);
  });

  /** 时间线用 t(`journey.day.${event_kind}`)：缺键等于把键名印在屏幕上。 */
  it.each(LANGUAGES)('%s：时刻表 6 类事件都有 journey.day 文案', (lng) => {
    const flat = journeyFlat(lng);
    expect(JOURNEY_TIMELINE_KINDS.filter((kind) => !flat[`journey.day.${kind}`])).toEqual([]);
  });

  /** 状态徽标同理走 t(`journey.status.${status}`)，四个状态机取值都要有。 */
  it.each(LANGUAGES)('%s：四种事件状态都有 journey.status 文案', (lng) => {
    const flat = journeyFlat(lng);
    expect(STATUSES.filter((status) => !flat[`journey.status.${status}`])).toEqual([]);
  });

  it.each(LANGUAGES)('%s：reflect.memoryRefs（气泡「记忆依据 ▾」）已就位', (lng) => {
    const value = TRANSLATIONS[lng].reflect?.memoryRefs;
    expect(typeof value === 'string' && value.length > 0).toBe(true);
  });

  /**
   * 参数名守卫：屏幕只传 n / date / arrow。传错名字 i18next 不报错，
   * 而是把 {{x}} 连同花括号原样渲染；用 {{count}} 还会触发复数键查找。
   */
  it.each(LANGUAGES)('%s：journey / memoryRefs 占位符不超出屏幕实传参数', (lng) => {
    const targets = { ...journeyFlat(lng), 'reflect.memoryRefs': TRANSLATIONS[lng].reflect?.memoryRefs };
    const offenders: string[] = [];
    for (const [key, value] of Object.entries(targets)) {
      if (typeof value !== 'string') continue;
      for (const [, param] of value.matchAll(/\{\{\s*(\w+)\s*\}\}/g)) {
        if (!ALLOWED_PLACEHOLDERS.has(param)) offenders.push(`${lng}.${key} → {{${param}}}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('getReport：投递判定', () => {
  it('sent 可读', () => {
    expect(getReport([event({ status: 'sent' })])?.id).toBe('e1');
  });

  /** 回归：首屏打开即回执 → 状态变 seen；此前判据写死 sent，第二次进页面只剩空态。 */
  it('seen（二次打开）仍可读，不落空态', () => {
    const report = getReport([event({ status: 'seen', acknowledged: true })]);
    expect(report?.event_kind).toBe('day7_report');
  });

  it('pending / skipped / 缺行都不算已投递', () => {
    expect(getReport([event({ status: 'pending' })])).toBeNull();
    expect(getReport([event({ status: 'skipped' })])).toBeNull();
    expect(getReport([event({ event_kind: 'day5_stat' })])).toBeNull();
  });
});

describe('getTimeline：时刻表取数', () => {
  it('level_reward（day_no 同为 7）不进时间线', () => {
    const rows = getTimeline([
      event({ id: 'a', event_kind: 'day7_report', day_no: 7 }),
      event({ id: 'b', event_kind: 'level_reward', day_no: 7 }),
    ]);
    expect(rows.map((e) => e.id)).toEqual(['a']);
  });

  it('按剧本日序排列，skipped 行保留（数据不足也是这一周的事实）', () => {
    const rows = getTimeline([
      event({ id: 'd7', day_no: 7, event_kind: 'day7_report' }),
      event({ id: 'd3', day_no: 3, event_kind: 'day3_weak_assert' }),
      event({ id: 'd1', day_no: 1, event_kind: 'day1_checkin_echo', status: 'skipped' }),
    ]);
    expect(rows.map((e) => e.id)).toEqual(['d1', 'd3', 'd7']);
  });

  it('不改动入参数组（React state 不做原地 sort）', () => {
    const input = [
      event({ id: 'd7', day_no: 7, event_kind: 'day7_report' }),
      event({ id: 'd0', day_no: 0, event_kind: 'day0_read' }),
    ];
    getTimeline(input);
    expect(input.map((e) => e.id)).toEqual(['d7', 'd0']);
  });
});

describe('getAckPending：红点消费范围', () => {
  it('只回执已投递且未确认的事件', () => {
    const rows = getAckPending([
      event({ id: 'a', status: 'sent', acknowledged: false }),
      event({ id: 'b', status: 'seen', acknowledged: true }),
      event({ id: 'c', status: 'pending', acknowledged: false }),
      event({ id: 'd', status: 'skipped', acknowledged: false }),
    ]);
    expect(rows.map((e) => e.id)).toEqual(['a']);
  });
});
