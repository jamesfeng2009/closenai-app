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
import React, { useEffect, useState, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { getGrowthDashboard, moodCheckin, getJourneyEvents, ApiError } from '../services/api';
import { isFirstReadUnseen, loadFirstReadCard } from '../services/firstRead';
import { settingsRepo } from '../services/db/settingsRepository';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import type { GrowthDashboard, MoodEmoji } from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 MOOD_MAP 严格一致（schemas.py: emoji 字段契约）
const MOOD_EMOJIS: MoodEmoji[] = ['😊', '😌', '😰', '😢', '😡', '😴'];

// 仪表盘本地缓存（SWR：先渲染 SQLite 缓存秒开首屏，后台刷新覆盖）
// 数据走本地 app_settings（Tier 2 AI 计算缓存），见 src/services/db/settingsRepository.ts
const DASHBOARD_CACHE_KEY = '@closenai/dashboard_cache';

// 与后端 ErrorCode.QUOTA_EXCEEDED 对齐（免费配额耗尽 → 引导付费）
const CODE_QUOTA_EXCEEDED = 40301;

export default function HomeScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const requireAuth = useRequireAuth();
  const [dashboard, setDashboard] = useState<GrowthDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [checkingIn, setCheckingIn] = useState(false);
  // 签到后的 AI 解读：屏内卡片承载（旧写法塞进 Alert，长文读不完）
  const [checkinNote, setCheckinNote] = useState<{ emoji: string; label: string; text: string } | null>(null);
  // 标记网络请求是否已完成，防止缓存 setDashboard 覆盖网络新数据（F8 竞态修复）
  const networkLoaded = useRef(false);

  const loadDashboard = React.useCallback(async () => {
    try {
      const data = await getGrowthDashboard();
      setDashboard(data);
      settingsRepo.setJson(DASHBOARD_CACHE_KEY, data).catch(() => {});
    } catch (e) {
      console.warn('Failed to load dashboard:', e);
    } finally {
      networkLoaded.current = true;
      setLoading(false);
    }
  }, []);

  // 首屏：先读缓存立即渲染（无缓存则保持 loading，由聚焦时的网络请求兜底）
  // 竞态防护：若网络请求已先完成则跳过缓存写入，避免过期缓存覆盖新数据
  useEffect(() => {
    (async () => {
      try {
        const cached = await settingsRepo.getJson<GrowthDashboard>(DASHBOARD_CACHE_KEY);
        if (cached && !networkLoaded.current) {
          setDashboard(cached);
          setLoading(false);
        }
      } catch {
        // 缓存损坏忽略，走网络
      }
    })();
  }, []);

  // Tab 常驻不卸载：每次聚焦都拉最新数据（mount 时也会触发一次）
  // 游客态跳过请求（401 噪声），显示空仪表盘
  useFocusEffect(
    React.useCallback(() => {
      if (isLoggedIn) {
        loadDashboard();
      } else {
        setDashboard(null);
        setLoading(false);
      }
    }, [loadDashboard, isLoggedIn])
  );

  // D0 初读卡入口（P1-B01）：卡已生成且未打开过时展示，打开后进 FirstReadCardScreen
  const [showFirstReadEntry, setShowFirstReadEntry] = useState(false);
  useFocusEffect(
    React.useCallback(() => {
      (async () => {
        const card = await loadFirstReadCard();
        setShowFirstReadEntry(!!card && (await isFirstReadUnseen()));
      })();
    }, [])
  );

  // 第一周剧本红点（P1-B02 · §5.19.3）：已投递未读的剧本事件 → App 内红点（除 D2 外不推送）
  const [journeyUnseen, setJourneyUnseen] = useState(0);
  useFocusEffect(
    React.useCallback(() => {
      if (!isLoggedIn) {
        setJourneyUnseen(0);
        return;
      }
      getJourneyEvents()
        .then((data) => setJourneyUnseen(data.unseen_count))
        .catch(() => setJourneyUnseen(0));
    }, [isLoggedIn])
  );

  const onMoodPress = async (emoji: MoodEmoji) => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
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
        // 长文不进 Alert：系统弹窗会截断、无法滚动，用户读不到半句解读。
        // 改为屏内卡片，签到后就留在本页慢慢看。
        setCheckinNote({
          emoji: res.mood_emoji,
          label: res.mood_label,
          text: res.deep_interpretation
            ? `${res.ai_interpretation}\n\n${res.deep_interpretation}`
            : res.ai_interpretation,
        });
      }
    } catch (e) {
      // 配额耗尽（40301）：引导付费，与 ReflectScreen 行为一致
      if (e instanceof ApiError && e.code === CODE_QUOTA_EXCEEDED) {
        Alert.alert(
          t('reflect.quotaTitle'),
          t('reflect.quotaMessage'),
          [
            { text: t('common.cancel'), style: 'cancel' },
            { text: t('reflect.quotaUpgrade'), onPress: () => navigation.navigate('Paywall') },
          ],
        );
        return;
      }
      const msg = e instanceof ApiError ? e.message : t('errors.checkinFailed');
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

      {/* D0 性格初读卡入口（P1-B01：初读卡 → 首条记录，死亡谷漏斗第一环） */}
      {showFirstReadEntry && (
        <TouchableOpacity
          style={styles.firstReadEntry}
          onPress={() => navigation.navigate('FirstReadCard')}
        >
          <View style={styles.firstReadTextBlock}>
            <Text style={styles.firstReadTitle}>🌱 {t('firstRead.homeEntryTitle')}</Text>
            <Text style={styles.firstReadHint}>{t('firstRead.homeEntry')}</Text>
          </View>
          <Text style={styles.detailArrow}>→</Text>
        </TouchableOpacity>
      )}

      {/* 第一周剧本红点（P1-B02：D0–D7 已投递未读 → App 内提示，不额外推送） */}
      {journeyUnseen > 0 && (
        <TouchableOpacity
          style={styles.firstReadEntry}
          onPress={() => navigation.navigate('Week1Report')}
        >
          <View style={styles.firstReadTextBlock}>
            <Text style={styles.firstReadTitle}>📋 {t('journey.homeEntryTitle')}</Text>
            <Text style={styles.firstReadHint}>{t('journey.homeEntry')}</Text>
          </View>
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{journeyUnseen}</Text>
          </View>
          <Text style={styles.detailArrow}>→</Text>
        </TouchableOpacity>
      )}

      {/* Streak + 等级（关系进度入口：点击进关系时间线 P0-F04，游客守卫） */}
      <TouchableOpacity
        style={styles.streakBar}
        onPress={() => { if (requireAuth()) navigation.navigate('RelationshipTimeline'); }}
      >
        <View style={styles.streakItem}>
          <Text style={styles.streakIcon}>🔥</Text>
          <Text style={styles.streakNum}>{dashboard?.streak_count || 0}</Text>
          <Text style={styles.streakLabel}>{t('common.days')}</Text>
        </View>
        <View style={styles.divider} />
        <View style={styles.streakItem}>
          <Text style={styles.levelLabel}>{dashboard?.relationship_level || ''}</Text>
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

        {checkinNote && (
          <View style={styles.checkinNote}>
            <View style={styles.checkinNoteHeader}>
              <Text style={styles.checkinNoteTitle}>
                {checkinNote.emoji} {checkinNote.label}
              </Text>
              <TouchableOpacity onPress={() => setCheckinNote(null)}>
                <Text style={styles.checkinNoteClose}>{t('common.close')}</Text>
              </TouchableOpacity>
            </View>
            <Text style={styles.checkinNoteBody}>{checkinNote.text}</Text>
          </View>
        )}
      </View>

      {/* Growth This Week（点击进入高级洞察页 P1-F03） */}
      <TouchableOpacity style={styles.card} onPress={() => navigation.navigate('Insights')}>
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

      {/* 本周 Insight（点击进入高级洞察页；等级徽章由洞察页展示真实 level） */}
      {dashboard?.weekly_insight && (
        <TouchableOpacity style={styles.card} onPress={() => navigation.navigate('Insights')}>
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitlePurple}>📈 {t('home.weeklyInsight')}</Text>
            <Text style={styles.detailArrow}>{t('home.details')} →</Text>
          </View>
          <Text style={styles.insightText}>{dashboard.weekly_insight}</Text>
        </TouchableOpacity>
      )}

      {/* 昨天记录延续（点开直达复盘 Tab，箭头不是装饰） */}
      {dashboard?.yesterday_record && (
        <TouchableOpacity
          style={styles.yesterdayCard}
          onPress={() => navigation.navigate('Tabs', { screen: 'Reflect' })}
        >
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
          {dashboard?.mbti_type || '—'} · {t('home.personalityDesc')}
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
  firstReadEntry: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    padding: Spacing.md, backgroundColor: 'rgba(255,215,0,0.06)',
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.18)', borderRadius: BorderRadius.lg,
    marginBottom: Spacing.md,
  },
  firstReadTextBlock: { flex: 1 },
  firstReadTitle: { fontSize: FontSize.md, fontWeight: '700', color: Colors.gold400 },
  firstReadHint: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 2 },
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
  checkinNote: {
    marginTop: Spacing.md,
    paddingTop: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.borderGold,
  },
  checkinNoteHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  checkinNoteTitle: { fontSize: FontSize.md, fontWeight: '600', color: Colors.gold400 },
  checkinNoteClose: { fontSize: FontSize.sm, color: Colors.textTertiary, paddingHorizontal: Spacing.xs },
  checkinNoteBody: {
    marginTop: Spacing.sm,
    fontSize: FontSize.lg,
    color: Colors.textSecondary,
    lineHeight: LineHeight.small,
  },
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
  insightText: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption },
  yesterdayCard: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.02)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: 10, padding: 10, paddingHorizontal: 14, marginBottom: Spacing.md,
  },
  yesterdayText: { fontSize: FontSize.lg, color: Colors.textSecondary },
  arrow: { fontSize: 16, color: Colors.textTertiary },
  detailArrow: { fontSize: FontSize.sm, color: Colors.textTertiary },
  badge: {
    minWidth: 20, height: 20, paddingHorizontal: 6, borderRadius: 10,
    backgroundColor: Colors.error, alignItems: 'center', justifyContent: 'center',
  },
  badgeText: { fontSize: FontSize.sm, fontWeight: '700', color: '#fff' },
  profileTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary },
  profileDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 4 },
});