/**
 * 三层次情绪分析面板（KG-28）。
 *
 * 一屏做三件事：选深度 → 读结论 → 处理结论（看原话／说「这不像我」／存下来）。
 * 判定不在这里（在 `services/moodAnalysis.ts`，那份能被 jest 直接测），这里只负责：
 *
 * 1. **降级与危机各是一种呈现，不是同一种**。`safety_status=crisis` 时服务端给的
 *    那段话就是全部内容——这里不再挂「存为反思」「这不像我」：把危机热线文案存成
 *    一条成长记录，用户回看时看到的是 AI 的话被标成自己的反思。
 * 2. **引用只显用户自己的原话**（`evidenceNote` 就是这句话），且展平在服务端：
 *    面板不改写 excerpt，也不把它拼进任何别的句子。
 * 3. **一次意图一个 `request_id`**：重试带同一个键，服务端 10 分钟幂等才不会让免费档
 *    那唯一一次被双击吃掉；换深度才换新键。
 *
 * 埋点只带 `feature/action/result/reason/duration_ms/count` 这几个白名单键——
 * 用户正文、focus、excerpt 一个字都不上送（`services/analytics.ts` 的白名单是第二道）。
 */
import React, { useCallback, useMemo, useState } from 'react';
import {
  ActivityIndicator, Modal, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { analyzeMood, ApiError, saveReflection } from '../services/api';
import { track } from '../services/analytics';
import { newTraceId } from '../utils/traceId';
import type { MoodAnalysisDepth, MoodAnalysisEvidence, MoodAnalysisResponse } from '../types';
import {
  DEPTH_ORDER,
  MOOD_ANALYSIS_EVENTS,
  analysisTrackProps,
  canRetryAfter,
  correctionContent,
  errorKindForCode,
  quotaRemaining,
  resultOf,
  savableAnalysisText,
  type MoodAnalysisErrorKind,
} from '../services/moodAnalysis';

interface Props {
  visible: boolean;
  onClose: () => void;
  /** 40303：本面板不套第二层 Modal（RN 里叠 Modal 在 Android 上不可靠），交给宿主去设置页。 */
  onOpenSettings: () => void;
  /** 证据出处的全集在记忆列表；这里只展开服务端给的那几段。 */
  onOpenMemories: () => void;
}

const SOURCE_EMOJI: Record<MoodAnalysisEvidence['source_type'], string> = {
  memory: '🧠',
  reflection: '✍️',
};

export default function MoodAnalysisSheet({ visible, onClose, onOpenSettings, onOpenMemories }: Props) {
  const { t } = useTranslation();
  const [depth, setDepth] = useState<MoodAnalysisDepth>('light');
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MoodAnalysisResponse | null>(null);
  const [errorKind, setErrorKind] = useState<MoodAnalysisErrorKind | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [openEvidenceId, setOpenEvidenceId] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const [correctionText, setCorrectionText] = useState('');
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);

  const remaining = useMemo(() => quotaRemaining(result?.quota), [result]);

  const run = useCallback(
    async (chosen: MoodAnalysisDepth, idempotencyKey: string | null) => {
      // 同一次意图复用同一个键（重试带回去），新意图才换新的
      const requestIdUsed = idempotencyKey ?? newTraceId();
      if (!idempotencyKey) setRequestId(requestIdUsed);
      const startedAt = Date.now();
      track(MOOD_ANALYSIS_EVENTS.requested, analysisTrackProps({ depth: chosen }));
      setBusy(true);
      setErrorKind(null);
      setSavedNote(null);
      setSaveFailed(false);
      setCorrecting(false);
      setOpenEvidenceId(null);
      try {
        const response = await analyzeMood({
          depth: chosen,
          focus: focus.trim() || null,
          request_id: requestIdUsed,
        });
        setResult(response);
        track(
          MOOD_ANALYSIS_EVENTS.completed,
          analysisTrackProps({
            depth: chosen,
            result: resultOf(response.safety_status),
            durationMs: Date.now() - startedAt,
          })
        );
      } catch (error) {
        const kind = errorKindForCode((error as ApiError).code);
        setResult(null);
        setErrorKind(kind);
        track(
          MOOD_ANALYSIS_EVENTS.completed,
          analysisTrackProps({
            depth: chosen,
            result: 'failed',
            reason: kind,
            durationMs: Date.now() - startedAt,
          })
        );
      } finally {
        setBusy(false);
      }
    },
    [focus]
  );

  const pickDepth = (next: MoodAnalysisDepth) => {
    setDepth(next);
    // 换深度＝换一次意图：旧键不再复用，否则服务端会把新深度判成同一次请求的重放
    setRequestId(null);
  };

  const openEvidence = (item: MoodAnalysisEvidence) => {
    setOpenEvidenceId(item.source_id);
    track(
      MOOD_ANALYSIS_EVENTS.evidenceOpened,
      analysisTrackProps({ depth, count: result?.evidence.length ?? 0 })
    );
  };

  /** 纠正与「存为反思」走同一个函数：`origin` 决定这条反思被标成哪一档来源。 */
  const persist = async (content: string, event: string) => {
    setSaving(true);
    setSaveFailed(false);
    try {
      await saveReflection(content, null, { source: 'mood_analysis' });
      track(event, analysisTrackProps({ depth }));
      setSavedNote(t('moodAnalysis.saved'));
      setCorrecting(false);
      setCorrectionText('');
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const saveCorrection = () => {
    const content = correctionContent(t('moodAnalysis.correctionPrefix'), correctionText);
    if (!content) return; // 一个字都没写：不存「这不像我：」这种空壳记录
    track(MOOD_ANALYSIS_EVENTS.corrected, analysisTrackProps({ depth }));
    void persist(content, MOOD_ANALYSIS_EVENTS.saved);
  };

  const saveSummary = () => {
    const content = savableAnalysisText(result?.summary);
    if (!content) return;
    void persist(content, MOOD_ANALYSIS_EVENTS.saved);
  };

  const crisis = result?.safety_status === 'crisis';
  const canSave = !!result && !crisis && !busy;
  const correctionDraft = correctionContent(t('moodAnalysis.correctionPrefix'), correctionText);

  const errorText: Record<MoodAnalysisErrorKind, string> = {
    invalid: t('moodAnalysis.failed'),
    consent: t('moodAnalysis.needConsent'),
    quota: t('moodAnalysis.quotaDone'),
    no_checkin: t('moodAnalysis.needCheckin'),
    network: t('moodAnalysis.failed'),
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>{t('moodAnalysis.sheetTitle')}</Text>
            <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel={t('common.close')}>
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.subtitle}>{t('moodAnalysis.sheetSubtitle')}</Text>

          <View style={styles.depthRow}>
            {DEPTH_ORDER.map((key) => {
              const active = depth === key;
              return (
                <TouchableOpacity
                  key={key}
                  style={[styles.depthCard, active && styles.depthCardActive]}
                  onPress={() => pickDepth(key)}
                  disabled={busy}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active, disabled: busy }}
                >
                  <Text style={[styles.depthName, active && styles.depthNameActive]}>
                    {t(`moodAnalysis.${key}`)}
                  </Text>
                  <Text style={styles.depthDesc}>{t(`moodAnalysis.${key}Desc`)}</Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <TextInput
            style={styles.focusInput}
            value={focus}
            onChangeText={setFocus}
            placeholder={t('moodAnalysis.focus')}
            placeholderTextColor={Colors.textTertiary}
            maxLength={200}
            multiline
            accessibilityLabel={t('moodAnalysis.focus')}
            editable={!busy}
          />

          <TouchableOpacity
            style={styles.startBtn}
            onPress={() => void run(depth, requestId)}
            disabled={busy}
            accessibilityRole="button"
            accessibilityState={{ disabled: busy }}
          >
            <Text style={styles.startText}>{busy ? t('moodAnalysis.analyzing') : t('moodAnalysis.start')}</Text>
          </TouchableOpacity>

          {busy && (
            <View style={styles.state}>
              <ActivityIndicator color={Colors.gold400} />
            </View>
          )}

          {!busy && errorKind && (
            <View style={styles.errorBlock}>
              <Text style={styles.errorText}>{errorText[errorKind]}</Text>
              {canRetryAfter(errorKind) && (
                <TouchableOpacity style={styles.ghostBtn} onPress={() => void run(depth, requestId)} accessibilityRole="button">
                  <Text style={styles.ghostText}>{t('common.retry')}</Text>
                </TouchableOpacity>
              )}
              {errorKind === 'consent' && (
                <TouchableOpacity style={styles.ghostBtn} onPress={onOpenSettings} accessibilityRole="button">
                  <Text style={styles.ghostText}>{t('push.openSettings')}</Text>
                </TouchableOpacity>
              )}
            </View>
          )}

          {!busy && !errorKind && result && (
            <ScrollView style={styles.resultScroll} accessibilityLabel={t('moodAnalysis.sheetTitle')}>
              <Text style={styles.summary}>{result.summary}</Text>

              {result.safety_status === 'degraded' && (
                <Text style={styles.degradedNote}>{t('moodAnalysis.degradedNote')}</Text>
              )}

              {!!result.pattern && (
                <View style={styles.pattern}>
                  <Text style={styles.sectionTitle}>{t('moodAnalysis.patternTitle')}</Text>
                  <Text style={styles.patternText}>{result.pattern.description}</Text>
                  <Text style={styles.confidence}>
                    {t('moodAnalysis.confidence', { percent: Math.round(result.pattern.confidence * 100) })}
                  </Text>
                </View>
              )}

              {result.alternatives.length > 0 && (
                <View style={styles.block}>
                  <Text style={styles.sectionTitle}>{t('moodAnalysis.alternativesTitle')}</Text>
                  {result.alternatives.map((alt, i) => (
                    <Text key={`${result.analysis_id}-alt-${i}`} style={styles.altText}>
                      · {alt}
                    </Text>
                  ))}
                </View>
              )}

              {result.evidence.length > 0 && (
                <View style={styles.block}>
                  <Text style={styles.sectionTitle}>{t('moodAnalysis.evidenceTitle')}</Text>
                  <Text style={styles.evidenceNote}>{t('moodAnalysis.evidenceNote')}</Text>
                  {result.evidence.map((ev) => (
                    <TouchableOpacity
                      key={ev.source_id}
                      style={[styles.evidenceCard, openEvidenceId === ev.source_id && styles.evidenceCardOpen]}
                      onPress={() => openEvidence(ev)}
                      accessibilityRole="button"
                      accessibilityState={{ expanded: openEvidenceId === ev.source_id }}
                    >
                      <Text style={styles.evidenceExcerpt}>
                        {SOURCE_EMOJI[ev.source_type]} {openEvidenceId === ev.source_id ? ev.excerpt : `${ev.excerpt.slice(0, 60)}${ev.excerpt.length > 60 ? '…' : ''}`}
                      </Text>
                      <Text style={styles.evidenceDate}>{ev.occurred_at.slice(0, 10)}</Text>
                    </TouchableOpacity>
                  ))}
                  <TouchableOpacity style={styles.ghostBtn} onPress={onOpenMemories} accessibilityRole="button">
                    <Text style={styles.ghostText}>{t('moodAnalysis.viewSources')}</Text>
                  </TouchableOpacity>
                </View>
              )}

              {!!result.next_step && result.next_step.action !== 'none' && (
                <Text style={styles.nextStep}>→ {result.next_step.text}</Text>
              )}

              {!crisis && remaining !== null && (
                <Text style={styles.quota}>{t('moodAnalysis.remaining', { n: remaining })}</Text>
              )}

              {!crisis && (
                <View style={styles.actionRow}>
                  <TouchableOpacity style={styles.ghostBtn} onPress={() => setCorrecting((v) => !v)} accessibilityRole="button">
                    <Text style={styles.ghostText}>{t('moodAnalysis.notLikeMe')}</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.ghostBtn, (!canSave || !savableAnalysisText(result.summary)) && styles.btnDim]}
                    onPress={saveSummary}
                    disabled={!canSave || !savableAnalysisText(result.summary) || saving}
                    accessibilityRole="button"
                    accessibilityState={{ disabled: !canSave || saving }}
                  >
                    <Text style={styles.ghostText}>
                      {saving ? t('common.saving') : t('moodAnalysis.saveAsReflection')}
                    </Text>
                  </TouchableOpacity>
                </View>
              )}

              {correcting && (
                <View style={styles.correction}>
                  <Text style={styles.correctionHint}>{t('moodAnalysis.correctionHint')}</Text>
                  <TextInput
                    style={styles.focusInput}
                    value={correctionText}
                    onChangeText={setCorrectionText}
                    placeholder={`${t('moodAnalysis.correctionPrefix')}`}
                    placeholderTextColor={Colors.textTertiary}
                    maxLength={2000}
                    multiline
                    accessibilityLabel={t('moodAnalysis.correctionHint')}
                    editable={!saving}
                  />
                  <TouchableOpacity
                    style={[styles.ghostBtn, !correctionDraft && styles.btnDim]}
                    onPress={saveCorrection}
                    disabled={!correctionDraft || saving}
                    accessibilityRole="button"
                    accessibilityState={{ disabled: !correctionDraft || saving }}
                  >
                    <Text style={styles.ghostText}>
                      {saving ? t('common.saving') : t('moodAnalysis.saveAsReflection')}
                    </Text>
                  </TouchableOpacity>
                </View>
              )}

              {saveFailed && <Text style={styles.errorText}>{t('common.loadFailed')}</Text>}
              {!!savedNote && <Text style={styles.savedNote}>{savedNote}</Text>}
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: Colors.bgPrimary,
    borderTopLeftRadius: BorderRadius.xl, borderTopRightRadius: BorderRadius.xl,
    padding: Spacing.lg, paddingTop: Spacing.xl, maxHeight: '86%', minHeight: 300,
    borderWidth: 1, borderColor: Colors.borderLight,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary },
  closeText: { fontSize: FontSize.xl, color: Colors.textTertiary, paddingHorizontal: Spacing.sm },
  subtitle: {
    fontSize: FontSize.md, color: Colors.textTertiary, lineHeight: LineHeight.body,
    marginTop: Spacing.xs, marginBottom: Spacing.md,
  },
  depthRow: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.md },
  depthCard: {
    flex: 1, borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    padding: Spacing.sm, backgroundColor: Colors.bgSecondary,
  },
  depthCardActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.08)' },
  depthName: { fontSize: FontSize.md, fontWeight: '700', color: Colors.textSecondary },
  depthNameActive: { color: Colors.gold400 },
  depthDesc: { fontSize: FontSize.sm, color: Colors.textTertiary, lineHeight: LineHeight.body, marginTop: 4 },
  focusInput: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    padding: Spacing.sm, minHeight: 56, color: Colors.textPrimary, fontSize: FontSize.md,
    lineHeight: LineHeight.body, backgroundColor: Colors.bgSecondary, textAlignVertical: 'top',
  },
  startBtn: {
    marginTop: Spacing.md, borderRadius: BorderRadius.pill, borderWidth: 1, borderColor: Colors.gold400,
    paddingVertical: 10, alignItems: 'center',
  },
  startText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '700' },
  state: { paddingVertical: Spacing.lg, alignItems: 'center' },
  errorBlock: { marginTop: Spacing.md, gap: Spacing.sm },
  errorText: { fontSize: FontSize.md, color: Colors.error, lineHeight: LineHeight.body },
  ghostBtn: {
    alignSelf: 'flex-start', marginTop: Spacing.sm,
    borderRadius: BorderRadius.pill, borderWidth: 1, borderColor: Colors.borderLight,
    paddingHorizontal: Spacing.md, paddingVertical: 6,
  },
  ghostText: { fontSize: FontSize.md, color: Colors.textSecondary },
  btnDim: { opacity: 0.4 },
  // RN 默认 flexShrink=0：不显式允许收缩，ScrollView 就不会滚（同 AIConsentModal.tsx:63）
  resultScroll: { flexGrow: 1, flexShrink: 1, marginTop: Spacing.md },
  summary: { fontSize: FontSize.lg, color: Colors.textPrimary, lineHeight: LineHeight.body },
  degradedNote: {
    fontSize: FontSize.sm, color: Colors.warning, lineHeight: LineHeight.body, marginTop: Spacing.sm,
  },
  block: { marginTop: Spacing.md },
  pattern: {
    marginTop: Spacing.md, borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, backgroundColor: Colors.bgSecondary,
  },
  sectionTitle: {
    fontSize: FontSize.md, fontWeight: '700', color: Colors.gold400, marginBottom: Spacing.xs,
  },
  patternText: { fontSize: FontSize.md, color: Colors.textPrimary, lineHeight: LineHeight.body },
  confidence: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
  altText: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.body, marginTop: 4 },
  evidenceNote: { fontSize: FontSize.sm, color: Colors.textTertiary, lineHeight: LineHeight.body },
  evidenceCard: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    padding: Spacing.sm, marginTop: Spacing.sm, backgroundColor: Colors.bgElevated,
  },
  evidenceCardOpen: { borderColor: Colors.gold400 },
  evidenceExcerpt: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.body },
  evidenceDate: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
  nextStep: {
    fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.body, marginTop: Spacing.md,
  },
  quota: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.md },
  actionRow: { flexDirection: 'row', gap: Spacing.md, marginTop: Spacing.sm },
  correction: { marginTop: Spacing.sm, gap: Spacing.xs },
  correctionHint: { fontSize: FontSize.sm, color: Colors.textTertiary, lineHeight: LineHeight.body },
  savedNote: {
    fontSize: FontSize.md, color: Colors.success, lineHeight: LineHeight.body, marginTop: Spacing.md,
  },
});
