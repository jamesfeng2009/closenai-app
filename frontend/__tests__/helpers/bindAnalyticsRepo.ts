import { analyticsEventsRepo } from '../../src/services/db/analyticsEventsRepository';
import type { AnalyticsEventsRepository } from '../../src/services/db/analyticsEventsRepository';

const METHODS = [
  'listPending',
  'markSynced',
  'cleanupSynced',
  'deleteByIds',
  'scheduleRetry',
  'markDeadLetter',
  'countPending',
  'countDeadLetters',
  'clearAll',
] as const;

export type AnalyticsRepoSpies = Record<typeof METHODS[number], jest.SpyInstance>;

/**
 * 把生产单例 analyticsEventsRepo 的每个方法转接到测试库的仓库实例。
 *
 * 为什么要逐个转：flushAnalytics 直接 import 单例，只 spy listPending 会让
 * deleteByIds / markDeadLetter / scheduleRetry 落到真实（expo）库上，测试库里的行
 * 不会变——看起来是「逐事件逻辑没生效」，实际是两套库。新增仓储方法时必须同步进 METHODS，
 * 否则同样会静默漏接（analyticsAck.test.ts 的混合状态用例就是这条的证人）。
 */
export function bindAnalyticsRepo(repo: AnalyticsEventsRepository): AnalyticsRepoSpies {
  const spies = {} as AnalyticsRepoSpies;
  for (const name of METHODS) {
    const impl = (repo[name] as unknown as (...args: unknown[]) => unknown).bind(repo);
    spies[name] = jest.spyOn(analyticsEventsRepo, name as never).mockImplementation(impl as never);
  }
  return spies;
}

export function restoreAnalyticsRepo(spies: AnalyticsRepoSpies): void {
  for (const spy of Object.values(spies)) spy.mockRestore();
}
