/**
 * 记忆管理页面 (P0-F02 + 合规)。
 *
 * - 列出 AI 当前记忆（current 状态）
 * - 新增一条（POST /memories：带向量化＋判重，不是瞬时写 ⇒ 有中间态、提交后回读）
 * - 点开详情（GET /memories/{id} 回读最新行）＋记录一次演变（POST /memories/version）
 * - 左滑/长按删除单条记忆（用户数据删除权）
 */
import React, { useCallback, useState } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, Alert,
  Modal, TextInput, ScrollView,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  listMemories, createMemory, getMemory, versionMemory, deleteMemory, ApiError,
} from '../services/api';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import type { Memory } from '../types';
import { useTranslation } from 'react-i18next';

const CATEGORY_ICONS: Record<Memory['category'], string> = {
  values: '💎',
  preference: '⭐',
  relationship: '💛',
  goal: '🎯',
  factual: '📌',
};

/** 类别集合与顺序以 backend/models/schemas.py 的 MemoryCreate.category 为准（闸在 memoriesWiring）。 */
const MEMORY_CATEGORIES: Memory['category'][] = ['values', 'preference', 'relationship', 'goal', 'factual'];

/** 内容上限同样取后端（MemoryCreate.content 的 max_length），前端另设小值＝第二真源。 */
const MEMORY_MAX_LENGTH = 4000;

export default function MemoriesScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const requireAuth = useRequireAuth();
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  // 失败态与空态必须分开：拉取失败若落到 memories=[] 就等于是对用户断言「你没有记忆」
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState('');
  const [draftCategory, setDraftCategory] = useState<Memory['category']>('factual');
  // 后端这一次写要过 embedding（0.75<s<0.85 时还要过一次 LLM 矛盾检测），耗时以秒计，
  // 所以按钮必须有「正在检查是否重复」这一档，而不是点完静默几秒
  const [saving, setSaving] = useState(false);

  const [detail, setDetail] = useState<Memory | null>(null);
  const [change, setChange] = useState('');
  const [versioning, setVersioning] = useState(false);

  const load = useCallback(async () => {
    if (!isLoggedIn) {
      setLoading(false);
      return; // 游客不请求（避免 401 噪声），呈现空态
    }
    try {
      setLoading(true);
      setError(null);
      setMemories(await listMemories());
    } catch (e) {
      console.warn('Failed to load memories:', e);
      setError(e instanceof ApiError ? e.message : t('common.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [isLoggedIn, t]);

  // 栈页会重复进入（删除后返回 / 从别处再次打开），聚焦即刷新，而不是只在 mount 时拉一次
  // load 是 async（返回 Promise），useFocusEffect 要的是「同步返回 cleanup」的回调，所以包一层
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const errorMessage = (e: unknown, fallbackKey: string) =>
    e instanceof ApiError ? e.message : t(fallbackKey);

  const onAdd = async () => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
    const content = draft.trim();
    if (!content || saving) return;
    try {
      setSaving(true);
      await createMemory({ content, category: draftCategory });
      // 清草稿在前：判重命中时后端根本不新建（只加固旧条），本地插一条会与库里分叉，
      // 所以一律回读列表，让界面说的就是库里那条
      setDraft('');
      await load();
    } catch (e) {
      Alert.alert(t('common.error'), errorMessage(e, 'errors.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const openDetail = (item: Memory) => {
    setDetail(item);
    setChange('');
    // 列表是上次聚焦时的快照，而演变/加固可能已由 cron 写过；回读失败就退回快照，不空屏
    if (isLoggedIn) getMemory(item.id).then(setDetail).catch(() => {});
  };

  const closeDetail = () => {
    setDetail(null);
    setChange('');
  };

  const onChange = async () => {
    const newContent = change.trim();
    if (!detail || !newContent || versioning) return;
    try {
      setVersioning(true);
      await versionMemory({ memoryId: detail.id, newContent });
      closeDetail();
      await load();
    } catch (e) {
      Alert.alert(t('common.error'), errorMessage(e, 'errors.saveFailed'));
    } finally {
      setVersioning(false);
    }
  };

  const onDelete = (item: Memory) => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
    Alert.alert(
      t('memories.deleteTitle'),
      t('memories.deleteConfirm'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('common.delete'),
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteMemory(item.id);
              setMemories((prev) => prev.filter((m) => m.id !== item.id));
              closeDetail();
            } catch (e) {
              const msg = e instanceof ApiError ? e.message : t('errors.deleteFailed');
              Alert.alert(t('common.error'), msg);
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {!!error && memories.length === 0 ? (
        <View style={styles.stateBox}>
          <Text style={styles.empty}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={load}>
            <Text style={styles.retryText}>{t('common.retry')}</Text>
          </TouchableOpacity>
        </View>
      ) : (
      <FlatList
        data={memories}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        ListHeaderComponent={
          <View style={styles.compose}>
            <Text style={styles.composeTitle}>{t('memories.add')}</Text>
            <TextInput
              style={styles.composeInput}
              value={draft}
              onChangeText={setDraft}
              placeholder={t('memories.addPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              multiline
              maxLength={MEMORY_MAX_LENGTH}
              accessibilityLabel={t('memories.addPlaceholder')}
            />
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipRow}>
              {MEMORY_CATEGORIES.map((c) => (
                <TouchableOpacity
                  key={c}
                  accessibilityRole="button"
                  style={[styles.chip, c === draftCategory && styles.chipActive]}
                  onPress={() => setDraftCategory(c)}
                >
                  <Text style={styles.chipText}>{CATEGORY_ICONS[c]} {t(`memories.category.${c}`)}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
            <TouchableOpacity
              accessibilityRole="button"
              style={[styles.addBtn, (!draft.trim() || saving) && styles.addBtnDisabled]}
              onPress={onAdd}
              disabled={!draft.trim() || saving}
            >
              <Text style={styles.addBtnText}>
                {saving ? t('memories.checking') : t('memories.addSubmit')}
              </Text>
            </TouchableOpacity>
          </View>
        }
        ListEmptyComponent={
          <Text style={styles.empty}>{t('memories.empty')}</Text>
        }
        renderItem={({ item }) => (
          <View style={styles.card}>
            <Text style={styles.icon}>{CATEGORY_ICONS[item.category] || '📌'}</Text>
            <TouchableOpacity
              style={styles.body}
              accessibilityRole="button"
              onPress={() => openDetail(item)}
            >
              <Text style={styles.text}>{item.content}</Text>
              <Text style={styles.meta}>
                {t(`memories.category.${item.category}`)}{item.created_at ? ` · ${new Date(item.created_at).toLocaleDateString()}` : ''}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.deleteBtn} onPress={() => onDelete(item)}>
              <Text style={styles.deleteIcon}>🗑</Text>
            </TouchableOpacity>
          </View>
        )}
      />
      )}

      {/* 详情 + 信念演变（D-G4）：字段全部来自 MemoryResponse，不自造状态 */}
      <Modal visible={!!detail} transparent animationType="fade" onRequestClose={closeDetail}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <ScrollView style={styles.modalScroll}>
              {!!detail && (
                <>
                  <Text style={styles.modalIcon}>{CATEGORY_ICONS[detail.category] || '📌'}</Text>
                  <Text style={styles.modalText}>{detail.content}</Text>
                  <Text style={styles.modalMeta}>{t(`memories.category.${detail.category}`)}</Text>
                  <Text style={styles.modalMeta}>
                    {t(`memories.status.${detail.status}`)} · {t('memories.conviction', {
                      value: Math.round(detail.conviction * 100),
                    })}
                  </Text>
                  {!!detail.created_at && (
                    <Text style={styles.modalMeta}>{new Date(detail.created_at).toLocaleDateString()}</Text>
                  )}
                  {!!detail.superseded_at && (
                    <Text style={styles.modalMeta}>
                      {t('memories.supersededAt', {
                        date: new Date(detail.superseded_at).toLocaleDateString(),
                      })}
                    </Text>
                  )}
                  {!!detail.transition_reason && (
                    <Text style={styles.modalReason}>{detail.transition_reason}</Text>
                  )}
                </>
              )}
            </ScrollView>
            <Text style={styles.changeTitle}>{t('memories.changed')}</Text>
            <TextInput
              style={styles.composeInput}
              value={change}
              onChangeText={setChange}
              placeholder={t('memories.changedPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              multiline
              maxLength={MEMORY_MAX_LENGTH}
              accessibilityLabel={t('memories.changedPlaceholder')}
            />
            <TouchableOpacity
              accessibilityRole="button"
              style={[styles.addBtn, (!change.trim() || versioning) && styles.addBtnDisabled]}
              onPress={onChange}
              disabled={!change.trim() || versioning}
            >
              <Text style={styles.addBtnText}>{t('memories.changedSubmit')}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.cancelBtn} onPress={closeDetail}>
              <Text style={styles.cancelText}>{t('common.close')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  empty: { textAlign: 'center', color: Colors.textTertiary, fontSize: FontSize.title, marginTop: 60, lineHeight: LineHeight.body },
  stateBox: { alignItems: 'center', paddingHorizontal: Spacing.lg, gap: Spacing.md },
  retryBtn: {
    paddingHorizontal: 20, paddingVertical: 8, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.12)', borderWidth: 1, borderColor: Colors.gold400,
  },
  retryText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  compose: {
    backgroundColor: 'rgba(99,102,241,0.06)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.lg, gap: Spacing.sm,
  },
  composeTitle: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  composeInput: {
    minHeight: 64, borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.md,
    paddingHorizontal: Spacing.md, paddingVertical: Spacing.sm, color: Colors.textPrimary,
    fontSize: FontSize.title, lineHeight: LineHeight.body, textAlignVertical: 'top',
    backgroundColor: 'rgba(255,255,255,0.03)',
  },
  chipRow: { flexDirection: 'row' },
  chip: {
    paddingHorizontal: Spacing.md, paddingVertical: 6, borderRadius: BorderRadius.pill,
    borderWidth: 1, borderColor: Colors.borderLight, marginRight: Spacing.sm,
  },
  chipActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.12)' },
  chipText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  addBtn: {
    paddingVertical: 10, borderRadius: BorderRadius.pill, backgroundColor: Colors.gold400,
    alignItems: 'center',
  },
  addBtnDisabled: { opacity: 0.4 },
  addBtnText: { fontSize: FontSize.md, color: Colors.bgPrimary, fontWeight: '700' },
  card: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  icon: { fontSize: 20 },
  body: { flex: 1 },
  text: { fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: LineHeight.body },
  meta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
  deleteBtn: { padding: 8 },
  deleteIcon: { fontSize: 18 },
  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: Spacing.lg,
  },
  modalCard: {
    backgroundColor: Colors.bgCard, borderRadius: BorderRadius.lg, padding: Spacing.lg,
    borderWidth: 1, borderColor: Colors.borderLight, maxHeight: '82%',
  },
  modalScroll: { flexGrow: 0, marginBottom: Spacing.md },
  modalIcon: { fontSize: 24, marginBottom: Spacing.sm },
  modalText: { fontSize: FontSize.lg, color: Colors.textPrimary, lineHeight: LineHeight.body, marginBottom: Spacing.sm },
  modalMeta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 2 },
  modalReason: { fontSize: FontSize.sm, color: Colors.textSecondary, marginTop: Spacing.sm, lineHeight: LineHeight.body },
  changeTitle: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600', marginBottom: Spacing.sm },
  cancelBtn: { marginTop: Spacing.md, alignItems: 'center', paddingVertical: 6 },
  cancelText: { fontSize: FontSize.md, color: Colors.textTertiary },
});
