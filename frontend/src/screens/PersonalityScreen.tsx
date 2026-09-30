/**
 * 性格画像页面（P0-F05 对话式发现 + P1-F03 九型渐进式评估）。
 *
 * 主视图（对话式发现模式）：
 * - 顶部：当前阶段（破冰/观察/探索/揭示/运用）+ 进度条 + 第 X/7 天
 * - 中部：已观察到的维度信号（EI/SN/TF/JP 倾向条）
 * - 底部：AI 引导文案 + 跳转对话页
 * - 九型人格卡片：36 题渐进式评估进度 / 断点续答 / 结果（含 MBTI×九型组合名）
 * - 手动补充测试：8 题问卷降级为折叠入口（次级路径）
 * - stage=揭示/运用（或已有画像）时：完整画像（16 型 + 观察 + 独特特质），
 *   画像由后端在 revealing 阶段自动生成，前端只展示「画像已生成」状态
 * - 保留：性格名片入口 / Pro 组合报告 / 刷新配额
 *
 * 游客守卫：所有写操作（开始/答题/刷新/组合报告）先过 useRequireAuth。
 */
import React, { useCallback, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert,
  TextInput, KeyboardAvoidingView, Platform,
} from 'react-native';
import { useNavigation, useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  getPersonalityProfile, refreshPersonality,
  getAssessment, startAssessment, answerAssessment, generateComboReport,
  getEnneagram, startEnneagram, answerEnneagram,
  ApiError,
} from '../services/api';
import type { AssessmentState, ComboReport, EnneagramState, PersonalityProfile } from '../types';
import { personalityAssessmentRepo } from '../services/db/personalityAssessmentRepository';
import { personalityProfileRepo } from '../services/db/personalityProfileRepository';
import { track } from '../services/analytics';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import { useTranslation } from 'react-i18next';

const STAGE_KEYS = ['stageIntro', 'stageObserve', 'stageExplore', 'stageReveal', 'stageApply'];

/** 后端 profile.stage 字符串 → 阶段下标（缺省时回退 cold_start_stage 下标）。 */
const STAGE_NAME_TO_INDEX: Record<string, number> = {
  icebreaking: 0, observing: 1, exploring: 2, revealing: 3, applying: 4,
};

/** 九型评估在本地断点续答表中的行 id（与 8 题评估的 'current' 隔离）。 */
const ENNEAGRAM_ROW_ID = 'enneagram';

/** 维度信号条配置：左极 → 右极（value 0-1，≥0.5 倾向右极）。 */
const DIMENSIONS = [
  { key: 'EI', left: 'E', right: 'I' },
  { key: 'SN', left: 'S', right: 'N' },
  { key: 'TF', left: 'T', right: 'F' },
  { key: 'JP', left: 'J', right: 'P' },
] as const;

type ScreenMode = 'overview' | 'assessment' | 'enneagram';

/** 本地画像快照 → 前端 Profile（缺省字段用安全默认值，其余待远端补齐）。 */
function profileRowToState(row: {
  id: string; mbti: string | null; enneagram: string | null; created_at: string;
}): PersonalityProfile {
  return {
    id: row.id,
    mbti_type: row.mbti,
    mbti_label: null,
    enneagram_type: row.enneagram,
    observations: [],
    unique_traits: null,
    version: 1,
    refresh_quota_remaining: 0,
    cold_start_progress: null,
    cold_start_stage: null,
    created_at: row.created_at,
  };
}

/** 前端 Profile → 本地画像快照（Tier 2 只缓存展示所需字段）。 */
function profileStateToRow(p: PersonalityProfile) {
  return {
    id: p.id,
    mbti: p.mbti_type,
    enneagram: p.enneagram_type,
    summary: null,
    refreshed_at: new Date().toISOString(),
    created_at: p.created_at,
    updated_at: new Date().toISOString(),
  };
}

/** 阶段下标解析：优先后端 stage 字符串，其次 cold_start_stage，最后按进度推算。 */
function resolveStageIndex(profile: PersonalityProfile | null): number {
  if (profile?.stage && STAGE_NAME_TO_INDEX[profile.stage] != null) {
    return STAGE_NAME_TO_INDEX[profile.stage];
  }
  if (typeof profile?.cold_start_stage === 'number') return profile.cold_start_stage;
  const progress = profile?.cold_start_progress ?? 0;
  return Math.min(Math.floor(progress * STAGE_KEYS.length), STAGE_KEYS.length - 1);
}

/**
 * 维度倾向值（0-1，null = 未观察到）：
 * 优先后端 dimension_signals；否则由已确认的 mbti_type 字母推导（右极 0.8 / 左极 0.2）。
 */
function dimensionValue(
  profile: PersonalityProfile | null,
  dim: (typeof DIMENSIONS)[number],
  index: number,
): number | null {
  const raw = profile?.dimension_signals?.[dim.key];
  if (typeof raw === 'number') return Math.min(1, Math.max(0, raw));
  const mbti = (profile?.mbti_type ?? '').toUpperCase();
  if (mbti.length !== 4) return null;
  const letter = mbti.charAt(index);
  if (letter !== dim.left && letter !== dim.right) return null;
  return letter === dim.right ? 0.8 : 0.2;
}

export default function PersonalityScreen() {
  const { t } = useTranslation();
  const navigation = useNavigation<any>();
  const { isLoggedIn } = useAuth();
  const requireAuth = useRequireAuth();
  const [mode, setMode] = useState<ScreenMode>('overview');
  const [profile, setProfile] = useState<PersonalityProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  // 8 题手动补充评估
  const [assessment, setAssessment] = useState<AssessmentState | null>(null);
  const [answering, setAnswering] = useState(false);
  const [freeText, setFreeText] = useState('');
  const [manualExpanded, setManualExpanded] = useState(false);
  // 36 题九型渐进式评估
  const [enneagram, setEnneagram] = useState<EnneagramState | null>(null);
  const [ennAnswering, setEnnAnswering] = useState(false);
  const [ennFreeText, setEnnFreeText] = useState('');
  // Pro 组合报告
  const [combo, setCombo] = useState<ComboReport | null>(null);
  const [comboLoading, setComboLoading] = useState(false);
  // 远端画像拉取失败且本地无缓存 → 失败态；不能与「还没有画像」的空态混成一句话
  const [profileFailed, setProfileFailed] = useState(false);

  const loadInitial = useCallback(async () => {
    // 首屏秒开：先读本地画像缓存 + 两项评估进度（Tier 2 展示缓存 + Tier 1 离线续答）
    setLoading(true);
    const [cachedProfile, cachedAssessment, cachedEnneagram] = await Promise.all([
      personalityProfileRepo.getLatest().catch(() => null),
      personalityAssessmentRepo.getCurrent().catch(() => null),
      personalityAssessmentRepo.getCurrent(ENNEAGRAM_ROW_ID).catch(() => null),
    ]);
    if (cachedProfile) setProfile(profileRowToState(cachedProfile));
    if (cachedAssessment && cachedAssessment.status !== 'not_started') {
      setAssessment({
        status: cachedAssessment.status,
        current_index: cachedAssessment.current_index,
        total: cachedAssessment.total_questions,
        question: null, // 题目内容由远端提供，离线时暂缺，进入答题后再拉取
      });
    }
    if (cachedEnneagram && cachedEnneagram.status !== 'not_started') {
      setEnneagram({
        status: cachedEnneagram.status,
        current_index: cachedEnneagram.current_index,
        total: cachedEnneagram.total_questions,
        question: null,
        result: null,
      });
    }
    setLoading(false);

    // 游客态不请求远端（避免 401 噪声），仅展示本地缓存
    if (!isLoggedIn) return;

    try {
      // 后台拉取远端最新，合并刷新（Tier 2 远端为准，覆盖本地缓存）
      let remoteProfileFailed = false;
      const [p, a, e] = await Promise.all([
        getPersonalityProfile().catch(() => {
          remoteProfileFailed = true;
          return null;
        }),
        getAssessment().catch(() => null),
        getEnneagram().catch(() => null),
      ]);
      // 远端画像挂了且本地无缓存 → 失败态（不能落到「还没有画像」的空态文案上）
      setProfileFailed(remoteProfileFailed && !cachedProfile);
      if (p) {
        setProfile(p);
        personalityProfileRepo.cache(profileStateToRow(p)).catch(() => {});
      }
      if (a) {
        // 本地有未完成进度而远端尚未开始（本地作答尚未同步到远端）：优先恢复本地续答进度
        const localInProgress =
          cachedAssessment?.status === 'in_progress' && cachedAssessment.current_index > 0;
        if (!(localInProgress && a.status === 'not_started')) {
          setAssessment(a);
          // 持久化评估进度（Tier 1 本地拥有，离线断点续答；saveProgress 保留本地已答答案）
          personalityAssessmentRepo
            .saveProgress({
              status: a.status,
              current_index: a.current_index,
              total_questions: a.total,
            })
            .catch(() => {});
        }
      }
      if (e) {
        const localEnnInProgress =
          cachedEnneagram?.status === 'in_progress' && cachedEnneagram.current_index > 0;
        if (!(localEnnInProgress && e.status === 'not_started')) {
          setEnneagram(e);
          personalityAssessmentRepo
            .saveProgress(
              { status: e.status, current_index: e.current_index, total_questions: e.total },
              ENNEAGRAM_ROW_ID,
            )
            .catch(() => {});
        }
      }
    } catch (err) {
      console.warn('Failed to load personality:', err);
      setProfileFailed(true);
    }
  }, [isLoggedIn]);

  // 栈页会被重复进入（答题、手动补充、Paywall 返回），聚焦即重新加载
  // loadInitial 是 async，useFocusEffect 需要「同步返回 cleanup」的回调，所以包一层
  useFocusEffect(
    useCallback(() => {
      loadInitial();
    }, [loadInitial])
  );

  const onRefresh = async () => {
    if (!requireAuth() || refreshing) return; // 游客拦截 → 跳 AuthScreen
    try {
      setRefreshing(true);
      const res = await refreshPersonality();
      if (res.success && res.profile) {
        setProfile(res.profile);
        // 缓存最新画像快照（Tier 2 展示缓存）
        personalityProfileRepo.cache(profileStateToRow(res.profile)).catch(() => {});
        // 行为事件：刷新画像（仅行为元数据，无内容）
        track('personality_refreshed', { feature: 'personality', action: 'refresh' });
      }
      if (res.message) {
        Alert.alert(t('common.error'), res.message);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.refreshFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setRefreshing(false);
    }
  };

  // ── 手动补充：8 题对话式评估（次级路径） ──

  const onStartAssessment = async () => {
    if (!requireAuth() || answering) return;
    try {
      setAnswering(true);
      const a = await startAssessment();
      setAssessment(a);
      // 持久化评估进度（Tier 1 离线断点续答；saveProgress 保留本地已答答案）
      personalityAssessmentRepo
        .saveProgress({
          status: a.status,
          current_index: a.current_index,
          total_questions: a.total,
        })
        .catch(() => {});
      if (a.question) setMode('assessment');
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setAnswering(false);
    }
  };

  const submitAnswer = async (payload: { option: 'a' | 'b' | 'neutral' } | { answer: string }) => {
    if (!requireAuth() || answering) return;
    // 作答时快照当前题（提交成功后 question 已切换，需用快照写本地答案日志）
    const answeredQuestion = assessment?.question ?? null;
    try {
      setAnswering(true);
      const res = await answerAssessment(payload);
      setFreeText('');
      if (res.completed) {
        if (res.profile) setProfile(res.profile);
        setAssessment((prev) =>
          prev ? { ...prev, status: 'completed', question: null } : prev,
        );
        // 完成：全部答案已在远端保存成功，本地标记完成并清除离线答案凭证（Tier 1 + Tier 2）
        personalityAssessmentRepo
          .save({
            id: 'current',
            status: 'completed',
            current_index: res.next_question?.index ?? 0,
            total_questions: assessment?.total ?? 8,
            answers: null,
            updated_at: new Date().toISOString(),
          })
          .catch(() => {});
        if (res.profile) {
          personalityProfileRepo.cache(profileStateToRow(res.profile)).catch(() => {});
        }
        setMode('overview');
        if (res.message) Alert.alert(t('common.error'), res.message);
      } else if (res.next_question) {
        setAssessment((prev) =>
          prev
            ? { ...prev, current_index: res.next_question!.index, question: res.next_question }
            : prev,
        );
        // 作答同步写本地（fire-and-forget）：追加答案 + 前移进度，支持离线断点续答
        if (answeredQuestion) {
          personalityAssessmentRepo
            .recordAnswer(
              `q${answeredQuestion.index}`,
              'option' in payload ? payload.option : payload.answer,
              res.next_question.total,
            )
            .catch(() => {});
        }
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.submitFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setAnswering(false);
    }
  };

  // ── 九型人格渐进式评估（36 题，断点续答） ──

  const onEnneagramEntry = async () => {
    if (!requireAuth() || ennAnswering) return;
    // 未完成时进入答题模式；本地无题（离线恢复/not_started）则向远端拉取当前题
    setMode('enneagram');
    if (enneagram?.status !== 'completed' && !enneagram?.question) {
      await onStartEnneagram();
    }
  };

  const onStartEnneagram = async () => {
    if (ennAnswering) return;
    try {
      setEnnAnswering(true);
      const s = await startEnneagram();
      setEnneagram(s);
      personalityAssessmentRepo
        .saveProgress(
          { status: s.status, current_index: s.current_index, total_questions: s.total },
          ENNEAGRAM_ROW_ID,
        )
        .catch(() => {});
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setEnnAnswering(false);
    }
  };

  const submitEnneagramAnswer = async (
    payload: { option: 'a' | 'b' | 'neutral' } | { answer: string },
  ) => {
    if (!requireAuth() || ennAnswering) return;
    const answeredQuestion = enneagram?.question ?? null;
    try {
      setEnnAnswering(true);
      const res = await answerEnneagram(payload);
      setEnnFreeText('');
      if (res.completed) {
        setEnneagram((prev) =>
          prev ? { ...prev, status: 'completed', question: null, result: res.result } : prev,
        );
        // 完成：远端已定型并写入 users.enneagram_type，本地标记完成并清除离线答案凭证
        personalityAssessmentRepo
          .save({
            id: ENNEAGRAM_ROW_ID,
            status: 'completed',
            current_index: enneagram?.total ?? 36,
            total_questions: enneagram?.total ?? 36,
            answers: null,
            updated_at: new Date().toISOString(),
          })
          .catch(() => {});
        // 同步画像九型字段（后端已写入 users.enneagram_type，前端本地先行展示）
        if (res.result) {
          setProfile((prev) => (prev ? { ...prev, enneagram_type: res.result!.enneagram_type } : prev));
          track('enneagram_completed', { feature: 'personality', action: 'enneagram_complete' });
        }
      } else if (res.next_question) {
        setEnneagram((prev) =>
          prev
            ? { ...prev, current_index: res.next_question!.index, question: res.next_question }
            : prev,
        );
        if (answeredQuestion) {
          personalityAssessmentRepo
            .recordAnswer(
              `q${answeredQuestion.index}`,
              'option' in payload ? payload.option : payload.answer,
              res.next_question.total,
              ENNEAGRAM_ROW_ID,
            )
            .catch(() => {});
        }
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.submitFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setEnnAnswering(false);
    }
  };

  // ── Pro 组合报告 ──

  const onComboReport = async () => {
    if (!requireAuth() || comboLoading) return;
    try {
      setComboLoading(true);
      setCombo(await generateComboReport());
    } catch (e) {
      // 组合报告为 Pro 权益（40302）：引导跳转 Paywall
      if (e instanceof ApiError && e.code === 40302) {
        Alert.alert(
          t('share.proGateTitle'),
          t('share.proGateMessage'),
          [
            { text: t('common.cancel'), style: 'cancel' },
            { text: t('share.upgrade'), onPress: () => navigation.navigate('Paywall') },
          ],
        );
        return;
      }
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setComboLoading(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  const progress = Math.min(1, Math.max(0, profile?.cold_start_progress ?? 0));
  const stageIndex = resolveStageIndex(profile);
  const day = Math.min(7, Math.max(1, Math.ceil(progress * 7)));
  // 揭示/运用阶段（或已有画像）→ 展示完整画像；画像由后端自动生成，前端只展示状态
  const revealed = stageIndex >= 3;
  const showProfile = revealed || !!profile?.mbti_type;
  const profileReady = revealed && !!profile?.mbti_type;
  const dimValues = DIMENSIONS.map((dim, i) => dimensionValue(profile, dim, i));
  const hasSignals = dimValues.some((v) => v !== null);

  // ── 答题模式共用：单题展示（进度 + 气泡 + 选项 + 自由输入） ──

  const renderQuestionFlow = (flow: 'assessment' | 'enneagram') => {
    const isEnneagram = flow === 'enneagram';
    const question = isEnneagram ? enneagram?.question : assessment?.question;
    const busy = isEnneagram ? ennAnswering : answering;
    const text = isEnneagram ? ennFreeText : freeText;
    const setText = isEnneagram ? setEnnFreeText : setFreeText;
    const submit = isEnneagram ? submitEnneagramAnswer : submitAnswer;
    const result = isEnneagram && enneagram?.status === 'completed' ? enneagram.result : null;

    return (
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        {/* 返回（断点已保存，可随时续答） */}
        <TouchableOpacity style={styles.backRow} onPress={() => setMode('overview')}>
          <Text style={styles.backText}>
            ‹ {isEnneagram ? t('personality.enneagramExit') : t('personality.backToOverview')}
          </Text>
        </TouchableOpacity>

        {result ? (
          /* 九型结果：类型 + 标签 + MBTI 组合名 */
          <View style={styles.card}>
            <Text style={styles.cardTitle}>🔢 {t('personality.enneagramTitle')}</Text>
            <Text style={styles.enneagramResult}>
              {t('personality.enneagramResult', {
                type: result.enneagram_type,
                label: result.type_label,
              })}
            </Text>
            {!!result.combo_name && (
              <Text style={styles.enneagramCombo}>
                {t('personality.enneagramCombo', {
                  mbti: result.combo_mbti,
                  type: result.enneagram_type,
                  name: result.combo_name,
                })}
              </Text>
            )}
            <TouchableOpacity style={styles.assessmentBtn} onPress={() => setMode('overview')}>
              <Text style={styles.assessmentBtnText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        ) : question ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>
              {isEnneagram ? `🔢 ${t('personality.enneagramTitle')}` : `💬 ${t('personality.assessmentTitle')}`}
            </Text>
            <Text style={styles.assessmentProgress}>
              {t('personality.assessmentProgress', {
                index: question.index + 1,
                total: question.total,
              })}
            </Text>
            <View style={styles.progressTrack}>
              <View
                style={[styles.progressFill, { width: `${((question.index + 1) / question.total) * 100}%` }]}
              />
            </View>
            {/* 问题气泡（对话式） */}
            <View style={styles.questionBubble}>
              <Text style={styles.questionText}>{question.text}</Text>
            </View>
            <TouchableOpacity
              style={styles.optionBtn}
              onPress={() => submit({ option: 'a' })}
              disabled={busy}
            >
              <Text style={styles.optionText}>{question.options.a}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.optionBtn}
              onPress={() => submit({ option: 'b' })}
              disabled={busy}
            >
              <Text style={styles.optionText}>{question.options.b}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.optionBtn}
              onPress={() => submit({ option: 'neutral' })}
              disabled={busy}
            >
              <Text style={styles.optionTextDim}>{question.options.neutral}</Text>
            </TouchableOpacity>
            {/* 自由输入（PRD：用户自己打字，AI 理解语义后归类） */}
            <View style={styles.freeInputRow}>
              <TextInput
                style={styles.freeInput}
                value={text}
                onChangeText={setText}
                placeholder={t('personality.assessmentInputPlaceholder')}
                placeholderTextColor={Colors.textMuted}
                editable={!busy}
                multiline
              />
              <TouchableOpacity
                style={[styles.sendBtn, (busy || !text.trim()) && styles.refreshBtnDisabled]}
                onPress={() => submit({ answer: text.trim() })}
                disabled={busy || !text.trim()}
              >
                {busy ? (
                  <ActivityIndicator color={Colors.bgPrimary} size="small" />
                ) : (
                  <Text style={styles.sendBtnText}>{t('personality.assessmentSend')}</Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        ) : (
          <View style={styles.card}>
            <ActivityIndicator color={Colors.gold400} />
          </View>
        )}
      </ScrollView>
    );
  };

  // ── 答题模式：8 题手动补充 / 36 题九型 ──
  if (mode === 'assessment' || mode === 'enneagram') {
    return (
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {renderQuestionFlow(mode)}
      </KeyboardAvoidingView>
    );
  }

  // ── 主视图：对话式发现 ──
  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        {profileFailed && !profile?.mbti_type && (
          <View style={styles.failBox}>
            <Text style={styles.failText}>{t('common.loadFailed')}</Text>
            <TouchableOpacity style={styles.retryBtn} onPress={loadInitial}>
              <Text style={styles.retryText}>{t('common.retry')}</Text>
            </TouchableOpacity>
          </View>
        )}
        {/* 顶部：发现阶段 + 进度 */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🧭 {t('personality.discoveryTitle')}</Text>
          <Text style={styles.stageCurrent}>
            {t('personality.currentStage')} · {t(`explore.${STAGE_KEYS[stageIndex]}`)}
          </Text>
          <Text style={styles.coldStatus}>
            {profileReady ? t('personality.profileReady') : t('personality.stageDay', { day })}
          </Text>
          <View style={styles.stageRow}>
            {STAGE_KEYS.map((key, i) => (
              <View key={key} style={styles.stageItem}>
                <View style={[styles.stageDot, i <= stageIndex && styles.stageDotActive]} />
                <Text style={[styles.stageName, i <= stageIndex && styles.stageNameActive]}>
                  {t(`explore.${key}`)}
                </Text>
              </View>
            ))}
          </View>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progress * 100}%` }]} />
          </View>
          {/* 渐进字段：后端补齐后展示信号数与预计完成天数 */}
          {typeof profile?.signals_count === 'number' && (
            <Text style={styles.stageMeta}>
              {t('personality.signalsCount', { count: profile.signals_count })}
            </Text>
          )}
          {typeof profile?.estimated_completion_days === 'number' && !profileReady && (
            <Text style={styles.stageMeta}>
              {t('personality.stageEta', { days: profile.estimated_completion_days, count: profile.estimated_completion_days })}
            </Text>
          )}
        </View>

        {/* 中部：维度信号 */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🧬 {t('personality.signalsTitle')}</Text>
          {hasSignals ? (
            DIMENSIONS.map((dim, i) => {
              const value = dimValues[i];
              const rightDominant = value != null && value >= 0.5;
              return (
                <View key={dim.key} style={styles.dimRow}>
                  <Text style={[styles.dimLetter, value != null && !rightDominant && styles.dimLetterActive]}>
                    {dim.left}
                  </Text>
                  <View style={styles.dimTrack}>
                    {value != null && (
                      <View style={[styles.dimFill, { width: `${value * 100}%` }]} />
                    )}
                  </View>
                  <Text style={[styles.dimLetter, rightDominant && styles.dimLetterActive]}>
                    {dim.right}
                  </Text>
                </View>
              );
            })
          ) : (
            <Text style={styles.assessmentDesc}>{t('personality.signalsEmpty')}</Text>
          )}
        </View>

        {/* 底部：AI 引导 → 对话页 */}
        <View style={styles.guidanceCard}>
          <Text style={styles.guidanceText}>{t('personality.guidanceText')}</Text>
          <TouchableOpacity
            style={styles.assessmentBtn}
            onPress={() => navigation.navigate('Tabs', { screen: 'Reflect' })}
          >
            <Text style={styles.assessmentBtnText}>💬 {t('personality.guidanceCta')}</Text>
          </TouchableOpacity>
        </View>

        {/* 九型人格卡片：进度 / 断点续答 / 结果 + 组合名 */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🔢 {t('personality.enneagramTitle')}</Text>
          {enneagram?.status === 'completed' && enneagram.result ? (
            <>
              <Text style={styles.enneagramResult}>
                {t('personality.enneagramResult', {
                  type: enneagram.result.enneagram_type,
                  label: enneagram.result.type_label,
                })}
              </Text>
              {!!enneagram.result.combo_name && (
                <Text style={styles.enneagramCombo}>
                  {t('personality.enneagramCombo', {
                    mbti: enneagram.result.combo_mbti,
                    type: enneagram.result.enneagram_type,
                    name: enneagram.result.combo_name,
                  })}
                </Text>
              )}
            </>
          ) : (
            <>
              <Text style={styles.assessmentDesc}>
                {enneagram?.status === 'in_progress'
                  ? t('personality.enneagramProgress', {
                      index: enneagram.current_index,
                      total: enneagram.total,
                    })
                  : t('personality.enneagramDesc')}
              </Text>
              {enneagram?.status === 'in_progress' && enneagram.total > 0 && (
                <View style={[styles.progressTrack, styles.cardProgress]}>
                  <View
                    style={[
                      styles.progressFill,
                      { width: `${(enneagram.current_index / enneagram.total) * 100}%` },
                    ]}
                  />
                </View>
              )}
              <TouchableOpacity
                style={[styles.assessmentBtn, ennAnswering && styles.refreshBtnDisabled]}
                onPress={onEnneagramEntry}
                disabled={ennAnswering}
              >
                {ennAnswering ? (
                  <ActivityIndicator color={Colors.bgPrimary} size="small" />
                ) : (
                  <Text style={styles.assessmentBtnText}>
                    {enneagram?.status === 'in_progress'
                      ? t('personality.enneagramResume')
                      : t('personality.enneagramStart')}
                  </Text>
                )}
              </TouchableOpacity>
            </>
          )}
        </View>

        {/* 完整画像（揭示/运用阶段或已有画像；后端自动生成，前端展示状态） */}
        {showProfile && (
          <View style={styles.typeCard}>
            <Text style={styles.typeEmoji}>🧬</Text>
            <Text style={styles.typeCode}>{profile?.mbti_type || '????'}</Text>
            <Text style={styles.typeLabel}>
              {profile?.mbti_label || t('explore.coldStartTitle')}
            </Text>
            <Text style={styles.version}>v{profile?.version || 1}</Text>
            {/* 九型维度（用户在个人页主动输入九型编号后展示） */}
            {!!profile?.enneagram_type && (
              <View style={styles.dimensionRow}>
                <Text style={styles.dimensionText}>
                  🔢 {t('personality.enneagramLabel')} · {profile.enneagram_type}
                </Text>
              </View>
            )}
          </View>
        )}

        {/* 性格名片入口（P1-F04：画像给自己看 / 名片给别人看） */}
        {!!profile?.mbti_type && (
          <TouchableOpacity style={styles.shareCardEntry} onPress={() => navigation.navigate('ShareCard')}>
            <Text style={styles.shareCardEntryText}>🃏 {t('share.cardEntry')}</Text>
            <Text style={styles.shareCardArrow}>›</Text>
          </TouchableOpacity>
        )}

        {/* AI 观察 */}
        {showProfile && !!profile?.observations?.length && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>🔍 {t('personality.observations')}</Text>
            {profile.observations.map((obs, i) => (
              <View key={i} style={styles.obsRow}>
                <Text style={styles.obsBullet}>•</Text>
                <Text style={styles.obsText}>{obs}</Text>
              </View>
            ))}
          </View>
        )}

        {/* 独特特质 */}
        {showProfile && !!profile?.unique_traits && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>💎 {t('personality.uniqueTraits')}</Text>
            <Text style={styles.traitText}>{profile.unique_traits}</Text>
          </View>
        )}

        {/* 手动补充测试（折叠入口，次级路径） */}
        {(!assessment || assessment.status !== 'completed') && (
          <View style={styles.card}>
            <TouchableOpacity
              style={styles.manualHeader}
              onPress={() => setManualExpanded((v) => !v)}
            >
              <Text style={styles.manualTitle}>✍️ {t('personality.manualTitle')}</Text>
              <Text style={styles.manualArrow}>{manualExpanded ? '⌄' : '›'}</Text>
            </TouchableOpacity>
            {manualExpanded && (
              <>
                <Text style={styles.assessmentDesc}>
                  {assessment?.status === 'in_progress'
                    ? t('personality.resumeHint')
                    : t('personality.assessmentDesc')}
                </Text>
                <TouchableOpacity
                  style={[styles.assessmentBtn, answering && styles.refreshBtnDisabled]}
                  onPress={onStartAssessment}
                  disabled={answering}
                >
                  {answering ? (
                    <ActivityIndicator color={Colors.bgPrimary} size="small" />
                  ) : (
                    <Text style={styles.assessmentBtnText}>
                      {assessment?.status === 'in_progress'
                        ? t('personality.assessmentResume', {
                            index: assessment.current_index + 1,
                            total: assessment.total,
                          })
                        : t('personality.assessmentStart')}
                    </Text>
                  )}
                </TouchableOpacity>
              </>
            )}
          </View>
        )}

        {/* Pro 组合报告（16 型 × 九型交叉解读） */}
        {!!profile?.mbti_type && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>🌌 {t('personality.comboTitle')}</Text>
            {combo ? (
              <>
                <Text style={styles.traitText}>{combo.content}</Text>
                {!!combo.enneagram_type && (
                  <Text style={styles.comboMeta}>
                    {t('personality.enneagramLabel')} · {combo.enneagram_type}
                  </Text>
                )}
              </>
            ) : (
              <>
                <Text style={styles.assessmentDesc}>{t('personality.comboDesc')}</Text>
                <TouchableOpacity
                  style={[styles.assessmentBtn, comboLoading && styles.refreshBtnDisabled]}
                  onPress={onComboReport}
                  disabled={comboLoading}
                >
                  {comboLoading ? (
                    <ActivityIndicator color={Colors.bgPrimary} size="small" />
                  ) : (
                    <Text style={styles.assessmentBtnText}>{t('personality.comboGenerate')}</Text>
                  )}
                </TouchableOpacity>
              </>
            )}
          </View>
        )}

        {/* 刷新 */}
        <TouchableOpacity
          style={[styles.refreshBtn, (refreshing || (profile?.refresh_quota_remaining ?? 0) <= 0) && styles.refreshBtnDisabled]}
          onPress={onRefresh}
          disabled={refreshing || (profile?.refresh_quota_remaining ?? 0) <= 0}
        >
          {refreshing ? (
            <ActivityIndicator color={Colors.bgPrimary} size="small" />
          ) : (
            <Text style={styles.refreshText}>
              {t('personality.refresh')} · {t('personality.quotaLeft', { count: profile?.refresh_quota_remaining ?? 0 })}
            </Text>
          )}
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: Colors.bgPrimary },
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  typeCard: {
    alignItems: 'center', padding: Spacing.xl, marginBottom: Spacing.md,
    backgroundColor: 'rgba(255,215,0,0.06)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl,
  },
  typeEmoji: { fontSize: 40, marginBottom: Spacing.sm },
  typeCode: { fontSize: 36, fontWeight: '700', color: Colors.gold400, letterSpacing: 4 },
  typeLabel: { fontSize: FontSize.xl, color: Colors.textSecondary, marginTop: 4 },
  version: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 8 },
  dimensionRow: { flexDirection: 'row', gap: Spacing.md, marginTop: Spacing.sm, flexWrap: 'wrap', justifyContent: 'center' },
  dimensionText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  shareCardEntry: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    backgroundColor: 'rgba(255,215,0,0.06)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  shareCardEntryText: { fontSize: FontSize.title, fontWeight: '600', color: Colors.gold400 },
  shareCardArrow: { fontSize: 20, color: Colors.gold400 },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  failBox: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: Spacing.md,
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    padding: Spacing.md, marginBottom: Spacing.md, backgroundColor: 'rgba(255,255,255,0.03)',
  },
  failText: { flex: 1, fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption },
  retryBtn: {
    paddingHorizontal: 16, paddingVertical: 6, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.12)', borderWidth: 1, borderColor: Colors.gold400,
  },
  retryText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.sm },
  coldStatus: { fontSize: FontSize.md, color: Colors.textSecondary, marginBottom: Spacing.md },
  stageCurrent: { fontSize: FontSize.title, fontWeight: '600', color: Colors.gold400, marginBottom: 4 },
  stageMeta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.sm },
  stageRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: Spacing.sm },
  stageItem: { alignItems: 'center', flex: 1 },
  stageDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: 'rgba(255,255,255,0.12)' },
  stageDotActive: { backgroundColor: Colors.gold400 },
  stageName: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 4 },
  stageNameActive: { color: Colors.gold400 },
  progressTrack: { height: 4, backgroundColor: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: Colors.gold400 },
  cardProgress: { marginBottom: Spacing.md },
  dimRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.md, marginBottom: Spacing.sm },
  dimLetter: { fontSize: FontSize.title, fontWeight: '700', color: Colors.textMuted, width: 16, textAlign: 'center' },
  dimLetterActive: { color: Colors.gold400 },
  dimTrack: {
    flex: 1, height: 6, backgroundColor: 'rgba(255,255,255,0.08)', borderRadius: 3, overflow: 'hidden',
  },
  dimFill: { height: '100%', backgroundColor: Colors.gold400, borderRadius: 3 },
  guidanceCard: {
    backgroundColor: 'rgba(99,102,241,0.08)', borderWidth: 1, borderColor: 'rgba(99,102,241,0.2)',
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  guidanceText: {
    fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: LineHeight.body, marginBottom: Spacing.md,
  },
  enneagramResult: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.gold400, marginBottom: 4 },
  enneagramCombo: { fontSize: FontSize.title, color: Colors.textSecondary, marginBottom: Spacing.sm },
  manualHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  manualTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textSecondary },
  manualArrow: { fontSize: 20, color: Colors.textTertiary },
  backRow: { paddingVertical: Spacing.sm, marginBottom: Spacing.sm },
  backText: { fontSize: FontSize.title, color: Colors.gold400, fontWeight: '600' },
  assessmentDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: Spacing.md, lineHeight: LineHeight.caption },
  assessmentBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 12, alignItems: 'center',
  },
  assessmentBtnText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
  assessmentProgress: { fontSize: FontSize.sm, color: Colors.textTertiary, marginBottom: Spacing.sm },
  questionBubble: {
    alignSelf: 'flex-start', backgroundColor: 'rgba(255,215,0,0.08)',
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.lg, borderTopLeftRadius: 4,
    padding: Spacing.md, marginTop: Spacing.md, marginBottom: Spacing.md,
  },
  questionText: { fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: LineHeight.body },
  optionBtn: {
    backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  optionText: { fontSize: FontSize.title, color: Colors.textPrimary },
  optionTextDim: { fontSize: FontSize.md, color: Colors.textTertiary },
  freeInputRow: { flexDirection: 'row', gap: Spacing.sm, marginTop: Spacing.sm, alignItems: 'flex-end' },
  freeInput: {
    flex: 1, minHeight: 40, maxHeight: 100,
    backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, paddingHorizontal: Spacing.md, paddingVertical: 10,
    color: Colors.textPrimary, fontSize: FontSize.md,
  },
  sendBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingHorizontal: Spacing.lg, paddingVertical: 10, alignItems: 'center', justifyContent: 'center',
  },
  sendBtnText: { fontSize: FontSize.md, fontWeight: '700', color: Colors.bgPrimary },
  comboMeta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.sm },
  obsRow: { flexDirection: 'row', gap: 8, marginBottom: 6 },
  obsBullet: { color: Colors.gold400, fontSize: FontSize.title },
  obsText: { flex: 1, fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: LineHeight.body },
  traitText: { fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: LineHeight.body },
  refreshBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 14, alignItems: 'center', marginTop: Spacing.sm,
  },
  refreshBtnDisabled: { opacity: 0.4 },
  refreshText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
});
