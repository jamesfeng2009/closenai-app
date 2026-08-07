import { createTestDb } from './helpers/testDb';
import { ReflectionRepository } from '../src/services/db/reflectionRepository';

describe('ReflectionRepository (Tier 1 每日反思)', () => {
  it('保存反思并可按日期读取', async () => {
    const db = createTestDb();
    const repo = new ReflectionRepository(db);
    await repo.save({
      id: 'r1',
      date: '2026-08-01',
      mood: 'calm',
      mood_emoji: '😌',
      summary: '今天很平静',
      full_content_url: null,
      is_ai_generated: 1,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    });
    const got = await repo.getByDate('2026-08-01');
    expect(got?.summary).toBe('今天很平静');
    db.closeSync();
  });

  it('getRecent 按日期倒序返回最近 N 条', async () => {
    const db = createTestDb();
    const repo = new ReflectionRepository(db);
    for (const d of ['2026-08-01', '2026-08-02', '2026-08-03']) {
      await repo.save({
        id: 'r-' + d,
        date: d,
        mood: null,
        mood_emoji: null,
        summary: 's' + d,
        full_content_url: null,
        is_ai_generated: 0,
        created_at: d + 'T00:00:00Z',
        updated_at: d + 'T00:00:00Z',
      });
    }
    const recent = await repo.getRecent(2);
    expect(recent.map((r) => r.date)).toEqual(['2026-08-03', '2026-08-02']);
    db.closeSync();
  });

  it('同日期重复保存按 id 覆盖（upsert）', async () => {
    const db = createTestDb();
    const repo = new ReflectionRepository(db);
    const base = {
      id: 'r1',
      mood: null,
      mood_emoji: null,
      full_content_url: null,
      is_ai_generated: 0,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    };
    await repo.save({ ...base, date: '2026-08-01', summary: '第一版' });
    await repo.save({ ...base, date: '2026-08-01', summary: '第二版' });
    const all = await repo.getRecent(10);
    expect(all).toHaveLength(1);
    expect(all[0].summary).toBe('第二版');
    db.closeSync();
  });
});
