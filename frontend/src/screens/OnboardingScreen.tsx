/**
 * 新用户引导流程 (P1-F05)。
 *
 * 成长优先 · 3 步 · 零占星信号：
 *   Step 1 — Welcome：展示 3 个核心成长卡片，主按钮「开始记录」，
 *            次级入口「先看个例子」→ 页内渲染 M5 演示档案（只读、零请求）
 *   Step 2 — 性格类型（可选）：16 型选择 / 跳过
 *   Step 3 — 三个快问（P1-B01 性格初读卡输入）：3 题 × 3 选项 / 跳过
 *
 * 演示档案不占路由：本屏渲染在 NavigationContainer 之外（App.tsx），
 * navigation.navigate 在这里根本不存在，所以用 showDemo 局部状态切页面。
 * CTA 只回到 Step 2，不直接 finish()——跳过 Step 3 会打断 PRD §5.19.2
 * 要求的「onboarding → 初读卡生成率 100%」。
 *
 * Step 3 提交后：游客态只能把答案暂存本地（无 token 调 API 必失败），
 * 登录/注册成功后由 AuthContext 补调 POST /onboarding/first-read 生成初读卡。
 * 完成后落地成长首页（Growth，第一位 Tab），不是对话页。
 * AI 不作为主体出现，底部声明强调「成长追踪」定位。
 */
import React, { useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, SafeAreaView, Alert,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { savePendingFirstRead } from '../services/firstRead';
import { track } from '../services/analytics';
import DemoArchiveScreen from './DemoArchiveScreen';
import type { FirstReadAnswer } from '../types';

const ONBOARDED_KEY = '@closenai/onboarded';
// 游客期选择的 MBTI 暂存 key（与 AuthContext 登录后补写逻辑共用）
const PENDING_MBTI_KEY = '@closenai/pending_mbti';

// 16 型 MBTI
const MBTI_TYPES = [
  'INTJ', 'INTP', 'ENTJ', 'ENTP',
  'INFJ', 'INFP', 'ENFJ', 'ENFP',
  'ISTJ', 'ISFJ', 'ESTJ', 'ESFJ',
  'ISTP', 'ISFP', 'ESTP', 'ESFP',
];

// 快问 i18n 键 → 后端模板题目 id（backend/data/first_read/{zh,en}.json）
// 选项 id 必须与模板一致：后端按 id 取断言槽位，因此各语言可自由翻译选项文案
const QUICK_QUESTIONS: {
  id: FirstReadAnswer['q'];
  titleKey: string;
  options: { id: string; key: string }[];
}[] = [
  {
    id: 'conflict',
    titleKey: 'onboarding.q1Title',
    options: [
      { id: 'speak_now', key: 'onboarding.q1Opt1' },
      { id: 'hold_in', key: 'onboarding.q1Opt2' },
      { id: 'step_back', key: 'onboarding.q1Opt3' },
    ],
  },
  {
    id: 'uncertainty',
    titleKey: 'onboarding.q2Title',
    options: [
      { id: 'keep_busy', key: 'onboarding.q2Opt1' },
      { id: 'list_out', key: 'onboarding.q2Opt2' },
      { id: 'talk_out', key: 'onboarding.q2Opt3' },
    ],
  },
  {
    id: 'night',
    titleKey: 'onboarding.q3Title',
    options: [
      { id: 'slow_down', key: 'onboarding.q3Opt1' },
      { id: 'replay', key: 'onboarding.q3Opt2' },
      { id: 'sharpest', key: 'onboarding.q3Opt3' },
    ],
  },
];

/**
 * 三步进度点。三屏是串行流程，没有进度指示时用户不知道还剩几步、也无处后退。
 * 计数用「1/3」这种数字形式，不需要文案。
 */
function StepDots({ step }: { step: 1 | 2 | 3 }) {
  return (
    <View style={styles.dotsRow}>
      <View style={styles.dotsGroup}>
        {[1, 2, 3].map((n) => (
          <View key={n} style={[styles.dot, n <= step && styles.dotActive]} />
        ))}
      </View>
      <Text style={styles.dotsCount}>{`${step}/3`}</Text>
    </View>
  );
}

/** 步骤内后退：Step1 是介绍页，Step2/3 各退一步（原来只能一路向前）。 */
function StepBack({ onPress, label }: { onPress: () => void; label: string }) {
  return (
    <TouchableOpacity style={styles.backBtn} onPress={onPress}>
      <Text style={styles.backText}>‹ {label}</Text>
    </TouchableOpacity>
  );
}

export default function OnboardingScreen({ onComplete }: { onComplete: () => void }) {
  const { t } = useTranslation();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  // 题目 id → 选项 id（未答的题目不进 answers）
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // 演示档案（M5）：引导阶段是游客、无 token，任何 api 调用都会 401，
  // 所以用组件内局部状态渲染只读示例，而不是注册一条新路由。
  const [showDemo, setShowDemo] = useState(false);

  const openDemo = () => {
    track('demo_archive_opened', { screen: 'onboarding', feature: 'demo_archive', action: 'open' });
    setShowDemo(true);
  };

  const [finishing, setFinishing] = useState(false);

  const finish = async (withType: string | null) => {
    if (finishing) return; // 单飞：写入是异步的，连点会重复落库/重复进主界面
    setFinishing(true);
    try {
      // 保存引导完成标记：本项失败不阻断后面的答案暂存（标记可由下次冷启动重写）
      try {
        await AsyncStorage.setItem(ONBOARDED_KEY, '1');
      } catch {
        // ignore
      }
      // 若用户选择了已知性格类型，先暂存本地（引导阶段必为游客，无 token 调 API 必失败）；
      // 登录/注册成功后由 AuthContext 读取该 key 补调 updateUserSettings 并清除
      if (withType) {
        try {
          await AsyncStorage.setItem(PENDING_MBTI_KEY, withType);
        } catch {
          // ignore
        }
      }
      // 快问答案同样暂存：AuthContext 登录后补调 POST /onboarding/first-read。
      // 空答案也要写入——PRD §5.19.2 要求 onboarding → 初读卡生成率 100%（后端 filler 兜底）
      const payload: FirstReadAnswer[] = QUICK_QUESTIONS
        .filter((q) => answers[q.id])
        .map((q) => ({ q: q.id, option: answers[q.id] }));
      await savePendingFirstRead(payload, withType);
      onComplete();
    } catch (e) {
      // 原来整条链路没有兜底：savePendingFirstRead 抛错就是未捕获 Promise rejection，
      // 用户停在第 3 步且不知道发生了什么。失败要提示并保持按钮可点。
      console.warn('Failed to finish onboarding:', e);
      Alert.alert(t('common.error'), t('onboarding.finishFailed'));
    } finally {
      setFinishing(false);
    }
  };

  const features = [
    { icon: '📝', title: t('onboarding.step1Feature1Title'), desc: t('onboarding.step1Feature1Desc') },
    { icon: '📈', title: t('onboarding.step1Feature2Title'), desc: t('onboarding.step1Feature2Desc') },
    { icon: '🌱', title: t('onboarding.step1Feature3Title'), desc: t('onboarding.step1Feature3Desc') },
  ];

  if (step === 1 && showDemo) {
    return <DemoArchiveScreen onClose={() => setShowDemo(false)} />;
  }

  if (step === 1) {
    return (
      <SafeAreaView style={styles.container}>
        <ScrollView contentContainerStyle={styles.content}>
          <StepDots step={1} />
          <Text style={styles.tagline}>{t('onboarding.step1Tagline')}</Text>

          <View style={styles.featureList}>
            {features.map((f) => (
              <View key={f.title} style={styles.featureCard}>
                <Text style={styles.featureIcon}>{f.icon}</Text>
                <View style={styles.featureTextBlock}>
                  <Text style={styles.featureTitle}>{f.title}</Text>
                  <Text style={styles.featureDesc}>{f.desc}</Text>
                </View>
              </View>
            ))}
          </View>

          <TouchableOpacity style={styles.primaryBtn} onPress={() => setStep(2)}>
            <Text style={styles.primaryBtnText}>{t('onboarding.startRecording')}</Text>
          </TouchableOpacity>

          {/* 演示档案入口（P1-1）：次级样式，不抢「开始记录」主 CTA */}
          <TouchableOpacity style={styles.demoEntry} onPress={openDemo}>
            <Text style={styles.demoEntryText}>{t('onboarding.demoEntry')}</Text>
          </TouchableOpacity>

          <Text style={styles.disclaimer}>{t('onboarding.disclaimer')}</Text>
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (step === 2) {
    return (
      <SafeAreaView style={styles.container}>
        <ScrollView contentContainerStyle={styles.content}>
          <StepDots step={2} />
          <StepBack onPress={() => setStep(1)} label={t('common.back')} />
          <Text style={styles.step2Title}>{t('onboarding.step2Title')}</Text>
          <Text style={styles.step2Subtitle}>{t('onboarding.step2Subtitle')}</Text>

          <View style={styles.typeGrid}>
            {MBTI_TYPES.map((type) => {
              const selected = selectedType === type;
              return (
                <TouchableOpacity
                  key={type}
                  style={[styles.typeCell, selected && styles.typeCellSelected]}
                  onPress={() => setSelectedType(type)}
                >
                  <Text style={[styles.typeText, selected && styles.typeTextSelected]}>{type}</Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <TouchableOpacity
            style={[styles.primaryBtn, !selectedType && styles.primaryBtnDisabled]}
            disabled={!selectedType}
            onPress={() => setStep(3)}
          >
            <Text style={styles.primaryBtnText}>{t('onboarding.step2Confirm')}</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.skipBtn} onPress={() => setStep(3)}>
            <Text style={styles.skipBtnText}>{t('onboarding.step2SkipCta')}</Text>
          </TouchableOpacity>
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <StepDots step={3} />
        <StepBack onPress={() => setStep(2)} label={t('common.back')} />
        <Text style={styles.step2Title}>{t('onboarding.step3Title')}</Text>
        <Text style={styles.step2Subtitle}>{t('onboarding.step3Subtitle')}</Text>

        {QUICK_QUESTIONS.map((question) => (
          <View key={question.id} style={styles.questionBlock}>
            <Text style={styles.questionTitle}>{t(question.titleKey)}</Text>
            {question.options.map((option) => {
              const selected = answers[question.id] === option.id;
              return (
                <TouchableOpacity
                  key={option.id}
                  style={[styles.optionRow, selected && styles.optionRowSelected]}
                  onPress={() => setAnswers((prev) => ({ ...prev, [question.id]: option.id }))}
                >
                  <Text style={[styles.optionText, selected && styles.optionTextSelected]}>
                    {t(option.key)}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ))}

        <TouchableOpacity
          style={[styles.primaryBtn, finishing && styles.primaryBtnDisabled]}
          disabled={finishing}
          onPress={() => finish(selectedType)}
        >
          <Text style={styles.primaryBtnText}>
            {finishing ? t('common.saving') : t('onboarding.step3Confirm')}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.skipBtn, finishing && styles.primaryBtnDisabled]}
          disabled={finishing}
          onPress={() => finish(selectedType)}
        >
          <Text style={styles.skipBtnText}>{t('onboarding.step2SkipCta')}</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.xl, paddingBottom: 48 },
  dotsRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: Spacing.sm,
    marginTop: Spacing.md, marginBottom: Spacing.md,
  },
  dotsGroup: { flexDirection: 'row', gap: 6 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.18)' },
  dotActive: { backgroundColor: Colors.gold400 },
  dotsCount: { fontSize: FontSize.sm, color: Colors.textTertiary },
  backBtn: { alignSelf: 'flex-start', paddingVertical: Spacing.xs, marginBottom: Spacing.sm },
  backText: { fontSize: FontSize.md, color: Colors.textTertiary },
  tagline: {
    fontSize: FontSize.h3,
    fontWeight: '700',
    color: Colors.gold400,
    textAlign: 'center',
    marginTop: Spacing.lg,
    marginBottom: Spacing.xl,
  },
  featureList: { gap: Spacing.lg, marginBottom: Spacing.xl },
  featureCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.md,
    backgroundColor: 'rgba(255,215,0,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.lg,
    padding: Spacing.lg,
  },
  featureIcon: { fontSize: 28 },
  featureTextBlock: { flex: 1 },
  featureTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary },
  featureDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 2 },
  primaryBtn: {
    backgroundColor: Colors.gold400,
    borderRadius: BorderRadius.pill,
    paddingVertical: 14,
    alignItems: 'center',
    marginBottom: Spacing.lg,
  },
  primaryBtnDisabled: { opacity: 0.4 },
  primaryBtnText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  disclaimer: {
    fontSize: FontSize.sm,
    color: Colors.textTertiary,
    textAlign: 'center',
    lineHeight: LineHeight.caption,
  },
  step2Title: {
    fontSize: FontSize.h3,
    fontWeight: '700',
    color: Colors.textPrimary,
    textAlign: 'center',
    marginTop: Spacing.lg,
  },
  step2Subtitle: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    textAlign: 'center',
    marginTop: Spacing.sm,
    marginBottom: Spacing.xl,
  },
  typeGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.sm, marginBottom: Spacing.xl },
  typeCell: {
    width: '30%',
    paddingVertical: 10,
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: BorderRadius.md,
  },
  typeCellSelected: {
    backgroundColor: 'rgba(255,215,0,0.12)',
    borderColor: Colors.gold400,
  },
  typeText: { fontSize: FontSize.lg, fontWeight: '600', color: Colors.textSecondary },
  typeTextSelected: { color: Colors.gold400 },
  questionBlock: { marginBottom: Spacing.xl },
  questionTitle: {
    fontSize: FontSize.lg,
    fontWeight: '600',
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  optionRow: {
    paddingVertical: 12,
    paddingHorizontal: Spacing.md,
    marginBottom: Spacing.sm,
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: BorderRadius.md,
  },
  optionRowSelected: {
    backgroundColor: 'rgba(255,215,0,0.12)',
    borderColor: Colors.gold400,
  },
  optionText: { fontSize: FontSize.md, color: Colors.textSecondary },
  optionTextSelected: { color: Colors.gold400, fontWeight: '600' },
  skipBtn: { alignItems: 'center', paddingVertical: 10 },
  skipBtnText: { fontSize: FontSize.md, color: Colors.textTertiary },
  demoEntry: { alignItems: 'center', paddingVertical: 10, marginBottom: Spacing.sm },
  demoEntryText: { fontSize: FontSize.md, color: Colors.gold400 },
});
