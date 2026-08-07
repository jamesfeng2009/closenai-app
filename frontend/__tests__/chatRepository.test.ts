import { createTestDb } from './helpers/testDb';
import { ChatRepository } from '../src/services/db/chatRepository';

describe('ChatRepository (Tier 1 对话历史)', () => {
  it('追加消息并可读取', async () => {
    const db = createTestDb();
    const repo = new ChatRepository(db);
    await repo.add({
      id: 'c1',
      role: 'user',
      content_preview: '你好',
      full_content_url: null,
      created_at: '2026-08-01T00:00:00Z',
    });
    const recent = await repo.getRecent(10);
    expect(recent).toHaveLength(1);
    expect(recent[0].role).toBe('user');
    expect(recent[0].content_preview).toBe('你好');
    db.closeSync();
  });

  it('getRecent 按时间倒序返回最近 N 条', async () => {
    const db = createTestDb();
    const repo = new ChatRepository(db);
    await repo.add({ id: 'c1', role: 'user', content_preview: '第一条', full_content_url: null, created_at: '2026-08-01T00:00:00Z' });
    await repo.add({ id: 'c2', role: 'assistant', content_preview: '第二条', full_content_url: null, created_at: '2026-08-02T00:00:00Z' });
    await repo.add({ id: 'c3', role: 'user', content_preview: '第三条', full_content_url: null, created_at: '2026-08-03T00:00:00Z' });
    const recent = await repo.getRecent(2);
    expect(recent.map((m) => m.id)).toEqual(['c3', 'c2']);
    db.closeSync();
  });

  it('countByRole 统计某角色消息数', async () => {
    const db = createTestDb();
    const repo = new ChatRepository(db);
    await repo.add({ id: 'c1', role: 'user', content_preview: 'a', full_content_url: null, created_at: '2026-08-01T00:00:00Z' });
    await repo.add({ id: 'c2', role: 'assistant', content_preview: 'b', full_content_url: null, created_at: '2026-08-02T00:00:00Z' });
    await repo.add({ id: 'c3', role: 'assistant', content_preview: 'c', full_content_url: null, created_at: '2026-08-03T00:00:00Z' });
    expect(await repo.countByRole('assistant')).toBe(2);
    db.closeSync();
  });
});
