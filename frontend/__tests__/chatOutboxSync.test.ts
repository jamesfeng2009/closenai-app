/**
 * P2 #28：对话离线补投队列 + 增量水位。
 *
 * 缺陷原状：离线时用户消息只写本机 SQLite，`sync_metadata.chat_history` 从未被写过，
 * 联网后既没有上传路径、也没有增量游标——同一句话还会因为本地 id 与服务端 uuid
 * 不同而在刷新后显示两遍。本文件判四件事：
 *
 * 1. outbox 入队幂等（已 sent 的行不会被重发打回 pending）；
 * 2. 补投成功 → 全部标 sent，再补投不会重复请求；
 * 3. 未同意共享（40303）→ 标 discarded 而不是无限重试；网络失败 → 留在 pending 并累计次数；
 * 4. 拉取用 `sync_metadata` 水位，用户消息按 client_message_id 落本地，水位只前进不后退。
 */
import { createTestDb } from './helpers/testDb';
import { ChatOutboxRepository } from '../src/services/db/chatOutboxRepository';
import { ChatRepository } from '../src/services/db/chatRepository';
import { flushChatOutbox, pullChatHistory, syncChat } from '../src/services/chatSync';
import type { ChatHistoryItem } from '../src/types';

jest.mock('../src/services/api', () => {
  class ApiError extends Error {
    code: number;
    constructor(code: number, message: string) {
      super(message);
      this.code = code;
      this.name = 'ApiError';
    }
  }
  return {
    ApiError,
    getChatHistory: jest.fn(),
    postChatMessagesBatch: jest.fn(),
  };
});

// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../src/services/api') as {
  ApiError: new (code: number, message: string) => Error;
  getChatHistory: jest.Mock;
  postChatMessagesBatch: jest.Mock;
};

function makeRuntime() {
  const db = createTestDb();
  return {
    db,
    outbox: new ChatOutboxRepository(db),
    history: new ChatRepository(db),
  };
}

async function enqueue(rt: ReturnType<typeof makeRuntime>, id: string, at = '2026-11-01T08:00:00Z') {
  await rt.outbox.enqueue({
    client_message_id: id,
    content: `内容-${id}`,
    message_type: 'reply',
    created_at: at,
  });
}

beforeEach(() => {
  api.getChatHistory.mockReset();
  api.postChatMessagesBatch.mockReset();
});

describe('chat_outbox 仓储', () => {
  it('入队幂等：已 sent 的行再次入队不会被打回 pending', async () => {
    const rt = makeRuntime();
    await enqueue(rt, 'u-1');
    await rt.outbox.markSent(['u-1']);

    await enqueue(rt, 'u-1');

    expect(await rt.outbox.listPending()).toEqual([]);
    expect(await rt.outbox.countPending()).toBe(0);
    rt.db.closeSync();
  });

  it('listPending 只取 pending，按入队时间正序', async () => {
    const rt = makeRuntime();
    await enqueue(rt, 'u-b', '2026-11-01T09:00:00Z');
    await enqueue(rt, 'u-a', '2026-11-01T08:00:00Z');
    await enqueue(rt, 'u-c', '2026-11-01T10:00:00Z');
    await rt.outbox.markDiscarded(['u-b'], '未同意');

    const pending = await rt.outbox.listPending();
    expect(pending.map((p) => p.client_message_id)).toEqual(['u-a', 'u-c']);
    rt.db.closeSync();
  });
});

describe('flushChatOutbox', () => {
  it('补投成功后全部标 sent，再跑一次不再发请求', async () => {
    const rt = makeRuntime();
    await enqueue(rt, 'u-1');
    await enqueue(rt, 'u-2');
    api.postChatMessagesBatch.mockResolvedValue({ created: ['u-1'], existing: ['u-2'] });

    expect(await flushChatOutbox(rt)).toBe(2);
    expect(api.postChatMessagesBatch).toHaveBeenCalledTimes(1);
    expect(api.postChatMessagesBatch.mock.calls[0][0]).toEqual([
      { client_message_id: 'u-1', content: '内容-u-1', message_type: 'reply', created_at: '2026-11-01T08:00:00Z' },
      { client_message_id: 'u-2', content: '内容-u-2', message_type: 'reply', created_at: '2026-11-01T08:00:00Z' },
    ]);
    expect(await rt.outbox.countPending()).toBe(0);

    await flushChatOutbox(rt);
    expect(api.postChatMessagesBatch).toHaveBeenCalledTimes(1);
    rt.db.closeSync();
  });

  it('未同意共享（40303）→ discarded，不留在 pending 里反复撞墙', async () => {
    const rt = makeRuntime();
    await enqueue(rt, 'u-1');
    api.postChatMessagesBatch.mockRejectedValue(new api.ApiError(40303, 'consent required'));

    await flushChatOutbox(rt);

    expect(await rt.outbox.countPending()).toBe(0);
    const row = rt.db.getFirstSync<{ status: string; last_error: string | null }>(
      'SELECT status, last_error FROM chat_outbox WHERE client_message_id = ?',
      ['u-1']
    );
    expect(row?.status).toBe('discarded');
    expect(row?.last_error).toContain('consent');
    rt.db.closeSync();
  });

  it('网络失败 → 留在 pending，累计 attempts 并记下最后一次错误', async () => {
    const rt = makeRuntime();
    await enqueue(rt, 'u-1');
    api.postChatMessagesBatch.mockRejectedValue(new api.ApiError(-1, 'network down'));

    await flushChatOutbox(rt);
    await flushChatOutbox(rt);

    const rows = await rt.outbox.listPending();
    expect(rows).toHaveLength(1);
    expect(rows[0].attempts).toBe(2);
    expect(rows[0].last_error).toBe('network down');
    rt.db.closeSync();
  });
});

describe('pullChatHistory', () => {
  const item = (over: Partial<ChatHistoryItem>): ChatHistoryItem => ({
    id: 'srv-1',
    role: 'user',
    content: '你好',
    created_at: '2026-11-01T08:00:00Z',
    ...over,
  });

  it('首次拉取不带 since，落本地并写水位', async () => {
    const rt = makeRuntime();
    api.getChatHistory.mockResolvedValue([
      item({ id: 'srv-user', client_message_id: 'u-1', created_at: '2026-11-01T08:00:00Z' }),
      item({ id: 'srv-ai', role: 'ai', content: '在的', created_at: '2026-11-01T08:00:05Z' }),
    ]);

    const pulled = await pullChatHistory(rt);

    expect(api.getChatHistory).toHaveBeenCalledWith(50, undefined);
    expect(pulled).toHaveLength(2);
    // 用户消息以 client_message_id 为本地主键：与本地缓存行同 id，刷新不会出现两份
    const rows = await rt.history.getRecent(10);
    expect(rows.map((r) => r.id).sort()).toEqual(['srv-ai', 'u-1']);
    expect(rows.find((r) => r.id === 'srv-ai')?.role).toBe('assistant');
    const wm = rt.db.getFirstSync<{ last_synced_at: string; record_count: number }>(
      "SELECT last_synced_at, record_count FROM sync_metadata WHERE table_name = 'chat_history'"
    );
    expect(wm?.last_synced_at).toBe('2026-11-01T08:00:05Z');
    expect(wm?.record_count).toBe(2);
    rt.db.closeSync();
  });

  it('第二次拉取带上次水位，且水位不因更旧的行后退', async () => {
    const rt = makeRuntime();
    rt.db.runSync(
      "UPDATE sync_metadata SET last_synced_at = '2026-11-02T00:00:00Z' WHERE table_name = 'chat_history'"
    );
    // 服务端返回一条比水位更旧的（补投 created_at 早于首次同步），水位必须停在原处
    api.getChatHistory.mockResolvedValue([
      item({ id: 'srv-old', client_message_id: 'u-old', created_at: '2026-11-01T00:00:00Z' }),
    ]);

    await pullChatHistory(rt);

    expect(api.getChatHistory).toHaveBeenCalledWith(50, '2026-11-02T00:00:00Z');
    const wm = rt.db.getFirstSync<{ last_synced_at: string }>(
      "SELECT last_synced_at FROM sync_metadata WHERE table_name = 'chat_history'"
    );
    expect(wm?.last_synced_at).toBe('2026-11-02T00:00:00Z');
    rt.db.closeSync();
  });

  it('拉取失败不抛，也不动水位（下次聚焦重试同一段）', async () => {
    const rt = makeRuntime();
    api.getChatHistory.mockRejectedValue(new api.ApiError(-1, 'offline'));

    await expect(pullChatHistory(rt)).resolves.toEqual([]);
    const wm = rt.db.getFirstSync<{ last_synced_at: string | null }>(
      "SELECT last_synced_at FROM sync_metadata WHERE table_name = 'chat_history'"
    );
    expect(wm?.last_synced_at).toBeNull();
    rt.db.closeSync();
  });
});

describe('syncChat', () => {
  it('先补投再拉新', async () => {
    const rt = makeRuntime();
    await enqueue(rt, 'u-1');
    api.postChatMessagesBatch.mockResolvedValue({ created: ['u-1'], existing: [] });
    api.getChatHistory.mockResolvedValue([]);

    await syncChat(rt);

    expect(api.postChatMessagesBatch).toHaveBeenCalledTimes(1);
    expect(api.getChatHistory).toHaveBeenCalledTimes(1);
    rt.db.closeSync();
  });
});
