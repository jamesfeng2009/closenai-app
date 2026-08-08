/**
 * 新用户引导流程 (P1-F05)。
 *
 * 成长优先 · 2 步 · 零占星信号：
 *   Step 1 — Welcome：展示 3 个核心成长卡片，主按钮「开始记录」
 *   Step 2 — 性格类型（可选）：16 型选择 / 跳过
 *
 * 完成后落地成长首页（Growth，第一位 Tab），不是对话页。
 * AI 不作为主体出现，底部声明强调「成长追踪」定位。
 */
import React, { useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, SafeAreaView,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';

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

export default function OnboardingScreen({ onComplete }: { onComplete: () => void }) {
  const { t } = useTranslation();
  const [step, setStep] = useState<1 | 2>(1);
  const [selectedType, setSelectedType] = useState<string | null>(null);

  const finish = async (withType: string | null) => {
    // 保存引导完成标记
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
    onComplete();
  };

  const features = [
    { icon: '📝', title: t('onboarding.step1Feature1Title'), desc: t('onboarding.step1Feature1Desc') },
    { icon: '📈', title: t('onboarding.step1Feature2Title'), desc: t('onboarding.step1Feature2Desc') },
    { icon: '🌱', title: t('onboarding.step1Feature3Title'), desc: t('onboarding.step1Feature3Desc') },
  ];

  if (step === 1) {
    return (
      <SafeAreaView style={styles.container}>
        <ScrollView contentContainerStyle={styles.content}>
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

          <Text style={styles.disclaimer}>{t('onboarding.disclaimer')}</Text>
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
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
          onPress={() => finish(selectedType)}
        >
          <Text style={styles.primaryBtnText}>{t('onboarding.step2Confirm')}</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.skipBtn} onPress={() => finish(null)}>
          <Text style={styles.skipBtnText}>{t('onboarding.step2SkipCta')}</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.xl, paddingBottom: 48 },
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
    lineHeight: 1.5,
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
  skipBtn: { alignItems: 'center', paddingVertical: 10 },
  skipBtnText: { fontSize: FontSize.md, color: Colors.textTertiary },
});