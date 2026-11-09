/**
 * P2 #28 接线闸：离线补投链路真的接在页面上，而不只是有仓储和端点。
 *
 * 判据分四段：
 * 1. 发送时入 outbox，且 SSE 请求带同一个 client_message_id（在线/离线落到同一行）；
 * 2. 流结束标 sent；同意/配额这类永久拒绝标 discarded（否则每次聚焦重复撞墙）；
 * 3. 聚焦时先 flush 再按水位拉，远端用户消息以 client_message_id 作本地 id；
 * 4. 登出清本地用户数据时包含 chat_outbox（否则换账号会把上一账号的正文补投过去）。
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

function block(src: string, signature: string): string {
  const start = src.indexOf(signature);
  expect(start).toBeGreaterThanOrEqual(0);
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error('花括号没配平');
}

describe('ReflectScreen 发送 → outbox', () => {
  const src = read('src/screens/ReflectScreen.tsx');
  const send = block(src, 'const sendMessage = () =>');

  it('发送时把同一条消息记进 chat_outbox', () => {
    expect(send).toContain('chatOutboxRepo');
    expect(send).toContain('client_message_id: userMsg.id');
  });

  it('SSE 请求带 client_message_id（与 outbox 同一个值）', () => {
    const call = block(send, 'streamChat(');
    expect(call).toContain('client_message_id: userMsg.id');
  });

  it('流结束标 sent，避免下次聚焦重复补投', () => {
    expect(send).toContain('markSent([userMsg.id])');
  });

  it('同意/配额失败标 discarded', () => {
    expect(send).toContain('CODE_AI_CONSENT_REQUIRED || code === CODE_QUOTA_EXCEEDED');
    expect(send).toContain('markDiscarded([userMsg.id]');
  });
});

describe('ReflectScreen 聚焦同步', () => {
  const src = read('src/screens/ReflectScreen.tsx');
  const load = block(src, 'const loadInitial = React.useCallback');

  it('先补投再按水位拉，且失败不阻塞首屏', () => {
    const flushAt = load.indexOf('await flushChatOutbox()');
    const pullAt = load.indexOf('await pullChatHistory()');
    const readAt = load.indexOf('chatRepo.getRecent(');
    expect(flushAt).toBeGreaterThanOrEqual(0);
    expect(pullAt).toBeGreaterThan(flushAt);
    expect(readAt).toBeGreaterThan(pullAt);
    expect(load).toContain('.catch(() => {})');
  });

  it('远端用户消息按 client_message_id 认成本地同一条（刷新不出现两份）', () => {
    expect(load).toContain('id: h.client_message_id || h.id');
  });
});

describe('api 层', () => {
  const src = read('src/services/api.ts');

  it('getChatHistory 支持 since 并转义', () => {
    expect(src).toMatch(
      /export async function getChatHistory\([\s\S]{0,120}since\?: string/
    );
    expect(src).toContain('encodeURIComponent(since)');
  });

  it('postChatMessagesBatch 打到 /chat/messages/batch', () => {
    expect(src).toContain("'/chat/messages/batch'");
  });
});

describe('类型与登出清理', () => {
  it('ChatStreamRequest 带可选的 client_message_id', () => {
    const src = read('src/types/index.ts');
    const req = block(src, 'export interface ChatStreamRequest');
    expect(req).toContain('client_message_id?: string');
  });

  it('user_version 已升到 4（V4 迁移会被执行）', () => {
    expect(read('src/services/db/migrations.ts')).toContain('CURRENT_DB_VERSION = 4');
  });

  it('登出清理的用户级表包含 chat_outbox', () => {
    const src = read('src/services/session.ts');
    const list = block(src, 'const USER_TABLES = [');
    expect(list).toContain("'chat_outbox'");
  });
});
