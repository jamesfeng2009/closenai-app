/**
 * 第一周观察报告（P1-B02 · PRD §5.19.3 D7 · 架构方案 §二 M2）。
 *
 * 数据全部来自 `GET /journey/events`：正文由后端模板直出、零 LLM，本页只做展示。
 * D7 报告的硬要求是「每句引用可回溯」：正文里的每条原话下方给出记忆依据（原话 + 日期），
 * 因此打开本页 = 已读到这份报告，进入时消费红点（POST /journey/events/acknowledge）。
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { useTranslation } from 'react-i18next';

import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { acknowledgeJourneyEvent, getJourneyEvents } from '../services/api';
import { getAckPending, getReport, getTimeline } from '../config/journey';
import type { JourneyEvent } from '../types';

export default function Week1ReportScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const [events, setEvents] = useState<JourneyEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stale = false;
    (async () => {
      try {
        const data = await getJourneyEvents();
        if (stale) return;
        setEvents(data.events);
        // 打开即消费红点：已投递但未回执的剧本事件逐条回执
        await Promise.all(
          getAckPending(data.events).map((e) => acknowledgeJourneyEvent(e.id).catch(() => null)),
        );
      } catch {
        // 拉取失败走空态，不弹错误（本页是回顾，不是操作）
      } finally {
        if (!stale) setLoading(false);
      }
    })();
    return () => { stale = true; };
  }, []);

  const report = getReport(events);
  const timeline = getTimeline(events);

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator color={Colors.gold400} style={styles.loading} />
      </View>
    );
  }

  // 报告未投递（pending/skipped）才走空态；seen 是回执过的已投递报告，必须仍能打开
  if (!report) {
    return (
      <View style={styles.container}>
        <View style={styles.emptyWrap}>
          <Text style={styles.emptyText}>{t('journey.empty')}</Text>
          <TouchableOpacity
            style={styles.primaryBtn}
            onPress={() => navigation.navigate('Tabs', { screen: 'Reflect' })}
          >
            <Text style={styles.primaryBtnText}>{t('journey.cta')}</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  const quotes = report.payload.quotes ?? [];
  const stats = (report.payload.stats ?? {}) as { chats?: number; checkin_days?: number };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.headerTitle}>{t('journey.title')}</Text>
      <Text style={styles.headerSubtitle}>{t('journey.subtitle')}</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>
          {report.payload.title || t('journey.reportTitle')}
        </Text>
        <Text style={styles.body}>{report.payload.body}</Text>

        {quotes.map((q) => (
          <View key={q.memory_id} style={styles.quoteRow}>
            <Text style={styles.quoteText}>「{q.text}」</Text>
            <Text style={styles.quoteDate}>
              {t('journey.source', { date: q.date })}
            </Text>
          </View>
        ))}

        <View style={styles.statRow}>
          <Text style={styles.statText}>
            {t('journey.statEntries', { n: stats.chats ?? 0 })}
          </Text>
          <Text style={styles.statText}>
            {t('journey.statCheckins', { n: stats.checkin_days ?? 0 })}
          </Text>
        </View>
        <Text style={styles.note}>{t('journey.evidenceNote')}</Text>
      </View>

      <Text style={styles.timelineTitle}>{t('journey.timelineTitle')}</Text>
      {timeline.map((e) => (
        <View key={e.id} style={styles.timelineRow}>
          <Text style={styles.timelineDay}>{t(`journey.day.${e.event_kind}`)}</Text>
          <Text style={[styles.timelineStatus, styles[`status_${e.status}` as 'status_pending']]}>
            {t(`journey.status.${e.status}`)}
          </Text>
        </View>
      ))}

      <TouchableOpacity
        style={styles.primaryBtn}
        onPress={() => navigation.navigate('Tabs', { screen: 'Reflect' })}
      >
        <Text style={styles.primaryBtnText}>{t('journey.cta')}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 48 },
  loading: { marginTop: 80 },
  headerTitle: {
    fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary, marginTop: Spacing.md,
  },
  headerSubtitle: {
    fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4, marginBottom: Spacing.lg,
  },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.xl,
  },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '700', color: Colors.gold400, marginBottom: Spacing.md },
  body: { fontSize: FontSize.md, color: Colors.textPrimary, lineHeight: 22 },
  quoteRow: {
    marginTop: Spacing.md, paddingTop: Spacing.md,
    borderTopWidth: 1, borderTopColor: 'rgba(255,215,0,0.12)',
  },
  quoteText: { fontSize: FontSize.md, color: Colors.textSecondary, fontStyle: 'italic', lineHeight: 21 },
  quoteDate: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 2 },
  statRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.md, marginTop: Spacing.lg },
  statText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  note: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.md, lineHeight: 18 },
  timelineTitle: {
    fontSize: FontSize.md, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.sm,
  },
  timelineRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  timelineDay: { fontSize: FontSize.md, color: Colors.textSecondary },
  timelineStatus: { fontSize: FontSize.sm },
  status_pending: { color: Colors.textTertiary },
  status_sent: { color: Colors.gold400 },
  status_seen: { color: Colors.textTertiary },
  status_skipped: { color: Colors.textMuted },
  primaryBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.pill,
    paddingVertical: 14, alignItems: 'center', marginTop: Spacing.xl,
  },
  primaryBtnText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  emptyWrap: { paddingTop: 80, paddingHorizontal: Spacing.xl },
  emptyText: {
    fontSize: FontSize.md, color: Colors.textSecondary, textAlign: 'center', marginBottom: Spacing.xl,
  },
});
