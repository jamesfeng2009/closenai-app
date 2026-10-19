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
  Modal, TextInput,
} from 'react-native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  getWeeklyInsight, getPatternDiscovery, getShareReport, getYearInReview, getTimeline,
  getUserProfile, getReportPdf, getYearInReviewPdf, getMoodTrend, listInsights, getInsight,
  postInsightFeedback,
  ApiError,
} from '../services/api';
import { exportPdf, PdfUnavailableError } from '../services/pdfExport';
import { reportPdfFile, YEAR_IN_REVIEW_PDF_FILE, pdfFailureKey } from '../config/pdfFiles';
import type {
  GrowthTimeline, MoodTrendData, SharePeriod, ShareReport, UserProfile, WeeklyInsight,
  InsightListItem, InsightDetail, InsightFeedbackType, InsightFeedbackReceipt,
} from '../types';
import { trackFunnelShare } from '../services/funnel';
import { track } from '../services/analytics';
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

// ── T3-05：反馈条（六态）与纠错输入 ──────────────────────────────────────────
// 六态逐字取 Go 的 `domain.FeedbackTypes`（outcome.go:125-136），顺序即按钮顺序。
// 不在这里重述合法性判据：未知档由 Go 回 400，空纠错由 Go 回 400（v3_feedback.go:80-84）。
const FEEDBACK_TYPES: InsightFeedbackType[] = [
  'confirm', 'partially_confirm', 'reject', 'correct', 'outdated', 'forget_from_model',
];

// 纠错正文上限逐字等于 Go 的 `maxCorrectionText`（v3_feedback.go:26）。
// 两处差一格的后果是用户写完 501 字才被拒，而这在输入框里看不出来。
const CORRECTION_MAX_LENGTH = 500;

// 重复表态的码：Go 把 `ErrDuplicateFeedback` 折成 CodeConflict=409（apierr.go:20），
// BFF 原样透传信封码（v3_proxy.py:145），所以客户端看到的就是 409 而不是 4xxxx。
const CODE_DUPLICATE_FEEDBACK = 409;

/**
 * 乐观更新要写的徽章值——**不是前端自己定的第二套状态机**。
 * 逐字抄 Go 的 `StatusForDerivedTarget(RefInsight, ·)`（outcome.go:629-641），
 * 由 `insightFeedbackWiring.test.ts` 拿 Go 源对账；`partially_confirm` 不在表里，
 * 因为 Go 明确「部分认可不改写状态」（outcome.go:611-615），前端替它猜一个就是假改写。
 */
const LOCAL_TARGET_STATUS: Partial<Record<InsightFeedbackType, string>> = {
  confirm: 'confirmed',
  reject: 'retracted',
  correct: 'corrected',
  outdated: 'expired',
  forget_from_model: 'dismissed',
};

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
  // T3-05：一条表态一格标记。`type` 是乐观写的（徽章与「已反馈」那行立刻跟上），
  // `receipt` 是服务端回执到达后才写（成功＝按钮收起，失败＝整格删掉＝回滚）。
  // 分两个 map 会分不清「回执说没改写」与「我猜它会改写」，所以合成一格但只允许
  // receipt 在有回执时非空。
  const [feedbackById, setFeedbackById] = useState<
    Record<string, { type: InsightFeedbackType; receipt?: InsightFeedbackReceipt }>
  >({});
  const [feedbackBusyId, setFeedbackBusyId] = useState<string | null>(null);
  // 「改口」：提交成功过的条目默认收起六枚按钮（工单 §怎么做.4「不重复弹按钮」），
  // 点「改口」才重新铺出来——重复提交由后端 409 兜底，不靠这里禁死用户。
  const [amendId, setAmendId] = useState<string | null>(null);
  const [correctionFor, setCorrectionFor] = useState<string | null>(null);
  const [correctionText, setCorrectionText] = useState('');
  const [correcting, setCorrecting] = useState(false);

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

  /** 徽章取哪张表：读面六档（`insights.status`）还是状态机五档（`insights.fbResult`）。 */
  const statusBadgeKey = (item: InsightListItem): string => {
    const mark = feedbackById[item.id];
    if (!mark) return `insights.status.${item.status}`;
    // changed=false＝服务端一个字没改（`partially_confirm` 永远是这格），
    // 这时回执里的 target_status 就是当前状态，仍归读面那张表管。
    if (mark.receipt && !mark.receipt.changed) return `insights.status.${mark.receipt.target_status}`;
    return `insights.fbResult.${mark.receipt?.target_status ?? LOCAL_TARGET_STATUS[mark.type]}`;
  };

  /** 「已反馈：{六态标签}」——标签仍取 Go 的 FeedbackTypes 词表，前端不另造一档。 */
  const doneLine = (item: InsightListItem): string => {
    const mark = feedbackById[item.id];
    return t('insights.fbDone', { label: mark ? t(`insights.fb.${mark.type}`) : '' });
  };

  /**
   * 一次表态：先乐观写徽章 → 发请求 → 成功用回执覆盖，失败把整格删掉（＝回滚）。
   *
   * 失败一律不弹 `e.message`：`ApiError.message` 是服务端给运维看的中英双语串
   * （「纠错必须填写说明文字 / correction_text is required for correct」），
   * 弹给用户就是把内部口径当界面（工单 §怎么做.3）。
   */
  const submitFeedback = async (
    item: InsightListItem, type: InsightFeedbackType, correction: string = '',
  ): Promise<boolean> => {
    if (feedbackBusyId === item.id) return false;
    const prev = feedbackById[item.id];
    const predicted = LOCAL_TARGET_STATUS[type];
    setFeedbackBusyId(item.id);
    if (predicted) setFeedbackById((p) => ({ ...p, [item.id]: { type } }));
    try {
      const receipt = await postInsightFeedback(item.id, type, correction);
      setFeedbackById((p) => ({ ...p, [item.id]: { type, receipt } }));
      setAmendId(null);
      // 埋点只带行为元数据：纠错正文是内容，进分析队列等于给它开第二条出网路径
      // （analytics.ts 的属性白名单与 Go/Python 三侧对齐，本票不扩表）。
      track('insight_feedback_submitted', { feature: 'insights', action: type });
      Alert.alert(t('insights.fbSavedTitle'), t('insights.fbSavedMessage'));
      return true;
    } catch (e) {
      setFeedbackById((p) => {
        const next = { ...p };
        if (prev) {
          next[item.id] = prev;
        } else {
          delete next[item.id];
        }
        return next;
      });
      Alert.alert(
        t('common.error'),
        e instanceof ApiError && e.code === CODE_DUPLICATE_FEEDBACK
          ? t('insights.fbDuplicate')
          : t('insights.fbFailed'),
      );
      return false;
    } finally {
      setFeedbackBusyId(null);
    }
  };

  const onFeedbackPress = (item: InsightListItem, type: InsightFeedbackType) => {
    // 「我要纠正」是唯一带正文的一档：Go 也这么分（v3_feedback.go:80-84 只对 correct 判非空）。
    if (type === 'correct') {
      setCorrectionFor(item.id);
      setCorrectionText('');
      return;
    }
    submitFeedback(item, type);
  };

  const closeCorrection = () => {
    setCorrectionFor(null);
    setCorrectionText('');
  };

  const submitCorrection = async () => {
    const text = correctionText.trim();
    // 空正文不发请求：Go 对 `correct` 判必填（400），但更坏的是用户以为纠正过了。
    if (!correctionFor || !text || correcting) return;
    const item = (insights ?? []).find((x) => x.id === correctionFor);
    if (!item) return;
    setCorrecting(true);
    const ok = await submitFeedback(item, 'correct', text);
    setCorrecting(false);
    if (ok) closeCorrection();
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
    <>
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
                  <Text style={styles.statusBadge}>{t(statusBadgeKey(item))}</Text>
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
                {/* T3-05 反馈条放在这一格的最后（结论与依据都铺完了才问用户认不认）。
                    evidence_count===0 那一格压根没有结论可表态，所以六枚按钮也只长在 else 分支里。 */}
                {feedbackById[item.id]?.receipt && amendId !== item.id ? (
                  // 提交成功过就收起六枚按钮（工单 §怎么做.4「不重复弹按钮」），
                  // 但留一个「改口」：重复表态由后端 409 判，前端不靠禁死用户来防。
                  <View style={styles.fbRow}>
                    <Text style={styles.fbDoneText}>{doneLine(item)}</Text>
                    <TouchableOpacity
                      style={styles.fbAmendBtn}
                      onPress={() => setAmendId(item.id)}
                      accessibilityRole="button"
                      accessibilityLabel={t('insights.fbAmend')}
                    >
                      <Text style={styles.fbAmendText}>{t('insights.fbAmend')}</Text>
                    </TouchableOpacity>
                  </View>
                ) : (
                  <View style={styles.fbRow}>
                    {FEEDBACK_TYPES.map((type) => (
                      <TouchableOpacity
                        key={type}
                        style={[
                          styles.fbBtn,
                          feedbackBusyId === item.id && styles.fbBtnDisabled,
                        ]}
                        onPress={() => onFeedbackPress(item, type)}
                        disabled={feedbackBusyId === item.id}
                        accessibilityRole="button"
                        accessibilityLabel={t(`insights.fb.${type}`)}
                      >
                        <Text style={styles.fbBtnText}>{t(`insights.fb.${type}`)}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
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

      {/* T3-05 纠错弹层：只有「我要纠正」这一档带正文，所以弹层也只服务它。
          放在 ScrollView 之外（用 fragment 包起来）——弹层里的 TextInput 嵌在
          竖向 ScrollView 里时键盘遮挡与滚动争用都归这一屏的滚动容器管。 */}
      <Modal
        visible={correctionFor !== null}
        transparent
        animationType="fade"
        onRequestClose={closeCorrection}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('insights.fbCorrectTitle')}</Text>
            <Text style={styles.modalHint}>{t('insights.fbCorrectHint')}</Text>
            <TextInput
              style={styles.correctInput}
              multiline
              value={correctionText}
              onChangeText={setCorrectionText}
              placeholder={t('insights.fbCorrectPlaceholder')}
              placeholderTextColor={Colors.textMuted}
              maxLength={CORRECTION_MAX_LENGTH}
              accessibilityLabel={t('insights.fbCorrectPlaceholder')}
            />
            {/* 计数不走 i18n：纯数字加斜杠在 15 门语言里同形，为它补 15 份键只会进
                i18nLocales 那张「同形词豁免表」，然后每条都要人再判一次为什么同形。 */}
            <Text style={styles.correctCount}>
              {`${correctionText.trim().length}/${CORRECTION_MAX_LENGTH}`}
            </Text>
            <View style={styles.modalActions}>
              <TouchableOpacity
                style={styles.modalCancelBtn}
                onPress={closeCorrection}
                accessibilityRole="button"
                accessibilityLabel={t('common.cancel')}
              >
                <Text style={styles.modalCancelText}>{t('common.cancel')}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[
                  styles.modalSubmitBtn,
                  (!correctionText.trim() || correcting) && styles.modalSubmitBtnDisabled,
                ]}
                onPress={submitCorrection}
                disabled={!correctionText.trim() || correcting}
                accessibilityRole="button"
                accessibilityLabel={t('insights.fbCorrectSubmit')}
              >
                <Text style={styles.modalSubmitText}>{t('insights.fbCorrectSubmit')}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </>
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
  // T3-05 反馈条：六枚按钮换行排（窄屏一行放不下），按 Go 的词表顺序铺。
  // 忙态必须压暗——TouchableOpacity 的 disabled 自己不改观感，否则用户会连点。
  fbRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: Spacing.sm, marginTop: Spacing.sm },
  fbBtn: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: Colors.borderLight,
  },
  fbBtnDisabled: { opacity: 0.4 },
  fbBtnText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  fbDoneText: { fontSize: FontSize.sm, color: Colors.textTertiary, flex: 1, lineHeight: 18 },
  fbAmendBtn: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.gold400,
  },
  fbAmendText: { fontSize: FontSize.sm, color: Colors.gold400, fontWeight: '600' },
  // 纠错弹层（工单 T3-05 §怎么做.2 的 Modal 多行输入）
  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'center', alignItems: 'center', padding: Spacing.lg,
  },
  modalCard: {
    width: '100%', maxWidth: 460, backgroundColor: Colors.bgSecondary,
    borderRadius: BorderRadius.xl, borderWidth: 1, borderColor: Colors.borderLight, padding: Spacing.lg,
  },
  modalTitle: { fontSize: FontSize.lg, fontWeight: '600', color: Colors.textPrimary },
  modalHint: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4, lineHeight: LineHeight.caption },
  correctInput: {
    minHeight: 96, marginTop: Spacing.md, padding: Spacing.md,
    borderRadius: BorderRadius.lg, borderWidth: 1, borderColor: Colors.borderLight,
    backgroundColor: 'rgba(255,255,255,0.04)',
    fontSize: FontSize.md, color: Colors.textPrimary, textAlignVertical: 'top',
  },
  correctCount: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 4 },
  modalActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: Spacing.sm, marginTop: Spacing.md },
  modalCancelBtn: {
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.borderLight,
  },
  modalCancelText: { fontSize: FontSize.md, color: Colors.textTertiary },
  modalSubmitBtn: {
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: BorderRadius.pill,
    backgroundColor: Colors.gold400,
  },
  modalSubmitBtnDisabled: { opacity: 0.4 },
  modalSubmitText: { fontSize: FontSize.md, fontWeight: '700', color: Colors.bgPrimary },
});
