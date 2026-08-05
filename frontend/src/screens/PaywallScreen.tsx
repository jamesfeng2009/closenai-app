/**
 * 订阅 Paywall 页面 (P1-F02)。
 *
 * 按 UI 原型 screen-paywall 实现：
 * - 当前订阅状态（/iap/status）
 * - Pro / Pro+ 权益对比
 * - StoreKit 购买（react-native-iap → /iap/verify 服务端校验）
 * - 恢复购买入口（App Store 合规必需）
 *
 * 注：react-native-iap 为原生模块，需开发构建（expo prebuild / dev client）；
 * Expo Go 中购买按钮显示环境不可用提示。
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, Linking,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getSubscriptionStatus, ApiError } from '../services/api';
import {
  DEFAULT_SKU, getProducts, initIAP, isUserCancelled, purchaseSubscription, restorePurchases,
} from '../services/iap';
import type { SubscriptionStatus } from '../types';
import { useTranslation } from 'react-i18next';

const PRO_FEATURES = [
  'paywall.featureUnlimitedChat',
  'paywall.featurePriorityLLM',
  'paywall.featurePersonalityRefresh',
  'paywall.featureTimeline',
] as const;

// Tier 显示名映射（品牌名，不走 i18n）。避免 toUpperCase() 把 pro_plus 显示成 PRO_PLUS。
const TIER_DISPLAY: Record<string, string> = {
  free: 'Free',
  pro: 'Pro',
  pro_plus: 'Pro+',
};

export default function PaywallScreen() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [price, setPrice] = useState<string | null>(null);
  // IAP 环境不可用（Expo Go 无原生模块）或商品列表为空时展示持久提示，
  // 而非仅静默禁用按钮。unavailableMsgKey 区分两种原因的文案。
  const [iapUnavailable, setIapUnavailable] = useState(false);
  const [unavailableMsgKey, setUnavailableMsgKey] = useState('paywall.unavailable');

  useEffect(() => {
    loadStatus();
    loadPrice();
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

  // 拉取本地化价格（App Store 要求购买前展示价格）；环境不可用或商品列表为空时
  // 展示持久提示，避免按钮静默禁用让用户困惑。
  const loadPrice = async () => {
    if (!(await initIAP())) {
      setUnavailableMsgKey('paywall.unavailable');
      setIapUnavailable(true);
      return;
    }
    const products = await getProducts();
    // getProducts 返回空数组（StoreKit 配置缺失 / 网络/环境异常）时同样视为不可用，
    // 让用户看到"暂不可用"提示而非无价格的灰色按钮。
    if (products.length === 0) {
      setUnavailableMsgKey('paywall.productsUnavailable');
      setIapUnavailable(true);
      return;
    }
    const product = products.find((p) => p.productId === DEFAULT_SKU) ?? products[0];
    const localized =
      (product as { localizedPrice?: string } | undefined)?.localizedPrice ??
      (product as { price?: string } | undefined)?.price ??
      null;
    if (localized) setPrice(localized);
  };

  // 订阅：StoreKit 购买 → 收据回传后端校验 → 权益生效
  const onSubscribe = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await purchaseSubscription();
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

  // 恢复购买（合规必需）：拉取可恢复交易 → 逐笔后端校验
  const onRestore = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const { restored } = await restorePurchases();
      Alert.alert(
        t('common.done'),
        restored ? t('paywall.restoreSuccess') : t('paywall.restoreNone'),
      );
      if (restored) await loadStatus(true);
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

  const isPro = status?.is_active && status.tier !== 'free';

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

      {/* 权益列表 */}
      <View style={styles.card}>
        {PRO_FEATURES.map((key) => (
          <View key={key} style={styles.featureRow}>
            <Text style={styles.featureCheck}>✓</Text>
            <Text style={styles.featureText}>{t(key)}</Text>
          </View>
        ))}
      </View>

      {/* 订阅按钮（购买前展示本地化价格，App Store 合规；价格缺失时禁用，防止无价格购买） */}
      {!isPro && (
        <TouchableOpacity
          style={[styles.subscribeBtn, (busy || !price) && styles.subscribeBtnDisabled]}
          onPress={onSubscribe}
          disabled={busy || !price}
        >
          {busy ? (
            <ActivityIndicator color={Colors.bgPrimary} />
          ) : (
            <>
              <Text style={styles.subscribeText}>{t('paywall.subscribe')}</Text>
              {!!price && <Text style={styles.priceText}>{price}</Text>}
            </>
          )}
        </TouchableOpacity>
      )}

      {/* IAP 环境不可用提示（Expo Go 等）：持久展示原因，避免按钮静默禁用 */}
      {iapUnavailable && !isPro && (
        <View style={styles.unavailableBox}>
          <Text style={styles.unavailableText}>{t(unavailableMsgKey)}</Text>
        </View>
      )}

      {/* 恢复购买（App Store 合规必需） */}
      <TouchableOpacity style={styles.restoreBtn} onPress={onRestore} disabled={busy}>
        <Text style={styles.restoreText}>{t('paywall.restore')}</Text>
      </TouchableOpacity>

      {/* 条款链接（App Store 审核 3.1.2：自动续期订阅必须展示 ToS / Privacy） */}
      <View style={styles.legalRow}>
        <TouchableOpacity onPress={() => Linking.openURL('https://closenai.app/terms')}>
          <Text style={styles.legalLink}>{t('paywall.terms')}</Text>
        </TouchableOpacity>
        <Text style={styles.legalSep}>·</Text>
        <TouchableOpacity onPress={() => Linking.openURL('https://closenai.app/privacy')}>
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
  card: {
    alignSelf: 'stretch', marginTop: Spacing.xl, padding: Spacing.lg,
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
});
