/**
 * Life Archive（M6 一次性买断）档位常量。
 *
 * 独立于 PaywallScreen 组件，便于在 node 环境（jest）直接断言：
 * SKU 形态、档位包含关系与 i18n 键名都是提审要对齐 App Store Connect 的硬约定。
 */
import type { LifeArchiveSku } from '../services/iap';
import type { LifeArchiveScope } from '../types';

/**
 * 三档展示顺序（1y ⊂ all ⊂ legacy）。`sku` 必须与 App Store Connect 的
 * 非消耗品产品 ID 一致；`nameKey` 必须是 `paywall` 命名空间下已存在的 i18n 键。
 */
export const ARCHIVE_SCOPES: {
  scope: LifeArchiveScope;
  sku: LifeArchiveSku;
  nameKey: string;
}[] = [
  { scope: '1y', sku: 'life_archive_1y', nameKey: 'paywall.archiveScope1y' },
  { scope: 'all', sku: 'life_archive_all', nameKey: 'paywall.archiveScopeAll' },
  { scope: 'legacy', sku: 'life_archive_legacy', nameKey: 'paywall.archiveScopeLegacy' },
];

/**
 * 导出范围包含关系：持 legacy 即拥有 all / 1y 的导出范围，反之不成立。
 * 与后端 `entitlement_repository.SCOPE_RANK` 同口径，两侧改动必须同步。
 */
export const ARCHIVE_SCOPE_RANK: Record<LifeArchiveScope, number> = {
  '1y': 1,
  all: 2,
  legacy: 3,
};

/** 已购判定：持有 ≥ 目标档位的权益即视为拥有该档（后端 owned_scopes 返回实际购买档位，不含向下兼容展开）。 */
export function isScopeOwned(
  ownedScopes: LifeArchiveScope[],
  scope: LifeArchiveScope,
): boolean {
  return ownedScopes.some((s) => ARCHIVE_SCOPE_RANK[s] >= ARCHIVE_SCOPE_RANK[scope]);
}
