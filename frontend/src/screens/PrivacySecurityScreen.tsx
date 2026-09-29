/**
 * 隐私与安全页面。
 *
 * App Store 合规：隐私政策 / 用户协议应用内常驻可达（外链打开）。
 * - 法律条款：隐私政策、用户协议（Linking 外链，失败降级 Alert）
 * - 你的数据：AI 数据共享同意状态（只读展示，修改走我的 → 数据共享设置的 consent 弹窗）、
 *   数据删除说明（注销路径指引）
 */
import React, { useCallback, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert, Linking,
} from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
// 类型级引用 App.tsx 的路由表（import type 会被完全擦除，不产生运行时循环依赖）
import type { RootStackParamList } from '../../App';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize } from '../theme/colors';
import { PRIVACY_POLICY_URL, TERMS_URL } from '../config/links';
import { getUserProfile } from '../services/api';
import { useAuth } from '../context/AuthContext';

// consent 展示态：loading / unknown（拉取失败）与 agreed / declined 必须区分——
// 把"没取到"渲染成"未同意"会误导用户（5.1.1 数据共享状态要如实呈现）。
type ConsentState = 'loading' | 'unknown' | 'agreed' | 'declined';

export default function PrivacySecurityScreen() {
  const { t } = useTranslation();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { isLoggedIn } = useAuth();
  const [consentState, setConsentState] = useState<ConsentState>('unknown');

  // 与 ProfileScreen 同源：consent 状态存于 users 表，经 /users/profile 下发。
  // 聚焦时刷新（用户可能刚从我的页变更同意状态后返回）；游客跳过请求避免 401 噪声。
  const loadConsent = useCallback(() => {
    if (!isLoggedIn) return;
    setConsentState('loading');
    getUserProfile()
      .then((profile) => setConsentState(profile?.ai_consent ? 'agreed' : 'declined'))
      .catch((e) => {
        console.warn('Failed to load profile:', e);
        setConsentState('unknown');
      });
  }, [isLoggedIn]);

  useFocusEffect(loadConsent);

  // 外链打开失败降级：Promises reject（无浏览器/邮件客户端）时给出 i18n 提示
  const openLink = async (url: string) => {
    try {
      await Linking.openURL(url);
    } catch {
      Alert.alert(t('common.error'), t('privacy.openFailed'));
    }
  };

  // 5.1.1：注销路径必须应用内可达——这里直接跳到「我的」并弹出既有注销确认框，
  // 而不是让用户自己去翻入口。
  const goToDeleteAccount = () => {
    navigation.navigate('Tabs', { screen: 'Profile', params: { openDelete: 1 } });
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.pageTitle}>{t('profile.privacy')}</Text>

      {/* 法律条款 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('privacy.sectionLegal')}</Text>
        <TouchableOpacity style={styles.settingItem} onPress={() => openLink(PRIVACY_POLICY_URL)}>
          <Text style={styles.settingIcon}>🔒</Text>
          <Text style={styles.settingLabel}>{t('privacy.policy')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem} onPress={() => openLink(TERMS_URL)}>
          <Text style={styles.settingIcon}>📄</Text>
          <Text style={styles.settingLabel}>{t('privacy.terms')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 你的数据 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('privacy.sectionData')}</Text>
        {isLoggedIn && (
          <View style={styles.settingItem}>
            <Text style={styles.settingIcon}>📋</Text>
            <Text style={styles.settingLabel}>{t('privacy.consentStatus')}</Text>
            {consentState === 'agreed' && (
              <Text style={styles.settingValueGreen}>{t('profile.agreed')}</Text>
            )}
            {consentState === 'declined' && (
              <Text style={styles.settingValueSmall}>{t('profile.declined')}</Text>
            )}
            {/* 加载中 / 拉取失败都显示占位符而非「未同意」，失败时给出重试入口 */}
            {(consentState === 'loading' || consentState === 'unknown') && (
              <Text style={styles.settingValueSmall}>—</Text>
            )}
            {consentState === 'unknown' && (
              <TouchableOpacity onPress={loadConsent}>
                <Text style={styles.retryLink}>{t('privacy.retry')}</Text>
              </TouchableOpacity>
            )}
          </View>
        )}
        {isLoggedIn && (
          <Text style={styles.hint}>{t('privacy.consentReadonlyHint')}</Text>
        )}
        <TouchableOpacity style={styles.deletionRow} onPress={goToDeleteAccount}>
          <Text style={styles.settingIcon}>🗑</Text>
          <View style={styles.deletionBody}>
            <Text style={styles.deletionTitle}>{t('privacy.deletionTitle')}</Text>
            <Text style={styles.deletionDesc}>{t('privacy.deletionDesc')}</Text>
            <Text style={styles.deletionAction}>{t('privacy.deletionAction')} ›</Text>
          </View>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  pageTitle: {
    fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary,
    marginBottom: Spacing.lg,
  },
  section: { marginBottom: Spacing.xl },
  sectionTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textTertiary, marginBottom: Spacing.md },
  settingItem: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  settingIcon: { fontSize: 18, width: 24 },
  settingLabel: { fontSize: FontSize.title, color: Colors.textPrimary, flex: 1 },
  settingValueSmall: { fontSize: FontSize.lg, color: Colors.textTertiary, marginRight: 8 },
  settingValueGreen: { fontSize: FontSize.lg, color: Colors.success, marginRight: 8 },
  arrow: { fontSize: 20, color: Colors.textTertiary },
  hint: {
    fontSize: FontSize.md, color: Colors.textTertiary, lineHeight: 18,
    paddingVertical: Spacing.sm,
  },
  deletionRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.md,
    paddingVertical: 12, borderTopWidth: 1, borderTopColor: Colors.borderLight,
  },
  deletionBody: { flex: 1 },
  deletionTitle: { fontSize: FontSize.title, color: Colors.textPrimary, marginBottom: 4 },
  deletionDesc: { fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: 20 },
  deletionAction: {
    fontSize: FontSize.lg, color: Colors.gold400, marginTop: 6, fontWeight: '600',
  },
  retryLink: {
    fontSize: FontSize.lg, color: Colors.gold400, marginRight: 8,
    textDecorationLine: 'underline',
  },
});
