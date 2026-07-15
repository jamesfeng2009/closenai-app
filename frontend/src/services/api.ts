/**
 * API 客户端
 *
 * 封装与 ClosenAI 后端 API 的所有通信。
 * 后端 API 基础路径：/api/v1
 *
 * TODO: 实现完整的 API 客户端
 */

const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

/**
 * 发送反思对话请求（SSE 流式）。
 * TODO: 实现 SSE 流式接收
 */
export async function createReflectionChat(content: string, sessionId?: string) {
  // TODO: 实现 SSE 流式请求
  console.log('TODO: createReflectionChat', { content, sessionId });
}

/**
 * 查询记忆列表。
 */
export async function listMemories() {
  // TODO: 实现 API 调用
  console.log('TODO: listMemories');
  return [];
}

/**
 * 获取单条记忆详情。
 */
export async function getMemory(memoryId: string) {
  // TODO: 实现 API 调用
  console.log('TODO: getMemory', memoryId);
  return null;
}

export const api = {
  createReflectionChat,
  listMemories,
  getMemory,
};
