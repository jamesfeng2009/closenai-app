/**
 * T7-02 共享契约用例的前端一侧。
 *
 * `contracts/analytics_ack_cases.json` 同时被 backend/tests/test_analytics_service.py
 * 与 go-core/internal/httpapi/analytics_ack_test.go 读取：服务端证明「响应长这样」，
 * 这里证明「同一份 results 落到本地队列后，留/删/死信与仓库期望一致」。
 *
 * 本地队列有 event_id 唯一索引，所以同一 event_id 只会入队一行；`in_batch_duplicate`
 * 这类服务端用例在前端等价于「服务端对同一条回了两次结果」——按最后一条处理，
 * 仍然必须删除（duplicate 也是确认接收）。
 */
import fs from 'fs';
import path from 'path';

import { createTestDb } from './helpers/testDb';
import { bindAnalyticsRepo, restoreAnalyticsRepo } from './helpers/bindAnalyticsRepo';
import type { AnalyticsRepoSpies } from './helpers/bindAnalyticsRepo';
import { AnalyticsEventsRepository } from '../src/services/db/analyticsEventsRepository';
import { flushAnalytics } from '../src/services/analytics';

jest.mock('../src/services/api', () => ({
  postAnalyticsBatch: jest.fn(),
}));
import { postAnalyticsBatch } from '../src/services/api';

const mockPost = postAnalyticsBatch as jest.Mock;

interface FixtureEvent {
  event: string;
  properties?: Record<string, unknown>;
  occurred_at: string;
  event_id?: string | null;
}

interface FixtureCase {
  name: string;
  user_found: boolean;
  consent: boolean;
  prior: Record<string, string>;
  relay_fail: string[];
  events: FixtureEvent[];
  expected: {
    counts: Record<string, number>;
    results: Array<{ event_id: string | null; status: string; reason?: string }>;
    local: {
      retained: string[];
      deleted: string[];
      dead: Array<{ event_id: string; reason: string }>;
    };
  };
}

const CASES: FixtureCase[] = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, '..', '..', 'contracts', 'analytics_ack_cases.json'),
    'utf8'
  )
).cases;

beforeEach(() => jest.clearAllMocks());

describe('T7-02 共享契约用例（前端队列落库）', () => {
  it('用例表不是空集，且覆盖了「无 event_id」的服务端分支', () => {
    expect(CASES.length).toBeGreaterThanOrEqual(5);
    expect(CASES.some((c) => c.events.some((e) => e.event_id == null))).toBe(true);
  });

  for (const testCase of CASES) {
    it(testCase.name, async () => {
      const db = createTestDb();
      const repo = new AnalyticsEventsRepository(db);
      const seenIds = new Set<string>();
      for (const ev of testCase.events) {
        if (!ev.event_id || seenIds.has(ev.event_id)) continue;
        seenIds.add(ev.event_id);
        // 队列里的属性只可能来自 track() 的白名单过滤结果；敏感键由服务端用例证明不出网。
        repo.enqueue(ev.event, { screen: 'home' }, ev.occurred_at, ev.event_id);
      }
      const spies: AnalyticsRepoSpies = bindAnalyticsRepo(repo);
      // 服务端响应体＝用例里的 counts 平铺 + results；local 只是前端期望，不上线。
      mockPost.mockResolvedValue({
        ...testCase.expected.counts,
        results: testCase.expected.results,
      });

      try {
        const res = await flushAnalytics();
        const local = testCase.expected.local;

        expect(mockPost).toHaveBeenCalledTimes(1);
        // 服务端计数原样上报（看板用它区分 relayed 与本次尝试条数）。
        expect(res.relayed).toBe(testCase.expected.counts.relayed);
        expect(res.attempted).toBe(seenIds.size);
        // 逐事件落库口径：确认接收的两类都删除，其余分别留在待送/死信。
        expect(res.accepted + res.duplicate).toBe(local.deleted.length);
        expect(res.retryable).toBe(local.retained.length);
        expect(res.permanent).toBe(local.dead.length);
        expect(res.queued).toBe(local.retained.length);

        const rows = db.getAllSync<{ event_id: string; dead_reason: string | null }>(
          'SELECT event_id, dead_reason FROM analytics_events ORDER BY occurred_at ASC'
        );
        const pendingIds = rows.filter((r) => r.dead_reason == null).map((r) => r.event_id);
        const dead = rows
          .filter((r) => r.dead_reason != null)
          .map((r) => ({ event_id: r.event_id, reason: r.dead_reason as string }));

        expect(pendingIds.sort()).toEqual([...local.retained].sort());
        expect(dead).toEqual(local.dead);
        for (const deleted of local.deleted) {
          expect(pendingIds).not.toContain(deleted);
          expect(dead.map((d) => d.event_id)).not.toContain(deleted);
        }
        expect(repo.countPending()).toBe(local.retained.length);
        expect(repo.countDeadLetters()).toBe(local.dead.length);
      } finally {
        restoreAnalyticsRepo(spies);
        db.closeSync();
      }
    });
  }
});
