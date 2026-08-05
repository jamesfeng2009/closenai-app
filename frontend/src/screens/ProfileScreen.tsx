/**
 * 我的/设置页面。
 *
 * 按 UI 原型 screen-profile 实现：
 * - 头像 + 姓名 + 性格类型
 * - AI 与记忆（记忆管理、对话风格、主动消息时间）
 * - 订阅（升级 Pro、使用情况）
 * - 其他设置（语言、隐私、帮助）
 * - 关于 AI（合规：免责声明、模型来源、数据共享）
 * - 退出登录
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Modal, FlatList, Alert,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import {
  getUserProfile, updateUserSettings, setAiConsent, clearPushToken, deleteAccount, ApiError,
} from '../services/api';
import { resetSession } from '../services/session';
import AIConsentModal from '../components/AIConsentModal';
import type { UserProfile } from '../types';
import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES, setAppLanguage } from '../i18n';

export default function ProfileScreen({ navigation }: { navigation: any }) {
  const { t, i18n } = useTranslation();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [langVisible, setLangVisible] = useState(false);
  const [aboutVisible, setAboutVisible] = useState(false);
  const [consentVisible, setConsentVisible] = useState(false);

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      setLoading(true);
      const data = await getUserProfile();
      setProfile(data);
    } catch (e) {
      console.warn('Failed to load profile:', e);
    } finally {
      setLoading(false);
    }
  };

  const changeLanguage = async (code: string) => {
    setLangVisible(false);
    await setAppLanguage(code);
    // 同步到后端 users.language（供 LLM 语言跟随）
    try {
      await updateUserSettings({ language: code });
    } catch (e) {
      console.warn('Failed to sync language:', e);
    }
  };

  // 数据共享设置：弹 consent 弹窗变更选择（合规 5.1.2(i)，可随时撤回）
  const onConsentChange = async (consent: boolean) => {
    try {
      await setAiConsent(consent);
      setProfile((prev) => (prev ? { ...prev, ai_consent: consent } : prev));
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'save failed';
      Alert.alert(t('common.error'), msg);
    }
    setConsentVisible(false);
  };

  // 退出登录：清除推送 Token + 重置设备身份（回到引导页）
  const onLogout = () => {
    Alert.alert(
      t('profile.logoutConfirmTitle'),
      t('profile.logoutConfirmMessage'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('profile.logout'),
          style: 'destructive',
          onPress: async () => {
            try {
              await clearPushToken();
            } catch (e) {
              console.warn('Failed to clear push token:', e);
            }
            await resetSession();
          },
        },
      ],
    );
  };

  // 注销账号：级联删除全部数据（不可恢复），随后重置本地会话
  const onDeleteAccount = () => {
    Alert.alert(
      t('profile.deleteConfirmTitle'),
      t('profile.deleteConfirmMessage'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('common.delete'),
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteAccount();
              await resetSession();
            } catch (e) {
              const msg = e instanceof ApiError ? e.message : 'delete failed';
              Alert.alert(t('common.error'), msg);
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  const currentLang = (i18n.language || 'en').slice(0, 2);

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* User Info */}
      <View style={styles.profileHeader}>
        <Text style={styles.avatar}>🧑</Text>
        <Text style={styles.name}>{profile?.name || t('profile.defaultName')}</Text>
        {profile?.mbti_type && (
          <Text style={styles.type}>{profile.mbti_type} · {t(`mbti.${profile.mbti_type}`)}</Text>
        )}
      </View>

      {/* AI 与记忆 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('profile.sectionAI')}</Text>
        <TouchableOpacity style={styles.settingItem} onPress={() => navigation.navigate('Memories')}>
          <Text style={styles.settingIcon}>🧠</Text>
          <Text style={styles.settingLabel}>{t('profile.memoryManagement')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>💬</Text>
          <Text style={styles.settingLabel}>{t('profile.chatStyle')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🔔</Text>
          <Text style={styles.settingLabel}>{t('profile.activeTime')}</Text>
          <Text style={styles.settingValue}>09:00</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 订阅 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('profile.sectionSubscription')}</Text>
        <TouchableOpacity
          style={styles.settingItem}
          onPress={() => navigation.navigate('Paywall')}
        >
          <Text style={styles.settingIcon}>⭐</Text>
          <Text style={styles.settingLabel}>{t('profile.upgradePro')}</Text>
          <Text style={styles.proBadge}>PRO</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>📊</Text>
          <Text style={styles.settingLabel}>{t('profile.usage')}</Text>
          <Text style={styles.settingValue}>
            {profile?.daily_quota_used || 0}/{profile?.daily_quota_total || 5} {t('profile.today')}
          </Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 其他设置 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('profile.sectionOther')}</Text>
        <TouchableOpacity style={styles.settingItem} onPress={() => setLangVisible(true)}>
          <Text style={styles.settingIcon}>🌐</Text>
          <Text style={styles.settingLabel}>{t('profile.language')}</Text>
          <Text style={styles.settingValue}>{t(`language.${currentLang}`)}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🔒</Text>
          <Text style={styles.settingLabel}>{t('profile.privacy')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>❓</Text>
          <Text style={styles.settingLabel}>{t('profile.help')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 关于 AI（合规） */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('profile.sectionAboutAI')}</Text>
        <View style={styles.complianceItem}>
          <Text style={styles.settingIcon}>⚠️</Text>
          <Text style={styles.complianceText}>{t('profile.compliance')}</Text>
        </View>
        <TouchableOpacity style={styles.settingItem} onPress={() => setAboutVisible(true)}>
          <Text style={styles.settingIcon}>🤖</Text>
          <Text style={styles.settingLabel}>{t('profile.modelSource')}</Text>
          <Text style={styles.settingValueSmall}>{t('profile.modelSourceValue')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem} onPress={() => setConsentVisible(true)}>
          <Text style={styles.settingIcon}>📋</Text>
          <Text style={styles.settingLabel}>{t('profile.dataSharing')}</Text>
          <Text style={profile?.ai_consent ? styles.settingValueGreen : styles.settingValueSmall}>
            {profile?.ai_consent ? t('profile.agreed') : t('profile.declined')}
          </Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 退出登录 */}
      <TouchableOpacity style={styles.logoutBtn} onPress={onLogout}>
        <Text style={styles.logoutText}>{t('profile.logout')}</Text>
      </TouchableOpacity>

      {/* 注销账号（合规：数据删除权） */}
      <TouchableOpacity style={styles.logoutBtn} onPress={onDeleteAccount}>
        <Text style={styles.deleteText}>{t('profile.deleteAccount')}</Text>
      </TouchableOpacity>

      {/* Version */}
      <Text style={styles.version}>{t('profile.version')}</Text>

      {/* 语言选择 Modal */}
      <Modal
        visible={langVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setLangVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('language.title')}</Text>
            <FlatList
              data={SUPPORTED_LANGUAGES}
              keyExtractor={(item) => item}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={[styles.langRow, item === currentLang && styles.langRowActive]}
                  onPress={() => changeLanguage(item)}
                >
                  <Text style={styles.langName}>{t(`language.${item}`)}</Text>
                  {item === currentLang && <Text style={styles.langCheck}>✓</Text>}
                </TouchableOpacity>
              )}
            />
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setLangVisible(false)}>
              <Text style={styles.cancelText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 关于 AI（免责声明 + 模型来源披露，合规 §4/§5） */}
      <Modal
        visible={aboutVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setAboutVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('profile.aboutAITitle')}</Text>
            <ScrollView>
              <Text style={styles.aboutText}>{t('profile.aboutAIDesc')}</Text>
              <Text style={styles.aboutText}>{t('profile.aboutAIDisclaimer')}</Text>
              <Text style={styles.aboutText}>{t('profile.aboutAIModel')}</Text>
            </ScrollView>
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setAboutVisible(false)}>
              <Text style={styles.cancelText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 数据共享设置（5.1.2(i)，可随时同意/撤回） */}
      <AIConsentModal
        visible={consentVisible}
        onAgree={() => onConsentChange(true)}
        onDecline={() => onConsentChange(false)}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 80 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  profileHeader: { alignItems: 'center', paddingVertical: Spacing.xl },
  avatar: { fontSize: 48, marginBottom: Spacing.md },
  name: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary },
  type: { fontSize: FontSize.lg, color: Colors.textSecondary, marginTop: 4 },
  section: { marginBottom: Spacing.xl },
  sectionTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textTertiary, marginBottom: Spacing.md },
  settingItem: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  settingIcon: { fontSize: 18, width: 24 },
  settingLabel: { fontSize: FontSize.title, color: Colors.textPrimary, flex: 1 },
  settingValue: { fontSize: FontSize.xl, color: Colors.textSecondary, marginRight: 8 },
  settingValueSmall: { fontSize: FontSize.lg, color: Colors.textTertiary, marginRight: 8 },
  settingValueGreen: { fontSize: FontSize.lg, color: Colors.success, marginRight: 8 },
  arrow: { fontSize: 20, color: Colors.textTertiary },
  proBadge: {
    fontSize: FontSize.sm, fontWeight: '700', color: Colors.gold400,
    backgroundColor: 'rgba(255,215,0,0.15)', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  complianceItem: {
    flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  complianceText: { fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: 1.6, flex: 1 },
  logoutBtn: { justifyContent: 'center', alignItems: 'center', paddingVertical: 14 },
  logoutText: { fontSize: FontSize.title, color: Colors.error },
  deleteText: { fontSize: FontSize.title, color: Colors.textMuted },
  aboutText: {
    fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 22, marginBottom: Spacing.md,
  },
  version: { textAlign: 'center', fontSize: FontSize.lg, color: Colors.textMuted, paddingVertical: 16 },
  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: Spacing.lg,
  },
  modalCard: {
    backgroundColor: Colors.bgPrimary, borderRadius: BorderRadius.xl, padding: Spacing.lg,
    maxHeight: '70%',
  },
  modalTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  langRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  langRowActive: { backgroundColor: 'rgba(255,215,0,0.06)' },
  langName: { fontSize: FontSize.title, color: Colors.textPrimary },
  langCheck: { fontSize: FontSize.title, color: Colors.gold400 },
  cancelBtn: { alignItems: 'center', paddingVertical: 14, marginTop: Spacing.xs },
  cancelText: { fontSize: FontSize.title, color: Colors.gold400, fontWeight: '600' },
});