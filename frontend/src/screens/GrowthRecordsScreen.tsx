/**
 * 成长记录页 (PRD P0-F07 §6)。
 *
 * - 顶部：我的行动（T4-04：进行中/已完成/已跳过三个分区＋行内完成/跳过＋结果四选一）
 * - 中部：标签筛选（全部 + 六个规范标签，本地筛，不重新请求）
 * - 中后：按本地日历日倒序分组的记录（原文 + 标签 chip）
 * - 底部：推荐话题（后端按近期高频标签生成，整句文案取 topics[].topic）
 *
 * 数据：/growth/records、/growth/topics 不吃参数；行动面走 /actions 那条列表
 * （`GET /api/v1/actions`，BFF 纯转发到 Go 的内部面，T4-02）。
 * 标签展示名/筛选/空态走 i18n；话题句与记录正文是后端内容，不入库前端文案表。
 *
 * 为什么行动长在这一屏而不是新建一个 ActionsScreen：工单 §怎么做.2 建议复用这一屏，
 * 少一条路由；这一屏今天已经是「翻自己历史的地方」，行动与结果同属历史。
 */
import React, { useCallback, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
  Modal, TextInput, Alert,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  getGrowthRecords, getRecommendedTopics, listActions, transitionAction, postActionOutcome,
} from '../services/api';
import { useAuth } from '../context/AuthContext';
import { toLocalDate } from '../utils/datetime';
import { newTraceId } from '../utils/traceId';
import type { ActionItem, ActionResultChoice, GrowthRecordItem, GrowthTopic } from '../types';

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

/**
 * 行动的三个分区（工单 §怎么做.2 逐字点名的三格）。
 *
 * `cancelled` 不在这里：这一屏没有取消入口，那一态的行在这一屏不可见——
 * 已作为发现缺陷登记在台账（不是本票顺手补的第四格）。
 */
const ACTION_BUCKETS: { key: string; status: string }[] = [
  { key: 'sectionActive', status: 'active' },
  { key: 'sectionCompleted', status: 'completed' },
  { key: 'sectionSkipped', status: 'skipped' },
];

/**
 * 「结果如何」四选一，顺序即按钮顺序；逐字取 Go 六值里的四格（outcome.go:68-73）。
 * 变差→`worse`、说不清→`unknown`：`unknown` 同时是服务端缺省值（action_outcome.go:96-99），
 * 所以这一档不需要前端另造一个「没答案」的态。
 */
const RESULT_CHOICES: ActionResultChoice[] = ['better', 'same', 'worse', 'unknown'];

// 结果备注的长度不由前端定：outcomes.description 没有上限判据，前端造一个就是第二套规则。

export default function GrowthRecordsScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [records, setRecords] = useState<GrowthRecordItem[]>([]);
  const [topics, setTopics] = useState<GrowthTopic[]>([]);
  const [loading, setLoading] = useState(true);
  // 「拉取失败」与「确实没记录」是两回事：后者给空态文案，前者必须报错 + 可重试。
  const [error, setError] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  // T4-04：行动面那一叠状态。它的三态与上面记录那三态**各记一份**——
  // 共用一个 flag 会把「一条行动都没立」显示成「加载失败」（与 InsightsScreen 同判据）。
  const [actions, setActions] = useState<ActionItem[] | null>(null);
  const [actionsLoading, setActionsLoading] = useState(true);
  const [actionsFailed, setActionsFailed] = useState(false);
  // 服务端截断（`ListCommitments` 是 created_at DESC + LIMIT，默认 50 条）必须说出口：
  // 不消费 `truncated` 就等于把「更早的行动没显示」讲成「你没有更早的行动」。
  const [actionsTruncated, setActionsTruncated] = useState(false);
  // 行内两个动作共用一个忙位：一次只让一颗行动在写，防的是同一次抖动里
  // 「完成」和「跳过」各发出去一次（服务端会回 409，但屏上先长出两种说法）。
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [resultFor, setResultFor] = useState<ActionItem | null>(null);
  const [resultChoice, setResultChoice] = useState<ActionResultChoice | null>(null);
  const [resultNote, setResultNote] = useState('');
  const [resultBusy, setResultBusy] = useState(false);

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

  /**
   * 行动列表单独一个函数：行内写完要重取，而记录与话题那两个源不必跟着重跑。
   * 一次取全量再本地分三格，而不是发三条带 status 的请求——同一个屏上长出三个
   * 真源时刻（三个请求的三个「现在」），分区之间就会互相打脸。
   */
  const loadActions = useCallback(async () => {
    if (!isLoggedIn) {
      setActionsLoading(false);
      return;
    }
    try {
      setActionsLoading(true);
      const page = await listActions();
      setActions(page.items);
      setActionsTruncated(page.truncated);
      setActionsFailed(false);
    } catch {
      setActionsFailed(true);
      setActionsTruncated(false);
    } finally {
      setActionsLoading(false);
    }
  }, [isLoggedIn]);

  // 栈页会重复进入（从 Explore 返回再进来），聚焦即刷新
  useFocusEffect(
    useCallback(() => {
      load();
      loadActions();
    }, [load, loadActions])
  );

  /** 行内「完成」：先落状态，再问结果——顺序反了就会问完结果却把行动留在 active。 */
  const markDone = async (item: ActionItem) => {
    if (busyActionId) return;
    setBusyActionId(item.id);
    try {
      await transitionAction(item.id, 'complete');
      // 分区按服务端给回的状态排，不自己推：`changed=false`（这颗早就是 completed）
      // 在屏上必须是同一种走法，而不是「那就不刷新」。
      await loadActions();
      setResultChoice(null);
      setResultNote('');
      setResultFor(item);
    } catch {
      // 不弹 e.message：那是服务端给运维看的中英双语串，不是界面文案。
      Alert.alert(t('common.error'), t('actions.failed'));
    } finally {
      setBusyActionId(null);
    }
  };

  const markSkip = async (item: ActionItem) => {
    if (busyActionId) return;
    setBusyActionId(item.id);
    try {
      await transitionAction(item.id, 'skip');
      await loadActions();
      Alert.alert(t('actions.skipSaved'));
    } catch {
      Alert.alert(t('common.error'), t('actions.failed'));
    } finally {
      setBusyActionId(null);
    }
  };

  const closeResult = () => setResultFor(null);

  /**
   * 记一次结果：四选一先选中、备注可留空，最后一枚按钮才发请求。
   *
   * 不做「点档位即提交」：那样用户写完备注的那一句已经晚了，而这一面唯一的幂等判据是
   * (同一天·同 type) —— 手滑多点一次就吃掉今天那一格。
   */
  const submitResult = async () => {
    if (!resultFor || !resultChoice || resultBusy) return;
    setResultBusy(true);
    try {
      const receipt = await postActionOutcome(
        resultFor.id, resultChoice, resultNote.trim(), null, newTraceId(),
      );
      setResultFor(null);
      setResultChoice(null);
      // changed=false ⇒ 今天这个 type 已经记过一行，服务端把首次那条回给你：
      // 这是「已经记过了」而不是失败，两种说法在屏上是两句话。
      Alert.alert(receipt.changed ? t('actions.resultSaved') : t('actions.resultSameDay'));
    } catch {
      // **不关弹层**：行动已经落成 completed，结果还没记上；
      // 关掉就等于让用户以为记过了。留在原地，档位与备注都还在。
      Alert.alert(t('common.error'), t('actions.failed'));
    } finally {
      setResultBusy(false);
    }
  };

  // 名字不叫 `buckets`：下面 `groups` 里那个局部桶就叫那个，两个桶同屏必然有人读错。
  const actionBuckets = useMemo(() => ACTION_BUCKETS.map((b) => ({
    ...b,
    items: (actions ?? []).filter((a) => a.status === b.status),
  })), [actions]);

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

  // 不做整屏早退：记录在拉的时候行动区照样能用（两段各有自己的三态位）。
  // 整屏 spinner 会把「行动区」变成记录加载的附属品——两段同源，
  // 一个源失败就连着把另一个源的可用时刻一起吃掉。
  if (!isLoggedIn) {
    return (
      <View style={styles.loading}>
        <Text style={styles.emptyText}>{t('growthRecords.guestEmpty')}</Text>
      </View>
    );
  }

  return (
    <>
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>{t('growthRecords.title')}</Text>

      {/* T4-04：我的行动。三个分区、行内两个动作、完成后立刻问结果。
          这一段的三态与下面记录那段各记一份，谁也不替谁说话。 */}
      <View style={styles.actionsSection}>
        <Text style={styles.sectionTitle}>{t('actions.title')}</Text>
        {actionsLoading && (
          <ActivityIndicator color={Colors.gold400} style={styles.actionsSpinner} />
        )}
        {!actionsLoading && actionsFailed && (
          <View style={styles.stateBox}>
            <Text style={styles.emptyText}>{t('common.loadFailed')}</Text>
            <TouchableOpacity
              style={styles.retryBtn}
              onPress={loadActions}
              accessibilityRole="button"
              accessibilityLabel={t('common.retry')}
            >
              <Text style={styles.retryText}>{t('common.retry')}</Text>
            </TouchableOpacity>
          </View>
        )}
        {!actionsLoading && !actionsFailed && (actions ?? []).length === 0 && (
          <Text style={styles.emptyText}>{t('actions.empty')}</Text>
        )}
        {!actionsLoading && !actionsFailed && actionBuckets.map((bucket) => (
          bucket.items.length === 0 ? null : (
            <View key={bucket.status} style={styles.actionBucket}>
              <Text style={styles.actionBucketTitle}>{t(`actions.${bucket.key}`)}</Text>
              {bucket.items.map((item) => (
                <View key={item.id} style={styles.actionCard}>
                  <Text style={styles.actionTitle}>{item.title}</Text>
                  {!!item.due_at && (
                    <Text style={styles.actionDue}>{t('actions.due', {
                      date: toLocalDate(item.due_at),
                    })}</Text>
                  )}
                  {bucket.status === 'active' && (
                    <View style={styles.actionRow}>
                      <TouchableOpacity
                        style={[styles.actionBtn, busyActionId === item.id && styles.actionBtnDisabled]}
                        onPress={() => markDone(item)}
                        disabled={busyActionId !== null}
                        accessibilityRole="button"
                        accessibilityLabel={t('actions.markDone')}
                      >
                        <Text style={styles.actionBtnText}>{t('actions.markDone')}</Text>
                      </TouchableOpacity>
                      <TouchableOpacity
                        style={[styles.actionBtnGhost, busyActionId === item.id && styles.actionBtnDisabled]}
                        onPress={() => markSkip(item)}
                        disabled={busyActionId !== null}
                        accessibilityRole="button"
                        accessibilityLabel={t('actions.skip')}
                      >
                        <Text style={styles.actionBtnGhostText}>{t('actions.skip')}</Text>
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              ))}
            </View>
          )
        ))}
        {!actionsLoading && !actionsFailed && actionsTruncated && (
          <Text style={styles.actionsTruncatedNote}>{t('actions.truncated')}</Text>
        )}
      </View>

      {/* 记录区的三态只归记录区：拉取中给 spinner，拉不到报错＋重试，
          确实一条都没有才给空态——省掉整屏早退之后，这里必须自己挡住「加载中就说没记录」。 */}
      {loading && (
        <View style={styles.stateBox}>
          <ActivityIndicator color={Colors.gold400} />
        </View>
      )}

      {!loading && (
        <>
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
        </>
      )}

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

      {/* 「结果如何」弹层：完成之后紧接着问，所以它长在 ScrollView 之外
          （弹层里的 TextInput 嵌在竖向 ScrollView 里时，键盘遮挡与滚动争用
          都归外层滚动容器管——与 InsightsScreen 的纠错弹层同一处置）。 */}
      <Modal
        visible={resultFor !== null}
        transparent
        animationType="fade"
        onRequestClose={closeResult}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{t('actions.resultTitle')}</Text>
            <Text style={styles.modalHint}>{t('actions.resultHint')}</Text>
            <View style={styles.resultRow}>
              {RESULT_CHOICES.map((choice) => (
                <TouchableOpacity
                  key={choice}
                  style={[
                    styles.resultChip,
                    resultChoice === choice && styles.resultChipActive,
                    resultBusy && styles.resultBtnDisabled,
                  ]}
                  onPress={() => setResultChoice(choice)}
                  disabled={resultBusy}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: resultChoice === choice }}
                  accessibilityLabel={t(`actions.result.${choice}`)}
                >
                  <Text
                    style={[
                      styles.resultChipText,
                      resultChoice === choice && styles.resultChipTextActive,
                    ]}
                  >
                    {t(`actions.result.${choice}`)}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
            <TextInput
              style={styles.resultNoteInput}
              multiline
              value={resultNote}
              onChangeText={setResultNote}
              placeholder={t('actions.notePlaceholder')}
              placeholderTextColor={Colors.textMuted}
              accessibilityLabel={t('actions.notePlaceholder')}
            />
            <View style={styles.modalActions}>
              <TouchableOpacity
                style={styles.modalCancelBtn}
                onPress={closeResult}
                accessibilityRole="button"
                accessibilityLabel={t('common.close')}
              >
                <Text style={styles.modalCancelText}>{t('common.close')}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[
                  styles.modalSubmitBtn,
                  (!resultChoice || resultBusy) && styles.modalSubmitBtnDisabled,
                ]}
                onPress={submitResult}
                disabled={!resultChoice || resultBusy}
                accessibilityRole="button"
                accessibilityLabel={t('actions.resultSubmit')}
              >
                <Text style={styles.modalSubmitText}>{t('actions.resultSubmit')}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </>
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
  // ── T4-04：我的行动 ──
  actionsSection: { marginBottom: Spacing.lg },
  actionsSpinner: { marginVertical: Spacing.md },
  actionBucket: { marginBottom: Spacing.md },
  actionsTruncatedNote: {
    fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 4,
    lineHeight: LineHeight.caption,
  },
  actionBucketTitle: {
    fontSize: FontSize.sm, color: Colors.gold400, marginBottom: 6, fontWeight: '600',
  },
  actionCard: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  actionTitle: {
    fontSize: FontSize.md, color: Colors.textPrimary, lineHeight: LineHeight.body,
  },
  actionDue: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 4 },
  actionRow: { flexDirection: 'row', gap: Spacing.sm, marginTop: Spacing.sm },
  actionBtn: {
    paddingHorizontal: 14, paddingVertical: 6, borderRadius: BorderRadius.pill,
    backgroundColor: Colors.gold400,
  },
  actionBtnText: { fontSize: FontSize.sm, fontWeight: '700', color: Colors.bgPrimary },
  actionBtnGhost: {
    paddingHorizontal: 14, paddingVertical: 6, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.borderLight,
  },
  actionBtnGhostText: { fontSize: FontSize.sm, color: Colors.textTertiary },
  actionBtnDisabled: { opacity: 0.4 },
  // ── 「结果如何」弹层 ──
  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'center', alignItems: 'center', padding: Spacing.lg,
  },
  modalCard: {
    width: '100%', maxWidth: 460, backgroundColor: Colors.bgSecondary,
    borderRadius: BorderRadius.xl, borderWidth: 1, borderColor: Colors.borderLight, padding: Spacing.lg,
  },
  modalTitle: { fontSize: FontSize.lg, fontWeight: '600', color: Colors.textPrimary },
  modalHint: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4, lineHeight: LineHeight.caption },
  modalActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: Spacing.sm, marginTop: Spacing.md },
  modalCancelBtn: {
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.borderLight,
  },
  modalCancelText: { fontSize: FontSize.md, color: Colors.textTertiary },
  modalSubmitBtn: {
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: BorderRadius.pill,
    backgroundColor: Colors.gold400,
  },
  modalSubmitBtnDisabled: { opacity: 0.4 },
  modalSubmitText: { fontSize: FontSize.md, fontWeight: '700', color: Colors.bgPrimary },
  resultRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: Spacing.md },
  resultChip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.borderLight, backgroundColor: 'rgba(255,255,255,0.03)',
  },
  resultChipActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.12)' },
  resultChipText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  resultChipTextActive: { color: Colors.gold400, fontWeight: '600' },
  resultBtnDisabled: { opacity: 0.4 },
  resultNoteInput: {
    minHeight: 72, marginTop: Spacing.md, padding: Spacing.md,
    borderRadius: BorderRadius.lg, borderWidth: 1, borderColor: Colors.borderLight,
    backgroundColor: 'rgba(255,255,255,0.04)',
    fontSize: FontSize.md, color: Colors.textPrimary, textAlignVertical: 'top',
  },
});
