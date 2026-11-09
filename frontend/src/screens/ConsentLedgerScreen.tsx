/**
 * 同意台账管理屏（P2 #29）。
 *
 * GET /user/consents 早已存在（六用途 × 文案版本 × 授予/撤回时刻），但全仓零调用：
 * 用户只能在「我的」里看到一个 ai_consent 布尔位，看不到「哪个版本、何时同意、何时撤回」，
 * 也没有任何地方能逐项撤回（除了两个旧端点各自的开关）。
 *
 * 口径：
 * - 用途与当前版本由服务端下发，前端不维护第二份枚举；
 * - 只展示当前文案版本的六行；历史版本留在台账里供导出/审计，不在这里堆；
 * - core_service 不给撤回按钮：它随账号存续，撤回语义等同注销（在别处走注销流程）。
 */
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TouchableOpacity, View,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getConsentLedger, setConsentPurpose } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { toLocalDate } from '../utils/datetime';
import type { ConsentLedger, ConsentLedgerItem } from '../types';

function statusKey(item: ConsentLedgerItem): string {
  if (item.granted) return 'consentLedger.granted';
  return item.revoked_at ? 'consentLedger.withdrawn' : 'consentLedger.notGiven';
}

export default function ConsentLedgerScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [ledger, setLedger] = useState<ConsentLedger | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  // 逐项占位：同一时刻只允许一个表态在飞（后端对同一用户串行，前端不必叠请求）
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!isLoggedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setFailed(false);
    getConsentLedger()
      .then(setLedger)
      .catch((e) => {
        console.warn('Failed to load consent ledger:', e);
        setFailed(true);
      })
      .finally(() => setLoading(false));
  }, [isLoggedIn]);

  useFocusEffect(load);

  const onToggle = async (item: ConsentLedgerItem) => {
    if (busy) return;
    setBusy(item.purpose);
    try {
      await setConsentPurpose(item.purpose, !item.granted);
      // 回读而不是本地改 granted：台账还带 granted_at/revoked_at，本地拼会写出假时刻
      getConsentLedger()
        .then(setLedger)
        .catch(() => {});
    } catch (e) {
      console.warn('Failed to update consent:', e);
      Alert.alert(t('common.error'), t('consentLedger.updateFailed'));
    } finally {
      setBusy(null);
    }
  };

  const current = ledger
    ? ledger.items.filter((item) => item.version === ledger.version)
    : [];

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.pageTitle}>{t('consentLedger.title')}</Text>
      <Text style={styles.intro}>{t('consentLedger.intro')}</Text>
      {ledger && (
        <Text style={styles.version}>
          {t('consentLedger.version', { version: ledger.version })}
        </Text>
      )}

      {loading && <ActivityIndicator color={Colors.gold400} style={styles.loading} />}
      {!loading && failed && (
        <TouchableOpacity onPress={load} testID="consentLedgerRetry">
          <Text style={styles.retry}>{t('common.loadFailed')} · {t('common.retry')}</Text>
        </TouchableOpacity>
      )}

      {current.map((item) => {
        const grantDate = item.granted_at ? toLocalDate(item.granted_at) : null;
        const revokeDate = item.revoked_at ? toLocalDate(item.revoked_at) : null;
        return (
          <View key={`${item.purpose}-${item.version}`} style={styles.row} testID={`consent-${item.purpose}`}>
            <View style={styles.rowBody}>
              <Text style={styles.label}>
                {t(`consentLedger.purposes.${item.purpose}`, { defaultValue: item.purpose })}
              </Text>
              <Text style={[styles.status, item.granted ? styles.statusOn : styles.statusOff]}>
                {t(statusKey(item))}
              </Text>
              {item.granted && grantDate && (
                <Text style={styles.meta}>{t('consentLedger.grantedAt', { date: grantDate })}</Text>
              )}
              {!item.granted && revokeDate && (
                <Text style={styles.meta}>{t('consentLedger.withdrawnAt', { date: revokeDate })}</Text>
              )}
              {item.purpose === 'core_service' && (
                <Text style={styles.hint}>{t('consentLedger.coreHint')}</Text>
              )}
            </View>
            {item.purpose !== 'core_service' && (
              <TouchableOpacity
                style={[styles.action, busy === item.purpose && styles.actionDisabled]}
                onPress={() => onToggle(item)}
                disabled={busy !== null}
                testID={`consentToggle-${item.purpose}`}
              >
                <Text style={styles.actionText}>
                  {t(item.granted ? 'consentLedger.withdraw' : 'consentLedger.grant')}
                </Text>
              </TouchableOpacity>
            )}
          </View>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  pageTitle: {
    fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  intro: {
    fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: 20,
    marginBottom: Spacing.sm,
  },
  version: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: Spacing.md },
  loading: { marginTop: Spacing.xl },
  retry: {
    fontSize: FontSize.lg, color: Colors.gold400, paddingVertical: Spacing.md,
    textDecorationLine: 'underline',
  },
  row: {
    flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  rowBody: { flex: 1 },
  label: { fontSize: FontSize.title, color: Colors.textPrimary },
  status: { fontSize: FontSize.md, marginTop: 2 },
  statusOn: { color: Colors.success },
  statusOff: { color: Colors.textTertiary },
  meta: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 2 },
  hint: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 4, lineHeight: 18 },
  action: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.sm,
    paddingHorizontal: Spacing.md, paddingVertical: 6, marginTop: 2,
  },
  actionDisabled: { opacity: 0.5 },
  actionText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
});
