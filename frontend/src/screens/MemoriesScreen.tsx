/**
 * 记忆管理页面 (P0-F02 + 合规)。
 *
 * - 列出 AI 当前记忆（current 状态）
 * - 左滑/长按删除单条记忆（用户数据删除权）
 */
import React, { useCallback, useState } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, Alert,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { listMemories, deleteMemory, ApiError } from '../services/api';
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

export default function MemoriesScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const requireAuth = useRequireAuth();
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  // 失败态与空态必须分开：拉取失败若落到 memories=[] 就等于是对用户断言「你没有记忆」
  const [error, setError] = useState<string | null>(null);

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
        ListEmptyComponent={
          <Text style={styles.empty}>{t('memories.empty')}</Text>
        }
        renderItem={({ item }) => (
          <View style={styles.card}>
            <Text style={styles.icon}>{CATEGORY_ICONS[item.category] || '📌'}</Text>
            <View style={styles.body}>
              <Text style={styles.text}>{item.content}</Text>
              <Text style={styles.meta}>
                {t(`memories.category.${item.category}`)}{item.created_at ? ` · ${new Date(item.created_at).toLocaleDateString()}` : ''}
              </Text>
            </View>
            <TouchableOpacity style={styles.deleteBtn} onPress={() => onDelete(item)}>
              <Text style={styles.deleteIcon}>🗑</Text>
            </TouchableOpacity>
          </View>
        )}
      />
      )}
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
});
