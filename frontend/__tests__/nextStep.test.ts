/**
 * KG-25「今天可以做什么」的判定测试。
 *
 * 这一条把三件容易各自漂移的事钉在一起：
 * ① 判定顺序＝台账写的那五跳（签到→反思→有证据的洞察→行动→结果回访）；
 * ② 完成判据读的是漏斗那把一次性门闸键，不是另立的键（否则引导与分析两套口径）；
 * ③ 每一格都有文案键与落点路由，不许出现「提示了但点不动」的死胡同。
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
      clear: jest.fn(async () => { store.clear(); }),
    },
  };
});

// funnel.ts 会 import analytics，analytics 又拖进 api → react-native（node 环境跑不动）。
// 这里只用 funnel 的 onceKey 命名，不触发上报，所以把 analytics 整段换成桩。
jest.mock('../src/services/analytics', () => ({
  track: jest.fn(),
}));

import AsyncStorage from '@react-native-async-storage/async-storage';
import { onceKey } from '../src/services/funnel';
import {
  decideNextStep,
  NEXT_STEP_STAGES,
  readCompletedSteps,
  STAGE_TARGET,
} from '../src/services/nextStep';

const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');

beforeEach(async () => {
  (AsyncStorage.getItem as jest.Mock).mockClear();
  await AsyncStorage.clear();
});

describe('判定顺序＝台账那五跳', () => {
  it('没签到永远排第一（3 秒的那一格是整条路径的入口）', () => {
    expect(decideNextStep({ checkedInToday: false, done: new Set() })).toBe('checkin');
    expect(decideNextStep({
      checkedInToday: false,
      done: new Set(['first_record_created', 'first_evidence_insight', 'first_action', 'first_outcome']),
    })).toBe('checkin');
  });

  it('逐格往后推，全跑通才收卡', () => {
    // 这格用 checkedInToday=true 起步：checkin 由上一用例单独证明排第一，
    // 这里要钉的是「后四格的先后」，别让入口那一跳把顺序混进来。
    const done = new Set<string>();
    const seen: string[] = [];
    const gateOf: Record<string, string> = {
      reflect: 'first_record_created',
      insight: 'first_evidence_insight',
      action: 'first_action',
      outcome: 'first_outcome',
    };
    for (let i = 0; i < 6; i += 1) {
      const stage = decideNextStep({ checkedInToday: true, done });
      seen.push(stage);
      if (stage === 'loop_done') break;
      done.add(gateOf[stage]!);
    }
    expect(seen).toEqual([...NEXT_STEP_STAGES.slice(1), 'loop_done']);
    expect(NEXT_STEP_STAGES[0]).toBe('checkin');
  });

  it('中间某格没做也不会跳过它（跳了就是把死胡同写进引导）', () => {
    const done = new Set(['first_record_created', 'first_action']);
    expect(decideNextStep({ checkedInToday: true, done })).toBe('insight');
  });
});

describe('完成判据与漏斗同一真源', () => {
  it('读的是 @closenai/funnel_once_<step> 那把键，不另立命名空间', async () => {
    await AsyncStorage.setItem(onceKey('first_evidence_insight'), '2026-11-01T00:00:00Z');
    const done = await readCompletedSteps();
    expect(done.has('first_evidence_insight')).toBe(true);
    expect(done.has('first_action')).toBe(false);
    const keys = (AsyncStorage.getItem as jest.Mock).mock.calls.map((c) => c[0]);
    expect(keys.every((k) => k.startsWith('@closenai/funnel_once_'))).toBe(true);
    // 四格都读到了才算这套判据真的接上；只读一格是假绿
    expect(keys).toHaveLength(4);
  });

  it('Storage 单格读失败只关掉那一格，其余照常判', async () => {
    // 反面证人：first_record_created 可读，必须仍然算达成——
    // 若整读失败就返回空集，这条会跟着一起绿，那就成了恒真断言。
    (AsyncStorage.getItem as jest.Mock).mockImplementation(async (k: string) => {
      if (k === onceKey('first_action')) throw new Error('boom');
      if (k === onceKey('first_record_created')) return '2026-11-01T00:00:00Z';
      return null;
    });
    const done = await readCompletedSteps();
    expect([...done]).toEqual(['first_record_created']);
    expect(decideNextStep({ checkedInToday: true, done })).toBe('insight');
  });
});

describe('每一格都可点、都有文案，不留死胡同', () => {
  it('五个台阶各自有落点与文案键', () => {
    for (const stage of NEXT_STEP_STAGES) {
      const target = STAGE_TARGET[stage];
      expect(target.name).toBeTruthy();
    }
    const src = read('src/screens/HomeScreen.tsx');
    for (const key of [
      'home.nextStepTitle', 'home.nextStepCheckin', 'home.nextStepReflect',
      'home.nextStepInsight', 'home.nextStepAction', 'home.nextStepOutcome', 'home.nextStepCta',
    ]) {
      expect(src).toContain(key);
    }
  });

  it('HomeScreen 真的接线：判定、渲染、点击三处都在', () => {
    const src = read('src/screens/HomeScreen.tsx');
    expect(src).toContain('decideNextStep({ checkedInToday, done })');
    expect(src).toContain('readCompletedSteps()');
    expect(src).toContain("nextStage !== 'loop_done' &&");
    expect(src).toContain('goNextStep(navigation, nextStage)');
  });

  it('Insights/GrowthRecords 在路由表里、Reflect 在 tab 里（引导不许指向不存在的屏）', () => {
    const app = read('App.tsx');
    for (const name of ['GrowthRecords', 'Insights', 'Tabs']) {
      expect(app).toContain(`name="${name}"`);
    }
    // 前两格的落点是 `navigate('Tabs', { screen: 'Reflect' })`，所以 Reflect 必须是 tab 名
    expect(app).toMatch(/name="Reflect"/);
  });

  it('引导卡上不许出现「诊断／看透」这类越界承诺', () => {
    const src = read('src/screens/HomeScreen.tsx');
    const block = src.slice(src.indexOf('nextStage !== '), src.indexOf('nextStage !== ') + 1600);
    expect(block).not.toMatch(/diagnos|治疗|诊断/);
  });
});
