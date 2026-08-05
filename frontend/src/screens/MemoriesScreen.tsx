/**
 * 记忆管理页面 (P0-F02 + 合规)。
 *
 * - 列出 AI 当前记忆（current 状态）
 * - 左滑/长按删除单条记忆（用户数据删除权）
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, Alert,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { listMemories, deleteMemory, ApiError } from '../services/api';
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
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    try {
      setLoading(true);
      setMemories(await listMemories());
    } catch (e) {
      console.warn('Failed to load memories:', e);
    } finally {
      setLoading(false);
    }
  };

  const onDelete = (item: Memory) => {
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
                {t(`memories.category.${item.category}`)} · {item.created_at?.slice(0, 10)}
              </Text>
            </View>
            <TouchableOpacity style={styles.deleteBtn} onPress={() => onDelete(item)}>
              <Text style={styles.deleteIcon}>🗑</Text>
            </TouchableOpacity>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  empty: { textAlign: 'center', color: Colors.textTertiary, fontSize: FontSize.title, marginTop: 60 },
  card: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  icon: { fontSize: 20 },
  body: { flex: 1 },
  text: { fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: 1.5 },
  meta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
  deleteBtn: { padding: 8 },
  deleteIcon: { fontSize: 18 },
});
