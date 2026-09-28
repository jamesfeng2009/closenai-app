/**
 * 我的/设置页面。
 *
 * 按 UI 原型 screen-profile 实现：
 * - 头像 + 姓名 + 性格类型
 * - AI 与记忆（记忆管理、对话风格、主动消息时间）
 * - 订阅（升级 Pro、使用情况）
 * - 其他设置（语言、隐私、帮助）
 * - 账号安全（修改用户名、修改密码）
 * - 关于 AI（合规：免责声明、模型来源、数据共享）
 * - 退出登录 / 注销账号
 *
 * 游客态（未登录）：展示登录引导 CTA，不加载用户资料。
 */
import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Modal, FlatList, Alert, TextInput,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import {
  getUserProfile, updateUserSettings, setAiConsent, ApiError,
  changePassword, updateUsername,
} from '../services/api';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import AIConsentModal from '../components/AIConsentModal';
import { SUPPORTED_LANGUAGES, setAppLanguage } from '../i18n';
import type { ConversationStyle, PrimaryFramework, UserProfile } from '../types';

// 主动消息时间：00:00–23:00 整点（不引入 datetimepicker 依赖）
const PUSH_HOURS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`);
// 对话风格三枚举（与后端 CONVERSATION_STYLES 一致，展示顺序即默认排序）
const CONVERSATION_STYLES: ConversationStyle[] = ['warm', 'rational', 'philosophical'];
// 主要性格框架三枚举（P0-F05：画像页与解读以所选框架为主）
const PRIMARY_FRAMEWORKS: PrimaryFramework[] = ['mbti', 'enneagram', 'astrology'];

export default function ProfileScreen({ navigation }: { navigation: any }) {
  const { t, i18n } = useTranslation();
  const { isLoggedIn, logout, deleteMyAccount } = useAuth();
  const requireAuth = useRequireAuth();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [langVisible, setLangVisible] = useState(false);
  const [aboutVisible, setAboutVisible] = useState(false);
  const [consentVisible, setConsentVisible] = useState(false);
  const [pwdVisible, setPwdVisible] = useState(false);
  const [usernameVisible, setUsernameVisible] = useState(false);
  const [timeVisible, setTimeVisible] = useState(false);
  const [styleVisible, setStyleVisible] = useState(false);
  const [frameworkVisible, setFrameworkVisible] = useState(false);

  // Tab 常驻不卸载：每次聚焦都刷新（仅登录态；游客跳过请求避免 401 噪声）
  useFocusEffect(
    React.useCallback(() => {
      if (isLoggedIn) {
        loadProfile();
      } else {
        setLoading(false);
      }
    }, [isLoggedIn])
  );

  const loadProfile = async () => {
    try {
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
      // 仅成功时关闭 Modal；失败时保持打开以便用户重试
      setConsentVisible(false);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.saveFailed');
      Alert.alert(t('common.error'), msg);
    }
  };

  // 主动消息时间：整点选择 → PATCH /user/settings → 用响应刷新本地展示
  const onSelectPushTime = async (time: string) => {
    try {
      const updated = await updateUserSettings({ push_time: time });
      setProfile(updated);
      setTimeVisible(false);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.saveFailed');
      Alert.alert(t('common.error'), msg);
    }
  };

  // 对话风格：三枚举选择 → PATCH /user/settings → 用响应刷新本地展示
  const onSelectStyle = async (style: ConversationStyle) => {
    try {
      const updated = await updateUserSettings({ conversation_style: style });
      setProfile(updated);
      setStyleVisible(false);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.saveFailed');
      Alert.alert(t('common.error'), msg);
    }
  };

  // 主要框架：三枚举选择 → PATCH /user/settings → 本地合并所选值刷新展示
  // （后端 UserSettingsUpdate 暂未持久化 primary_framework，响应不回显，本地先行）
  const onSelectFramework = async (framework: PrimaryFramework) => {
    try {
      const updated = await updateUserSettings({ primary_framework: framework });
      setProfile({ ...updated, primary_framework: framework });
      setFrameworkVisible(false);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.saveFailed');
      Alert.alert(t('common.error'), msg);
    }
  };

  // 退出登录：吊销 refresh + 清本机态（保留引导，游客可继续浏览）
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
              await logout();
            } catch (e) {
              console.warn('Logout failed:', e);
              // 即使后端吊销失败，本地仍需清态（logout 内部已处理 resetSession）
            }
          },
        },
      ],
    );
  };

  // 注销账号：级联删除全部数据（不可恢复），随后全量重置回引导页
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
              await deleteMyAccount();
            } catch (e) {
              // ApiError 透传服务端文案；其余（网络异常、非 Error 等）走 i18n 通用兜底
              const msg = e instanceof ApiError && e.message ? e.message : t('errors.deleteFailed');
              Alert.alert(t('common.error'), msg);
            }
          },
        },
      ],
    );
  };

  // ── 游客态：登录引导 CTA ──
  if (!isLoggedIn) {
    return (
      <View style={styles.guestContainer}>
        <Text style={styles.guestAvatar}>🧑</Text>
        <Text style={styles.guestTitle}>{t('auth.guestTitle')}</Text>
        <Text style={styles.guestDesc}>{t('auth.guestDesc')}</Text>
        <TouchableOpacity style={styles.primaryBtn} onPress={() => navigation.navigate('Auth')}>
          <Text style={styles.primaryBtnText}>{t('auth.login')}</Text>
        </TouchableOpacity>
      </View>
    );
  }

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
        <TouchableOpacity
          style={styles.settingItem}
          onPress={() => { if (requireAuth()) setStyleVisible(true); }}
        >
          <Text style={styles.settingIcon}>💬</Text>
          <Text style={styles.settingLabel}>{t('profile.chatStyle')}</Text>
          <Text style={styles.settingValueSmall}>
            {t(`profile.style.${profile?.conversation_style || 'warm'}`)}
          </Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.settingItem}
          onPress={() => { if (requireAuth()) setFrameworkVisible(true); }}
        >
          <Text style={styles.settingIcon}>🧭</Text>
          <Text style={styles.settingLabel}>{t('profile.framework')}</Text>
          <Text style={styles.settingValueSmall}>
            {t(`profile.frameworkOption.${profile?.primary_framework || 'mbti'}`)}
          </Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.settingItem}
          onPress={() => { if (requireAuth()) setTimeVisible(true); }}
        >
          <Text style={styles.settingIcon}>🔔</Text>
          <Text style={styles.settingLabel}>{t('profile.activeTime')}</Text>
          <Text style={styles.settingValue}>{profile?.push_time || '09:00'}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 账号安全 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('profile.sectionAccount')}</Text>
        <TouchableOpacity style={styles.settingItem} onPress={() => setUsernameVisible(true)}>
          <Text style={styles.settingIcon}>👤</Text>
          <Text style={styles.settingLabel}>{t('auth.changeUsername')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem} onPress={() => setPwdVisible(true)}>
          <Text style={styles.settingIcon}>🔑</Text>
          <Text style={styles.settingLabel}>{t('auth.changePassword')}</Text>
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
        <TouchableOpacity style={styles.settingItem} onPress={() => navigation.navigate('PrivacySecurity')}>
          <Text style={styles.settingIcon}>🔒</Text>
          <Text style={styles.settingLabel}>{t('profile.privacy')}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem} onPress={() => navigation.navigate('HelpFeedback')}>
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

      {/* 修改密码 */}
      <ChangePasswordModal visible={pwdVisible} onClose={() => setPwdVisible(false)} />

      {/* 修改用户名 */}
      <ChangeUsernameModal
        visible={usernameVisible}
        onClose={() => setUsernameVisible(false)}
        currentUsername={profile?.username || ''}
        onUpdated={() => loadProfile()}
      />

      {/* 主动消息时间选择（00:00–23:00 整点） */}
      <Modal
        visible={timeVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setTimeVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('profile.pushTimeTitle')}</Text>
            <FlatList
              data={PUSH_HOURS}
              keyExtractor={(item) => item}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={[styles.langRow, item === (profile?.push_time || '09:00') && styles.langRowActive]}
                  onPress={() => onSelectPushTime(item)}
                >
                  <Text style={styles.langName}>
                    {t('profile.hourOption', { hour: item.slice(0, 2) })}
                  </Text>
                  {item === (profile?.push_time || '09:00') && <Text style={styles.langCheck}>✓</Text>}
                </TouchableOpacity>
              )}
            />
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setTimeVisible(false)}>
              <Text style={styles.cancelText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 对话风格选择（warm / rational / philosophical） */}
      <Modal
        visible={styleVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setStyleVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('profile.chatStyle')}</Text>
            {CONVERSATION_STYLES.map((s) => {
              const active = s === (profile?.conversation_style || 'warm');
              return (
                <TouchableOpacity
                  key={s}
                  style={[styles.langRow, active && styles.langRowActive]}
                  onPress={() => onSelectStyle(s)}
                >
                  <View style={styles.styleOptionText}>
                    <Text style={styles.langName}>{t(`profile.style.${s}`)}</Text>
                    <Text style={styles.styleDesc}>{t(`profile.style.${s}Desc`)}</Text>
                  </View>
                  {active && <Text style={styles.langCheck}>✓</Text>}
                </TouchableOpacity>
              );
            })}
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setStyleVisible(false)}>
              <Text style={styles.cancelText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 主要框架选择（mbti / enneagram / astrology） */}
      <Modal
        visible={frameworkVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setFrameworkVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('profile.framework')}</Text>
            {PRIMARY_FRAMEWORKS.map((f) => {
              const active = f === (profile?.primary_framework || 'mbti');
              return (
                <TouchableOpacity
                  key={f}
                  style={[styles.langRow, active && styles.langRowActive]}
                  onPress={() => onSelectFramework(f)}
                >
                  <Text style={styles.langName}>{t(`profile.frameworkOption.${f}`)}</Text>
                  {active && <Text style={styles.langCheck}>✓</Text>}
                </TouchableOpacity>
              );
            })}
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setFrameworkVisible(false)}>
              <Text style={styles.cancelText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}

// ═══ 修改密码 Modal ═══
function ChangePasswordModal({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { logout } = useAuth();
  const [oldPwd, setOldPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [loading, setLoading] = useState(false);

  const reset = () => {
    setOldPwd('');
    setNewPwd('');
    setConfirmPwd('');
  };

  const onSubmit = async () => {
    if (!oldPwd || !newPwd) {
      Alert.alert(t('common.error'), t('auth.fillAllFields'));
      return;
    }
    if (newPwd !== confirmPwd) {
      Alert.alert(t('common.error'), t('auth.passwordMismatch'));
      return;
    }
    try {
      setLoading(true);
      await changePassword(oldPwd, newPwd);
      reset();
      onClose();
      // 改密后后端吊销全部 refresh，需重新登录
      Alert.alert(t('auth.passwordChangedTitle'), t('auth.passwordChangedMessage'), [
        { text: t('auth.login'), onPress: () => logout() },
      ]);
    } catch (e) {
      Alert.alert(t('common.error'), e instanceof ApiError ? e.message : t('errors.requestFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalOverlay}>
        <View style={styles.modalCard}>
          <Text style={styles.modalTitle}>{t('auth.changePassword')}</Text>
          <TextInput
            style={styles.modalInput}
            placeholder={t('auth.oldPassword')}
            placeholderTextColor={Colors.textTertiary}
            value={oldPwd}
            onChangeText={setOldPwd}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
          />
          <TextInput
            style={styles.modalInput}
            placeholder={t('auth.passwordPlaceholder')}
            placeholderTextColor={Colors.textTertiary}
            value={newPwd}
            onChangeText={setNewPwd}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
          />
          <TextInput
            style={styles.modalInput}
            placeholder={t('auth.confirmPassword')}
            placeholderTextColor={Colors.textTertiary}
            value={confirmPwd}
            onChangeText={setConfirmPwd}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
          />
          <View style={styles.modalActions}>
            <TouchableOpacity style={styles.modalCancelBtn} onPress={() => { reset(); onClose(); }}>
              <Text style={styles.modalCancelText}>{t('common.cancel')}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalConfirmBtn, loading && styles.primaryBtnDisabled]}
              disabled={loading}
              onPress={onSubmit}
            >
              <Text style={styles.modalConfirmText}>
                {loading ? t('common.loading') : t('common.done')}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

// ═══ 修改用户名 Modal ═══
function ChangeUsernameModal({
  visible, onClose, currentUsername, onUpdated,
}: {
  visible: boolean; onClose: () => void; currentUsername: string; onUpdated: () => void;
}) {
  const { t } = useTranslation();
  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(false);

  const onSubmit = async () => {
    if (!newName.trim()) {
      Alert.alert(t('common.error'), t('auth.fillAllFields'));
      return;
    }
    try {
      setLoading(true);
      await updateUsername(newName.trim());
      setNewName('');
      onClose();
      onUpdated();
    } catch (e) {
      Alert.alert(t('common.error'), e instanceof ApiError ? e.message : t('errors.requestFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalOverlay}>
        <View style={styles.modalCard}>
          <Text style={styles.modalTitle}>{t('auth.changeUsername')}</Text>
          <Text style={styles.modalHint}>
            {t('auth.currentUsername')}: {currentUsername || '-'}
          </Text>
          <TextInput
            style={styles.modalInput}
            placeholder={t('auth.newUsername')}
            placeholderTextColor={Colors.textTertiary}
            value={newName}
            onChangeText={setNewName}
            autoCapitalize="none"
            autoCorrect={false}
          />
          <View style={styles.modalActions}>
            <TouchableOpacity style={styles.modalCancelBtn} onPress={() => { setNewName(''); onClose(); }}>
              <Text style={styles.modalCancelText}>{t('common.cancel')}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalConfirmBtn, loading && styles.primaryBtnDisabled]}
              disabled={loading}
              onPress={onSubmit}
            >
              <Text style={styles.modalConfirmText}>
                {loading ? t('common.loading') : t('auth.save')}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 80 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  guestContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary, padding: Spacing.xl },
  guestAvatar: { fontSize: 64, marginBottom: Spacing.lg },
  guestTitle: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary, marginBottom: Spacing.sm },
  guestDesc: { fontSize: FontSize.lg, color: Colors.textSecondary, textAlign: 'center', marginBottom: Spacing.xl, lineHeight: 1.6 },
  primaryBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.pill,
    paddingVertical: 14, paddingHorizontal: 48, alignItems: 'center',
  },
  primaryBtnText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  primaryBtnDisabled: { opacity: 0.5 },
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
    backgroundColor: Colors.bgSecondary, borderRadius: BorderRadius.xl, padding: Spacing.lg,
    maxHeight: '70%',
  },
  modalTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  modalHint: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: Spacing.md },
  modalInput: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    paddingVertical: 12, paddingHorizontal: Spacing.md, marginBottom: Spacing.md,
    color: Colors.textPrimary, fontSize: FontSize.title,
  },
  modalActions: { flexDirection: 'row', gap: Spacing.md, marginTop: Spacing.sm },
  modalCancelBtn: {
    flex: 1, alignItems: 'center', paddingVertical: 12,
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
  },
  modalCancelText: { fontSize: FontSize.title, color: Colors.textSecondary },
  modalConfirmBtn: {
    flex: 1, alignItems: 'center', paddingVertical: 12,
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
  },
  modalConfirmText: { fontSize: FontSize.title, fontWeight: '700', color: '#050508' },
  langRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  langRowActive: { backgroundColor: 'rgba(255,215,0,0.06)' },
  langName: { fontSize: FontSize.title, color: Colors.textPrimary },
  langCheck: { fontSize: FontSize.title, color: Colors.gold400 },
  styleOptionText: { flex: 1, marginRight: Spacing.md },
  styleDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 2 },
  cancelBtn: { alignItems: 'center', paddingVertical: 14, marginTop: Spacing.xs },
  cancelText: { fontSize: FontSize.title, color: Colors.gold400, fontWeight: '600' },
});
