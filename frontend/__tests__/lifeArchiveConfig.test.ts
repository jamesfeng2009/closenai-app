/**
 * Life Archive 买断档位常量与 15 语言文案齐备性（M6 / Paywall 第四区块）。
 *
 * 断言的是提审会被打回的三类硬失败：
 * 1. SKU 与 App Store Connect 非消耗品产品 ID 不一致（买断收不到权益）；
 * 2. 档位序与后端 entitlement_repository.SCOPE_RANK 漂移（前端显示已购、后端 40304）；
 * 3. 任一语言缺 archive 文案键，或 ARCHIVE_SCOPES.nameKey 指向不存在的 i18n 键
 *    （界面直接显示键名字符串）。
 */
import { ARCHIVE_SCOPE_RANK, ARCHIVE_SCOPES, isScopeOwned } from '../src/config/archive';
import resources from '../src/i18n/locales';

const LANGUAGES = Object.keys(resources);

/** Paywall 档案区用到的全部文案键。 */
const ARCHIVE_KEYS = [
  'archiveTitle',
  'archiveSubtitle',
  'archiveOneTime',
  'archiveScope1y',
  'archiveScopeAll',
  'archiveScopeLegacy',
  'archiveBuy',
  'archiveOwned',
  'archivePurchaseSuccess',
  'archiveRestoreSuccess',
  'archiveSubscriberHint',
];

/** App Store Connect 侧的三个非消耗品产品 ID（架构方案 §二 M6 拍板形态）。 */
const EXPECTED_SKUS = ['life_archive_1y', 'life_archive_all', 'life_archive_legacy'];

function paywallBlock(lang: string): Record<string, unknown> {
  const bundle = (resources as Record<string, { translation: Record<string, unknown> }>)[lang]
    .translation;
  return bundle.paywall as Record<string, unknown>;
}

describe('Life Archive 档位常量', () => {
  it('三档 SKU 与 App Store Connect 产品 ID 一致', () => {
    expect(ARCHIVE_SCOPES.map((item) => item.sku)).toEqual(EXPECTED_SKUS);
  });

  it('档位序 legacy > all > 1y（与后端 SCOPE_RANK 同口径）', () => {
    expect(ARCHIVE_SCOPE_RANK).toEqual({ '1y': 1, all: 2, legacy: 3 });
    expect(ARCHIVE_SCOPES.map((item) => ARCHIVE_SCOPE_RANK[item.scope])).toEqual([1, 2, 3]);
  });

  it('高档向下兼容低档导出范围，低档不兼容高档', () => {
    expect(isScopeOwned(['legacy'], 'all')).toBe(true);
    expect(isScopeOwned(['legacy'], '1y')).toBe(true);
    expect(isScopeOwned(['all'], 'legacy')).toBe(false);
    expect(isScopeOwned(['1y'], 'all')).toBe(false);
    expect(isScopeOwned([], '1y')).toBe(false);
    // 未持有该档时不能因为 best_scope 为空而放行
    expect(isScopeOwned(['1y'], '1y')).toBe(true);
  });
});

describe('Life Archive Paywall 文案', () => {
  it('覆盖 15 种语言', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)('%s 的 paywall 档案文案齐备且非空', (lang) => {
    const block = paywallBlock(lang);
    for (const key of ARCHIVE_KEYS) {
      expect(typeof block[key]).toBe('string');
      expect((block[key] as string).trim()).not.toBe('');
    }
  });

  it.each(LANGUAGES)('%s 存在 ARCHIVE_SCOPES 引用的档位名键', (lang) => {
    const block = paywallBlock(lang);
    for (const item of ARCHIVE_SCOPES) {
      const shortKey = item.nameKey.replace('paywall.', '');
      expect(typeof block[shortKey]).toBe('string');
    }
  });

  it.each(LANGUAGES)('%s 的三档档位名互不相同', (lang) => {
    const block = paywallBlock(lang);
    const names = ARCHIVE_SCOPES.map(
      (item) => block[item.nameKey.replace('paywall.', '')] as string,
    );
    expect(new Set(names).size).toBe(3);
  });
});
