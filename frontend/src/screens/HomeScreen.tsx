/**
 * 成长首页 (P0-F06)。
 *
 * 按 UI 原型 screen-home 实现：
 * - 日期 + 问候
 * - Streak 条（🔥 天数 + 等级 + 聊天次数）
 * - Today's Reflection（情绪签到）
 * - Growth This Week（6 维度指标 + 迷你热力图）
 * - 本周 Insight
 * - 昨天记录延续
 * - 性格画像入口
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getGrowthDashboard, moodCheckin, ApiError } from '../services/api';
import type { GrowthDashboard, MoodEmoji } from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 MOOD_MAP 严格一致（schemas.py: emoji 字段契约）
const MOOD_EMOJIS: MoodEmoji[] = ['😊', '😌', '😰', '😢', '😡', '😴'];

// 仪表盘本地缓存（SWR：先渲染缓存秒开首屏，后台刷新覆盖）
const DASHBOARD_CACHE_KEY = '@closenai/dashboard_cache';

export default function HomeScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const [dashboard, setDashboard] = useState<GrowthDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [checkingIn, setCheckingIn] = useState(false);

  const loadDashboard = React.useCallback(async () => {
    try {
      const data = await getGrowthDashboard();
      setDashboard(data);
      AsyncStorage.setItem(DASHBOARD_CACHE_KEY, JSON.stringify(data)).catch(() => {});
    } catch (e) {
      console.warn('Failed to load dashboard:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  // 首屏：先读缓存立即渲染（无缓存则保持 loading，由聚焦时的网络请求兜底）
  useEffect(() => {
    (async () => {
      try {
        const cached = await AsyncStorage.getItem(DASHBOARD_CACHE_KEY);
        if (cached) {
          setDashboard(JSON.parse(cached));
          setLoading(false);
        }
      } catch {
        // 缓存损坏忽略，走网络
      }
    })();
  }, []);

  // Tab 常驻不卸载：每次聚焦都拉最新数据（mount 时也会触发一次）
  useFocusEffect(
    React.useCallback(() => {
      loadDashboard();
    }, [loadDashboard])
  );

  const onMoodPress = async (emoji: MoodEmoji) => {
    if (checkingIn || dashboard?.today_mood) return; // 已签到或请求中则忽略
    try {
      setCheckingIn(true);
      const res = await moodCheckin(emoji);
      // 本地立即更新：标记今日已签到 + 刷新 streak
      setDashboard((prev) =>
        prev
          ? { ...prev, today_mood: res.mood_emoji, streak_count: res.streak_updated }
          : prev,
      );
      if (res.ai_interpretation) {
        const text = res.deep_interpretation
          ? `${res.ai_interpretation}\n\n${res.deep_interpretation}`
          : res.ai_interpretation;
        Alert.alert(`${res.mood_emoji} ${res.mood_label}`, text);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '签到失败，请稍后重试';
      Alert.alert(t('common.error'), msg);
    } finally {
      setCheckingIn(false);
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
      {/* 日期 + 问候 */}
      <View style={styles.header}>
        <Text style={styles.date}>{dashboard?.date || '...'}</Text>
        <Text style={styles.greeting}>{dashboard?.greeting || t('home.greeting')}</Text>
      </View>

      {/* Streak + 等级 */}
      <TouchableOpacity style={styles.streakBar}>
        <View style={styles.streakItem}>
          <Text style={styles.streakIcon}>🔥</Text>
          <Text style={styles.streakNum}>{dashboard?.streak_count || 0}</Text>
          <Text style={styles.streakLabel}>{t('common.days')}</Text>
        </View>
        <View style={styles.divider} />
        <View style={styles.streakItem}>
          <Text style={styles.levelLabel}>{dashboard?.relationship_level || '初识 L1'}</Text>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${(dashboard?.level_progress || 0) * 100}%` }]} />
          </View>
        </View>
        <View style={styles.divider} />
        <Text style={styles.chatCount}>💬 {dashboard?.chat_count || 0} {t('common.times')}</Text>
        {/* Streak 冻结券（PRD P0-F05：断签日自动消耗 1 张保住连续记录） */}
        {(dashboard?.streak_freeze_count ?? 0) > 0 && (
          <>
            <View style={styles.divider} />
            <Text style={styles.freezeCount}>
              🧊 {dashboard?.streak_freeze_count} {t('home.streakFreeze')}
            </Text>
          </>
        )}
      </TouchableOpacity>

      {/* 里程碑徽章（PRD P0-F05：连续 7/30/100/365 天） */}
      {!!dashboard?.milestones?.length && (
        <View style={styles.milestoneRow}>
          {dashboard.milestones.map((m) => (
            <View key={m.milestone_type} style={[styles.milestoneItem, !m.achieved && styles.milestoneLocked]}>
              <Text style={styles.milestoneIcon}>{m.achieved ? '🏅' : '🔒'}</Text>
              <Text style={[styles.milestoneDays, m.achieved && styles.milestoneDaysAchieved]}>
                {t('home.milestoneStreak', { days: m.days })}
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Today's Reflection */}
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitle}>✓ {t('home.todayReflection')}</Text>
          {dashboard?.today_mood && (
            <Text style={styles.doneTag}>✓ {t('common.done')}</Text>
          )}
        </View>
        <Text style={styles.cardSubtitle}>{t('home.todayReflectionSubtitle')}</Text>
        <View style={styles.moodRow}>
          {MOOD_EMOJIS.map((emoji) => {
            const selected = dashboard?.today_mood === emoji;
            const disabled = checkingIn || !!dashboard?.today_mood;
            return (
              <TouchableOpacity
                key={emoji}
                style={[styles.moodBtn, selected && styles.moodBtnSelected]}
                disabled={disabled}
                onPress={() => onMoodPress(emoji)}
              >
                <Text style={[styles.moodEmoji, disabled && !selected && styles.moodEmojiDim]}>
                  {emoji}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </View>

      {/* Growth This Week */}
      <TouchableOpacity style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitlePurple}>📈 {t('home.growthThisWeek')}</Text>
          <Text style={styles.detailArrow}>{t('home.details')} →</Text>
        </View>
        <View style={styles.metricsRow}>
          {dashboard?.growth_metrics && ([
            ['😌', t('home.emotion'), dashboard.growth_metrics.emotion_change],
            ['💛', t('home.social'), dashboard.growth_metrics.social_change],
            ['💪', t('home.confidence'), dashboard.growth_metrics.confidence_change],
            ['⚡', t('home.stress'), dashboard.growth_metrics.stress_change],
            ['🔍', t('home.curiosity'), dashboard.growth_metrics.curiosity_change],
            ['🎯', t('home.goals'), dashboard.growth_metrics.goals_change],
          ] as [string, string, string | number][]).map(([emoji, name, value]) => {
            const isPositive = typeof value === 'number' ? value > 0 : value === '↑';
            const isNegative = typeof value === 'number' ? value < 0 : value === '↓';
            const color = isPositive ? Colors.success : isNegative ? Colors.error : Colors.warning;
            const arrow = isPositive ? '↑' : isNegative ? '↓' : '→';
            const displayValue = typeof value === 'number' ? `${Math.abs(value)}%` : '';
            return (
              <View key={name} style={styles.metricItem}>
                <Text style={styles.metricEmoji}>{emoji}</Text>
                <Text style={styles.metricName}>{name}</Text>
                <Text style={[styles.metricValue, { color }]}>
                  {arrow}{displayValue}
                </Text>
              </View>
            );
          })}
        </View>
        {/* 迷你热力图 */}
        <View style={styles.miniTimeline}>
          {(dashboard?.mood_timeline || []).map((color, i) => (
            <View key={i} style={[styles.timelineDot, { backgroundColor: color }]} />
          ))}
        </View>
        <Text style={styles.timelineHint}>
          {t('common.days')} {dashboard?.streak_count || 0} · {t('home.timelineHint')} →
        </Text>
      </TouchableOpacity>

      {/* 本周 Insight */}
      {dashboard?.weekly_insight && (
        <TouchableOpacity style={styles.card}>
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitlePurple}>📈 {t('home.weeklyInsight')}</Text>
            <Text style={styles.insightBadge}>L2 Pattern</Text>
          </View>
          <Text style={styles.insightText}>{dashboard.weekly_insight}</Text>
        </TouchableOpacity>
      )}

      {/* 昨天记录延续 */}
      {dashboard?.yesterday_record && (
        <TouchableOpacity style={styles.yesterdayCard}>
          <Text style={styles.yesterdayText}>{dashboard.yesterday_record}</Text>
          <Text style={styles.arrow}>→</Text>
        </TouchableOpacity>
      )}

      {/* 性格画像入口 */}
      <TouchableOpacity
        style={styles.card}
        onPress={() => navigation.navigate('Personality')}
      >
        <View style={styles.cardHeader}>
          <Text style={styles.profileTitle}>🧬 {t('home.personality')}</Text>
          <Text style={styles.arrow}>→</Text>
        </View>
        <Text style={styles.profileDesc}>
          {dashboard?.mbti_type || 'INTP'} · {t('home.personalityDesc')}
        </Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 80 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  header: { marginBottom: Spacing.md },
  date: { fontSize: FontSize.md, color: Colors.textTertiary },
  greeting: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.gold400, marginTop: Spacing.xs },
  streakBar: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    padding: 10, backgroundColor: 'rgba(255,215,0,0.04)',
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.1)', borderRadius: BorderRadius.lg,
    marginBottom: Spacing.md,
  },
  streakItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  streakIcon: { fontSize: 16 },
  streakNum: { fontSize: 16, fontWeight: '700', color: Colors.gold400 },
  streakLabel: { fontSize: FontSize.sm, color: Colors.textTertiary },
  levelLabel: { fontSize: FontSize.sm, color: Colors.textSecondary },
  progressBar: {
    width: 40, height: 4, backgroundColor: 'rgba(255,255,255,0.1)',
    borderRadius: 2, overflow: 'hidden', marginLeft: 4,
  },
  progressFill: { height: '100%', backgroundColor: Colors.gold400, borderRadius: 2 },
  divider: { width: 1, height: 16, backgroundColor: 'rgba(255,255,255,0.1)' },
  chatCount: { fontSize: FontSize.sm, color: Colors.textTertiary },
  freezeCount: { fontSize: FontSize.sm, color: Colors.textTertiary },
  milestoneRow: {
    flexDirection: 'row', gap: 6, marginBottom: Spacing.md,
  },
  milestoneItem: {
    flex: 1, alignItems: 'center', paddingVertical: 8,
    backgroundColor: 'rgba(255,215,0,0.06)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.md,
  },
  milestoneLocked: { opacity: 0.45, backgroundColor: 'rgba(255,255,255,0.02)', borderColor: Colors.borderLight },
  milestoneIcon: { fontSize: 16 },
  milestoneDays: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 2 },
  milestoneDaysAchieved: { color: Colors.gold400, fontWeight: '600' },
  card: {
    backgroundColor: 'rgba(255,215,0,0.06)',
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  cardHeader: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10,
  },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.gold400 },
  cardTitlePurple: { fontSize: FontSize.xl, fontWeight: '600', color: '#A78BFA' },
  cardSubtitle: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: 10 },
  doneTag: { fontSize: FontSize.sm, color: Colors.success, backgroundColor: 'rgba(16,185,129,0.1)', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10 },
  moodRow: { flexDirection: 'row', justifyContent: 'space-around', gap: 8 },
  moodBtn: { flex: 1, alignItems: 'center', padding: 8, backgroundColor: 'rgba(255,255,255,0.03)', borderRadius: 10 },
  moodBtnSelected: { backgroundColor: 'rgba(255,215,0,0.18)', borderWidth: 1, borderColor: Colors.gold400 },
  moodEmoji: { fontSize: 22 },
  moodEmojiDim: { opacity: 0.35 },
  metricsRow: { flexDirection: 'row', gap: 6, marginBottom: 10 },
  metricItem: {
    flex: 1, alignItems: 'center', padding: 6,
    backgroundColor: 'rgba(99,102,241,0.06)', borderRadius: 8,
  },
  metricEmoji: { fontSize: 14 },
  metricName: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 2 },
  metricValue: { fontSize: FontSize.sm, fontWeight: '600' },
  miniTimeline: { flexDirection: 'row', gap: 2, height: 20, marginTop: 10 },
  timelineDot: { flex: 1, borderRadius: 2 },
  timelineHint: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 4 },
  insightBadge: {
    fontSize: FontSize.xs, fontWeight: '600', color: '#A78BFA',
    backgroundColor: 'rgba(99,102,241,0.15)', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  insightText: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: 1.5 },
  yesterdayCard: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.02)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: 10, padding: 10, paddingHorizontal: 14, marginBottom: Spacing.md,
  },
  yesterdayText: { fontSize: FontSize.lg, color: Colors.textSecondary },
  arrow: { fontSize: 16, color: Colors.textTertiary },
  detailArrow: { fontSize: FontSize.sm, color: Colors.textTertiary },
  profileTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary },
  profileDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 4 },
});