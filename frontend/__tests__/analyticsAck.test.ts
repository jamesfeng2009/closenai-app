/**
 * T7-02：分析事件按**逐事件结果**落库。
 *
 * 验收口径（任务板）：
 * - 一批 accepted / duplicate / retryable / permanent 混合时，本地保留/删除逐条正确；
 * - 可重试失败保留并退避，永久拒绝进有原因的死信，不得无限重发；
 * - 不能再用「请求条数」代表中继成功；
 * - 撤回同意仍清空本地队列（含死信）。
 */
import { createTestDb } from './helpers/testDb';
import { bindAnalyticsRepo, restoreAnalyticsRepo } from './helpers/bindAnalyticsRepo';
import type { AnalyticsRepoSpies } from './helpers/bindAnalyticsRepo';
import { AnalyticsEventsRepository } from '../src/services/db/analyticsEventsRepository';
import { flushAnalytics, clearAnalyticsQueue, MAX_FLUSH_ATTEMPTS } from '../src/services/analytics';

jest.mock('../src/services/api', () => ({
  postAnalyticsBatch: jest.fn(),
}));
import { postAnalyticsBatch } from '../src/services/api';

const mockPost = postAnalyticsBatch as jest.Mock;

function setup(events: Array<{ name: string; occurred: string }>) {
  const db = createTestDb();
  const repo = new AnalyticsEventsRepository(db);
  for (const e of events) repo.enqueue(e.name, { screen: 'home' }, e.occurred);
  const spies: AnalyticsRepoSpies = bindAnalyticsRepo(repo);
  return { db, repo, spies };
}

beforeEach(() => jest.clearAllMocks());

describe('逐事件结果：本地保留/删除', () => {
  it('accepted/duplicate 删除，retryable 保留退避，permanent 进死信', async () => {
    const { db, repo, spies } = setup([
      { name: 'accepted_ev', occurred: '2026-10-01T00:00:00Z' },
      { name: 'duplicate_ev', occurred: '2026-10-01T01:00:00Z' },
      { name: 'retryable_ev', occurred: '2026-10-01T02:00:00Z' },
      { name: 'permanent_ev', occurred: '2026-10-01T03:00:00Z' },
    ]);
    const byName = new Map(repo.listPending().map((p) => [p.event, p.event_id]));
    mockPost.mockResolvedValue({
      relayed: 1, dropped: 2, accepted: 1, duplicate: 1, retryable: 1, permanent: 1,
      results: [
        { event_id: byName.get('accepted_ev'), status: 'accepted' },
        { event_id: byName.get('duplicate_ev'), status: 'duplicate' },
        { event_id: byName.get('retryable_ev'), status: 'retryable', reason: 'relay_failed' },
        { event_id: byName.get('permanent_ev'), status: 'permanent', reason: 'consent_off' },
      ],
    });

    const res = await flushAnalytics();
    expect(res).toEqual({
      relayed: 1, attempted: 4, accepted: 1, duplicate: 1,
      retryable: 1, permanent: 1, dropped: 2, queued: 1,
    });
    // 本地只剩可重试那条；死信仍在库里但不再进队列
    expect(repo.listPending()).toHaveLength(0);
    expect(repo.countPending()).toBe(1);
    expect(repo.countDeadLetters()).toBe(1);
    const left = db.getAllSync<{ event: string; attempts: number; next_retry_at: string | null }>(
      'SELECT event, attempts, next_retry_at FROM analytics_events WHERE dead_reason IS NULL'
    );
    expect(left).toEqual([
      { event: 'retryable_ev', attempts: 1, next_retry_at: expect.any(String) },
    ]);
    const dead = db.getFirstSync<{ event: string; dead_reason: string }>(
      'SELECT event, dead_reason FROM analytics_events WHERE dead_reason IS NOT NULL'
    );
    expect(dead).toEqual({ event: 'permanent_ev', dead_reason: 'consent_off' });

    restoreAnalyticsRepo(spies); db.closeSync();
  });

  it('旧后端不返回 results：不许把 HTTP 成功当成全部接收', async () => {
    const { db, repo, spies } = setup([
      { name: 'legacy_ev', occurred: '2026-10-01T00:00:00Z' },
    ]);
    mockPost.mockResolvedValue({ relayed: 1, dropped: 0 });

    const res = await flushAnalytics();
    expect(res.retryable).toBe(1);
    expect(res.accepted).toBe(0);
    expect(repo.countPending()).toBe(1);
    expect(repo.countDeadLetters()).toBe(0);

    restoreAnalyticsRepo(spies); db.closeSync();
  });

  it('缺某一条 result：只有那一条保留，其余照结果处理', async () => {
    const { db, repo, spies } = setup([
      { name: 'acked_ev', occurred: '2026-10-01T00:00:00Z' },
      { name: 'missing_ev', occurred: '2026-10-01T01:00:00Z' },
    ]);
    const byName = new Map(repo.listPending().map((p) => [p.event, p.event_id]));
    mockPost.mockResolvedValue({
      relayed: 1, dropped: 0, accepted: 1, duplicate: 0, retryable: 0, permanent: 0,
      results: [{ event_id: byName.get('acked_ev'), status: 'accepted' }],
    });

    const res = await flushAnalytics();
    expect(res.accepted).toBe(1);
    expect(res.retryable).toBe(1);
    expect(repo.countPending()).toBe(1);
    expect(db.getAllSync<{ event: string }>('SELECT event FROM analytics_events')).toEqual([
      { event: 'missing_ev' },
    ]);

    restoreAnalyticsRepo(spies); db.closeSync();
  });
});

describe('重试退避与死信上限', () => {
  it('可重试失败按指数退避，下一次 flush 在到点前不再发请求', async () => {
    const { db, repo, spies } = setup([
      { name: 'flaky_ev', occurred: '2026-10-01T00:00:00Z' },
    ]);
    mockPost.mockRejectedValue(new Error('network'));

    await flushAnalytics();
    const first = db.getFirstSync<{ attempts: number; next_retry_at: string }>(
      'SELECT attempts, next_retry_at FROM analytics_events'
    );
    expect(first?.attempts).toBe(1);
    expect(new Date(first!.next_retry_at).getTime()).toBeGreaterThan(Date.now());

    // 退避未到点：listPending 取不到，flush 不会发请求
    await flushAnalytics();
    expect(mockPost).toHaveBeenCalledTimes(1);

    restoreAnalyticsRepo(spies); db.closeSync();
  });

  it('重试次数到顶进死信并写明原因，不会无限重发', async () => {
    const { db, repo, spies } = setup([
      { name: 'always_fail', occurred: '2026-10-01T00:00:00Z' },
    ]);
    mockPost.mockRejectedValue(new Error('network'));

    for (let i = 0; i < MAX_FLUSH_ATTEMPTS; i += 1) {
      // 把退避时钟拨到点，模拟一次次重试
      db.runSync('UPDATE analytics_events SET next_retry_at = NULL');
      await flushAnalytics();
    }
    expect(mockPost).toHaveBeenCalledTimes(MAX_FLUSH_ATTEMPTS);
    expect(repo.countPending()).toBe(0);
    expect(repo.countDeadLetters()).toBe(1);
    const dead = db.getFirstSync<{ dead_reason: string }>(
      'SELECT dead_reason FROM analytics_events'
    );
    expect(dead?.dead_reason).toMatch(/^retry_exhausted:/);

    // 再 flush 也不会再发
    db.runSync('UPDATE analytics_events SET next_retry_at = NULL');
    await flushAnalytics();
    expect(mockPost).toHaveBeenCalledTimes(MAX_FLUSH_ATTEMPTS);

    restoreAnalyticsRepo(spies); db.closeSync();
  });

  it('并发 flush：第二个入口直接返回，不会重复发送同一批', async () => {
    const { db, repo, spies } = setup([
      { name: 'once_ev', occurred: '2026-10-01T00:00:00Z' },
    ]);
    const eventId = repo.listPending()[0].event_id;
    let resolvePost!: (value: unknown) => void;
    mockPost.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));

    const first = flushAnalytics();
    const second = flushAnalytics();
    await expect(second).resolves.toEqual({
      relayed: 0, attempted: 0, accepted: 0, duplicate: 0,
      retryable: 0, permanent: 0, dropped: 0, queued: 1,
    });

    resolvePost({
      relayed: 1, dropped: 0, accepted: 1, duplicate: 0, retryable: 0, permanent: 0,
      results: [{ event_id: eventId, status: 'accepted' }],
    });
    const done = await first;
    expect(done.accepted).toBe(1);
    expect(mockPost).toHaveBeenCalledTimes(1);

    restoreAnalyticsRepo(spies); db.closeSync();
  });
});

describe('撤回同意', () => {
  it('clearAnalyticsQueue 连死信一起清掉', async () => {
    const { db, repo, spies } = setup([
      { name: 'dead_ev', occurred: '2026-10-01T00:00:00Z' },
      { name: 'queued_ev', occurred: '2026-10-01T01:00:00Z' },
    ]);
    const eventId = repo.listPending()[0].event_id;
    mockPost.mockResolvedValue({
      relayed: 0, dropped: 1, accepted: 0, duplicate: 0, retryable: 0, permanent: 1,
      results: [{ event_id: eventId, status: 'permanent', reason: 'consent_off' }],
    });
    await flushAnalytics();
    expect(repo.countDeadLetters()).toBe(1);
    expect(repo.countPending()).toBe(1);

    clearAnalyticsQueue();
    expect(repo.countPending()).toBe(0);
    expect(repo.countDeadLetters()).toBe(0);

    restoreAnalyticsRepo(spies); db.closeSync();
  });
});
