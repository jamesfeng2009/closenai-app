/**
 * 「今天可以做什么」——首日价值路径的下一步判定（KG-25）。
 *
 * 为什么放在前端而不是新加一个 `/next-step` 端点（D1：本批只加 3 个端点，全是内容型）：
 * 判据要的四个信号里，「今天签没签」本来就在 `GET /growth/dashboard` 的
 * `today_mood` 上（`backend/services/growth_service.py:132` 已经算好并随仪表盘下发），
 * 另外三个「这台设备做到过哪一格」住在漏斗的一次性门闸键里。
 * 再加一个端点＝多一处真源，两处对「做没做过」的答案可以不一致。
 *
 * **与漏斗同一真源**是这一条的核心：`trackFunnelOnce` 写的那把
 * `@closenai/funnel_once_<step>` 键既是埋点门闸，也是这里的完成判据。
 * 于是「引导让你做的下一格」与「分析里数到的下一格」是同一个事实，
 * 不会出现界面喊你去写反思、PostHog 那边 `funnel_first_reflection_saved` 早就报过的分裂。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import { onceKey, type OneTimeStep } from './funnel';

/** 循环里的五格；`loop_done` ＝ 这一环跑通过，不再挂引导卡。 */
export type NextStepStage = 'checkin' | 'reflect' | 'insight' | 'action' | 'outcome' | 'loop_done';

/** 展示顺序（也是判定顺序）：台账 §KG-25 的路径原文「首次签到→首次反思→首条有证据的洞察→首个行动→结果回访」。 */
export const NEXT_STEP_STAGES = ['checkin', 'reflect', 'insight', 'action', 'outcome'] as const;

/** 后四格各读哪一个一次性台阶——不另立「做过没有」的键，防两个真源。 */
const GATE: Record<Exclude<NextStepStage, 'checkin' | 'loop_done'>, OneTimeStep> = {
  reflect: 'first_record_created',
  insight: 'first_evidence_insight',
  action: 'first_action',
  outcome: 'first_outcome',
};

const ORDER = ['reflect', 'insight', 'action', 'outcome'] as const;

/** 每格的落点路由（`loop_done` 不可点，所以不在表里）。 */
export const STAGE_TARGET: Record<
  Exclude<NextStepStage, 'loop_done'>,
  { name: 'Tabs' | 'Insights' | 'GrowthRecords'; screen?: 'Reflect' }
> = {
  // 签到面板与反思输入同在 Reflect  tab（ReflectScreen 的 moodPanelTitle）
  checkin: { name: 'Tabs', screen: 'Reflect' },
  reflect: { name: 'Tabs', screen: 'Reflect' },
  insight: { name: 'Insights' },
  action: { name: 'Insights' },
  outcome: { name: 'GrowthRecords' },
};

export function decideNextStep(input: {
  checkedInToday: boolean;
  done: ReadonlySet<string>;
}): NextStepStage {
  if (!input.checkedInToday) return 'checkin';
  for (const stage of ORDER) {
    if (!input.done.has(GATE[stage])) return stage;
  }
  return 'loop_done';
}

/**
 * 读这台设备已完成的后四格。
 * 读失败按「都没做过」处理：宁可每天多提示一格反思，也不要在 Storage 抖动时
 * 把整条首日路径关掉——关掉的表现是「新用户看不到下一步」，正是这一条要修的缺陷。
 */
export async function readCompletedSteps(): Promise<ReadonlySet<string>> {
  const done = new Set<string>();
  for (const step of Object.values(GATE)) {
    try {
      if (await AsyncStorage.getItem(onceKey(step))) done.add(step);
    } catch {
      // 单格读失败：这一格按未达成
    }
  }
  return done;
}
