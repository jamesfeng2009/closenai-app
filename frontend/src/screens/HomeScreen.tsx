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
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getGrowthDashboard } from '../services/api';
import type { GrowthDashboard } from '../types';

export default function HomeScreen({ navigation }: { navigation: any }) {
  const [dashboard, setDashboard] = useState<GrowthDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboard();
  }, []);

  const loadDashboard = async () => {
    try {
      setLoading(true);
      const data = await getGrowthDashboard();
      setDashboard(data);
    } catch (e) {
      console.warn('Failed to load dashboard:', e);
    } finally {
      setLoading(false);
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
        <Text style={styles.greeting}>{dashboard?.greeting || '你好，今天感觉怎么样？'}</Text>
      </View>

      {/* Streak + 等级 */}
      <TouchableOpacity style={styles.streakBar}>
        <View style={styles.streakItem}>
          <Text style={styles.streakIcon}>🔥</Text>
          <Text style={styles.streakNum}>{dashboard?.streak_count || 0}</Text>
          <Text style={styles.streakLabel}>天</Text>
        </View>
        <View style={styles.divider} />
        <View style={styles.streakItem}>
          <Text style={styles.levelLabel}>{dashboard?.relationship_level || '初识 L1'}</Text>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${(dashboard?.level_progress || 0) * 100}%` }]} />
          </View>
        </View>
        <View style={styles.divider} />
        <Text style={styles.chatCount}>💬 {dashboard?.chat_count || 0} 次</Text>
      </TouchableOpacity>

      {/* Today's Reflection */}
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitle}>✓ Today's Reflection</Text>
          <Text style={styles.doneTag}>✓ 已完成</Text>
        </View>
        <Text style={styles.cardSubtitle}>今天的成长任务 · 选择一个开始记录</Text>
        <View style={styles.moodRow}>
          {['😞', '😐', '🙂', '😊', '😄'].map((emoji) => (
            <TouchableOpacity key={emoji} style={styles.moodBtn}>
              <Text style={styles.moodEmoji}>{emoji}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {/* Growth This Week */}
      <TouchableOpacity style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitlePurple}>📈 Growth This Week</Text>
          <Text style={styles.detailArrow}>详情 →</Text>
        </View>
        <View style={styles.metricsRow}>
          {dashboard?.growth_metrics && Object.entries({
            '😌 情绪': dashboard.growth_metrics.emotion_change,
            '💛 社交': dashboard.growth_metrics.social_change,
            '💪 自信': dashboard.growth_metrics.confidence_change,
            '⚡ 压力': dashboard.growth_metrics.stress_change,
            '🔍 好奇': dashboard.growth_metrics.curiosity_change,
            '🎯 目标': dashboard.growth_metrics.goals_change,
          }).map(([label, value]) => {
            const [emoji, name] = label.split(' ');
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
        <Text style={styles.timelineHint}>连续 {dashboard?.streak_count || 0} 天 · 查看 365 天 Timeline →</Text>
      </TouchableOpacity>

      {/* 本周 Insight */}
      {dashboard?.weekly_insight && (
        <TouchableOpacity style={styles.card}>
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitlePurple}>📈 本周 Insight</Text>
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
          <Text style={styles.profileTitle}>🧬 性格画像</Text>
          <Text style={styles.arrow}>→</Text>
        </View>
        <Text style={styles.profileDesc}>
          {dashboard?.mbti_type || 'INTP'} · 逻辑学家 · 7 天相处发现
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
  moodEmoji: { fontSize: 22 },
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