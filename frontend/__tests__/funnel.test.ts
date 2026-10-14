/**
 * §5.19.5 获客漏斗埋点（H3）测试。
 *
 * 两件事：
 * 1. funnel.ts 的一次性门闸语义（每个节点每台设备只报一次）+ 属性合规；
 * 2. 六个屏幕的调用点在位，且没有「代码里冒出一个测试不认识的 step/source」的漂移。
 *
 * analytics 与 AsyncStorage 都被 mock：真 analytics 会拖进 api/SQLite 依赖链，node 环境跑不动。
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
      // 每个用例要独立：门闸状态如果跨用例残留，「第二次不报」会因为上一条测试写过而假绿
      clear: jest.fn(async () => { store.clear(); }),
    },
  };
});

const trackMock = jest.fn();
jest.mock('../src/services/analytics', () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  FUNNEL_SOURCES,
  funnelEventName,
  funnelProperties,
  ONE_TIME_STEPS,
  onceKey,
  SHARE_STEPS,
  trackFunnelOnce,
  trackFunnelShare,
} from '../src/services/funnel';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
const readBackend = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', 'backend', rel), 'utf8');

const quotedKeys = (src: string) =>
  Array.from(src.matchAll(/["']([a-z_]+)["']/g)).map((m) => m[1]);

beforeEach(async () => {
  trackMock.mockClear();
  (AsyncStorage.getItem as jest.Mock).mockClear();
  (AsyncStorage.setItem as jest.Mock).mockClear();
  await AsyncStorage.clear();
});

describe('事件名与属性', () => {
  it('事件统一带 funnel_ 前缀（PostHog 侧按前缀就能排出漏斗）', () => {
    for (const step of [...ONE_TIME_STEPS, ...SHARE_STEPS]) {
      expect(funnelEventName(step)).toBe(`funnel_${step}`);
    }
  });

  it('属性只用 feature/source 两个键，feature 固定 funnel', () => {
    expect(funnelProperties()).toEqual({ feature: 'funnel' });
    expect(funnelProperties('first_read')).toEqual({ feature: 'funnel', source: 'first_read' });
  });

  it('五段台阶 + 两个分享环路事件都在枚举里，一个不多一个不少', () => {
    expect([...ONE_TIME_STEPS].sort()).toEqual([
      'first_checkin', 'first_read_opened', 'first_record_created', 'week1_report_opened',
    ].sort());
    expect([...SHARE_STEPS].sort()).toEqual(['share_completed', 'share_tapped']);
    expect([...FUNNEL_SOURCES].sort()).toEqual([
      'first_read', 'life_archive', 'memory', 'period_report', 'personality_card', 'year_review',
    ].sort());
  });
});

describe('双端埋点白名单对齐', () => {
  // 前端 analytics.ts 与后端 analytics_service.py 各有一份 ALLOWED_PROPERTY_KEYS。
  // 漂移的后果是静默丢事件：前端入队成功、后端匿名化时整条属性被剔空。
  const front = read('src/services/analytics.ts').match(
    /const ALLOWED_PROPERTY_KEYS = new Set\(\[([\s\S]*?)\]\)/
  )?.[1];
  const back = readBackend('services/analytics_service.py').match(
    /ALLOWED_PROPERTY_KEYS = frozenset\(\s*\{([\s\S]*?)\}\s*\)/
  )?.[1];

  it('两份白名单都能从源码里取到（防止改写法把守卫变成空跑）', () => {
    expect(front).toBeTruthy();
    expect(back).toBeTruthy();
  });

  it('两份白名单键集完全一致', () => {
    const f = new Set(quotedKeys(front!));
    const b = new Set(quotedKeys(back!));
    const onlyFront = [...f].filter((k) => !b.has(k));
    const onlyBack = [...b].filter((k) => !f.has(k));
    expect(onlyFront).toEqual([]);
    expect(onlyBack).toEqual([]);
    expect(f.size).toBeGreaterThan(10);
  });

  it('漏斗事件的每个属性键都在两份白名单内', () => {
    const allowed = new Set(quotedKeys(back!));
    for (const source of [undefined, ...FUNNEL_SOURCES]) {
      for (const key of Object.keys(funnelProperties(source))) {
        expect(allowed.has(key)).toBe(true);
      }
    }
  });
});

describe('一次性节点门闸', () => {
  it.each([...ONE_TIME_STEPS])('%s：首次达成入队一条并落门闸', async (step) => {
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(null as string | null);
    const fired = await trackFunnelOnce(step);
    expect(fired).toBe(true);
    expect(trackMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledWith(`funnel_${step}`, { feature: 'funnel' });
    expect(AsyncStorage.setItem).toHaveBeenCalledWith(
      `@closenai/funnel_once_${step}`,
      expect.any(String)
    );
  });

  it.each([...ONE_TIME_STEPS])('%s：报过第二次不再入队（否则分母被 DAU 淹没）', async (step) => {
    await trackFunnelOnce(step);
    trackMock.mockClear();
    const fired = await trackFunnelOnce(step);
    expect(fired).toBe(false);
    expect(trackMock).not.toHaveBeenCalled();
  });

  it('门闸键名带 step，四个节点互不干扰', () => {
    expect(onceKey('first_checkin')).toBe('@closenai/funnel_once_first_checkin');
    expect(new Set(ONE_TIME_STEPS.map(onceKey)).size).toBe(ONE_TIME_STEPS.length);
  });

  it('读 Storage 抛错时照常上报（宁可重复也不能让整个节点哑火）', async () => {
    (AsyncStorage.getItem as jest.Mock).mockRejectedValueOnce(new Error('boom'));
    await expect(trackFunnelOnce('first_read_opened')).resolves.toBe(true);
    expect(trackMock).toHaveBeenCalledTimes(1);
  });

  it('写 Storage 抛错时事件已入队（重复上报的代价小于丢整个节点）', async () => {
    (AsyncStorage.setItem as jest.Mock).mockRejectedValueOnce(new Error('boom'));
    await expect(trackFunnelOnce('first_record_created')).resolves.toBe(true);
    expect(trackMock).toHaveBeenCalledTimes(1);
  });
});

describe('分享环路事件', () => {
  it.each([...SHARE_STEPS])('%s：可重复上报，且带 source', (step) => {
    trackFunnelShare(step, 'personality_card');
    trackFunnelShare(step, 'period_report');
    expect(trackMock).toHaveBeenCalledTimes(2);
    expect(trackMock).toHaveBeenNthCalledWith(1, `funnel_${step}`, {
      feature: 'funnel',
      source: 'personality_card',
    });
  });

  it('分享不走一次性门闸（分享次数是病毒环路的分子）', () => {
    trackFunnelShare('share_tapped', 'first_read');
    expect(AsyncStorage.getItem).not.toHaveBeenCalled();
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
  });
});

describe('调用点在位（.tsx 无法渲染，按源码守卫）', () => {
  const oneTimeSites: [string, string][] = [
    ['src/screens/FirstReadCardScreen.tsx', 'first_read_opened'],
    ['src/screens/ReflectScreen.tsx', 'first_record_created'],
    ['src/screens/HomeScreen.tsx', 'first_checkin'],
    ['src/screens/Week1ReportScreen.tsx', 'week1_report_opened'],
  ];

  it.each(oneTimeSites)('%s 调用 trackFunnelOnce 且只调这一个台阶', (rel, step) => {
    const src = read(rel);
    expect(src).toContain(`trackFunnelOnce('${step}')`);
    const used = Array.from(src.matchAll(/trackFunnelOnce\(['"]([a-z_0-9]+)['"]/g)).map((m) => m[1]);
    expect(used).toEqual([step]);
  });

  it.each(oneTimeSites)('%s 已 import funnel 模块', (rel) => {
    expect(read(rel)).toMatch(/from '\.\.\/services\/funnel'/);
  });

  it('ReflectScreen 的首条记录判定挂在「发送前消息为空」上', () => {
    const src = read('src/screens/ReflectScreen.tsx');
    const line = src.split('\n').find((l) => l.includes("trackFunnelOnce('first_record_created')")) ?? '';
    expect(line).toContain('messages.length === 0');
  });

  it('FirstReadCardScreen 只在卡片非空时计数（空态不算看到初读卡）', () => {
    const src = read('src/screens/FirstReadCardScreen.tsx');
    const i = src.indexOf("trackFunnelOnce('first_read_opened')");
    expect(i).toBeGreaterThan(-1);
    expect(src.slice(Math.max(0, i - 260), i)).toContain('if (card)');
  });

  it('Week1ReportScreen 只在报告投递后计数', () => {
    const src = read('src/screens/Week1ReportScreen.tsx');
    const i = src.indexOf("trackFunnelOnce('week1_report_opened')");
    expect(src.slice(Math.max(0, i - 260), i)).toContain('if (report)');
  });

  it('HomeScreen 的签到埋点在成功路径（moodCheckin 之后、catch 之前）', () => {
    const src = read('src/screens/HomeScreen.tsx');
    const start = src.indexOf('const onMoodPress');
    expect(start).toBeGreaterThan(-1);
    const catchAt = src.indexOf('} catch (e) {', start);
    // 找不到 catch 时 slice(start, -1) 会退化成「整个文件尾部」，断言就白跑了
    expect(catchAt).toBeGreaterThan(start);
    const tryBlock = src.slice(start, catchAt);
    expect(tryBlock).toContain('await moodCheckin(emoji)');
    expect(tryBlock).toContain("trackFunnelOnce('first_checkin')");
    expect(src.split("trackFunnelOnce('first_checkin')").length - 1).toBe(1);
  });

  const shareSites: [string, string][] = [
    ['src/screens/FirstReadCardScreen.tsx', 'first_read'],
    ['src/screens/ShareCardScreen.tsx', 'personality_card'],
    ['src/screens/InsightsScreen.tsx', 'period_report'],
    ['src/screens/InsightsScreen.tsx', 'year_review'],
  ];

  it.each(shareSites)('%s：调起上报 tapped、真的分享出去才报 completed', (rel, source) => {
    const src = read(rel);
    expect(src).toContain(`trackFunnelShare('share_tapped', '${source}')`);
    expect(src).toContain(`trackFunnelShare('share_completed', '${source}')`);
    // 取消分享在 iOS 同样 resolve，必须按 action 判完成，否则完成率＝100% 是假的
    expect(src).toContain('res.action === Share.sharedAction');
    // 一个屏里可以挂多种物料（InsightsScreen 同时有周期报告与年报卡），
    // 所以这道邻接断言必须按 source 定位，否则第二条物料会被第一条的 Share.share 顶包
    const call = src.indexOf(`trackFunnelShare('share_completed', '${source}')`);
    expect(call).toBeGreaterThan(0);
    expect(src.slice(Math.max(0, call - 500), call)).toContain('await Share.share(');
  });

  it('全仓漏斗调用里没有枚举外的 step/source（防口径漂移）', () => {
    const files = fs
      .readdirSync(path.join(__dirname, '..', 'src', 'screens'))
      .filter((f) => f.endsWith('.tsx'));
    const steps = new Set<string>();
    const sources = new Set<string>();
    for (const f of files) {
      const src = read(`src/screens/${f}`);
      for (const m of src.matchAll(/trackFunnel(?:Once|Share)\(\s*['"]([a-z_0-9]+)['"]/g)) steps.add(m[1]);
      for (const m of src.matchAll(/trackFunnelShare\(\s*['"][a-z_0-9]+['"]\s*,\s*['"]([a-z_0-9]+)['"]/g)) sources.add(m[1]);
    }
    for (const s of steps) {
      expect([...ONE_TIME_STEPS, ...SHARE_STEPS]).toContain(s);
    }
    for (const s of sources) {
      expect([...FUNNEL_SOURCES]).toContain(s);
    }
    // 五段台阶 + 三个分享物料都真的被调用
    for (const step of ONE_TIME_STEPS) expect(steps.has(step)).toBe(true);
    for (const source of ['first_read', 'personality_card', 'period_report', 'year_review']) {
      expect(sources.has(source)).toBe(true);
    }
  });
});
