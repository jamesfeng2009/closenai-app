/**
 * 睡前复盘（PRD §5.16 场景 S1 / UI 原型 Screen 26 · tasks C1 的前端面）。
 *
 * 三步各一次交互：第 1 步用户写一句话（可跳过，零 LLM）→ 第 2 步 AI 回看今天写下
 * 的内容 → 第 3 步「留给明天」只观察不预测。第 3 步提交即完成并落成长时间线。
 *
 * 三条硬口径（都在 backend/services/bedtime_service.py 侧成立，这里只接线）：
 * - 引用依据（memory_refs）由服务端从当日真实记忆行取，前端不拼、不猜；
 * - 第 1 步命中危机触发词时服务端返回转介载荷且**不落复盘**，所以这里停在原地、
 *   输入框内容留给用户改，不推进步骤也不 Alert（系统弹窗读不完关怀文案）；
 * - 转介文案与热线由服务端按用户语言出稿，前端不自行翻译或另写一份危机话术。
 *
 * 40303（未同意 AI 数据共享）弹同意窗而不是报错：step2/step3 是 AI 生成内容，
 * 合规 5.1.2(i) 要求先取得同意，同意后自动续上原流程。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  getBedtimeToday, postBedtimeStep, setAiConsent, ApiError,
} from '../services/api';
import AIConsentModal from '../components/AIConsentModal';
import {
  isCrisisResponse, nextPhase, resumePhase, type BedtimePhase,
} from '../config/dailyCards';
import type {
  BedtimeCrisis, BedtimeDigest, BedtimeRecord, MemoryRef,
} from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode.AI_CONSENT_REQUIRED 对齐（ReflectScreen.tsx 同款处理）
const CODE_AI_CONSENT_REQUIRED = 40303;

export default function BedtimeReviewScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<BedtimePhase>('step1');
  const [loading, setLoading] = useState(true);
  // 提交中标志：三步都会发请求（step2/step3 耗 LLM），无反馈时用户会连点重复生成
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [consentVisible, setConsentVisible] = useState(false);
  const [note, setNote] = useState('');
  const [digest, setDigest] = useState<BedtimeDigest | null>(null);
  const [step2, setStep2] = useState<{ content: string; refs: MemoryRef[] } | null>(null);
  const [step3, setStep3] = useState<string | null>(null);
  const [crisis, setCrisis] = useState<BedtimeCrisis | null>(null);
  const [yesterday, setYesterday] = useState<BedtimeRecord | null>(null);
  const [refsOpen, setRefsOpen] = useState(false);
  // 每次进入只自动续跑一次生成请求（依赖数组里的 busy 复位会再次触发 effect）
  const generating = useRef<Set<number>>(new Set());

  /** 已落库的半程内容灌回视图状态（恢复时不重复展示空白第 2 步）。 */
  const hydrate = useCallback((record: BedtimeRecord | null) => {
    const steps = record?.steps ?? {};
    if (steps.step1?.text) setNote(steps.step1.text);
    if (steps.step2) setStep2({ content: steps.step2.content, refs: steps.step2.memory_refs ?? [] });
    if (steps.step3) setStep3(steps.step3.content);
  }, []);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      generating.current = new Set();
      const data = await getBedtimeToday();
      setDigest(data.digest);
      setYesterday(data.yesterday);
      hydrate(data.record);
      setPhase(resumePhase(data.record));
    } catch (e) {
      if (e instanceof ApiError && e.code === CODE_AI_CONSENT_REQUIRED) {
        // 未同意 AI 数据共享：弹同意窗而不是报错（step2/step3 是 AI 生成内容）
        setConsentVisible(true);
      } else {
        setError(e instanceof ApiError ? e.message : t('bedtime.loadFailed'));
      }
    } finally {
      setLoading(false);
    }
  }, [hydrate, t]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  /** 第 2 步（AI 回看今天）与第 3 步（留给明天）在进入该阶段时自动取一次。 */
  const runStep = useCallback(
    async (step: 2 | 3) => {
      if (generating.current.has(step)) return;
      generating.current.add(step);
      try {
        setBusy(true);
        setError(null);
        const res = await postBedtimeStep(step);
        if (isCrisisResponse(res)) return; // step2/3 不带输入，理论上不会走到；不推进即可
        if (res.step === 2) {
          setStep2({ content: res.content, refs: res.memory_refs ?? [] });
          setPhase(nextPhase('step2', res));
        } else if (res.step === 3) {
          setStep3(res.content);
          setPhase('done');
        }
      } catch (e) {
        if (e instanceof ApiError && e.code === CODE_AI_CONSENT_REQUIRED) {
          setConsentVisible(true);
        } else {
          setError(e instanceof ApiError ? e.message : t('bedtime.loadFailed'));
        }
      } finally {
        setBusy(false);
      }
    },
    [t]
  );

  useEffect(() => {
    if (loading || consentVisible) return;
    if (phase === 'step2' && !step2) runStep(2);
    if (phase === 'step3' && !step3) runStep(3);
  }, [phase, step2, step3, loading, consentVisible, runStep]);

  /** 第 1 步：提交一句话或跳过；服务端对同一天重复提交会覆盖上一句（改口是被支持的）。 */
  const submitStep1 = async (text: string | null) => {
    if (busy) return;
    try {
      setBusy(true);
      setError(null);
      const res = await postBedtimeStep(1, text);
      if (isCrisisResponse(res)) {
        // 拦截：不落复盘、不推进步骤，输入内容留着让用户自己改
        setCrisis(res.crisis);
        return;
      }
      setCrisis(null);
      // 出参按步分形，只有 step:1 带 digest；按步判定而不是硬当第 1 步的响应读
      if (res.step === 1) {
        setDigest(res.digest);
        setPhase(nextPhase('step1', res));
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === CODE_AI_CONSENT_REQUIRED) {
        setConsentVisible(true);
      } else {
        setError(e instanceof ApiError ? e.message : t('bedtime.loadFailed'));
      }
    } finally {
      setBusy(false);
    }
  };

  const onConsentAgree = async () => {
    try {
      await setAiConsent(true);
    } catch (e) {
      console.warn('Failed to save AI consent:', e);
    }
    setConsentVisible(false);
    load();
  };

  const onConsentDecline = async () => {
    try {
      await setAiConsent(false);
    } catch (e) {
      console.warn('Failed to save AI consent:', e);
    }
    setConsentVisible(false);
    navigation.goBack();
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* 窗口标签：固定时点与「零输入也能开始」是这一屏的定位，不是装饰 */}
      <View style={styles.pillRow}>
        <Text style={styles.pill}>{t('bedtime.windowTag')}</Text>
      </View>

      {!!error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={load} accessibilityRole="button">
            <Text style={styles.retryText}>{t('common.retry')}</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* 第 1 步 · 今天的一句话（可跳过）：原型三步同屏，所以这一步过了也留在屏上，
          收成一行引用（跳过则显「今天没写」），不给用户一个会消失的锚点 */}
      <View style={styles.stepBlock}>
        <Text style={styles.stepLabel}>{t('bedtime.step1Label')}</Text>
        <View style={styles.card}>
          {phase === 'step1' ? (
            <>
              <Text style={styles.stepBody}>
                {digest?.has_data
                  ? t('bedtime.digestWith', {
                      chats: digest.chat_count,
                      memories: digest.memory_count,
                    })
                  : t('bedtime.digestEmpty')}
              </Text>
              {!!digest?.mood && (
                <Text style={styles.stepMeta}>
                  {t('bedtime.digestMood', {
                    emoji: digest.mood.emoji,
                    label: digest.mood.label,
                  })}
                </Text>
              )}
              {!!digest?.insight && <Text style={styles.stepInsight}>{digest.insight}</Text>}
              <TextInput
                style={styles.input}
                value={note}
                onChangeText={setNote}
                placeholder={t('bedtime.step1Placeholder')}
                placeholderTextColor={Colors.textTertiary}
                multiline
                maxLength={1000}
                editable={!busy}
              />
              {crisis && (
                <View style={styles.crisisBox}>
                  <Text style={styles.crisisTitle}>{t('bedtime.crisisTitle')}</Text>
                  {/* 正文与热线来自服务端（已按用户语言出稿），这里逐字渲染不改写 */}
                  <Text style={styles.crisisBody}>{crisis.reply}</Text>
                  <Text style={styles.crisisHotline}>
                    {t('bedtime.crisisHotline', { hotline: crisis.hotline })}
                  </Text>
                  <TouchableOpacity
                    style={styles.crisisDismiss}
                    onPress={() => setCrisis(null)}
                    accessibilityRole="button"
                  >
                    <Text style={styles.crisisDismissText}>{t('bedtime.crisisBack')}</Text>
                  </TouchableOpacity>
                </View>
              )}
              <View style={styles.actions}>
                <TouchableOpacity
                  style={styles.ghostBtn}
                  disabled={busy}
                  onPress={() => submitStep1(null)}
                  accessibilityRole="button"
                >
                  <Text style={styles.ghostText}>{t('bedtime.skip')}</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.primaryBtn}
                  disabled={busy}
                  onPress={() => submitStep1(note.trim() || null)}
                  accessibilityRole="button"
                >
                  <Text style={styles.primaryText}>
                    {busy ? t('common.saving') : t('bedtime.next')}
                  </Text>
                </TouchableOpacity>
              </View>
            </>
          ) : (
            <Text style={styles.stepBody}>
              {note.trim() ? `“${note.trim()}”` : t('bedtime.skippedNote')}
            </Text>
          )}
        </View>
      </View>

      {/* 第 2 步 · 你今天值得记下的（服务端从当日真实内容取，带引用依据） */}
      {(phase === 'step2' || phase === 'step3' || phase === 'done') && (
        <View style={styles.stepBlock}>
          <Text style={styles.stepLabel}>{t('bedtime.step2Label')}</Text>
          <View style={styles.card}>
            {step2 ? (
              <>
                <Text style={styles.quoteBody}>{step2.content}</Text>
                {step2.refs.length > 0 && (
                  <>
                    <TouchableOpacity
                      onPress={() => setRefsOpen((v) => !v)}
                      accessibilityRole="button"
                    >
                      <Text style={styles.refsToggle}>
                        {t('bedtime.refsCount', { n: step2.refs.length })} {refsOpen ? '▾' : '▸'}
                      </Text>
                    </TouchableOpacity>
                    {refsOpen &&
                      step2.refs.map((r) => (
                        <Text key={r.memory_id} style={styles.refItem} numberOfLines={3}>
                          · {r.excerpt}
                        </Text>
                      ))}
                  </>
                )}
                <Text style={styles.stepMeta}>{t('bedtime.step2Note')}</Text>
              </>
            ) : (
              <Text style={styles.generating}>
                {error ? t('bedtime.step2Failed') : t('bedtime.generating')}
              </Text>
            )}
          </View>
          {phase === 'step2' && (
            <TouchableOpacity
              style={[styles.primaryBtn, styles.stepAdvance]}
              disabled={busy || !step2}
              onPress={() => setPhase('step3')}
              accessibilityRole="button"
            >
              <Text style={styles.primaryText}>
                {busy ? t('common.saving') : t('bedtime.next')}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      )}

      {/* 第 3 步 · 留给明天（只观察，不预测、不承诺） */}
      {(phase === 'step3' || phase === 'done') && (
        <View style={styles.stepBlock}>
          <Text style={styles.stepLabel}>{t('bedtime.step3Label')}</Text>
          <View style={styles.card}>
            {step3 ? (
              <>
                <Text style={styles.stepBody}>{step3}</Text>
                <Text style={styles.stepMeta}>{t('bedtime.step3Note')}</Text>
              </>
            ) : (
              <Text style={styles.generating}>
                {error ? t('bedtime.step3Failed') : t('bedtime.generating')}
              </Text>
            )}
          </View>
          {phase === 'step3' && (
            <TouchableOpacity
              style={[styles.primaryBtn, styles.stepAdvance]}
              disabled={busy || !step3}
              onPress={() => setPhase('done')}
              accessibilityRole="button"
            >
              <Text style={styles.primaryText}>
                {busy ? t('common.saving') : t('bedtime.finish')}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      )}

      {phase === 'done' && (
        <Text style={styles.doneFooter}>{t('bedtime.doneFooter')}</Text>
      )}

      {/* 昨日归档：23:59 后没完成的复盘会落到这里，不重复打扰但保留连续性 */}
      {!!yesterday && (
        <View style={styles.yesterdayBox}>
          <Text style={styles.stepMeta}>{t('bedtime.yesterdayTitle')}</Text>
          <Text style={styles.stepBody}>
            {yesterday.steps.step3?.content || yesterday.content || '—'}
          </Text>
        </View>
      )}

      <AIConsentModal
        visible={consentVisible}
        onAgree={onConsentAgree}
        onDecline={onConsentDecline}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  centered: {
    flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary,
  },
  content: { padding: Spacing.lg, paddingBottom: 60 },
  pillRow: { alignItems: 'center', marginBottom: Spacing.lg },
  pill: {
    fontSize: FontSize.md, color: Colors.gold400,
    backgroundColor: 'rgba(255,215,0,0.08)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.2)',
    paddingHorizontal: Spacing.md, paddingVertical: 4, borderRadius: BorderRadius.pill,
    lineHeight: LineHeight.caption, textAlign: 'center',
  },
  errorBox: {
    backgroundColor: 'rgba(239,68,68,0.08)', borderWidth: 1, borderColor: Colors.borderError,
    borderRadius: BorderRadius.md, padding: Spacing.md, marginBottom: Spacing.md,
  },
  errorText: {
    fontSize: FontSize.md, color: Colors.error, lineHeight: LineHeight.caption, marginBottom: Spacing.sm,
  },
  retryBtn: {
    alignSelf: 'flex-start', paddingVertical: 6, paddingHorizontal: 10,
    borderRadius: BorderRadius.sm, borderWidth: 1, borderColor: Colors.borderLight,
  },
  retryText: { fontSize: FontSize.md, color: Colors.textSecondary },
  stepBlock: { marginBottom: Spacing.md },
  // 步骤标签用紫色（与首页「理解的呈现」同色系），卡片体走原型 grad-card 同款低透明底
  stepLabel: {
    fontSize: FontSize.md, fontWeight: '600', color: '#A78BFA',
    marginBottom: 6, lineHeight: LineHeight.caption,
  },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: BorderRadius.xl, padding: Spacing.md,
  },
  stepBody: { fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: LineHeight.caption },
  stepMeta: {
    fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.sm,
    lineHeight: LineHeight.caption,
  },
  stepInsight: {
    fontSize: FontSize.md, color: Colors.textSecondary, marginTop: Spacing.sm,
    lineHeight: LineHeight.caption,
  },
  quoteBody: {
    fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: LineHeight.caption,
    borderLeftWidth: 3, borderLeftColor: 'rgba(255,215,0,0.5)', paddingLeft: Spacing.sm,
  },
  input: {
    minHeight: 76, marginTop: Spacing.md, padding: Spacing.md,
    backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: BorderRadius.md, color: Colors.textPrimary, fontSize: FontSize.xxl,
    lineHeight: LineHeight.small, textAlignVertical: 'top',
  },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: Spacing.sm, marginTop: Spacing.md },
  ghostBtn: {
    paddingVertical: 10, paddingHorizontal: 14, borderRadius: BorderRadius.lg,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.12)',
  },
  ghostText: { fontSize: FontSize.title, color: Colors.textTertiary },
  primaryBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 12, paddingHorizontal: 18, alignItems: 'center',
  },
  stepAdvance: { marginTop: Spacing.sm, alignSelf: 'flex-end' },
  primaryText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
  generating: {
    fontSize: FontSize.md, color: Colors.textTertiary, lineHeight: LineHeight.caption,
  },
  refsToggle: {
    fontSize: FontSize.md, color: '#A78BFA', marginTop: Spacing.sm, lineHeight: LineHeight.caption,
  },
  refItem: {
    fontSize: FontSize.sm, color: Colors.textTertiary, lineHeight: LineHeight.caption, marginTop: 4,
  },
  crisisBox: {
    marginTop: Spacing.md, backgroundColor: 'rgba(16,185,129,0.07)', borderWidth: 1,
    borderColor: 'rgba(16,185,129,0.3)', borderRadius: BorderRadius.md, padding: Spacing.md,
  },
  crisisTitle: {
    fontSize: FontSize.lg, fontWeight: '700', color: Colors.success,
    marginBottom: Spacing.sm, lineHeight: LineHeight.caption,
  },
  crisisBody: { fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: LineHeight.body },
  crisisHotline: {
    fontSize: FontSize.md, color: Colors.textPrimary, marginTop: Spacing.sm,
    lineHeight: LineHeight.caption,
  },
  crisisDismiss: { marginTop: Spacing.md, alignSelf: 'flex-start' },
  crisisDismissText: { fontSize: FontSize.md, color: Colors.textTertiary },
  doneFooter: {
    fontSize: FontSize.md, color: Colors.success, textAlign: 'center', marginTop: Spacing.sm,
    marginBottom: Spacing.lg, lineHeight: LineHeight.caption,
  },
  yesterdayBox: {
    backgroundColor: 'rgba(255,255,255,0.02)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: BorderRadius.md, padding: Spacing.md, marginTop: Spacing.lg,
  },
});
