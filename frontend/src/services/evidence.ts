/**
 * 引用依据读路径聚合（PRD §5.19.3 / 任务 #34）。
 *
 * 「每句可回溯到原话+日期」这条规范，后端只在 /chat/history 与 /journey/events 上
 * 逐条解析好了 memory_refs；洞察侧的读路径不带 id（/growth/insights 响应只有
 * level/content/period，见 backend/models/schemas.py:161）。所以这里用已有端点
 * GET /memories 在前端补齐能补齐的那一半：
 * - 带 related_memory_ids 的（反思草稿）→ 真·id 级回链
 * - 只有周期锚点的（周洞察）→ 退化为「这一周你记下的 N 条」期间级依据
 * 两者都不编造正文与记录的对应关系：解析不到的 id 直接丢弃。
 */
import { listMemories } from './api';
import type { Memory, MemoryRef } from '../types';

// 后端 /memories 的 limit 上限（routers/memories.py:31 le=100）
const LIST_LIMIT = 100;

const DAY_MS = 24 * 60 * 60 * 1000;

/** 与后端同口径：把原话里的连续空白压成单个空格再展示。 */
function toRef(mem: Memory): MemoryRef {
  return {
    memory_id: mem.id,
    created_at: mem.created_at,
    excerpt: mem.content.split(/\s+/).filter(Boolean).join(' '),
  };
}

/**
 * 按 id 解析引用依据，顺序即传入顺序。
 * 只在当前可见记忆（status=current、未删除、最近 100 条）内解析——
 * 已更新/已删的记忆不在列表里，对应 id 被丢弃而不是猜测。
 */
export async function resolveMemoryRefs(ids?: string[] | null): Promise<MemoryRef[]> {
  const wanted = [...new Set((ids ?? []).filter((id): id is string => !!id))];
  if (wanted.length === 0) return [];
  const byId = new Map((await listMemories(LIST_LIMIT)).map((m) => [m.id, m]));
  return wanted
    .map((id) => byId.get(id))
    .filter((m): m is Memory => m !== undefined)
    .map(toRef);
}

/**
 * 期间级依据：取 `[until - days, until]` 内创建的记忆。
 * until 用后端回传的周期锚点（如周洞察的 period），缺失则用当前时间。
 * 锚点是裸日历日（YYYY-MM-DD）时右端进到次日 0 点，否则当天的记录会被自己排除。
 * 锚点解析不出来时返回空数组——宁可不显示，也不把无关记录说成依据。
 */
export async function memoriesWithinDays(
  days: number,
  untilIso?: string | null
): Promise<MemoryRef[]> {
  let until = untilIso ? new Date(untilIso).getTime() : Date.now();
  if (!Number.isFinite(until)) return [];
  if (untilIso && /^\d{4}-\d{2}-\d{2}$/.test(untilIso)) until += DAY_MS;
  const since = until - days * DAY_MS;
  const memories = await listMemories(LIST_LIMIT);
  return memories
    .filter((m) => {
      const t = new Date(m.created_at).getTime();
      return Number.isFinite(t) && t >= since && t <= until;
    })
    .map(toRef);
}
