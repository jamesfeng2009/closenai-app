/**
 * 统一冲突解决策略：last-write-wins（基于 updated_at，缺省退回 created_at）。
 * - 本地无 -> 采用远端
 * - 远端无 -> 保留本地（待向上同步）
 * - 远端 updated_at 更新 -> 远端覆盖本地
 * - 否则保留本地
 */
export function resolveConflict<T extends { updated_at?: string; created_at?: string }>(
  local: T | null,
  remote: T | null
): T | null {
  if (!local) return remote;
  if (!remote) return local;

  const lts = local.updated_at ?? local.created_at ?? '';
  const rts = remote.updated_at ?? remote.created_at ?? '';
  return rts > lts ? remote : local;
}
