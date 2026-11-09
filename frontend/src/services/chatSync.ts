/**
 * 对话离线同步（P2 #28）。
 *
 * 两件事，都在「页面聚焦」这一个时机做：
 * 1. `flushChatOutbox`：把本地 outbox 里 pending 的用户消息补投到
 *    `POST /chat/messages/batch`（服务端按 client_message_id 幂等）；
 * 2. `pullChatHistory`：按 `sync_metadata.chat_history` 的水位增量拉新消息，
 *    写进本地 `chat_history`，再把水位推到本批最大 created_at。
 *
 * 网络失败不抛给调用方：这是后台同步，页面照常用本地缓存渲染，下次聚焦再试。
 */
import { ApiError, getChatHistory, postChatMessagesBatch } from './api';
import type { ChatHistoryItem } from '../types';
import { ChatOutboxRepository, chatOutboxRepo } from './db/chatOutboxRepository';
import { ChatRepository, chatRepo } from './db/chatRepository';
import { getDatabase } from './db/database';
import type { SqliteDb } from './db/types';

// 与后端 ErrorCode.AI_CONSENT_REQUIRED 对齐
const CODE_AI_CONSENT_REQUIRED = 40303;
const WATERMARK_TABLE = 'chat_history';
const PULL_LIMIT = 50;

export interface ChatSyncRuntime {
  db: SqliteDb;
  outbox: ChatOutboxRepository;
  history: ChatRepository;
}

function defaultRuntime(): ChatSyncRuntime {
  const db = getDatabase();
  return { db, outbox: chatOutboxRepo, history: chatRepo };
}

/** 补投 outbox；返回本次确认到服务端（新建或已存在）的条数。 */
export async function flushChatOutbox(rt: ChatSyncRuntime = defaultRuntime()): Promise<number> {
  const pending = await rt.outbox.listPending();
  if (pending.length === 0) return 0;
  const ids = pending.map((p) => p.client_message_id);

  try {
    const receipt = await postChatMessagesBatch(
      pending.map((p) => ({
        client_message_id: p.client_message_id,
        content: p.content,
        message_type: p.message_type,
        created_at: p.created_at,
      }))
    );
    // created 与 existing 都算「已经在服务端」：后者是上一次补投已成功、本地没来得及标记
    await rt.outbox.markSent(ids);
    return receipt.created.length + receipt.existing.length;
  } catch (e) {
    if (e instanceof ApiError && e.code === CODE_AI_CONSENT_REQUIRED) {
      // 永久拒绝：正文不可能再被接收，重试只会一直 403
      await rt.outbox.markDiscarded(ids, String(e.message));
      return 0;
    }
    await rt.outbox.recordFailure(ids, e instanceof Error ? e.message : String(e));
    return 0;
  }
}

/**
 * 按水位增量拉取并落本地；返回本次拉到的远端条目（调用方还要用它们同步主动消息）。
 *
 * 只前进不后退：服务端可能返回比水位旧的行（时钟/补投 created_at），
 * 回写旧水位会让下一次重复拉同一段。
 */
export async function pullChatHistory(
  rt: ChatSyncRuntime = defaultRuntime()
): Promise<ChatHistoryItem[]> {
  const watermark = readWatermark(rt.db);
  let items: ChatHistoryItem[];
  try {
    items = await getChatHistory(PULL_LIMIT, watermark ?? undefined);
  } catch {
    return [];
  }
  if (items.length === 0) return [];

  // 用户消息的本地 id 就是 client_message_id，这样同一句话在线/离线只落一行；
  // 助手消息没有客户端 id，用服务端 id。
  await rt.history.addAll(
    items.map((m) => ({
      id: m.client_message_id || m.id,
      role: m.role === 'ai' ? ('assistant' as const) : ('user' as const),
      content_preview: m.content,
      full_content_url: null,
      created_at: m.created_at,
    }))
  );

  const newest = items.reduce(
    (max, m) => (m.created_at > max ? m.created_at : max),
    items[0].created_at
  );
  if (!watermark || newest > watermark) {
    writeWatermark(rt.db, newest, items.length);
  }
  return items;
}

/** 聚焦时的一次完整同步：先补投，再拉新。 */
export async function syncChat(rt: ChatSyncRuntime = defaultRuntime()): Promise<void> {
  await flushChatOutbox(rt);
  await pullChatHistory(rt);
}

function readWatermark(db: SqliteDb): string | null {
  const row = db.getFirstSync<{ last_synced_at: string | null }>(
    'SELECT last_synced_at FROM sync_metadata WHERE table_name = ?',
    [WATERMARK_TABLE]
  );
  return row?.last_synced_at || null;
}

function writeWatermark(db: SqliteDb, iso: string, count: number): void {
  db.runSync(
    `UPDATE sync_metadata
        SET last_synced_at = ?, record_count = record_count + ?
      WHERE table_name = ?`,
    [iso, count, WATERMARK_TABLE]
  );
}
