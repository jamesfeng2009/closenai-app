import { createTestDb } from './helpers/testDb';
import { AiMessagesRepository } from '../src/services/db/aiMessagesRepository';

/**
 * P1 #14：/chat/history 回灌不许把已读清成未读。
 *
 * 判据走真 SQLite（不是读源码）：缺陷就长在 `ON CONFLICT ... is_read = excluded.is_read`
 * 这一条 SQL 上，只有真跑一次 upsert 才看得见「红点永远清不掉」。
 */
describe('AiMessagesRepository 回灌与已读状态', () => {
  it('首次回灌插入未读行', async () => {
    const db = createTestDb();
    const repo = new AiMessagesRepository(db);

    await repo.upsertFromRemote('m1', '今天的发现', '2026-11-09T08:00:00.000Z');

    const row = await repo.getById('m1');
    expect(row?.is_read).toBe(0);
    expect(row?.read_at).toBeNull();
    expect(row?.content_preview).toBe('今天的发现');
    expect(await repo.getUnreadCount()).toBe(1);
    db.closeSync();
  });

  it('已读后再次回灌：is_read/read_at 保留，正文与日期仍更新', async () => {
    const db = createTestDb();
    const repo = new AiMessagesRepository(db);

    await repo.upsertFromRemote('m1', '第一版正文', '2026-11-01T12:00:00.000Z');
    const firstDay = (await repo.getById('m1'))?.scheduled_date;
    await repo.markAsRead('m1');
    const readAt = (await repo.getById('m1'))?.read_at;
    expect(readAt).not.toBeNull();

    await repo.upsertFromRemote('m1', '第二版正文', '2026-11-09T12:00:00.000Z');

    const row = await repo.getById('m1');
    expect(row?.is_read).toBe(1);
    expect(row?.read_at).toBe(readAt);
    // 正向证人：这条 upsert 确实执行了，不是「整个字段都没写」的另一种坏法。
    expect(row?.content_preview).toBe('第二版正文');
    // 日期的本地日随时区而变，这里只钉「跟着服务端更新时间走了」。
    expect(row?.scheduled_date).not.toBe(firstDay);
    expect(await repo.getUnreadCount()).toBe(0);
    db.closeSync();
  });

  it('重复回灌不产生第二行', async () => {
    const db = createTestDb();
    const repo = new AiMessagesRepository(db);

    await repo.upsertFromRemote('m1', '正文', '2026-11-09T08:00:00.000Z');
    await repo.upsertFromRemote('m1', '正文', '2026-11-09T08:00:00.000Z');

    expect(await repo.getAll()).toHaveLength(1);
    db.closeSync();
  });
});
