import { createTestDb } from './helpers/testDb';
import { MoodCheckinRepository } from '../src/services/db/moodCheckinRepository';
import type { MoodCheckinRow } from '../src/services/db/moodCheckinRepository';
import type { MoodCheckinResponse } from '../src/types';

const base = {
  emoji: '😄',
  intensity: 0.8,
  note: null,
  ai_interpretation: null,
  created_at: '2026-08-01T00:00:00Z',
};

/** 服务端签到响应：契约里没有 note / intensity（schemas.py MoodCheckinResponse）。 */
function remote(over: Partial<MoodCheckinResponse> = {}): MoodCheckinResponse {
  return {
    id: 'm1',
    mood_emoji: '😢',
    mood_label: 'Sad',
    ai_interpretation: 'server interpretation',
    streak_updated: 0,
    created_at: '2026-08-01T12:00:00',
    ...over,
  };
}

describe('MoodCheckinRepository (Tier 1 情绪签到)', () => {
  it('保存签到并可按日期读取', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    await repo.save({ id: 'm1', date: '2026-08-01', ...base } as MoodCheckinRow);
    const got = await repo.getByDate('2026-08-01');
    expect(got?.id).toBe('m1');
    db.closeSync();
  });

  it('hasCheckedInToday 判断今日是否已签到', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    expect(await repo.hasCheckedInToday('2026-08-01')).toBe(false);
    await repo.save({ id: 'm1', date: '2026-08-01', ...base } as MoodCheckinRow);
    expect(await repo.hasCheckedInToday('2026-08-01')).toBe(true);
    db.closeSync();
  });

  it('getRecentDays 按日期倒序返回最近 N 天', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    for (const d of ['2026-08-01', '2026-08-02', '2026-08-03']) {
      await repo.save({ id: 'm-' + d, date: d, ...base } as MoodCheckinRow);
    }
    const recent = await repo.getRecentDays(2);
    expect(recent.map((r) => r.date)).toEqual(['2026-08-03', '2026-08-02']);
    db.closeSync();
  });

  it('同日期重复签到按 id 覆盖（upsert）', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    await repo.save({ id: 'm1', date: '2026-08-01', ...base, emoji: '😄' } as MoodCheckinRow);
    await repo.save({ id: 'm1', date: '2026-08-01', ...base, emoji: '😢' } as MoodCheckinRow);
    const all = await repo.getRecentDays(10);
    expect(all).toHaveLength(1);
    expect(all[0].emoji).toBe('😢');
    db.closeSync();
  });
});

describe('MoodCheckinRepository · 服务端历史合并（T7-01）', () => {
  it('同 id 合并不抹掉本地 note 与非默认 intensity', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    await repo.save({
      id: 'm1',
      date: '2026-08-01',
      emoji: '😊',
      intensity: 0.9,
      note: '早上把压了很久的那句话说出来了',
      ai_interpretation: 'local old text',
      created_at: '2026-08-01T08:00:00Z',
    } as MoodCheckinRow);

    await repo.mergeRemoteHistory([remote()]);

    const got = await repo.getById('m1');
    expect(got?.note).toBe('早上把压了很久的那句话说出来了');
    expect(got?.intensity).toBe(0.9);
    // 服务端确实提供的字段仍然更新
    expect(got?.emoji).toBe('😢');
    expect(got?.ai_interpretation).toBe('server interpretation');
    db.closeSync();
  });

  it('本地没有的字段才用默认值（新行 note=null、intensity=0.5）', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    await repo.mergeRemoteHistory([remote({ id: 'm2' })]);

    const got = await repo.getById('m2');
    expect(got?.note).toBeNull();
    expect(got?.intensity).toBe(0.5);
    expect(got?.date).toBe('2026-08-01');
    db.closeSync();
  });

  it('全量同步幂等：重复合并不会多出行，也不改写已保留的本地值', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    await repo.save({ id: 'm1', date: '2026-08-01', ...base, note: '留住的备注' } as MoodCheckinRow);
    const history = [remote(), remote({ id: 'm2', mood_emoji: '😊' })];

    await repo.mergeRemoteHistory(history);
    await repo.mergeRemoteHistory(history);

    const all = await repo.getRecentDays(10);
    expect(all).toHaveLength(2);
    expect(all.find((r) => r.id === 'm1')?.note).toBe('留住的备注');
    db.closeSync();
  });

  it('只有调用方拿着用户刚输入的 note 时才覆盖本地备注', async () => {
    const db = createTestDb();
    const repo = new MoodCheckinRepository(db);
    await repo.save({ id: 'm1', date: '2026-08-01', ...base, note: '旧备注' } as MoodCheckinRow);

    await repo.mergeRemoteCheckin(remote(), { note: '新备注' });

    expect((await repo.getById('m1'))?.note).toBe('新备注');
    db.closeSync();
  });
});
