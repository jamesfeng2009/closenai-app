/**
 * 关系时间线页面 (PRD P0-F04)。
 *
 * - 顶部：关系等级卡片（L1-L4 图标 + 等级名/说明 + 到下一级进度条 + 解锁能力 + 等级说明）
 * - 中部：统计概览（认识第 N 天 · 聊了 M 次 · 共同经历 K 个时刻）+ 徽章墙
 * - 底部：倒序里程碑时间线（徽章 + 成长记录），点击某日展开单日摘要
 *
 * 数据：/growth/level、/growth/badges、/growth/relationship-timeline、/growth/timeline/day。
 * 等级名/说明、统计与徽章条件走 i18n；等级解锁能力/说明、徽章与时间线标题为后端本地化内容。
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import {
  getLevelDetail, getBadges, getRelationshipTimeline, getTimelineDay, getGrowthDashboard,
} from '../services/api';
import { useAuth } from '../context/AuthContext';
import BadgeWall, { type BadgeWallItem } from '../components/BadgeWall';
import type {
  LevelDetail, RelationshipTimeline, RelationshipTimelineEvent, TimelineDaySummary,
} from '../types';
import { useTranslation } from 'react-i18next';

// 等级图标（L1-L4）
const LEVEL_ICONS: Record<number, string> = { 1: '🌱', 2: '🌿', 3: '🌳', 4: '🌟' };
// 徽章图标（与 badge_type 对齐）
const BADGE_ICONS: Record<string, string> = {
  first_chat: '💬',
  first_mood: '😊',
  streak_7: '🔥',
  streak_30: '🏅',
  streak_100: '💎',
  streak_365: '👑',
  level_up_L2: '🌿',
  level_up_L3: '🌳',
  level_up_L4: '🌟',
};
// 时间线事件图标：徽章事件用徽章图标，成长记录按 record_type
const RECORD_ICONS: Record<string, string> = {
  daily_insight: '💡',
  weekly_summary: '📈',
  monthly_report: '📊',
  quarterly_report: '📊',
  yearly_report: '📊',
};

function eventIcon(e: RelationshipTimelineEvent): string {
  if (e.type === 'milestone') return BADGE_ICONS[e.milestone_type || ''] || '🏅';
  return RECORD_ICONS[e.record_type || ''] || '📝';
}

export default function RelationshipTimelineScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [level, setLevel] = useState<LevelDetail | null>(null);
  const [timeline, setTimeline] = useState<RelationshipTimeline | null>(null);
  const [wallBadges, setWallBadges] = useState<BadgeWallItem[]>([]);
  const [loading, setLoading] = useState(true);
  // 单日摘要展开态：expandedDate 当前展开日，dayCache 按日期缓存结果
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const [dayCache, setDayCache] = useState<Record<string, TimelineDaySummary>>({});
  const [dayLoading, setDayLoading] = useState<string | null>(null);

  useEffect(() => {
    if (isLoggedIn) {
      load();
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn]);

  const load = async () => {
    try {
      setLoading(true);
      const [levelRes, badgesRes, timelineRes, dashboardRes] = await Promise.all([
        getLevelDetail().catch(() => null),
        getBadges().catch(() => null),
        getRelationshipTimeline().catch(() => null),
        // dashboard 仅用于推导 first_mood（streak>0 说明至少签到过一次）
        getGrowthDashboard().catch(() => null),
      ]);
      setLevel(levelRes);
      setTimeline(timelineRes);
      setWallBadges(buildBadgeWall(
        badgesRes?.badges || [],
        levelRes,
        timelineRes,
        !!dashboardRes && (dashboardRes.streak_count > 0 || !!dashboardRes.today_mood),
      ));
    } catch (e) {
      console.warn('Failed to load relationship timeline:', e);
    } finally {
      setLoading(false);
    }
  };

  // 徽章墙数据：streak 徽章（后端）+ 等级徽章（按当前等级推导）+ first_chat/first_mood（按统计推导）
  const buildBadgeWall = (
    streakBadges: { milestone_type: string; name: string; achieved: boolean; achieved_at: string | null }[],
    levelRes: LevelDetail | null,
    timelineRes: RelationshipTimeline | null,
    moodCheckedIn: boolean,
  ): BadgeWallItem[] => {
    const items: BadgeWallItem[] = [];
    items.push({
      key: 'first_chat',
      icon: BADGE_ICONS.first_chat,
      name: t('relationship.badgeName.first_chat'),
      condition: t('relationship.badgeCondition.first_chat'),
      unlocked: (timelineRes?.chat_count || 0) > 0,
    });
    items.push({
      key: 'first_mood',
      icon: BADGE_ICONS.first_mood,
      name: t('relationship.badgeName.first_mood'),
      condition: t('relationship.badgeCondition.first_mood'),
      unlocked: moodCheckedIn,
    });
    for (const b of streakBadges) {
      items.push({
        key: b.milestone_type,
        icon: BADGE_ICONS[b.milestone_type] || '🏅',
        name: b.name,
        condition: t(`relationship.badgeCondition.${b.milestone_type}`),
        unlocked: b.achieved,
        unlockedAt: b.achieved_at,
      });
    }
    for (const lv of levelRes?.levels || []) {
      if (lv.level <= 1) continue; // L1 为初始等级，无晋升徽章
      items.push({
        key: `level_up_L${lv.level}`,
        icon: BADGE_ICONS[`level_up_L${lv.level}`],
        name: lv.label,
        condition: t(`relationship.badgeCondition.level_up_L${lv.level}`),
        unlocked: lv.reached,
      });
    }
    return items;
  };

  const onToggleDay = async (date: string) => {
    if (expandedDate === date) {
      setExpandedDate(null);
      return;
    }
    setExpandedDate(date);
    if (dayCache[date]) return;
    try {
      setDayLoading(date);
      const data = await getTimelineDay(date);
      setDayCache((prev) => ({ ...prev, [date]: data }));
    } catch (e) {
      console.warn('Failed to load day summary:', e);
    } finally {
      setDayLoading(null);
    }
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  if (!isLoggedIn) {
    return (
      <View style={styles.loading}>
        <Text style={styles.empty}>{t('relationship.guestEmpty')}</Text>
      </View>
    );
  }

  const current = level?.current;
  const levelName = current ? t(`relationship.level.L${current.level}.name`) : '';
  const levelDesc = current ? t(`relationship.level.L${current.level}.desc`) : '';

  const renderDaySummary = (date: string) => {
    const day = dayCache[date];
    if (dayLoading === date && !day) {
      return <ActivityIndicator color={Colors.gold400} style={styles.dayLoading} />;
    }
    if (!day) return null;
    if (!day.has_data) {
      return <Text style={styles.dayEmpty}>{t('relationship.dayEmpty')}</Text>;
    }
    return (
      <View style={styles.dayBody}>
        {day.mood && (
          <View style={styles.dayRow}>
            <View style={[styles.moodColor, { backgroundColor: day.color }]} />
            <Text style={styles.dayText}>
              {t('relationship.dayMood')}: {day.mood.emoji} {day.mood.label}
            </Text>
          </View>
        )}
        {day.mood?.interpretation ? (
          <Text style={styles.dayInsight}>{day.mood.interpretation}</Text>
        ) : null}
        <Text style={styles.dayMeta}>{t('relationship.dayChats', { count: day.chat_count })}</Text>
        {day.records.map((r, i) => (
          <Text key={i} style={styles.dayInsight}>
            {t('relationship.dayInsight')}: {r.content}
          </Text>
        ))}
        {day.milestones.map((m, i) => (
          <Text key={i} style={styles.dayBadge}>
            🏅 {t('relationship.dayBadges')}: {m.name}
          </Text>
        ))}
      </View>
    );
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* 关系等级卡片 */}
      {current && (
        <View style={styles.levelCard}>
          <View style={styles.levelHeader}>
            <Text style={styles.levelIcon}>{LEVEL_ICONS[current.level] || '🌱'}</Text>
            <View style={styles.levelTitleWrap}>
              <Text style={styles.levelName}>
                L{current.level} · {levelName}
              </Text>
              <Text style={styles.levelDesc}>{levelDesc}</Text>
            </View>
            <Text style={styles.levelPercent}>{Math.round(current.progress * 100)}%</Text>
          </View>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${current.progress * 100}%` }]} />
          </View>
          <Text style={styles.progressHint}>
            {current.days_to_next > 0
              ? t('relationship.toNextLevel', { days: current.days_to_next })
              : t('relationship.maxLevel')}
          </Text>
          {/* §5.15a 情感回报：等级页先讲"关系变了什么"，再列功能解锁 */}
          {!!current.reward && (
            <View style={styles.rewardWrap}>
              <Text style={styles.rewardTitle}>{t('relationship.rewardTitle')}</Text>
              <Text style={styles.rewardText}>{current.reward}</Text>
            </View>
          )}
          {current.unlocks.length > 0 && (
            <View style={styles.unlocksWrap}>
              <Text style={styles.unlocksTitle}>{t('relationship.unlocksTitle')}</Text>
              <View style={styles.unlocksRow}>
                {current.unlocks.map((u) => (
                  <View key={u} style={styles.unlockChip}>
                    <Text style={styles.unlockText}>✓ {u}</Text>
                  </View>
                ))}
              </View>
            </View>
          )}
          {!!level?.note && <Text style={styles.levelNote}>{level.note}</Text>}
        </View>
      )}

      {/* 统计概览 */}
      {timeline && (
        <View style={styles.statsRow}>
          <View style={styles.statItem}>
            <Text style={styles.statNum}>{timeline.days_known}</Text>
            <Text style={styles.statLabel}>{t('relationship.statsDay')}</Text>
          </View>
          <View style={styles.statItem}>
            <Text style={styles.statNum}>{timeline.chat_count}</Text>
            <Text style={styles.statLabel}>{t('relationship.statsChats')}</Text>
          </View>
          <View style={styles.statItem}>
            <Text style={styles.statNum}>{timeline.event_count}</Text>
            <Text style={styles.statLabel}>{t('relationship.statsMoments')}</Text>
          </View>
        </View>
      )}

      {/* 徽章墙 */}
      {wallBadges.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('relationship.badgesTitle')}</Text>
          <BadgeWall badges={wallBadges} />
        </View>
      )}

      {/* 里程碑时间线（倒序） */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('relationship.timelineTitle')}</Text>
        {!timeline?.events.length && (
          <Text style={styles.empty}>{t('relationship.emptyTimeline')}</Text>
        )}
        {timeline?.events.map((e, idx) => (
          <TouchableOpacity
            key={`${e.type}-${e.date}-${idx}`}
            style={styles.eventCard}
            onPress={() => onToggleDay(e.date)}
            activeOpacity={0.7}
          >
            <Text style={styles.eventIcon}>{eventIcon(e)}</Text>
            <View style={styles.eventBody}>
              <Text style={styles.eventDate}>{e.date}</Text>
              <Text style={styles.eventTitle}>{e.title}</Text>
              {!!e.content && (
                <Text style={styles.eventContent} numberOfLines={expandedDate === e.date ? undefined : 2}>
                  {e.content}
                </Text>
              )}
              {expandedDate === e.date && renderDaySummary(e.date)}
            </View>
          </TouchableOpacity>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary, padding: Spacing.xl },
  empty: { textAlign: 'center', color: Colors.textTertiary, fontSize: FontSize.title, marginTop: 20 },
  // ── 等级卡片 ──
  levelCard: {
    backgroundColor: 'rgba(255,215,0,0.06)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  levelHeader: { flexDirection: 'row', alignItems: 'center', gap: Spacing.md },
  levelIcon: { fontSize: 32 },
  levelTitleWrap: { flex: 1 },
  levelName: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.gold400 },
  levelDesc: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: 2 },
  levelPercent: { fontSize: FontSize.xl, fontWeight: '700', color: Colors.gold400 },
  progressBar: {
    height: 6, backgroundColor: 'rgba(255,255,255,0.1)', borderRadius: 3,
    overflow: 'hidden', marginTop: Spacing.md,
  },
  progressFill: { height: '100%', backgroundColor: Colors.gold400, borderRadius: 3 },
  progressHint: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 6 },
  unlocksWrap: { marginTop: Spacing.md },
  // §5.15a 情感回报：与功能 chip 区分开（独立底色 + 整句文案，不是能力标签）
  rewardWrap: {
    marginTop: Spacing.md, padding: Spacing.sm,
    backgroundColor: 'rgba(99,102,241,0.10)', borderWidth: 1,
    borderColor: 'rgba(99,102,241,0.28)', borderRadius: BorderRadius.md,
  },
  rewardTitle: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.textTertiary },
  rewardText: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary, marginTop: 2 },
  unlocksTitle: { fontSize: FontSize.md, fontWeight: '600', color: Colors.textSecondary, marginBottom: 6 },
  unlocksRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  unlockChip: {
    backgroundColor: 'rgba(255,215,0,0.08)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.2)',
    borderRadius: BorderRadius.pill, paddingHorizontal: 10, paddingVertical: 4,
  },
  unlockText: { fontSize: FontSize.sm, color: Colors.gold400 },
  levelNote: { fontSize: FontSize.sm, color: Colors.textMuted, marginTop: Spacing.md, lineHeight: 1.5 },
  // ── 统计概览 ──
  statsRow: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.md },
  statItem: {
    flex: 1, alignItems: 'center', paddingVertical: Spacing.md,
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg,
  },
  statNum: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary },
  statLabel: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 4, textAlign: 'center' },
  // ── 分区标题 ──
  section: { marginBottom: Spacing.lg },
  sectionTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textSecondary, marginBottom: Spacing.sm },
  // ── 时间线 ──
  eventCard: {
    flexDirection: 'row', gap: Spacing.md,
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  eventIcon: { fontSize: 20 },
  eventBody: { flex: 1 },
  eventDate: { fontSize: FontSize.xs, color: Colors.textTertiary },
  eventTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary, marginTop: 2 },
  eventContent: { fontSize: FontSize.md, color: Colors.textSecondary, marginTop: 4, lineHeight: 1.5 },
  // ── 单日摘要 ──
  dayLoading: { marginTop: Spacing.sm },
  dayEmpty: { fontSize: FontSize.md, color: Colors.textTertiary, marginTop: Spacing.sm },
  dayBody: {
    marginTop: Spacing.sm, borderTopWidth: 1, borderTopColor: Colors.borderLight, paddingTop: Spacing.sm,
  },
  dayRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  moodColor: { width: 12, height: 12, borderRadius: 3 },
  dayText: { fontSize: FontSize.md, color: Colors.textPrimary },
  dayMeta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
  dayInsight: { fontSize: FontSize.md, color: Colors.textSecondary, marginTop: 4, lineHeight: 1.5 },
  dayBadge: { fontSize: FontSize.md, color: Colors.gold400, marginTop: 4 },
});
