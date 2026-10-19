/**
 * 隐私与安全页面。
 *
 * App Store 合规：隐私政策 / 用户协议应用内常驻可达（外链打开）。
 * - 法律条款：隐私政策、用户协议（Linking 外链，失败降级 Alert）
 * - 你的数据：AI 数据共享同意状态（只读展示，修改走我的 → 数据共享设置的 consent 弹窗）、
 *   数据导出（T1-01：全部个人数据打包带走）、数据删除说明（注销路径指引）
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert, Linking,
} from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
// 类型级引用 App.tsx 的路由表（import type 会被完全擦除，不产生运行时循环依赖）
import type { RootStackParamList } from '../../App';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { PRIVACY_POLICY_URL, TERMS_URL } from '../config/links';
import {
  ApiError, getUserProfile, listUserExports, postUserExport, getUserExport,
  getUserExportDownload,
} from '../services/api';
import { ArchiveUnavailableError, saveArchive } from '../services/archiveExport';
import { EXPORT_ERROR_KEYS, EXPORT_STATUS_KEYS } from '../config/exportArchive';
import type { ExportJob } from '../types';
import { useAuth } from '../context/AuthContext';

// consent 展示态：loading / unknown（拉取失败）与 agreed / declined 必须区分——
// 把"没取到"渲染成"未同意"会误导用户（5.1.1 数据共享状态要如实呈现）。
type ConsentState = 'loading' | 'unknown' | 'agreed' | 'declined';

// 排队/生成中的轮询间隔。产物由后端每 5 分钟的 cron 生成，10 秒已经足够跟得上状态变化，
// 又不把「我在等我的档案」变成对后端的压力源。
const EXPORT_POLL_MS = 10_000;
const BLOCKING_EXPORT_STATUSES = ['queued', 'running'];

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

  // ── 数据导出（T1-01 · Baseline §2.2-6「带走」／§14.2 可携）──
  // 界面只说后端告诉它的话：五态取自 job.status，保存周期取自列表的 retention_days，
  // 文件名取自 ready 的 file_name。这里一处都不自己算——§14.2 要求说明的那三件事，
  // 前端各自写一份就是第二个真源。
  const [exportJobs, setExportJobs] = useState<ExportJob[]>([]);
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const latestExport = exportJobs[0] ?? null;
  const exportPolling = latestExport
    ? BLOCKING_EXPORT_STATUSES.includes(latestExport.status)
    : false;

  const loadExports = useCallback(() => {
    if (!isLoggedIn) return;
    listUserExports()
      .then((res) => {
        setExportJobs(res.jobs);
        setRetentionDays(res.retention_days);
      })
      .catch((e) => {
        // 列表拉不到只影响「找回旧产物」，申请与下载仍然可用，所以不打扰用户。
        console.warn('Failed to load export jobs:', e);
      });
  }, [isLoggedIn]);

  useFocusEffect(loadExports);

  // 排队/生成中才自己转；ready／failed／expired 停下来等用户动作。
  // 依赖只按 job_id 取：整对象进依赖会让每次轮询回填都重建一次定时器。
  // 卸载或离开页面（pollJobId 变 null）时清定时器——Tab 常驻不卸载，漏清就是后台一直打接口。
  const pollJobId = exportPolling ? latestExport?.job_id ?? null : null;
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!pollJobId) return undefined;
    pollRef.current = setInterval(() => {
      getUserExport(pollJobId)
        .then((job) => setExportJobs((prev) => [job, ...prev.slice(1)]))
        .catch(() => { /* 单次轮询失败等下一轮，不打断 */ });
    }, EXPORT_POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [pollJobId]);

  const onRequestExport = async () => {
    setExportBusy(true);
    try {
      const job = await postUserExport('all');
      setExportJobs((prev) => [job, ...prev]);
    } catch (e) {
      Alert.alert(t('common.error'), exportFailureMessage(e));
    } finally {
      setExportBusy(false);
    }
  };

  const onDownloadExport = async () => {
    if (!latestExport) return;
    setExportBusy(true);
    try {
      // 下载前重读一次状态：轮询之间的 10 秒里后端可能已把它置 expired（cron 每 5 分钟扫）。
      const fresh = await getUserExport(latestExport.job_id);
      setExportJobs((prev) => [fresh, ...prev.slice(1)]);
      if (!fresh.download_ready) return;
      const bytes = await getUserExportDownload(fresh.job_id);
      await saveArchive(bytes, fresh.file_name ?? 'closenai-export.zip');
    } catch (e) {
      Alert.alert(t('common.error'), exportFailureMessage(e));
    } finally {
      setExportBusy(false);
    }
  };

  /** ApiError 的业务码翻成对应文案；未知错误退回服务端文案或通用失败提示。 */
  function exportFailureMessage(e: unknown): string {
    if (e instanceof ArchiveUnavailableError) return t('export.unavailable');
    if (e instanceof ApiError) {
      const key = EXPORT_ERROR_KEYS[e.code];
      if (key) return t(key);
      if (e.message) return e.message;
    }
    return t('export.failedGeneric');
  }

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
        {isLoggedIn && (
          <View style={styles.exportBlock}>
            <Text style={styles.exportTitle}>{t('export.title')}</Text>
            {/* §14.2 三件事：数据范围／保存周期／撤回方式。周期用后端返回值插值，不写死。 */}
            <Text style={styles.exportDesc}>
              {retentionDays === null
                ? t('export.descNoRetention')
                : t('export.desc', { days: retentionDays })}
            </Text>
            {latestExport && (
              <Text style={styles.exportStatus} testID="exportStatus">
                {t(EXPORT_STATUS_KEYS[latestExport.status])}
                {latestExport.status === 'ready' && latestExport.byte_size > 0
                  ? ` · ${(latestExport.byte_size / 1024 / 1024).toFixed(1)} MB`
                  : ''}
              </Text>
            )}
            <View style={styles.exportActions}>
              <TouchableOpacity
                style={[styles.exportBtn, exportBusy && styles.exportBtnDisabled]}
                onPress={onRequestExport}
                disabled={exportBusy || exportPolling}
                testID="exportRequestBtn"
              >
                <Text style={styles.exportBtnText}>
                  {latestExport && !latestExport.download_ready
                    && BLOCKING_EXPORT_STATUSES.includes(latestExport.status)
                    ? t('export.inProgress')
                    : t('export.request')}
                </Text>
              </TouchableOpacity>
              {latestExport?.download_ready && (
                <TouchableOpacity
                  style={[styles.exportBtn, styles.exportBtnPrimary, exportBusy && styles.exportBtnDisabled]}
                  onPress={onDownloadExport}
                  disabled={exportBusy}
                  testID="exportDownloadBtn"
                >
                  <Text style={styles.exportBtnTextPrimary}>{t('export.download')}</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
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
  // 数据导出（T1-01）：与注销同权重但不同色——它是「带走」，不是「删掉」，
  // 所以不进危险区，放在同意状态与删除说明之间。
  exportBlock: {
    paddingVertical: Spacing.md, borderTopWidth: 1, borderTopColor: Colors.borderLight,
  },
  exportTitle: { fontSize: FontSize.title, color: Colors.textPrimary, marginBottom: 4 },
  exportDesc: {
    fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: 20, marginBottom: Spacing.sm,
  },
  exportStatus: {
    fontSize: FontSize.lg, color: Colors.gold400, marginBottom: Spacing.sm, fontWeight: '600',
  },
  exportActions: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.sm },
  exportBtn: {
    paddingHorizontal: Spacing.lg, paddingVertical: Spacing.sm,
    borderRadius: BorderRadius.md, borderWidth: 1, borderColor: Colors.gold400,
  },
  exportBtnPrimary: { backgroundColor: Colors.gold400 },
  exportBtnDisabled: { opacity: 0.5 },
  exportBtnText: { fontSize: FontSize.lg, color: Colors.gold400, fontWeight: '600' },
  // 主按钮是金底，文字必须换深色——同色写上去等于把「下载」两个字藏起来。
  exportBtnTextPrimary: { fontSize: FontSize.lg, color: Colors.bgPrimary, fontWeight: '700' },
});
