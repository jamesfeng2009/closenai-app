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
  getWeeklyInsight, getPatternDiscovery, getShareReport, getYearInReview, getTimeline,
  getUserProfile, getReportPdf, getYearInReviewPdf, getMoodTrend, listInsights, getInsight,
  ApiError,
} from '../services/api';
import { exportPdf, PdfUnavailableError } from '../services/pdfExport';
import { reportPdfFile, YEAR_IN_REVIEW_PDF_FILE, pdfFailureKey } from '../config/pdfFiles';
import type {
  GrowthTimeline, MoodTrendData, SharePeriod, ShareReport, UserProfile, WeeklyInsight,
  InsightListItem, InsightDetail,
} from '../types';
import { trackFunnelShare } from '../services/funnel';
import { memoriesWithinDays } from '../services/evidence';
import type { MemoryRef } from '../types';
import { useAuth } from '../context/AuthContext';
import ShareManifest from '../components/ShareManifest';
import InsightBlurGate from '../components/InsightBlurGate';
import MoodLineChart from '../components/charts/MoodLineChart';
import HeatGrid from '../components/charts/HeatGrid';
import { isProTier } from '../services/insightTier';
import type { ChartDay } from '../components/charts/transforms';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode 对齐
const CODE_PRO_REQUIRED = 40302;
// 年报卡空态：整年零记录（后端不出数字全 0 的卡，回 40402）
const CODE_RESOURCE_NOT_FOUND = 40402;

const PERIODS: SharePeriod[] = ['weekly', 'monthly', 'quarterly', 'yearly'];
const PRO_PERIODS: SharePeriod[] = ['monthly', 'quarterly', 'yearly'];

// 热力图默认近 90 天（PRD Growth Timeline：13 周一行放得下），「查看全年」切 365 天
const HEAT_DEFAULT_DAYS = 90;
const HEAT_FULL_DAYS = 365;

// 趋势摘要窗口（D-G9）：与这张卡的折线同窗（7 天），不跟热力图的 90/365 切换。
// 为什么不拿 /mood/trend 换掉折线的数据源：它的 labels 是 "%m-%d" 不带年份
// （mood_service.py:334），与 90 天/整年热力图切换会错位，且同一屏会长出第二个时间真源。
const TREND_DAYS = 7;

// 依据折叠区一次最多铺几条（工单 T3-04 §怎么做.3）。
// 条数由这一屏的排版决定，不是后端裁剪判据：`detail.truncated` 与
// `evidence_summary.count` 才是「裁剪前有多少条」的出处，两者都要显示，
// 否则用户会以为 5 条就是全部依据。
const EVIDENCE_MAX = 5;

/** 按日期升序取最近 N 天（后端窗口顺序不做假设，本地排序保证折线/矩阵方向） */
const recentDays = (days: { date: string; value: number }[], n: number): ChartDay[] =>
  [...days].sort((a, b) => a.date.localeCompare(b.date)).slice(-n);

export default function InsightsScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [weekly, setWeekly] = useState<WeeklyInsight | null>(null);
  // 周洞察的期间级依据（#34）：/growth/insights 不带引用 id，用 period 聚合当期记忆
  const [weeklyRefs, setWeeklyRefs] = useState<MemoryRef[]>([]);
  const [refsOpen, setRefsOpen] = useState(false);
  const [timeline, setTimeline] = useState<GrowthTimeline | null>(null);
  // 后端算好的趋势一句话（high/mid/low 三档，按 X-Language 返回本地化文案）
  const [trend, setTrend] = useState<MoodTrendData | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [pattern, setPattern] = useState<string | null>(null);
  const [patternLoading, setPatternLoading] = useState(false);
  const [report, setReport] = useState<ShareReport | null>(null);
  const [reportPeriod, setReportPeriod] = useState<SharePeriod | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  // E2：热力图默认近 90 天，「查看全年」展开 365 天（横向滑动）
  const [heatFull, setHeatFull] = useState(false);
  // 年度报告卡（M4 year_in_review）的分享入口：免费引流卡，不走 Pro 门控
  const [yearReviewLoading, setYearReviewLoading] = useState(false);
  // PDF 导出（U2）：两个入口共用一个忙位，防「点了周报又点年报」并发写同名文件
  const [pdfBusy, setPdfBusy] = useState<'report' | 'year' | null>(null);

  // 首屏并行加载 4 个独立数据源；任一失败 .catch(() => null) 降级为空，不阻塞其余 section 渲染
  // 游客态跳过请求（接口均需登录，游客必 401），展示空态
  // 周报是本页主数据源：它的失败要单独记（不能和「本周还没有周报」共用一句文案）
  const [weeklyFailed, setWeeklyFailed] = useState(false);
  // T3-04：canonical 洞察面（`GET /api/v1/insights` → Go 的内部面）。它和上面那张周报卡
  // 是两个真源（一个是 Python 的 growth_records 周报，一个是 Go 的 personal_insights），
  // 加载/空/失败三态必须各记一份——共用一个 flag 会把「还没有洞察」显示成「加载失败」。
  const [insights, setInsights] = useState<InsightListItem[] | null>(null);
  const [insightsLoading, setInsightsLoading] = useState(true);
  const [insightsFailed, setInsightsFailed] = useState(false);
  // 逐条依据在第二跳（详情端点）里，所以一次只展开一张卡，详情按 id 缓存
  const [evidenceOpenId, setEvidenceOpenId] = useState<string | null>(null);
  const [detailById, setDetailById] = useState<Record<string, InsightDetail>>({});
  const [detailBusyId, setDetailBusyId] = useState<string | null>(null);
  const [detailFailedId, setDetailFailedId] = useState<string | null>(null);

  const loadInsights = useCallback(async () => {
    if (!isLoggedIn) {
      // 游客态：这一面需要 JWT，请求必 401，所以直接落到空态而不是失败态
      setInsightsLoading(false);
      return;
    }
    try {
      setInsightsLoading(true);
      const page = await listInsights();
      setInsights(page.items);
      setInsightsFailed(false);
    } catch {
      // 失败不是空：两者共用一句文案，用户就分不清「模型还没看够」和「这一屏没连上」
      setInsightsFailed(true);
    } finally {
      setInsightsLoading(false);
    }
  }, [isLoggedIn]);

  /** 详情只有这一个调用点：缓存命中就不重复发请求，失败也只标记这一张卡。 */
  const fetchDetail = useCallback(async (id: string) => {
    setDetailBusyId(id);
    setDetailFailedId(null);
    try {
      const detail = await getInsight(id);
      setDetailById((prev) => ({ ...prev, [id]: detail }));
    } catch {
      setDetailFailedId(id);
    } finally {
      setDetailBusyId(null);
    }
  }, []);

  const onToggleEvidence = async (item: InsightListItem) => {
    if (evidenceOpenId === item.id) {
      setEvidenceOpenId(null);
      return;
    }
    setEvidenceOpenId(item.id);
    if (!detailById[item.id]) await fetchDetail(item.id);
  };

  const load = useCallback(async () => {
    if (!isLoggedIn) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      let wFailed = false;
      const [w, tl, p, tr] = await Promise.all([
        getWeeklyInsight().catch(() => {
          wFailed = true;
          return null;
        }),
        getTimeline(365).catch(() => null),
        getUserProfile().catch(() => null),
        getMoodTrend(TREND_DAYS).catch(() => null),
      ]);
      setWeeklyFailed(wFailed);
      setWeekly(w);
      setTimeline(tl);
      setProfile(p);
      setTrend(tr);
      // #34 期间级依据：周报正文在才聚合当期记忆；取不到就不显示这一栏
      setRefsOpen(false);
      setWeeklyRefs(
        w?.content ? await memoriesWithinDays(7, w.period).catch(() => []) : []
      );
    } finally {
      setLoading(false);
    }
  }, [isLoggedIn]);

  useEffect(() => {
    load();
    loadInsights();
  }, [load, loadInsights]);

  const isPro = isProTier(profile?.tier);

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

  // 模式发现：LLM 实时生成（有成本），点击触发而非自动加载。
  // 免费态不调 LLM（§5.20：模糊卡是「变清晰的邀请」，样张静态模板，不花生成成本）
  const onGeneratePattern = async () => {
    if (patternLoading) return;
    if (!isPro) {
      navigation.navigate('Paywall');
      return;
    }
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

  /**
   * 分享年度报告卡（M4）。文案直接用后端组装好的 share_text——里面只有计数
   * 与带本人推荐码的深链，原文一条都不带（这张卡会发到公开社交平台）。
   * 整年零记录回 40402，按空态提示而不是按错误提示：用户没做错什么。
   */
  const onShareYearReview = async () => {
    if (yearReviewLoading) return;
    setYearReviewLoading(true);
    try {
      const card = await getYearInReview();
      trackFunnelShare('share_tapped', 'year_review');
      const res = await Share.share({ message: card.share_text });
      if (res.action === Share.sharedAction) {
        trackFunnelShare('share_completed', 'year_review');
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === CODE_RESOURCE_NOT_FOUND) {
        Alert.alert(t('common.error'), t('insights.yearReviewEmpty'));
        return;
      }
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setYearReviewLoading(false);
    }
  };

  /** PDF 失败的正文：可用性单独一句，其余按后端码分档，不弹裸 error.message。 */
  const pdfFailureText = (e: unknown): string =>
    e instanceof PdfUnavailableError
      ? t('pdf.unavailable')
      : t(pdfFailureKey(e instanceof ApiError ? e.code : undefined));

  /**
   * 周期报告 PDF（U2）。排版全在后端（reportlab + 中文 CID 字体在册），
   * 这里只取字节、落盘、交给系统分享面板。
   * 按钮只在报告已生成时出现，所以 Pro 周期（40302）在选周期那一步已被拦下。
   */
  const onExportReportPdf = async () => {
    if (!reportPeriod || pdfBusy) return;
    setPdfBusy('report');
    try {
      const bytes = await getReportPdf(reportPeriod);
      await exportPdf(bytes, reportPdfFile(reportPeriod));
    } catch (e) {
      Alert.alert(t('common.error'), pdfFailureText(e));
    } finally {
      setPdfBusy(null);
    }
  };

  /** 年度报告 PDF：年份照旧不自己算，缺省交后端定。 */
  const onExportYearReviewPdf = async () => {
    if (pdfBusy) return;
    setPdfBusy('year');
    try {
      const bytes = await getYearInReviewPdf();
      await exportPdf(bytes, YEAR_IN_REVIEW_PDF_FILE);
    } catch (e) {
      Alert.alert(t('common.error'), pdfFailureText(e));
    } finally {
      setPdfBusy(null);
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
            {!!weeklyRefs.length && (
              <>
                <TouchableOpacity onPress={() => setRefsOpen((v) => !v)}>
                  <Text style={styles.refsToggle}>
                    {t('reflect.memoryRefs', {
                      n: weeklyRefs.length,
                      arrow: refsOpen ? '▲' : '▾',
                    })}
                  </Text>
                </TouchableOpacity>
                {refsOpen && (
                  <>
                    <Text style={styles.refsNote}>{t('journey.evidenceNote')}</Text>
                    {weeklyRefs.map((r) => (
                      <Text key={r.memory_id} style={styles.refsItem}>
                        「{r.excerpt}」
                        {r.created_at
                          ? ` · ${new Date(r.created_at).toLocaleDateString()}`
                          : ''}
                      </Text>
                    ))}
                  </>
                )}
              </>
            )}
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

      {/* V3 洞察（T3-04）：逐条可回溯的依据＋状态徽章。
          周报卡保留在上面（ADR-003：新面不替换旧卡），这一张吃的是 Go 的 canonical 洞察。 */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>🧭 {t('insights.canonicalTitle')}</Text>
        {insightsLoading && (
          <ActivityIndicator color={Colors.gold400} style={styles.reportLoading} />
        )}
        {!insightsLoading && insightsFailed && (
          <View style={styles.failRow}>
            <Text style={styles.emptyText}>{t('common.loadFailed')}</Text>
            <TouchableOpacity
              style={styles.retryBtn}
              onPress={loadInsights}
              accessibilityRole="button"
            >
              <Text style={styles.retryText}>{t('common.retry')}</Text>
            </TouchableOpacity>
          </View>
        )}
        {!insightsLoading && !insightsFailed && !!insights && insights.length === 0 && (
          <Text style={styles.emptyText}>{t('insights.canonicalEmpty')}</Text>
        )}
        {!insightsLoading && !insightsFailed && (insights ?? []).map((item) => (
          <View key={item.id} style={styles.insightItem}>
            {item.evidence_count === 0 ? (
              // 闸 G-05 的 UI 面：没有活支撑就不把结论铺给用户——不是灰一下、也不是留个空壳，
              // 而是这一格压根不显示 statement（屏上出现的那句话必须是可回溯的）。
              <Text style={styles.emptyText}>{t('insights.evidenceInsufficient')}</Text>
            ) : (
              <>
                <Text style={styles.bodyText}>{item.statement}</Text>
                <View style={styles.badgeRow}>
                  <Text style={styles.claimBadge}>{t(`insights.claim.${item.claim_type}`)}</Text>
                  <Text style={styles.statusBadge}>{t(`insights.status.${item.status}`)}</Text>
                  {item.confidence != null && (
                    <Text style={styles.metaText}>
                      {t('insights.confidence', { value: Math.round(item.confidence * 100) })}
                    </Text>
                  )}
                </View>
                <TouchableOpacity
                  onPress={() => onToggleEvidence(item)}
                  accessibilityRole="button"
                >
                  <Text style={styles.refsToggle}>
                    {t('insights.evidenceToggle', {
                      n: item.evidence_count,
                      arrow: evidenceOpenId === item.id ? '▲' : '▾',
                    })}
                  </Text>
                </TouchableOpacity>
                {evidenceOpenId === item.id && (
                  <>
                    {detailBusyId === item.id && (
                      <ActivityIndicator
                        color={Colors.gold400}
                        size="small"
                        style={styles.reportLoading}
                      />
                    )}
                    {!detailById[item.id] && detailFailedId === item.id && (
                      <View style={styles.failRow}>
                        <Text style={styles.emptyText}>{t('common.loadFailed')}</Text>
                        <TouchableOpacity
                          style={styles.retryBtn}
                          onPress={() => fetchDetail(item.id)}
                          accessibilityRole="button"
                        >
                          <Text style={styles.retryText}>{t('common.retry')}</Text>
                        </TouchableOpacity>
                      </View>
                    )}
                    {!!detailById[item.id] && (
                      <>
                        <Text style={styles.refsNote}>{t('journey.evidenceNote')}</Text>
                        {detailById[item.id].evidence.slice(0, EVIDENCE_MAX).map((ev) => (
                          <View key={ev.evidence_id} style={styles.evidenceItem}>
                            <Text style={styles.refsItem}>
                              「{ev.excerpt ?? ev.memory_ref?.excerpt ?? ''}」
                            </Text>
                            <Text style={styles.evidenceMeta}>
                              {t(`insights.sourceType.${ev.source.source_type}`)}
                              {ev.observed_at
                                ? ` · ${new Date(ev.observed_at).toLocaleDateString()}`
                                : ''}
                              {ev.strength != null
                                ? ` · ${t('insights.confidence', {
                                  value: Math.round(ev.strength * 100),
                                })}`
                                : ''}
                            </Text>
                          </View>
                        ))}
                        {/* truncated 与裁剪前的总数逐条显示：只给 5 条不说是 5/23，
                            用户会以为自己的依据就这么多（N9-11 的「如实给 truncated」）。 */}
                        {detailById[item.id].truncated && (
                          <Text style={styles.refsNote}>
                            {t('insights.evidenceTruncated', {
                              shown: Math.min(EVIDENCE_MAX, detailById[item.id].evidence.length),
                              total: detailById[item.id].evidence_summary.count,
                            })}
                          </Text>
                        )}
                      </>
                    )}
                  </>
                )}
              </>
            )}
          </View>
        ))}
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

      {/* 模式发现（Pro 高清；免费 = 模糊样张 + 解锁角标，不调 LLM） */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>🔍 {t('insights.patternTitle')}</Text>
        {pattern ? (
          <Text style={styles.bodyText}>{pattern}</Text>
        ) : isPro ? (
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
        ) : (
          <InsightBlurGate locked onPressUnlock={() => navigation.navigate('Paywall')}>
            <Text style={styles.bodyText}>{t('insights.patternPreview')}</Text>
          </InsightBlurGate>
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
            {/* PDF 出口：分享的是后端排好版的整份报告，不是屏内截屏 */}
            <TouchableOpacity
              style={[styles.shareReportBtn, pdfBusy !== null && styles.pdfBtnDisabled]}
              onPress={onExportReportPdf}
              disabled={pdfBusy !== null}
              accessibilityRole="button"
            >
              <Text style={styles.shareReportText}>
                📄 {pdfBusy === 'report' ? t('pdf.exporting') : t('pdf.export')}
              </Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

      {/* 情绪趋势 + 年度热力图（E2 SVG 图表；免费 = 模糊 + 解锁角标，聚合摘要保留在周报卡） */}
      {!!timeline?.days?.length && (
        <View style={styles.card}>
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitle}>🗓️ {t('insights.timelineTitle')}</Text>
            <TouchableOpacity onPress={() => setHeatFull((v) => !v)}>
              <Text style={styles.heatToggle}>
                {heatFull ? t('insights.heatmap90') : t('insights.heatmapFullYear')}
              </Text>
            </TouchableOpacity>
          </View>
          <InsightBlurGate locked={!isPro} onPressUnlock={() => navigation.navigate('Paywall')}>
            {/* 近 7 天折线（X=日期，Y=情绪值；未签到断线不插值） */}
            <MoodLineChart days={recentDays(timeline.days, 7)} width={340} height={56} />
            {/* 趋势一句话（D-G9）：文案由后端按 X-Language 生成（mood_service.py:349-352 三档），
                前端不重算平均值——否则同一屏会有第二个「这段情绪算高还是低」的真源。
                放在模糊门之内：免费/Pro 的分界不因这条而变。 */}
            {!!trend?.summary && (
              <Text style={styles.trendSummary}>{trend.summary}</Text>
            )}
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              <HeatGrid days={recentDays(timeline.days, heatFull ? HEAT_FULL_DAYS : HEAT_DEFAULT_DAYS)} />
            </ScrollView>
          </InsightBlurGate>
          {/* M4 年度报告卡：免费引流物料，所以放在模糊门之外——Pro 与否都能分享出去 */}
          <TouchableOpacity
            style={styles.shareReportBtn}
            onPress={onShareYearReview}
            disabled={yearReviewLoading}
          >
            <Text style={styles.shareReportText}>🧾 {t('insights.shareYearReview')}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.shareReportBtn, pdfBusy !== null && styles.pdfBtnDisabled]}
            onPress={onExportYearReviewPdf}
            disabled={pdfBusy !== null}
            accessibilityRole="button"
          >
            <Text style={styles.shareReportText}>
              📄 {pdfBusy === 'year' ? t('pdf.exporting') : t('pdf.export')}
            </Text>
          </TouchableOpacity>
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
  refsToggle: { fontSize: FontSize.sm, color: Colors.gold400, opacity: 0.8, marginTop: 6, lineHeight: 18 },
  refsNote: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 6, lineHeight: 16 },
  refsItem: {
    fontSize: FontSize.sm, color: Colors.textTertiary, fontStyle: 'italic',
    lineHeight: 18, marginTop: 4, paddingLeft: 8,
    borderLeftWidth: 2, borderLeftColor: 'rgba(255,215,0,0.25)',
  },
  evidenceItem: { marginTop: Spacing.xs },
  evidenceMeta: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 2, paddingLeft: 8 },
  entryRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.sm },
  entryBody: { flex: 1 },
  // V3 洞察卡（T3-04）：一条洞察一项，徽章行与依据行分开
  insightItem: {
    marginTop: Spacing.md, paddingTop: Spacing.md,
    borderTopWidth: 1, borderTopColor: Colors.borderLight,
  },
  badgeRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: Spacing.sm, marginTop: 4 },
  claimBadge: {
    fontSize: FontSize.xs, fontWeight: '700', color: Colors.gold400,
    backgroundColor: 'rgba(255,215,0,0.10)', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6,
  },
  statusBadge: {
    fontSize: FontSize.xs, fontWeight: '600', color: Colors.textSecondary,
    backgroundColor: 'rgba(255,255,255,0.06)', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6,
  },
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
  // TouchableOpacity 的 disabled 自己不改观感，忙态得压暗，否则看不出两个入口互斥
  pdfBtnDisabled: { opacity: 0.45 },
  heatToggle: { fontSize: FontSize.sm, color: Colors.gold400, opacity: 0.85 },
  trendSummary: {
    fontSize: FontSize.md, color: Colors.textSecondary,
    marginTop: Spacing.xs, marginBottom: Spacing.sm, lineHeight: LineHeight.caption,
  },
});
