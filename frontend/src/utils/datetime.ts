/**
 * 本地日工具（与后端 core/time_utils.py 的「今日按用户本地时区」口径对齐）。
 *
 * 本地 SQLite 缓存的 date 列一律存设备本地日（YYYY-MM-DD），禁用
 * created_at.slice(0, 10) —— 那是 UTC 日，跨时区会偏 ±1 天。
 * 仅用于展示层缓存，±1 天偏差可接受（设备时区 ≠ 注册时区时）。
 */

const pad = (n: number): string => String(n).padStart(2, '0');

/** 任意可被 Date 解析的时间（UTC ISO 等）→ 设备本地日（YYYY-MM-DD）。 */
export function toLocalDate(input: string | Date): string {
  const d = input instanceof Date ? input : new Date(input);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
