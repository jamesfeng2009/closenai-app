/**
 * M5 演示档案（P1-1 · 架构方案 §二 M5）。
 *
 * Onboarding Step 1「先看个例子」的落地页：虚构人物"小 A"的 30 天成熟形态，
 * 三段式（概览+曲线 / Pattern 断言卡 / 时间线+原话），底部 CTA 回到自己的引导。
 *
 * 三条硬约束（都有测试兜着）：
 * - 零请求：数据全部来自 src/config/demoPersona.ts 的纯函数，本屏不 import 任何
 *   api/service，游客态无 token 也不会 401；
 * - 禁 AI 对话入口：屏内不渲染任何跳转 Messages/Reflect 的控件（审核期防误判为聊天产品）；
 * - 文案不写死数字：断言卡上的每个数字都由 buildDemoArchive 反算后作为参数注入。
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
} from 'react-native';
import type { NativeScrollEvent, NativeSyntheticEvent } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { buildDemoArchive, isScrollViewAtEnd, DEMO_PERSONA_KEY } from '../config/demoPersona';
import type { DemoPattern } from '../config/demoPersona';
import { track } from '../services/analytics';

// 与 InsightsScreen 热力图同色阶（1 低落 → 5 开心，0 无数据）
const MOOD_COLORS: Record<number, string> = {
  0: 'rgba(255,255,255,0.06)',
  1: '#EF9A9A',
  2: '#FFCC80',
  3: '#FFF59D',
  4: '#A5D6A7',
  5: '#66BB6A',
};

export default function DemoArchiveScreen({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const archive = useMemo(() => buildDemoArchive(), []);
  const [completed, setCompleted] = useState(false);
  const completedRef = useRef(false);

  // 看完率：触底判定在 config 层（能被 jest 断言），这里只负责"只报一次"
  const onScroll = useCallback((e: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (completedRef.current) return;
    if (!isScrollViewAtEnd(e.nativeEvent)) return;
    completedRef.current = true;
    setCompleted(true);
    track('demo_archive_completed', { feature: 'demo_archive', action: 'scroll_end' });
  }, []);

  const startOwn = () => {
    track('demo_archive_start_own', { feature: 'demo_archive', action: 'start_own' });
    onClose();
  };

  const back = () => {
    track('demo_archive_closed', { feature: 'demo_archive', action: 'back' });
    onClose();
  };

  const basis = (pattern: DemoPattern) =>
    t('demo.patternBasis', { refs: pattern.evidence.join(' · ') });

  const { stats } = archive;

  return (
    <View style={styles.container}>
      {/* 顶部：演示模式徽章 + 返回。徽章常驻，滚动时也不会被划走 */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={back} activeOpacity={0.7}>
          <Text style={styles.backText}>‹ {t('demo.back')}</Text>
        </TouchableOpacity>
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{t('demo.badge')}</Text>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={styles.content}
        onScroll={onScroll}
        scrollEventThrottle={16}
      >
        <Text style={styles.title}>{t('demo.title', { name: t(DEMO_PERSONA_KEY) })}</Text>
        <Text style={styles.subtitle}>{t('demo.subtitle')}</Text>

        {/* 第 1 段：30 天概览 + 情绪曲线 */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>📊 {t('demo.overviewTitle')}</Text>
          <View style={styles.statGrid}>
            <View style={styles.statCell}>
              <Text style={styles.statValue}>{stats.checkins}</Text>
              <Text style={styles.statLabel}>{t('demo.statCheckins', { n: stats.checkins })}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statValue}>{stats.moodAvg.toFixed(1)}</Text>
              <Text style={styles.statLabel}>{t('demo.statMoodAvg', { value: stats.moodAvg.toFixed(1) })}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statValue}>{stats.streak}</Text>
              <Text style={styles.statLabel}>{t('demo.statStreak', { n: stats.streak })}</Text>
            </View>
          </View>

          <Text style={styles.sectionTitle}>{t('demo.chartTitle')}</Text>
          <View style={styles.chart}>
            {archive.days.map((d) => (
              <View
                key={d.dayNo}
                style={[
                  styles.barSlot,
                  { height: 8 + d.mood * 8, backgroundColor: MOOD_COLORS[d.mood] ?? MOOD_COLORS[0] },
                ]}
              />
            ))}
          </View>
          <Text style={styles.metaText}>{t('demo.moodScale')}</Text>
        </View>

        {/* 第 2 段：Pattern 断言卡（商店截图同源素材，护城河 §6.1） */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🔍 {t('demo.patternTitle')}</Text>
          {archive.patterns.map((p) => (
            <View key={p.id} style={styles.patternCard}>
              <Text style={styles.patternText}>{t(p.textKey, p.params)}</Text>
              <Text style={styles.patternBasis}>{basis(p)}</Text>
            </View>
          ))}
        </View>

        {/* 第 3 段：时间线 + 原话 + CTA */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🗓️ {t('demo.timelineTitle')}</Text>
          {archive.timeline.map((item) => (
            <View key={item.id} style={styles.timelineRow}>
              <View style={styles.timelineDot} />
              <View style={styles.timelineBody}>
                <Text style={styles.bodyText}>{t(item.textKey)}</Text>
                <Text style={styles.metaText}>{t('demo.dayNo', { n: item.dayNo })}</Text>
              </View>
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>💬 {t('demo.memoryTitle')}</Text>
          {archive.memories.map((m) => (
            <Text key={m.id} style={styles.quoteText}>“{t(m.textKey)}”</Text>
          ))}
        </View>

        <Text style={styles.disclaimer}>{t('demo.disclaimer')}</Text>

        <TouchableOpacity style={styles.ctaBtn} onPress={startOwn} activeOpacity={0.85}>
          <Text style={styles.ctaText}>{t('demo.cta')}</Text>
        </TouchableOpacity>

        {!completed && <Text style={styles.scrollHint}>{t('demo.scrollHint')}</Text>}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: Spacing.lg, paddingTop: Spacing.sm, paddingBottom: Spacing.xs,
  },
  backBtn: { paddingRight: Spacing.sm },
  backText: { fontSize: FontSize.md, color: Colors.textTertiary },
  badge: {
    backgroundColor: 'rgba(255,215,0,0.14)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.3)',
    borderRadius: BorderRadius.pill, paddingHorizontal: 10, paddingVertical: 3,
  },
  badgeText: { fontSize: FontSize.xs, fontWeight: '700', color: Colors.gold400 },
  content: { padding: Spacing.lg, paddingBottom: 48 },
  title: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary, marginTop: Spacing.sm },
  subtitle: {
    fontSize: FontSize.md, color: Colors.textSecondary, marginTop: Spacing.xs, marginBottom: Spacing.lg,
    lineHeight: LineHeight.caption,
  },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  sectionTitle: {
    fontSize: FontSize.lg, fontWeight: '600', color: Colors.textSecondary,
    marginTop: Spacing.lg, marginBottom: Spacing.sm,
  },
  statGrid: { flexDirection: 'row', gap: Spacing.sm },
  statCell: {
    flex: 1, alignItems: 'center', backgroundColor: 'rgba(255,215,0,0.06)',
    borderRadius: BorderRadius.lg, paddingVertical: Spacing.md, paddingHorizontal: 4,
  },
  statValue: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.gold400 },
  statLabel: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 2, textAlign: 'center' },
  chart: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 2, height: 48,
  },
  barSlot: { flex: 1, borderRadius: 2, minHeight: 8 },
  metaText: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.sm },
  patternCard: {
    backgroundColor: 'rgba(255,215,0,0.05)', borderRadius: BorderRadius.lg,
    padding: Spacing.md, marginBottom: Spacing.sm,
  },
  patternText: { fontSize: FontSize.md, color: Colors.textPrimary, lineHeight: LineHeight.caption },
  patternBasis: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.xs },
  timelineRow: { flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.md, marginBottom: Spacing.md },
  timelineDot: {
    width: 10, height: 10, borderRadius: 5, backgroundColor: Colors.gold400, marginTop: 5,
  },
  timelineBody: { flex: 1 },
  bodyText: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption },
  quoteText: {
    fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption, marginBottom: Spacing.sm,
  },
  disclaimer: {
    fontSize: FontSize.sm, color: Colors.textTertiary, textAlign: 'center', marginBottom: Spacing.md,
  },
  ctaBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.pill,
    paddingVertical: 14, alignItems: 'center',
  },
  ctaText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  scrollHint: {
    fontSize: FontSize.sm, color: Colors.textTertiary, textAlign: 'center', marginTop: Spacing.md,
  },
});
