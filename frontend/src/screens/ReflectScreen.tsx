/**
 * Reflect 对话页面 (P0-F01/P0-F03)。
 *
 * 按 UI 原型 screen-chat 实现：
 * - Streak badge + 等级指示器
 * - 情绪签到提醒条（今日未签到时显示）
 * - 快捷签到面板（点击提醒条展开）
 * - AI 记忆延续消息
 * - AI 预填 Reflection 草稿
 * - 对话消息列表
 * - 输入框
 */
import React, { useState, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity, KeyboardAvoidingView, Platform,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { streamChat, moodCheckin } from '../services/api';
import type { MoodEmoji } from '../types';

interface Message {
  id: string;
  role: 'user' | 'ai';
  content: string;
  isDraft?: boolean;
}

export default function ReflectScreen() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'ai-1', role: 'ai',
      content: '你昨天提到项目 deadline 提前了，今天还是因为这个感到 😰 吗？\n\n我根据昨天的对话帮你起草了今天的 Reflection，你看看需要修改吗？',
    },
    {
      id: 'ai-draft', role: 'ai', isDraft: true,
      content: '📋 Today\'s Reflection · 草稿\n\n情绪：😰 焦虑\n可能的原因：项目 deadline 提前，团队人手不足（基于昨天对话）\n值得感谢的事：（点击补充）',
    },
  ]);
  const [input, setInput] = useState('');
  const [showMoodPanel, setShowMoodPanel] = useState(false);
  const [moodCheckedIn, setMoodCheckedIn] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const scrollRef = useRef<ScrollView>(null);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');

    const aiMsgId = `ai-${Date.now()}`;
    setMessages((prev) => [...prev, { id: aiMsgId, role: 'ai', content: '' }]);
    setIsStreaming(true);

    await streamChat(
      { content: text, message_type: 'reply' },
      (token) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === aiMsgId ? { ...m, content: m.content + token } : m))
        );
      },
      () => setIsStreaming(false),
      (error) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === aiMsgId ? { ...m, content: `[错误: ${error}]` } : m))
        );
        setIsStreaming(false);
      }
    );
  };

  const quickMoodCheckin = async (emoji: MoodEmoji) => {
    try {
      const result = await moodCheckin(emoji);
      setMessages((prev) => [
        ...prev,
        { id: `mood-${Date.now()}`, role: 'ai', content: result.ai_interpretation },
      ]);
      setMoodCheckedIn(true);
      setShowMoodPanel(false);
    } catch (e) {
      console.warn('Mood checkin failed:', e);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <View style={styles.streakBadge}>
            <Text style={styles.streakIcon}>🔥</Text>
            <Text style={styles.streakText}>15</Text>
          </View>
          <Text style={styles.levelBadge}>✨ 知己 L2</Text>
        </View>
        <Text style={styles.headerTitle}>ClosenAI</Text>
        <View style={styles.headerRight}>
          <Text style={styles.iconBtn}>🔔</Text>
          <Text style={styles.iconBtn}>⚙️</Text>
        </View>
      </View>

      {/* 情绪签到提醒条 */}
      {!moodCheckedIn && !showMoodPanel && (
        <TouchableOpacity style={styles.moodReminder} onPress={() => setShowMoodPanel(true)}>
          <Text style={styles.moodReminderIcon}>💭</Text>
          <Text style={styles.moodReminderText}>今天还没签到 · 点这里选个表情</Text>
          <Text style={styles.moodReminderTime}>3 秒完成</Text>
        </TouchableOpacity>
      )}

      {/* 快捷签到面板 */}
      {showMoodPanel && (
        <View style={styles.moodPanel}>
          <Text style={styles.moodPanelTitle}>今天心情怎么样？</Text>
          <View style={styles.moodPanelRow}>
            {(['😊', '😌', '😰', '😢', '😡', '😴'] as MoodEmoji[]).map((emoji) => (
              <TouchableOpacity
                key={emoji}
                style={styles.moodQuickBtn}
                onPress={() => quickMoodCheckin(emoji)}
              >
                <Text style={styles.moodQuickEmoji}>{emoji}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>
      )}

      {/* Messages */}
      <ScrollView
        ref={scrollRef}
        style={styles.messages}
        onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
      >
        <Text style={styles.modeLabel}>✨ Reflection Mode · 智能草稿</Text>
        {messages.map((msg) => (
          <View key={msg.id} style={[styles.msgRow, msg.role === 'user' && styles.msgRowUser]}>
            {msg.role === 'ai' && <Text style={styles.avatar}>✨</Text>}
            <View style={[styles.bubble, msg.role === 'user' ? styles.bubbleUser : styles.bubbleAi, msg.isDraft && styles.bubbleDraft]}>
              <Text style={[styles.bubbleText, msg.role === 'user' && styles.bubbleTextUser]}>
                {msg.content}
              </Text>
              {msg.role === 'ai' && <Text style={styles.aiTag}>AI 生成</Text>}
            </View>
          </View>
        ))}
      </ScrollView>

      {/* Input */}
      <View style={styles.inputBar}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="输入消息..."
          placeholderTextColor={Colors.textMuted}
          multiline
          onSubmitEditing={sendMessage}
        />
        <TouchableOpacity style={styles.sendBtn} onPress={sendMessage}>
          <Text style={styles.sendBtnText}>→</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: Spacing.lg, paddingTop: 50, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  headerTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary },
  headerRight: { flexDirection: 'row', gap: 12 },
  iconBtn: { fontSize: 18 },
  streakBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: 'rgba(255,215,0,0.1)', borderWidth: 1, borderColor: Colors.borderGoldStrong,
    borderRadius: BorderRadius.pill, paddingHorizontal: 12, paddingVertical: 4,
  },
  streakIcon: { fontSize: 14 },
  streakText: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.gold400 },
  levelBadge: { fontSize: FontSize.lg, color: Colors.textSecondary },
  moodReminder: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    padding: 8, paddingHorizontal: 16,
    backgroundColor: 'rgba(99,102,241,0.08)', borderBottomWidth: 1, borderBottomColor: 'rgba(99,102,241,0.12)',
  },
  moodReminderIcon: { fontSize: 14 },
  moodReminderText: { fontSize: FontSize.lg, color: Colors.textSecondary, flex: 1 },
  moodReminderTime: { fontSize: FontSize.sm, color: Colors.textTertiary },
  moodPanel: {
    padding: 10, paddingHorizontal: 16,
    backgroundColor: 'rgba(99,102,241,0.04)', borderBottomWidth: 1, borderBottomColor: 'rgba(99,102,241,0.1)',
  },
  moodPanelTitle: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: 8 },
  moodPanelRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  moodQuickBtn: { width: 40, height: 40, justifyContent: 'center', alignItems: 'center' },
  moodQuickEmoji: { fontSize: 24 },
  messages: { flex: 1, padding: Spacing.lg },
  modeLabel: { fontSize: FontSize.sm, color: Colors.textTertiary, textAlign: 'center', marginBottom: 12 },
  msgRow: { flexDirection: 'row', marginBottom: 12, alignItems: 'flex-start' },
  msgRowUser: { justifyContent: 'flex-end' },
  avatar: { fontSize: 20, marginRight: 8, marginTop: 4 },
  bubble: { maxWidth: '80%', padding: 12, paddingHorizontal: 16, borderRadius: 18 },
  bubbleAi: { backgroundColor: Colors.bgTertiary, borderWidth: 1, borderColor: Colors.borderLight, borderBottomLeftRadius: 4 },
  bubbleUser: { backgroundColor: Colors.accent800, borderBottomRightRadius: 4 },
  bubbleDraft: {
    backgroundColor: 'rgba(255,215,0,0.08)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
  },
  bubbleText: { fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: 1.5 },
  bubbleTextUser: { color: Colors.textPrimary },
  aiTag: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 6, paddingTop: 4 },
  inputBar: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 8,
    padding: Spacing.md, paddingHorizontal: Spacing.lg,
    backgroundColor: 'rgba(13,13,18,0.95)', borderTopWidth: 1, borderTopColor: Colors.borderLight,
  },
  input: {
    flex: 1, maxHeight: 100,
    backgroundColor: Colors.bgTertiary, borderRadius: BorderRadius.lg,
    paddingHorizontal: 14, paddingVertical: 10,
    fontSize: FontSize.title, color: Colors.textPrimary,
  },
  sendBtn: {
    width: 40, height: 40, borderRadius: 20,
    backgroundColor: Colors.accent500, justifyContent: 'center', alignItems: 'center',
  },
  sendBtnText: { fontSize: 20, color: Colors.textPrimary, fontWeight: '700' },
});