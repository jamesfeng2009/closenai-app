/**
 * 高级洞察页面 (P1-F03)。
 *
 * 独立报告页（非对话流）：
 * - 每周洞察（Insight Level L1-L4 自动晋升标识，cron 生成只读）
 * - AI 消息历史入口（P0-F10：每日发现已融入 AI 主动消息，跳消息中心查看）
 * - 模式发现（跨维度关联，LLM 实时生成，点击触发）
 * - 周期报告（周报免费；月/季/年报 Pro，走后端同周期缓存）
 * - 年度热力图（365 天情绪值）
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, Share,
} from 'react-native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  getWeeklyInsight, getPatternDiscovery, getShareReport, getTimeline,
  getUserProfile, ApiError,
} from '../services/api';
import type {
  GrowthTimeline, SharePeriod, ShareReport, UserProfile, WeeklyInsight,
} from '../types';
import { trackFunnelShare } from '../services/funnel';
import { useAuth } from '../context/AuthContext';
import ShareManifest from '../components/ShareManifest';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode 对齐
const CODE_PRO_REQUIRED = 40302;

const PERIODS: SharePeriod[] = ['weekly', 'monthly', 'quarterly', 'yearly'];
const PRO_PERIODS: SharePeriod[] = ['monthly', 'quarterly', 'yearly'];

// 热力图配色（与后端 MOOD_COLORS 同语义：1 低落 → 5 开心，0 无数据）
const HEAT_COLORS: Record<number, string> = {
  0: 'rgba(255,255,255,0.06)',
  1: '#EF9A9A',
  2: '#FFCC80',
  3: '#FFF59D',
  4: '#A5D6A7',
  5: '#66BB6A',
};

export default function InsightsScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [weekly, setWeekly] = useState<WeeklyInsight | null>(null);
  const [timeline, setTimeline] = useState<GrowthTimeline | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [pattern, setPattern] = useState<string | null>(null);
  const [patternLoading, setPatternLoading] = useState(false);
  const [report, setReport] = useState<ShareReport | null>(null);
  const [reportPeriod, setReportPeriod] = useState<SharePeriod | null>(null);
  const [reportLoading, setReportLoading] = useState(false);

  // 首屏并行加载 3 个独立数据源；任一失败 .catch(() => null) 降级为空，不阻塞其余 section 渲染
  // 游客态跳过请求（接口均需登录，游客必 401），展示空态
  // 周报是本页主数据源：它的失败要单独记（不能和「本周还没有周报」共用一句文案）
  const [weeklyFailed, setWeeklyFailed] = useState(false);

  const load = useCallback(async () => {
    if (!isLoggedIn) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      let wFailed = false;
      const [w, tl, p] = await Promise.all([
        getWeeklyInsight().catch(() => {
          wFailed = true;
          return null;
        }),
        getTimeline(365).catch(() => null),
        getUserProfile().catch(() => null),
      ]);
      setWeeklyFailed(wFailed);
      setWeekly(w);
      setTimeline(tl);
      setProfile(p);
    } finally {
      setLoading(false);
    }
  }, [isLoggedIn]);

  useEffect(() => {
    load();
  }, [load]);

  const isPro = !!profile && profile.tier !== 'free';

  // Pro 权益门（月/季/年报）：前端先按 tier 拦截，后端 40302 兜底
  const showProAlert = () => {
    Alert.alert(
      t('insights.proTitle'),
      t('insights.proMessage'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        { text: t('insights.upgrade'), onPress: () => navigation.navigate('Paywall') },
      ],
    );
  };

  // 模式发现：LLM 实时生成（有成本），点击触发而非自动加载
  const onGeneratePattern = async () => {
    if (patternLoading) return;
    try {
      setPatternLoading(true);
      const res = await getPatternDiscovery();
      setPattern(res.content);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setPatternLoading(false);
    }
  };

  // 选中周期报告：双口径 Pro 门控——先按本地 tier 前端拦截（省一次请求），
  // 请求仍返回 40302 时兜底再弹一次（防 tier 未同步/后端为唯一事实源）
  const onSelectPeriod = async (period: SharePeriod) => {
    if (reportLoading) return;
    if (PRO_PERIODS.includes(period) && !isPro) {
      showProAlert();
      return;
    }
    try {
      setReportLoading(true);
      setReportPeriod(period);
      setReport(await getShareReport(period));
    } catch (e) {
      if (e instanceof ApiError && e.code === CODE_PRO_REQUIRED) {
        // 40302 后回滚 reportPeriod，避免按钮高亮但无报告内容
        setReportPeriod(null);
        showProAlert();
        return;
      }
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setReportLoading(false);
    }
  };

  const onShareReport = async () => {
    if (!report) return;
    const text = [
      `${report.period_label} · ${report.range}`,
      report.mood_trend,
      report.content,
      report.share_url,
    ].filter(Boolean).join('\n\n');
    try {
      trackFunnelShare('share_tapped', 'period_report');
      const res = await Share.share({ message: text });
      if (res.action === Share.sharedAction) {
        trackFunnelShare('share_completed', 'period_report');
      }
    } catch {
      // 用户取消分享不提示
    }
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* 每周洞察（Insight Level 标识，PRD §1） */}
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitle}>📈 {t('insights.weeklyTitle')}</Text>
          {!!weekly?.level && <Text style={styles.levelBadge}>{weekly.level}</Text>}
        </View>
        {weekly?.content ? (
          <>
            {!!weekly.period && <Text style={styles.metaText}>{weekly.period}</Text>}
            <Text style={styles.bodyText}>{weekly.content}</Text>
          </>
        ) : weeklyFailed ? (
          <View style={styles.failRow}>
            <Text style={styles.emptyText}>{t('common.loadFailed')}</Text>
            <TouchableOpacity style={styles.retryBtn} onPress={load}>
              <Text style={styles.retryText}>{t('common.retry')}</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <Text style={styles.emptyText}>{t('insights.weeklyEmpty')}</Text>
        )}
      </View>

      {/* AI 消息历史入口（P0-F10：每日发现融入主动消息，历史消息在消息中心查看） */}
      <TouchableOpacity
        style={styles.card}
        onPress={() => navigation.navigate('Messages')}
        activeOpacity={0.7}
      >
        <View style={styles.entryRow}>
          <View style={styles.entryBody}>
            <Text style={styles.cardTitle}>🔔 {t('insights.messagesTitle')}</Text>
            <Text style={styles.emptyText}>{t('insights.messagesDesc')}</Text>
          </View>
          <Text style={styles.entryChevron}>›</Text>
        </View>
      </TouchableOpacity>

      {/* 模式发现（点击触发，LLM 成本） */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>🔍 {t('insights.patternTitle')}</Text>
        {pattern ? (
          <Text style={styles.bodyText}>{pattern}</Text>
        ) : (
          <>
            <Text style={styles.emptyText}>{t('insights.patternDesc')}</Text>
            <TouchableOpacity
              style={[styles.actionBtn, patternLoading && styles.actionBtnDisabled]}
              onPress={onGeneratePattern}
              disabled={patternLoading}
            >
              {patternLoading ? (
                <ActivityIndicator color={Colors.bgPrimary} size="small" />
              ) : (
                <Text style={styles.actionBtnText}>{t('insights.patternGenerate')}</Text>
              )}
            </TouchableOpacity>
          </>
        )}
      </View>

      {/* 周期报告（PRD §5：独立报告页；月/季/年报 Pro） */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>📊 {t('insights.reportTitle')}</Text>
        <View style={styles.periodRow}>
          {PERIODS.map((p) => {
            const locked = PRO_PERIODS.includes(p) && !isPro;
            return (
              <TouchableOpacity
                key={p}
                style={[styles.periodBtn, reportPeriod === p && styles.periodBtnActive]}
                onPress={() => onSelectPeriod(p)}
                disabled={reportLoading}
              >
                <Text style={[styles.periodText, reportPeriod === p && styles.periodTextActive]}>
                  {t(`insights.${p}`)}
                </Text>
                {locked && <Text style={styles.lockIcon}>🔒</Text>}
              </TouchableOpacity>
            );
          })}
        </View>
        {reportLoading && <ActivityIndicator color={Colors.gold400} style={styles.reportLoading} />}
        {!reportLoading && report && (
          <View style={styles.reportBody}>
            <View style={styles.cardHeader}>
              <Text style={styles.reportLabel}>{report.period_label} · {report.range}</Text>
              {!!report.insight_level && <Text style={styles.levelBadge}>{report.insight_level}</Text>}
            </View>
            {!!report.mood_trend && <Text style={styles.bodyText}>{report.mood_trend}</Text>}
            {report.chat_count != null && (
              <Text style={styles.metaText}>{t('insights.chatCount', { count: report.chat_count })}</Text>
            )}
            <Text style={styles.bodyText}>{report.content}</Text>
            {/* 分享内容清单：与 onShareReport 拼装的字段严格一致，不含昵称 */}
            <ShareManifest
              items={[
                `${report.period_label} · ${report.range}`,
                report.mood_trend || '',
                report.content,
                t('share.manifestLink'),
              ]}
            />
            <TouchableOpacity style={styles.shareReportBtn} onPress={onShareReport}>
              <Text style={styles.shareReportText}>📤 {t('insights.shareReport')}</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

      {/* 年度热力图（365 天情绪值） */}
      {!!timeline?.days?.length && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🗓️ {t('insights.timelineTitle')}</Text>
          <View style={styles.heatGrid}>
            {timeline.days.map((d) => (
              <View
                key={d.date}
                style={[styles.heatDot, { backgroundColor: HEAT_COLORS[d.value] ?? HEAT_COLORS[0] }]}
              />
            ))}
          </View>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: Spacing.sm },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.sm },
  levelBadge: {
    fontSize: FontSize.xs, fontWeight: '700', color: '#C084FC',
    backgroundColor: 'rgba(192,132,252,0.12)', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6,
  },
  metaText: { fontSize: FontSize.sm, color: Colors.textTertiary, marginBottom: Spacing.xs },
  entryRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.sm },
  entryBody: { flex: 1 },
  entryChevron: { fontSize: FontSize.h3, color: Colors.textTertiary },
  bodyText: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption, marginTop: 4 },
  emptyText: { fontSize: FontSize.md, color: Colors.textTertiary, lineHeight: LineHeight.caption },
  failRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: Spacing.md },
  retryBtn: {
    paddingHorizontal: 16, paddingVertical: 6, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.12)', borderWidth: 1, borderColor: Colors.gold400,
  },
  retryText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  actionBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 10, alignItems: 'center', marginTop: Spacing.md,
  },
  actionBtnDisabled: { opacity: 0.4 },
  actionBtnText: { fontSize: FontSize.md, fontWeight: '700', color: Colors.bgPrimary },
  periodRow: { flexDirection: 'row', gap: Spacing.sm, marginTop: 4 },
  periodBtn: {
    flex: 1, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 2,
    paddingVertical: 10, borderRadius: BorderRadius.lg,
    backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: Colors.borderLight,
  },
  periodBtnActive: { backgroundColor: 'rgba(255,215,0,0.12)', borderColor: Colors.gold400 },
  periodText: { fontSize: FontSize.md, color: Colors.textSecondary },
  periodTextActive: { color: Colors.gold400, fontWeight: '600' },
  lockIcon: { fontSize: 10 },
  reportLoading: { marginTop: Spacing.md },
  reportBody: { marginTop: Spacing.md },
  reportLabel: { fontSize: FontSize.md, fontWeight: '600', color: Colors.textPrimary, flex: 1 },
  shareReportBtn: {
    marginTop: Spacing.md, alignItems: 'center', paddingVertical: 10,
    borderRadius: BorderRadius.lg, borderWidth: 1, borderColor: Colors.gold400,
  },
  shareReportText: { fontSize: FontSize.md, fontWeight: '600', color: Colors.gold400 },
  heatGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 3, marginTop: 4 },
  heatDot: { width: 12, height: 12, borderRadius: 3 },
});
