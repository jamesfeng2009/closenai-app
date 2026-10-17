/**
 * 订阅 Paywall 页面 (P1-F02)。
 *
 * 按 UI 原型 screen-paywall 实现：
 * - 当前订阅状态（/iap/status）
 * - Pro / Pro+ 档位 × 月付/年付周期 SKU 选择器（年付首选，带省钱锚点）
 * - Pro / Pro+ 权益对比
 * - StoreKit 购买（react-native-iap → /iap/verify 服务端校验）
 * - 第四区块 Life Archive 一次性买断（M6：三档 scope + /iap/entitlements 已购态，
 *   与订阅链路完全独立）
 * - 恢复购买入口（App Store 合规必需，同一入口覆盖订阅与买断）
 *
 * 注：react-native-iap 为原生模块，需开发构建（expo prebuild / dev client）；
 * Expo Go 中购买按钮显示环境不可用提示。
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, Linking,
} from 'react-native';
import type { Product, Subscription } from 'react-native-iap';
import { BlurView } from 'expo-blur';
import { LinearGradient } from 'expo-linear-gradient';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { ARCHIVE_SCOPES, isScopeOwned } from '../config/archive';
import { PRIVACY_POLICY_URL, TERMS_URL } from '../config/links';
import { lifeArchivePdfFile, pdfFailureKey } from '../config/pdfFiles';
import { getEntitlements, getLifeArchivePdf, getSubscriptionStatus, ApiError } from '../services/api';
import { exportPdf, PdfUnavailableError } from '../services/pdfExport';
import { parseIntroTrialOffer } from '../services/introOffer';
import {
  getLifeArchiveProducts, getProducts, initIAP, isUserCancelled, purchaseLifeArchive,
  purchaseSubscription, restorePurchases,
} from '../services/iap';
import type { LifeArchiveSku } from '../services/iap';
import type {
  EntitlementsResult, LifeArchiveScope, SubscriptionStatus, UserTier,
} from '../types';
import { useTranslation } from 'react-i18next';

type PaidTier = 'pro' | 'pro_plus';
type BillingPeriod = 'monthly' | 'yearly';

/** 档位 × 周期 → SKU（与 App Store Connect 配置一致，同一订阅组支持升降级）。 */
const SKU_MAP: Record<PaidTier, Record<BillingPeriod, string>> = {
  pro: { monthly: 'closenai_pro_monthly', yearly: 'closenai_pro_yearly' },
  pro_plus: { monthly: 'closenai_pro_plus_monthly', yearly: 'closenai_pro_plus_yearly' },
};

/** 档位高低排序（free < pro < pro_plus），用于升级/降级语义判断。 */
const TIER_ORDER: Record<UserTier, number> = { free: 0, pro: 1, pro_plus: 2 };

const PRO_FEATURES = [
  'paywall.featureUnlimitedChat',
  'paywall.featurePriorityLLM',
  'paywall.featurePersonalityRefresh',
  'paywall.featureTimeline',
] as const;

// Pro+ 权益口径见 monetization-plan §2.1：Pro 全部权益 + 对话 100 条/日 +
// 画像刷新不限 + 每日多条主动消息。
const PRO_PLUS_FEATURES = [
  'paywall.plusFeatureAllPro',
  'paywall.plusFeatureChat',
  'paywall.plusFeatureRefresh',
  'paywall.plusFeatureProactive',
] as const;

// Tier 显示名映射（品牌名，不走 i18n）。避免 toUpperCase() 把 pro_plus 显示成 PRO_PLUS。
const TIER_DISPLAY: Record<string, string> = {
  free: 'Free',
  pro: 'Pro',
  pro_plus: 'Pro+',
};

type BadgeTone = 'current' | 'upgrade' | 'downgrade';

export default function PaywallScreen() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [products, setProducts] = useState<Subscription[]>([]);
  const [selectedTier, setSelectedTier] = useState<PaidTier>('pro');
  // 年付为首选策略（monetization-plan §2.2）：默认选中年付。
  const [selectedPeriod, setSelectedPeriod] = useState<BillingPeriod>('yearly');
  // IAP 环境不可用（Expo Go 无原生模块）或商品列表为空时展示持久提示，
  // 而非仅静默禁用按钮。unavailableMsgKey 区分两种原因的文案。
  const [iapUnavailable, setIapUnavailable] = useState(false);
  const [unavailableMsgKey, setUnavailableMsgKey] = useState('paywall.unavailable');
  // Life Archive 一次性买断：三档商品（本地化价格）+ 已购权益
  const [archiveProducts, setArchiveProducts] = useState<Product[]>([]);
  const [entitlements, setEntitlements] = useState<EntitlementsResult | null>(null);
  const [archiveBusyScope, setArchiveBusyScope] = useState<LifeArchiveScope | null>(null);
  // 已购档位的 PDF 导出（批1 G2）：与购买链路共用忙语义，避免一边导出一边成交互盖状态。
  const [archivePdfScope, setArchivePdfScope] = useState<LifeArchiveScope | null>(null);

  useEffect(() => {
    loadStatus();
    loadProducts();
    loadArchive();
  }, []);

  // silent=true 时为后台刷新（购买/恢复成功后刷新状态），不触发全屏 loading 闪烁。
  // 仅首次 mount 用 loading 展示全屏 ActivityIndicator；后续刷新复用已有数据。
  const loadStatus = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      setStatus(await getSubscriptionStatus());
    } catch (e) {
      console.warn('Failed to load subscription:', e);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  // 拉取全部 4 个 SKU 的本地化价格（App Store 要求购买前展示价格）；
  // 环境不可用或商品列表为空时展示持久提示，避免按钮静默禁用让用户困惑。
  const loadProducts = async () => {
    if (!(await initIAP())) {
      setUnavailableMsgKey('paywall.unavailable');
      setIapUnavailable(true);
      return;
    }
    const list = await getProducts();
    // getProducts 返回空数组（StoreKit 配置缺失 / 网络/环境异常）时同样视为不可用，
    // 让用户看到"暂不可用"提示而非无价格的灰色按钮。
    if (list.length === 0) {
      setUnavailableMsgKey('paywall.productsUnavailable');
      setIapUnavailable(true);
      return;
    }
    setProducts(list);
  };

  // 档案买断区数据：三档本地化价格 + 已购权益（owned 状态来自后端 user_entitlements，
  // 不读 tier——买断与订阅是两条独立链路）。任一失败都只影响本区，不阻塞订阅区。
  const loadArchive = async () => {
    try {
      setEntitlements(await getEntitlements());
    } catch (e) {
      console.warn('Failed to load entitlements:', e);
    }
    const list = await getLifeArchiveProducts();
    setArchiveProducts(list);
  };

  const findProduct = (sku: string): Subscription | undefined =>
    products.find((p) => p.productId === sku);

  // iOS 字段（Android 的 SubscriptionAndroid 无顶层 price，经可选链收窄后兜底 null）。
  const priceField = (sku: string, field: 'localizedPrice' | 'price'): string | null =>
    (findProduct(sku) as { localizedPrice?: string; price?: string } | undefined)?.[field] || null;

  // 展示用本地化价格（如 $4.99）。拉不到返回 null，对应卡片置灰、购买按钮禁用。
  const localizedPrice = (sku: string): string | null =>
    priceField(sku, 'localizedPrice') ?? priceField(sku, 'price');

  // 档案档位 → 展示价（SKU 未拉到价格时为 null，按钮禁用，避免无价格购买）。
  const archivePrice = (sku: string): string | null => {
    const product = archiveProducts.find((p) => p.productId === sku);
    return product?.localizedPrice || product?.price || null;
  };

  // 已购判定走后端权益（含高档向下兼容：持 legacy 即拥有 all/1y 的导出范围）。
  const isArchiveOwned = (scope: LifeArchiveScope): boolean =>
    isScopeOwned(entitlements?.owned_scopes ?? [], scope);

  // price 为本币数字字符串；同档月/年 SKU 同币种，可直接比价。
  const numericPrice = (sku: string): number | null => {
    const n = parseFloat(priceField(sku, 'price') ?? '');
    return Number.isFinite(n) && n > 0 ? n : null;
  };

  // 年付省钱锚点：月付价 ×12 对比年付价动态计算。
  // 任一价格拉不到或年付并无优惠（pct <= 0）时不显示角标。
  const savePercent = (tier: PaidTier): number | null => {
    const monthly = numericPrice(SKU_MAP[tier].monthly);
    const yearly = numericPrice(SKU_MAP[tier].yearly);
    if (monthly === null || yearly === null) return null;
    const pct = Math.round((1 - yearly / (monthly * 12)) * 100);
    return pct > 0 ? pct : null;
  };

  const isPro = status?.is_active && status.tier !== 'free';
  const currentTier: UserTier = isPro && status ? status.tier : 'free';

  // 卡片角标语义（App Store 同订阅组规则，文案简化）：
  // 当前档位 →「当前」；更高档 →「升级」；更低档 →「降级」；免费用户不显示。
  const badgeFor = (tier: PaidTier): { key: string; tone: BadgeTone } | null => {
    if (currentTier === 'free') return null;
    if (currentTier === tier) return { key: 'paywall.currentPlan', tone: 'current' };
    if (TIER_ORDER[tier] > TIER_ORDER[currentTier]) {
      return { key: 'paywall.planUpgrade', tone: 'upgrade' };
    }
    return { key: 'paywall.planDowngrade', tone: 'downgrade' };
  };

  const selectedSku = SKU_MAP[selectedTier][selectedPeriod];
  const selectedPrice = localizedPrice(selectedSku);
  // F2 · PRD §5.21：选中 SKU 带 7 天免费试用 intro offer 且用户免费时，主按钮切试用文案
  const introDays = parseIntroTrialOffer(findProduct(selectedSku))?.days ?? null;
  const showTrialIntro = currentTier === 'free' && introDays !== null;
  // 已订阅且选中的正是当前生效 SKU：按钮置为"当前方案"并禁用（重复购买无意义）。
  const isCurrentPlan = !!status?.is_active && status.product_id === selectedSku;
  // 订阅、档案买断与档案导出共用 busy 语义：任一流程进行中时三条链路都禁用，
  // 避免并发交易互相覆盖 listener，也避免同一次付费里导出两份档案。
  const anyBusy = busy || archiveBusyScope !== null || archivePdfScope !== null;
  // 价格缺失（该 SKU 拉价失败）时禁用购买，防止无价格购买（App Store 合规）。
  const canPurchase = !anyBusy && !!selectedPrice && !iapUnavailable && !isCurrentPlan;

  // 订阅：StoreKit 购买 → 收据回传后端校验 → 权益生效
  const onSubscribe = async () => {
    if (!canPurchase) return;
    setBusy(true);
    try {
      const result = await purchaseSubscription(selectedSku);
      // 超时但交易可能仍在处理：全局 listener 会接管后续送达的交易，
      // 提示用户稍后查看，不视为成功（避免误报订阅已激活）也不视为失败（避免扣费却报错）。
      if (result && typeof result === 'object' && 'status' in result && result.status === 'timeout_pending') {
        Alert.alert(t('paywall.processing'), t('paywall.timeoutPending'));
        await loadStatus(true);
        return;
      }
      Alert.alert(t('common.done'), t('paywall.purchaseSuccess'));
      await loadStatus(true);
    } catch (e) {
      if (isUserCancelled(e)) {
        // 用户主动取消，不提示
      } else if (e instanceof Error && e.message === 'iap_unavailable') {
        Alert.alert(t('common.error'), t('paywall.unavailable'));
      } else {
        const msg = e instanceof ApiError ? e.message : t('paywall.purchaseFailed');
        Alert.alert(t('common.error'), msg);
      }
    } finally {
      setBusy(false);
    }
  };

  // 档案买断（一次性非消耗品）：requestPurchase → 校验（kind=entitlement）→ finishTransaction
  // 买断不改 tier，因此成功后刷新的是 entitlements 而不是订阅状态。
  const onBuyArchive = async (scope: LifeArchiveScope, sku: LifeArchiveSku) => {
    if (anyBusy || iapUnavailable || isArchiveOwned(scope)) return;
    setArchiveBusyScope(scope);
    try {
      const result = await purchaseLifeArchive(sku);
      if (result && typeof result === 'object' && 'status' in result && result.status === 'timeout_pending') {
        Alert.alert(t('paywall.processing'), t('paywall.timeoutPending'));
        await loadArchive();
        return;
      }
      Alert.alert(t('common.done'), t('paywall.archivePurchaseSuccess'));
      await loadArchive();
    } catch (e) {
      if (isUserCancelled(e)) {
        // 用户主动取消，不提示
      } else if (e instanceof Error && e.message === 'iap_unavailable') {
        Alert.alert(t('common.error'), t('paywall.unavailable'));
      } else {
        const msg = e instanceof ApiError ? e.message : t('paywall.purchaseFailed');
        Alert.alert(t('common.error'), msg);
      }
    } finally {
      setArchiveBusyScope(null);
    }
  };

  /** PDF 失败正文：设备不能分享单独一句，其余按后端码分档，不弹裸 error.message。 */
  const pdfFailureText = (e: unknown): string =>
    e instanceof PdfUnavailableError
      ? t('pdf.unavailable')
      : t(pdfFailureKey(e instanceof ApiError ? e.code : undefined));

  // 已购档位导出 PDF（批1 G2）。权益判定不放在这里：按钮只对 owned 出现，
  // 真无权益（权益刚被退款回收、订阅月度额度用尽）由后端 40304 把关，文案分档到
  // pdf.failed.entitlement——App 自己判一遍就是第二个真源。
  const onExportArchivePdf = async (scope: LifeArchiveScope) => {
    if (anyBusy || !isArchiveOwned(scope)) return;
    setArchivePdfScope(scope);
    try {
      const bytes = await getLifeArchivePdf(scope);
      await exportPdf(bytes, lifeArchivePdfFile(scope));
    } catch (e) {
      Alert.alert(t('common.error'), pdfFailureText(e));
    } finally {
      setArchivePdfScope(null);
    }
  };

  // 恢复购买（合规必需）：拉取可恢复交易 → 逐笔后端校验
  // 订阅与档案买断共用同一入口（后端 /iap/verify 按收据内交易类型分流），
  // 提示文案按恢复到的类型区分，避免把买断恢复说成"订阅已恢复"。
  const onRestore = async () => {
    if (anyBusy) return;
    setBusy(true);
    try {
      const { restored, entitlementRestored } = await restorePurchases();
      if (restored && entitlementRestored) {
        Alert.alert(t('common.done'), `${t('paywall.restoreSuccess')}\n${t('paywall.archiveRestoreSuccess')}`);
      } else if (entitlementRestored) {
        Alert.alert(t('common.done'), t('paywall.archiveRestoreSuccess'));
      } else {
        Alert.alert(
          t('common.done'),
          restored ? t('paywall.restoreSuccess') : t('paywall.restoreNone'),
        );
      }
      if (restored || entitlementRestored) {
        await Promise.all([loadStatus(true), loadArchive()]);
      }
    } catch (e) {
      if (e instanceof Error && e.message === 'iap_unavailable') {
        Alert.alert(t('common.error'), t('paywall.unavailable'));
      } else {
        const msg = e instanceof ApiError ? e.message : t('paywall.purchaseFailed');
        Alert.alert(t('common.error'), msg);
      }
    } finally {
      setBusy(false);
    }
  };

  // 全屏 loading 仅在首次加载（loading 且尚无数据）时展示，避免后台刷新闪烁。
  if (loading && !status) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  const features = selectedTier === 'pro' ? PRO_FEATURES : PRO_PLUS_FEATURES;
  const yearlySave = savePercent(selectedTier);

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.hero}>⭐</Text>
      <Text style={styles.title}>{t('paywall.title')}</Text>
      <Text style={styles.subtitle}>{t('paywall.subtitle')}</Text>

      {/* 当前状态（试用中显示试用标记，P1-F02 §4） */}
      {isPro && (
        <View style={styles.statusCard}>
          <View style={styles.statusRow}>
            <Text style={styles.statusText}>
              {t('paywall.currentPlan')}: {TIER_DISPLAY[status?.tier || 'free'] || status?.tier}
            </Text>
            {status?.is_trial && (
              <View style={styles.trialBadge}>
                <Text style={styles.trialBadgeText}>{t('paywall.trialBadge')}</Text>
              </View>
            )}
          </View>
          {!!status?.expires_at && (
            <Text style={styles.statusExpiry}>
              {t('paywall.expiresAt')}: {new Date(status.expires_at).toLocaleDateString()}
            </Text>
          )}
        </View>
      )}

      {/* 周期选择（年付首选：带「省 ~X%」锚点角标，按拉到的价格动态计算） */}
      <View style={styles.periodRow}>
        {(['monthly', 'yearly'] as const).map((period) => {
          const active = selectedPeriod === period;
          return (
            <TouchableOpacity
              key={period}
              style={[styles.periodOption, active && styles.periodOptionActive]}
              onPress={() => setSelectedPeriod(period)}
            >
              <Text style={[styles.periodText, active && styles.periodTextActive]}>
                {t(period === 'monthly' ? 'paywall.periodMonthly' : 'paywall.periodYearly')}
              </Text>
              {period === 'yearly' && yearlySave !== null && (
                <View style={styles.saveBadge}>
                  <Text style={styles.saveBadgeText}>
                    {t('paywall.saveBadge', { percent: yearlySave })}
                  </Text>
                </View>
              )}
            </TouchableOpacity>
          );
        })}
      </View>

      {/* 档位卡片（SKU 拉价失败时对应卡片置灰显示「—」，不崩溃） */}
      <View style={styles.tierRow}>
        {(['pro', 'pro_plus'] as const).map((tier) => {
          const active = selectedTier === tier;
          const price = localizedPrice(SKU_MAP[tier][selectedPeriod]);
          const badge = badgeFor(tier);
          const cardContent = (
            <>
              <Text style={styles.tierName}>
                {t(tier === 'pro' ? 'paywall.planPro' : 'paywall.planProPlus')}
              </Text>
              <Text style={styles.tierPrice}>{price ?? '—'}</Text>
              <Text style={styles.tierPeriod}>
                {t(selectedPeriod === 'monthly' ? 'paywall.periodMonthly' : 'paywall.periodYearly')}
              </Text>
              {badge && (
                <View
                  style={[
                    styles.tierBadge,
                    badge.tone === 'current' && styles.tierBadgeCurrent,
                    badge.tone === 'upgrade' && styles.tierBadgeUpgrade,
                  ]}
                >
                  <Text
                    style={[
                      styles.tierBadgeText,
                      badge.tone === 'current' && styles.tierBadgeTextCurrent,
                      badge.tone === 'upgrade' && styles.tierBadgeTextUpgrade,
                    ]}
                  >
                    {t(badge.key)}
                  </Text>
                </View>
              )}
            </>
          );
          // Pro+ 卡片：渐变边框凸显高档位（未选中霓虹紫 gradNeon / 选中金 gradGold）
          if (tier === 'pro_plus') {
            return (
              <LinearGradient
                key={tier}
                colors={active ? Colors.gradGold : Colors.gradNeon}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={[styles.tierCardGradient, !price && styles.tierCardUnavailable]}
              >
                <TouchableOpacity
                  style={[styles.tierCardInner, active && styles.tierCardInnerActive]}
                  onPress={() => setSelectedTier(tier)}
                >
                  {cardContent}
                </TouchableOpacity>
              </LinearGradient>
            );
          }
          return (
            <TouchableOpacity
              key={tier}
              style={[
                styles.tierCard,
                active && styles.tierCardActive,
                !price && styles.tierCardUnavailable,
              ]}
              onPress={() => setSelectedTier(tier)}
            >
              {cardContent}
            </TouchableOpacity>
          );
        })}
      </View>

      {/* 权益列表（随选中档位切换） */}
      <View style={styles.card}>
        {features.map((key) => (
          <View key={key} style={styles.featureRow}>
            <Text style={styles.featureCheck}>✓</Text>
            <Text style={styles.featureText}>{t(key)}</Text>
          </View>
        ))}
      </View>

      {/* 免费 vs Pro 对比区块（F2 · PRD §5.20 划入：左模糊右高清，免费用户可见） */}
      {!isPro && (
        <View style={styles.compareCard}>
          <Text style={styles.compareTitle}>{t('paywall.compareTitle')}</Text>
          <View style={styles.compareRow}>
            <View style={styles.compareCol}>
              <Text style={styles.compareColLabel}>Free</Text>
              <View style={styles.compareSampleBox}>
                <Text style={styles.compareSample}>{t('paywall.compareSample')}</Text>
                <BlurView intensity={30} tint="dark" style={styles.compareBlur}>
                  <Text style={styles.compareLock}>🔒 {t('insights.unlockHd')}</Text>
                </BlurView>
              </View>
            </View>
            <View style={[styles.compareCol, styles.compareColPro]}>
              <Text style={[styles.compareColLabel, styles.compareColLabelPro]}>Pro</Text>
              <View style={[styles.compareSampleBox, styles.compareSampleBoxPro]}>
                <Text style={[styles.compareSample, styles.compareSamplePro]}>
                  {t('paywall.compareSample')}
                </Text>
              </View>
            </View>
          </View>
        </View>
      )}

      {/* 订阅按钮（购买前展示本地化价格，App Store 合规；价格缺失时禁用，防止无价格购买） */}
      <TouchableOpacity
        style={[styles.subscribeBtn, !canPurchase && styles.subscribeBtnDisabled]}
        onPress={onSubscribe}
        disabled={!canPurchase}
      >
        {busy ? (
          <ActivityIndicator color={Colors.bgPrimary} />
        ) : (
          <>
            <Text style={styles.subscribeText}>
              {isCurrentPlan
                ? t('paywall.currentPlan')
                : showTrialIntro
                  ? t('paywall.trialButton', { days: introDays })
                  : t('paywall.subscribe')}
            </Text>
            {!!selectedPrice && !isCurrentPlan && (
              <Text style={styles.priceText}>
                {showTrialIntro ? t('paywall.trialThen', { price: selectedPrice }) : selectedPrice}
              </Text>
            )}
          </>
        )}
      </TouchableOpacity>

      {/* 自动续订声明（App Store 3.1.2：续订条款须在购买界面直接可见，不能只在隐私政策里） */}
      <Text style={styles.renewNote}>{t('paywall.autoRenewNote')}</Text>

      {/* ── 第四区块：Life Archive 一次性买断（M6 / 架构方案 §二 M6）──
          与订阅区用分隔线 + 独立卡片底分离：§九-4 口径"订阅卖活体、买断卖标本"，
          两者是互不影响的权益链路，UI 上不能被读成"第三个订阅档位"。 */}
      <View style={styles.archiveDivider} />
      <View style={styles.archiveSection}>
        <View style={styles.archiveHeader}>
          <Text style={styles.archiveTitle}>{t('paywall.archiveTitle')}</Text>
          <View style={styles.archiveOneTimeBadge}>
            <Text style={styles.archiveOneTimeText}>{t('paywall.archiveOneTime')}</Text>
          </View>
        </View>
        <Text style={styles.archiveSubtitle}>{t('paywall.archiveSubtitle')}</Text>
        {/* 买断与订阅的计费差异要说清（3.1.2：不能让用户误以为买断也会续订扣费） */}
        <Text style={styles.archiveOneTimeNote}>{t('paywall.oneTimeNote')}</Text>

        {ARCHIVE_SCOPES.map(({ scope, sku, nameKey }) => {
          const price = archivePrice(sku);
          const owned = isArchiveOwned(scope);
          const rowBusy = archiveBusyScope === scope;
          const exporting = archivePdfScope === scope;
          // 已拥有（含更高档向下兼容）时不给重复购买入口；价格缺失时禁用，防无价格购买。
          const disabled = owned || rowBusy || anyBusy || iapUnavailable || !price;
          return (
            <View key={scope} style={styles.archiveRow}>
              <View style={styles.archiveRowMain}>
                <Text style={styles.archiveScopeName}>{t(nameKey)}</Text>
                {!!price && !owned && (
                  <Text style={styles.archivePrice}>{price}</Text>
                )}
              </View>
              {owned ? (
                <View style={styles.archiveOwnedActions}>
                  <View style={styles.archiveOwnedBadge}>
                    <Text style={styles.archiveOwnedText}>{t('paywall.archiveOwned')}</Text>
                  </View>
                  <TouchableOpacity
                    style={[styles.archiveExportBtn, anyBusy && styles.archiveExportBtnDisabled]}
                    onPress={() => onExportArchivePdf(scope)}
                    disabled={anyBusy}
                  >
                    {exporting ? (
                      <ActivityIndicator color={Colors.bgPrimary} />
                    ) : (
                      <Text style={styles.archiveExportText}>{t('pdf.export')}</Text>
                    )}
                  </TouchableOpacity>
                </View>
              ) : (
                <TouchableOpacity
                  style={[styles.archiveBuyBtn, disabled && styles.archiveBuyBtnDisabled]}
                  onPress={() => onBuyArchive(scope, sku)}
                  disabled={disabled}
                >
                  {rowBusy ? (
                    <ActivityIndicator color={Colors.bgPrimary} />
                  ) : (
                    <Text style={styles.archiveBuyText}>{t('paywall.archiveBuy')}</Text>
                  )}
                </TouchableOpacity>
              )}
            </View>
          );
        })}

        {/* Pro 联动权益（架构方案 §二 决策点：订阅加分，不取消买断） */}
        <Text style={styles.archiveHint}>{t('paywall.archiveSubscriberHint')}</Text>
      </View>

      {/* IAP 环境不可用提示（Expo Go 等）：持久展示原因，避免按钮静默禁用 */}
      {iapUnavailable && (
        <View style={styles.unavailableBox}>
          <Text style={styles.unavailableText}>{t(unavailableMsgKey)}</Text>
        </View>
      )}

      {/* 恢复购买（App Store 合规必需；同一入口覆盖订阅与档案买断） */}
      <TouchableOpacity style={styles.restoreBtn} onPress={onRestore} disabled={anyBusy}>
        <Text style={styles.restoreText}>{t('paywall.restore')}</Text>
      </TouchableOpacity>

      {/* 条款链接（App Store 审核 3.1.2：自动续期订阅必须展示 ToS / Privacy） */}
      <View style={styles.legalRow}>
        <TouchableOpacity onPress={() => Linking.openURL(TERMS_URL)}>
          <Text style={styles.legalLink}>{t('paywall.terms')}</Text>
        </TouchableOpacity>
        <Text style={styles.legalSep}>·</Text>
        <TouchableOpacity onPress={() => Linking.openURL(PRIVACY_POLICY_URL)}>
          <Text style={styles.legalLink}>{t('paywall.privacy')}</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40, alignItems: 'center' },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  hero: { fontSize: 56, marginTop: Spacing.xl },
  title: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary, marginTop: Spacing.md },
  subtitle: { fontSize: FontSize.title, color: Colors.textSecondary, marginTop: 6, textAlign: 'center' },
  statusCard: {
    marginTop: Spacing.lg, padding: Spacing.md, alignItems: 'center',
    backgroundColor: 'rgba(16,185,129,0.08)', borderWidth: 1, borderColor: 'rgba(16,185,129,0.25)',
    borderRadius: BorderRadius.lg, alignSelf: 'stretch',
  },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  statusText: { fontSize: FontSize.title, fontWeight: '600', color: Colors.success },
  statusExpiry: { fontSize: FontSize.md, color: Colors.textSecondary, marginTop: 4 },
  trialBadge: {
    backgroundColor: 'rgba(255,215,0,0.15)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.4)',
    borderRadius: BorderRadius.sm, paddingHorizontal: 8, paddingVertical: 2,
  },
  trialBadgeText: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.gold400 },
  periodRow: {
    flexDirection: 'row', alignSelf: 'stretch', marginTop: Spacing.xl, gap: Spacing.sm,
  },
  periodOption: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
    paddingVertical: 12, borderRadius: BorderRadius.lg,
    backgroundColor: Colors.bgTertiary, borderWidth: 1, borderColor: 'transparent',
  },
  periodOptionActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.08)' },
  periodText: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textSecondary },
  periodTextActive: { color: Colors.gold400 },
  saveBadge: {
    backgroundColor: 'rgba(255,215,0,0.15)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.4)',
    borderRadius: BorderRadius.sm, paddingHorizontal: 6, paddingVertical: 2,
  },
  saveBadgeText: { fontSize: FontSize.sm, fontWeight: '700', color: Colors.gold400 },
  tierRow: { flexDirection: 'row', alignSelf: 'stretch', marginTop: Spacing.md, gap: Spacing.sm },
  tierCard: {
    flex: 1, alignItems: 'center', padding: Spacing.md,
    backgroundColor: Colors.bgTertiary, borderWidth: 1, borderColor: 'transparent',
    borderRadius: BorderRadius.lg,
  },
  tierCardActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.08)' },
  // Pro+ 渐变边框卡片：外层 1px 渐变边 + 内层深色内容底
  tierCardGradient: { flex: 1, borderRadius: BorderRadius.lg, padding: 1 },
  tierCardInner: {
    flex: 1, alignItems: 'center', padding: Spacing.md,
    backgroundColor: Colors.bgTertiary, borderRadius: BorderRadius.lg - 1,
  },
  tierCardInnerActive: { backgroundColor: 'rgba(255,215,0,0.08)' },
  tierCardUnavailable: { opacity: 0.45 },
  tierName: { fontSize: FontSize.title, fontWeight: '700', color: Colors.textPrimary },
  tierPrice: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.gold400, marginTop: 4 },
  tierPeriod: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 2 },
  tierBadge: {
    marginTop: 8, borderRadius: BorderRadius.sm, paddingHorizontal: 8, paddingVertical: 2,
    backgroundColor: Colors.bgSecondary, borderWidth: 1, borderColor: Colors.textMuted,
  },
  tierBadgeCurrent: { backgroundColor: 'rgba(16,185,129,0.12)', borderColor: 'rgba(16,185,129,0.4)' },
  tierBadgeUpgrade: { backgroundColor: 'rgba(255,215,0,0.15)', borderColor: 'rgba(255,215,0,0.4)' },
  tierBadgeText: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.textTertiary },
  tierBadgeTextCurrent: { color: Colors.success },
  tierBadgeTextUpgrade: { color: Colors.gold400 },
  card: {
    alignSelf: 'stretch', marginTop: Spacing.lg, padding: Spacing.lg,
    backgroundColor: 'rgba(255,215,0,0.05)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl,
  },
  featureRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 },
  featureCheck: { fontSize: FontSize.title, color: Colors.gold400, fontWeight: '700' },
  featureText: { fontSize: FontSize.title, color: Colors.textPrimary },
  subscribeBtn: {
    alignSelf: 'stretch', marginTop: Spacing.xl,
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 16, alignItems: 'center',
  },
  subscribeText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
  subscribeBtnDisabled: { opacity: 0.6 },
  priceText: { fontSize: FontSize.md, color: Colors.bgPrimary, marginTop: 2, opacity: 0.85 },
  // 免费 vs Pro 对比区块（F2）：左列模糊样张（BlurView 盖字 + 🔒），右列 Pro 高清全文
  compareCard: {
    alignSelf: 'stretch', marginTop: Spacing.lg, padding: Spacing.lg,
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl,
  },
  compareTitle: { fontSize: FontSize.title, fontWeight: '700', color: Colors.textPrimary },
  compareRow: { flexDirection: 'row', gap: Spacing.sm, marginTop: Spacing.md },
  compareCol: { flex: 1 },
  compareColPro: { borderRadius: BorderRadius.lg, borderWidth: 1, borderColor: 'rgba(255,215,0,0.4)' },
  compareColLabel: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.textTertiary, marginBottom: 6 },
  compareColLabelPro: { color: Colors.gold400 },
  compareSampleBox: {
    flex: 1, overflow: 'hidden', borderRadius: BorderRadius.lg,
    backgroundColor: 'rgba(255,255,255,0.03)', padding: Spacing.md, minHeight: 96,
  },
  compareSampleBoxPro: { backgroundColor: 'rgba(255,215,0,0.06)' },
  compareSample: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: 20 },
  compareSamplePro: { color: Colors.textPrimary },
  compareBlur: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
  compareLock: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.gold400 },
  // 自动续订声明（3.1.2）：紧随订阅按钮，字号小但不可省略
  renewNote: {
    alignSelf: 'stretch', marginTop: Spacing.md,
    fontSize: FontSize.md, color: Colors.textTertiary, textAlign: 'center', lineHeight: 18,
  },
  restoreBtn: { marginTop: Spacing.lg, paddingVertical: 10 },
  unavailableBox: {
    alignSelf: 'stretch', marginTop: Spacing.md, padding: Spacing.md,
    backgroundColor: 'rgba(255,159,67,0.08)', borderWidth: 1, borderColor: 'rgba(255,159,67,0.25)',
    borderRadius: BorderRadius.lg,
  },
  unavailableText: { fontSize: FontSize.md, color: Colors.textSecondary, textAlign: 'center', lineHeight: 20 },
  restoreText: { fontSize: FontSize.md, color: Colors.textTertiary, textDecorationLine: 'underline' },
  legalRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: Spacing.md },
  legalLink: { fontSize: FontSize.md, color: Colors.textTertiary, textDecorationLine: 'underline' },
  legalSep: { fontSize: FontSize.md, color: Colors.textTertiary },
  // Life Archive 买断区：霓虹紫底 + 顶部 hairline，与金色订阅区在色彩与结构上都分离
  archiveDivider: {
    alignSelf: 'stretch', height: 1, marginTop: Spacing.xl,
    backgroundColor: 'rgba(99,102,241,0.3)',
  },
  archiveSection: {
    alignSelf: 'stretch', marginTop: Spacing.lg, padding: Spacing.lg,
    backgroundColor: 'rgba(99,102,241,0.07)', borderWidth: 1,
    borderColor: 'rgba(99,102,241,0.25)', borderRadius: BorderRadius.xl,
  },
  archiveHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8,
  },
  archiveTitle: { fontSize: FontSize.title, fontWeight: '700', color: Colors.textPrimary, flexShrink: 1 },
  archiveOneTimeBadge: {
    backgroundColor: 'rgba(99,102,241,0.18)', borderRadius: BorderRadius.sm,
    paddingHorizontal: 8, paddingVertical: 2,
  },
  archiveOneTimeText: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.textSecondary },
  archiveSubtitle: {
    fontSize: FontSize.md, color: Colors.textSecondary, marginTop: 6, lineHeight: 20,
  },
  archiveOneTimeNote: {
    fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 4, lineHeight: 18,
  },
  archiveRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: 10, marginTop: Spacing.md,
  },
  archiveRowMain: { flex: 1, flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  archiveScopeName: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary },
  archivePrice: { fontSize: FontSize.md, color: Colors.textSecondary },
  archiveBuyBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.md,
    paddingHorizontal: 16, paddingVertical: 8, minWidth: 76, alignItems: 'center',
  },
  archiveBuyBtnDisabled: { opacity: 0.45 },
  archiveBuyText: { fontSize: FontSize.md, fontWeight: '700', color: Colors.bgPrimary },
  archiveOwnedBadge: {
    backgroundColor: 'rgba(16,185,129,0.12)', borderWidth: 1, borderColor: 'rgba(16,185,129,0.4)',
    borderRadius: BorderRadius.sm, paddingHorizontal: 8, paddingVertical: 2,
  },
  archiveOwnedText: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.success },
  archiveOwnedActions: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  // 导出按钮刻意做成描边（不抢金色购买 CTA）：已购用户回到这页要买的是下一档，不是再买一次。
  archiveExportBtn: {
    borderWidth: 1, borderColor: Colors.gold400, borderRadius: BorderRadius.md,
    paddingHorizontal: 12, paddingVertical: 6, minWidth: 76, alignItems: 'center',
  },
  archiveExportBtnDisabled: { opacity: 0.45 },
  archiveExportText: { fontSize: FontSize.sm, fontWeight: '700', color: Colors.gold400 },
  archiveHint: {
    fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.md, lineHeight: 18,
  },
});
