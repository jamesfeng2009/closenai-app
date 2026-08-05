/**
 * 性格画像页面 (P0-F06)。
 *
 * 按 UI 原型 screen-personality 实现：
 * - 性格类型 + 标签（16 型人格，不使用 MBTI 商标）
 * - 冷启动进度（7 天五阶段：破冰/观察/探索/揭示/运用）
 * - 8 题对话式评估（快捷选项 + 自由输入，答完生成画像）
 * - 太阳星座 / 九型人格维度展示
 * - AI 观察列表 + 独特特质
 * - 刷新画像（配额限制：免费 3 次总计 / Pro 5 次每月）
 * - Pro 组合报告（16 型 × 星座 × 九型交叉解读，每月 1 份）
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert,
  TextInput, KeyboardAvoidingView, Platform,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import {
  getPersonalityProfile, refreshPersonality,
  getAssessment, startAssessment, answerAssessment, generateComboReport,
  ApiError,
} from '../services/api';
import type { AssessmentState, ComboReport, PersonalityProfile } from '../types';
import { useTranslation } from 'react-i18next';

const STAGE_KEYS = ['stageIntro', 'stageObserve', 'stageExplore', 'stageReveal', 'stageApply'];

export default function PersonalityScreen() {
  const { t } = useTranslation();
  const navigation = useNavigation<any>();
  const [profile, setProfile] = useState<PersonalityProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [assessment, setAssessment] = useState<AssessmentState | null>(null);
  const [answering, setAnswering] = useState(false);
  const [freeText, setFreeText] = useState('');
  const [combo, setCombo] = useState<ComboReport | null>(null);
  const [comboLoading, setComboLoading] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const [p, a] = await Promise.all([
          getPersonalityProfile(),
          getAssessment().catch(() => null),
        ]);
        setProfile(p);
        setAssessment(a);
      } catch (e) {
        console.warn('Failed to load personality:', e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const onRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      const res = await refreshPersonality();
      if (res.success && res.profile) {
        setProfile(res.profile);
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

  // ── 对话式评估 ──

  const onStartAssessment = async () => {
    if (answering) return;
    try {
      setAnswering(true);
      setAssessment(await startAssessment());
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.requestFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setAnswering(false);
    }
  };

  const submitAnswer = async (payload: { option: 'a' | 'b' | 'neutral' } | { answer: string }) => {
    if (answering) return;
    try {
      setAnswering(true);
      const res = await answerAssessment(payload);
      setFreeText('');
      if (res.completed) {
        if (res.profile) setProfile(res.profile);
        setAssessment((prev) =>
          prev ? { ...prev, status: 'completed', question: null } : prev,
        );
        if (res.message) Alert.alert(t('common.error'), res.message);
      } else if (res.next_question) {
        setAssessment((prev) =>
          prev
            ? { ...prev, current_index: res.next_question!.index, question: res.next_question }
            : prev,
        );
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.submitFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setAnswering(false);
    }
  };

  // ── Pro 组合报告 ──

  const onComboReport = async () => {
    if (comboLoading) return;
    try {
      setComboLoading(true);
      setCombo(await generateComboReport());
    } catch (e) {
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

  const progress = profile?.cold_start_progress ?? 1;
  const inColdStart = progress < 1;
  const stageIndex = Math.min(Math.floor(progress * STAGE_KEYS.length), STAGE_KEYS.length - 1);
  const question = assessment?.question ?? null;

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        {/* 类型卡片 */}
        <View style={styles.typeCard}>
          <Text style={styles.typeEmoji}>🧬</Text>
          <Text style={styles.typeCode}>{profile?.mbti_type || '????'}</Text>
          <Text style={styles.typeLabel}>
            {profile?.mbti_label || t('explore.coldStartTitle')}
          </Text>
          <Text style={styles.version}>v{profile?.version || 1}</Text>
          {/* 星座 / 九型维度（用户主动输入生日与九型编号后展示） */}
          {(profile?.zodiac || profile?.enneagram_type) && (
            <View style={styles.dimensionRow}>
              {!!profile?.zodiac && (
                <Text style={styles.dimensionText}>
                  ♈ {t('personality.zodiacLabel')} · {t(`zodiac.${profile.zodiac}`)}
                </Text>
              )}
              {!!profile?.enneagram_type && (
                <Text style={styles.dimensionText}>
                  🔢 {t('personality.enneagramLabel')} · {profile.enneagram_type}
                </Text>
              )}
            </View>
          )}
        </View>

        {/* 性格名片入口（P1-F04：画像给自己看 / 名片给别人看） */}
        {!!profile?.mbti_type && (
          <TouchableOpacity style={styles.shareCardEntry} onPress={() => navigation.navigate('ShareCard')}>
            <Text style={styles.shareCardEntryText}>🃏 {t('share.cardEntry')}</Text>
            <Text style={styles.shareCardArrow}>›</Text>
          </TouchableOpacity>
        )}
        {/* 冷启动进度 */}
        {inColdStart && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>❄️ {t('explore.coldStartTitle')}</Text>
            <Text style={styles.coldStatus}>
              {t('explore.coldStartStatus', { day: Math.ceil(progress * 7) || 1 })}
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
          </View>
        )}

        {/* 8 题对话式评估（未完成时展示入口/答题区） */}
        {assessment && assessment.status !== 'completed' && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>💬 {t('personality.assessmentTitle')}</Text>
            {!question ? (
              <>
                <Text style={styles.assessmentDesc}>{t('personality.assessmentDesc')}</Text>
                <TouchableOpacity
                  style={[styles.assessmentBtn, answering && styles.refreshBtnDisabled]}
                  onPress={onStartAssessment}
                  disabled={answering}
                >
                  {answering ? (
                    <ActivityIndicator color={Colors.bgPrimary} size="small" />
                  ) : (
                    <Text style={styles.assessmentBtnText}>
                      {assessment.status === 'in_progress'
                        ? t('personality.assessmentResume', {
                            index: assessment.current_index + 1,
                            total: assessment.total,
                          })
                        : t('personality.assessmentStart')}
                    </Text>
                  )}
                </TouchableOpacity>
              </>
            ) : (
              <>
                <Text style={styles.assessmentProgress}>
                  {t('personality.assessmentProgress', {
                    index: question.index + 1,
                    total: question.total,
                  })}
                </Text>
                {/* 问题气泡（对话式） */}
                <View style={styles.questionBubble}>
                  <Text style={styles.questionText}>{question.text}</Text>
                </View>
                <TouchableOpacity
                  style={styles.optionBtn}
                  onPress={() => submitAnswer({ option: 'a' })}
                  disabled={answering}
                >
                  <Text style={styles.optionText}>{question.options.a}</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.optionBtn}
                  onPress={() => submitAnswer({ option: 'b' })}
                  disabled={answering}
                >
                  <Text style={styles.optionText}>{question.options.b}</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.optionBtn}
                  onPress={() => submitAnswer({ option: 'neutral' })}
                  disabled={answering}
                >
                  <Text style={styles.optionTextDim}>{question.options.neutral}</Text>
                </TouchableOpacity>
                {/* 自由输入（PRD：用户自己打字，AI 理解语义后归类维度权重） */}
                <View style={styles.freeInputRow}>
                  <TextInput
                    style={styles.freeInput}
                    value={freeText}
                    onChangeText={setFreeText}
                    placeholder={t('personality.assessmentInputPlaceholder')}
                    placeholderTextColor={Colors.textMuted}
                    editable={!answering}
                    multiline
                  />
                  <TouchableOpacity
                    style={[styles.sendBtn, (answering || !freeText.trim()) && styles.refreshBtnDisabled]}
                    onPress={() => submitAnswer({ answer: freeText.trim() })}
                    disabled={answering || !freeText.trim()}
                  >
                    {answering ? (
                      <ActivityIndicator color={Colors.bgPrimary} size="small" />
                    ) : (
                      <Text style={styles.sendBtnText}>{t('personality.assessmentSend')}</Text>
                    )}
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        )}

        {/* AI 观察 */}
        {!!profile?.observations?.length && (
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
        {!!profile?.unique_traits && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>💎 {t('personality.uniqueTraits')}</Text>
            <Text style={styles.traitText}>{profile.unique_traits}</Text>
          </View>
        )}

        {/* Pro 组合报告（16 型 × 星座 × 九型交叉解读） */}
        {!!profile?.mbti_type && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>🌌 {t('personality.comboTitle')}</Text>
            {combo ? (
              <>
                <Text style={styles.traitText}>{combo.content}</Text>
                {!!combo.zodiac && (
                  <Text style={styles.comboMeta}>
                    {t(`zodiac.${combo.zodiac}`)}
                    {combo.enneagram_type ? ` · ${t('personality.enneagramLabel')} ${combo.enneagram_type}` : ''}
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
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.sm },
  coldStatus: { fontSize: FontSize.md, color: Colors.textSecondary, marginBottom: Spacing.md },
  stageRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: Spacing.sm },
  stageItem: { alignItems: 'center', flex: 1 },
  stageDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: 'rgba(255,255,255,0.12)' },
  stageDotActive: { backgroundColor: Colors.gold400 },
  stageName: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 4 },
  stageNameActive: { color: Colors.gold400 },
  progressTrack: { height: 4, backgroundColor: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: Colors.gold400 },
  assessmentDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: Spacing.md, lineHeight: 1.5 },
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
    padding: Spacing.md, marginBottom: Spacing.md,
  },
  questionText: { fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: 1.5 },
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
  obsText: { flex: 1, fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 1.5 },
  traitText: { fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 1.6 },
  refreshBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 14, alignItems: 'center', marginTop: Spacing.sm,
  },
  refreshBtnDisabled: { opacity: 0.4 },
  refreshText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
});
