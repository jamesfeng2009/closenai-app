import { createTestDb } from './helpers/testDb';
import { MoodCheckinRepository } from '../src/services/db/moodCheckinRepository';
import type { MoodCheckinRow } from '../src/services/db/moodCheckinRepository';

const base = {
  emoji: '😄',
  intensity: 0.8,
  note: null,
  ai_interpretation: null,
  created_at: '2026-08-01T00:00:00Z',
};

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
