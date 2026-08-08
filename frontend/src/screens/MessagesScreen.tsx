/**
 * 消息中心页面（Reflect 头部 🔔 入口）。
 *
 * 纯本地页：读本地 SQLite ai_messages 表（主动消息缓存），不调后端。
 * - 消息列表（按 scheduled_date 倒序）
 * - 未读高亮，点击标记已读（更新 is_read/read_at）
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { aiMessagesRepo, type AiMessageRow } from '../services/db/aiMessagesRepository';
import { useTranslation } from 'react-i18next';

export default function MessagesScreen() {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<AiMessageRow[]>([]);
  const [loading, setLoading] = useState(true);

  // Stack 页：mount 加载即可
  useEffect(() => {
    aiMessagesRepo
      .getAll()
      .then(setMessages)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // 点击标记已读（本地写，无需登录拦截）
  const onPressMessage = (item: AiMessageRow) => {
    if (item.is_read) return;
    aiMessagesRepo.markAsRead(item.id).catch(() => {});
    setMessages((prev) =>
      prev.map((m) =>
        m.id === item.id ? { ...m, is_read: 1, read_at: new Date().toISOString() } : m
      )
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
        data={messages}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        ListHeaderComponent={<Text style={styles.title}>🔔 {t('messages.title')}</Text>}
        ListEmptyComponent={<Text style={styles.empty}>{t('messages.empty')}</Text>}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={[styles.card, !item.is_read && styles.cardUnread]}
            onPress={() => onPressMessage(item)}
            activeOpacity={item.is_read ? 1 : 0.7}
          >
            {!item.is_read && <View style={styles.unreadDot} />}
            <View style={styles.body}>
              <Text style={[styles.text, !item.is_read && styles.textUnread]}>
                {item.content_preview}
              </Text>
              <Text style={styles.meta}>
                {item.scheduled_date ? new Date(item.scheduled_date).toLocaleDateString() : ''}
              </Text>
            </View>
          </TouchableOpacity>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  title: { fontSize: FontSize.h3, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  empty: { textAlign: 'center', color: Colors.textTertiary, fontSize: FontSize.title, marginTop: 60 },
  card: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.sm,
  },
  cardUnread: {
    backgroundColor: 'rgba(255,215,0,0.06)', borderColor: 'rgba(255,215,0,0.2)',
  },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: Colors.error },
  body: { flex: 1 },
  text: { fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 1.5 },
  textUnread: { color: Colors.textPrimary, fontWeight: '600' },
  meta: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
});
