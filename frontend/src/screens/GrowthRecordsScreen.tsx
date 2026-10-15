/**
 * 成长记录页 (PRD P0-F07 §6)。
 *
 * - 顶部：标签筛选（全部 + 六个规范标签，本地筛，不重新请求）
 * - 中部：按本地日历日倒序分组的记录（原文 + 标签 chip）
 * - 底部：推荐话题（后端按近期高频标签生成，整句文案取 topics[].topic）
 *
 * 数据：/growth/records、/growth/topics，两个都不带参数、吃后端缺省。
 * 标签展示名/筛选/空态走 i18n；话题句与记录正文是后端内容，不入库前端文案表。
 */
import React, { useCallback, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { getGrowthRecords, getRecommendedTopics } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { toLocalDate } from '../utils/datetime';
import type { GrowthRecordItem, GrowthTopic } from '../types';

/**
 * 后端中文规范标签 → 展示文案键。
 *
 * 键集必须与 backend/services/growth_service.py 的 _TOPIC_KEYS 逐字相等（有跨仓闸守着）：
 * 这里少一个键，那个标签只能显中文原文（非中文界面等于没翻译）；多一个键，
 * 那个筛选档永远点不出记录。
 */
const TAG_KEYS: Record<string, string> = {
  情绪: 'mood',
  关系: 'relationship',
  工作: 'work',
  目标: 'goal',
  沟通: 'communication',
  成长: 'growth',
};

// 筛选档顺序固定（不是按当前数据里出现过哪些标签动态生成），否则用户一动筛选，
// 标签栏自己就变，下一次想切回去找不到位置。
const TAG_ORDER = Object.keys(TAG_KEYS);

export default function GrowthRecordsScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [records, setRecords] = useState<GrowthRecordItem[]>([]);
  const [topics, setTopics] = useState<GrowthTopic[]>([]);
  const [loading, setLoading] = useState(true);
  // 「拉取失败」与「确实没记录」是两回事：后者给空态文案，前者必须报错 + 可重试。
  const [error, setError] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!isLoggedIn) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const [recordsRes, topicsRes] = await Promise.all([
        getGrowthRecords().catch(() => null),
        // 话题是锦上添花：拉不到就整段不显，不为它单开一个错误态
        getRecommendedTopics().catch(() => null),
      ]);
      if (!recordsRes) {
        setError(t('common.loadFailed'));
        setRecords([]);
      } else {
        setRecords(recordsRes.records);
      }
      setTopics(topicsRes?.topics || []);
    } catch (e) {
      console.warn('Failed to load growth records:', e);
      setError(t('common.loadFailed'));
    } finally {
      setLoading(false);
    }
    // t 进 deps 会让每次渲染换回调、聚焦即重复请求；与同族屏同口径
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn]);

  // 栈页会重复进入（从 Explore 返回再进来），聚焦即刷新
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const visible = useMemo(
    () => (tagFilter ? records.filter((r) => r.tags.includes(tagFilter)) : records),
    [records, tagFilter]
  );

  // 后端已按 created_at 倒序给回，分桶保持这个顺序即可：不再自己排序，
  // 免得「同一天内部顺序」被前端字典序改写。
  const groups = useMemo(() => {
    const buckets: Array<{ date: string; items: GrowthRecordItem[] }> = [];
    for (const r of visible) {
      // created_at 缺失归到 '—' 桶并照样显示：直接丢掉就是「记录变少」这种最难查的假象
      const date = r.created_at ? toLocalDate(r.created_at) : '—';
      const last = buckets[buckets.length - 1];
      if (last && last.date === date) last.items.push(r);
      else buckets.push({ date, items: [r] });
    }
    return buckets;
  }, [visible]);

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
        <Text style={styles.emptyText}>{t('growthRecords.guestEmpty')}</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>{t('growthRecords.title')}</Text>

      {/* 标签筛选 */}
      <View style={styles.filterRow}>
        <TouchableOpacity
          style={[styles.filterChip, tagFilter === null && styles.filterChipActive]}
          onPress={() => setTagFilter(null)}
        >
          <Text style={[styles.filterChipText, tagFilter === null && styles.filterChipTextActive]}>
            {t('growthRecords.filterAll')}
          </Text>
        </TouchableOpacity>
        {TAG_ORDER.map((tag) => (
          <TouchableOpacity
            key={tag}
            style={[styles.filterChip, tagFilter === tag && styles.filterChipActive]}
            onPress={() => setTagFilter(tag)}
          >
            <Text style={[styles.filterChipText, tagFilter === tag && styles.filterChipTextActive]}>
              {t(`growthRecords.tag.${TAG_KEYS[tag]}`)}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {error && (
        <View style={styles.stateBox}>
          <Text style={styles.emptyText}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={load}>
            <Text style={styles.retryText}>{t('common.retry')}</Text>
          </TouchableOpacity>
        </View>
      )}

      {!error && groups.length === 0 && (
        <Text style={styles.emptyText}>{t('growthRecords.empty')}</Text>
      )}

      {!error && groups.map((g) => (
        <View key={g.date} style={styles.dayGroup}>
          <Text style={styles.dayLabel}>{g.date}</Text>
          {g.items.map((r) => (
            <View key={r.id} style={styles.recordCard}>
              <Text style={styles.recordContent}>{r.content}</Text>
              {r.tags.length > 0 && (
                <View style={styles.tagRow}>
                  {r.tags.map((tag) => (
                    <View key={tag} style={styles.tagChip}>
                      <Text style={styles.tagText}>
                        {TAG_KEYS[tag] ? t(`growthRecords.tag.${TAG_KEYS[tag]}`) : tag}
                      </Text>
                    </View>
                  ))}
                </View>
              )}
            </View>
          ))}
        </View>
      ))}

      {/* 推荐话题（后端整句文案，前端不自造句式） */}
      {topics.length > 0 && (
        <View style={styles.topicsSection}>
          <Text style={styles.sectionTitle}>{t('growthRecords.topicsTitle')}</Text>
          {topics.map((tp) => (
            <View key={tp.tag} style={styles.topicItem}>
              <Text style={styles.topicText}>{tp.topic}</Text>
              <Text style={styles.topicCount}>{tp.count}</Text>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: {
    flex: 1, justifyContent: 'center', alignItems: 'center',
    backgroundColor: Colors.bgPrimary, padding: Spacing.xl,
  },
  title: {
    fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary,
    marginBottom: Spacing.md,
  },
  // ── 标签筛选 ──
  filterRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: Spacing.md },
  filterChip: {
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.borderLight, backgroundColor: 'rgba(255,255,255,0.03)',
  },
  filterChipActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.12)' },
  filterChipText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  filterChipTextActive: { color: Colors.gold400, fontWeight: '600' },
  // ── 状态 ──
  stateBox: { alignItems: 'center', gap: Spacing.md, marginVertical: Spacing.md },
  emptyText: { textAlign: 'center', color: Colors.textTertiary, fontSize: FontSize.md },
  retryBtn: {
    paddingHorizontal: 20, paddingVertical: 8, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.12)', borderWidth: 1, borderColor: Colors.gold400,
  },
  retryText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  // ── 记录 ──
  dayGroup: { marginBottom: Spacing.md },
  dayLabel: { fontSize: FontSize.sm, color: Colors.gold400, marginBottom: 6 },
  recordCard: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  recordContent: {
    fontSize: FontSize.md, color: Colors.textPrimary, lineHeight: LineHeight.body,
  },
  tagRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: Spacing.sm },
  tagChip: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.08)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.2)',
  },
  tagText: { fontSize: FontSize.xs, color: Colors.gold400 },
  // ── 推荐话题 ──
  topicsSection: { marginTop: Spacing.lg },
  sectionTitle: {
    fontSize: FontSize.xl, fontWeight: '600', color: Colors.textSecondary, marginBottom: Spacing.sm,
  },
  topicItem: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    borderBottomWidth: 1, borderBottomColor: Colors.borderLight, paddingVertical: Spacing.md,
  },
  topicText: { flex: 1, fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption },
  topicCount: { fontSize: FontSize.sm, color: Colors.gold400, fontWeight: '600' },
});
