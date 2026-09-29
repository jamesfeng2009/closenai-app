/**
 * Reflect 对话页面 (P0-F01/P0-F03/P0-F04)。
 *
 * 按 UI 原型 screen-chat 实现：
 * - Streak badge + 等级指示器（来自 /growth/dashboard）
 * - 情绪签到提醒条（今日未签到时显示）
 * - 快捷签到面板（点击提醒条展开）
 * - 真实对话历史（/chat/history）
 * - AI 预填 Reflection 草稿（/reflections/generate，可保存 /reflections）
 * - SSE 流式对话（/chat/stream）
 */
import React, { useEffect, useState, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity, KeyboardAvoidingView,
  Platform, ActivityIndicator, Alert,
} from 'react-native';
import { useNavigation, useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import {
  streamChat, moodCheckin, getChatHistory, getGrowthDashboard,
  generateReflectionDraft, saveReflection, getUserProfile, setAiConsent, ApiError,
} from '../services/api';
import AIConsentModal from '../components/AIConsentModal';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import { chatRepo } from '../services/db/chatRepository';
import { aiMessagesRepo } from '../services/db/aiMessagesRepository';
import { track } from '../services/analytics';
import type { MemoryRef, MoodEmoji } from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode.AI_CONSENT_REQUIRED 对齐
const CODE_AI_CONSENT_REQUIRED = 40303;
// 与后端 ErrorCode.QUOTA_EXCEEDED 对齐（免费配额耗尽 → 引导付费）
const CODE_QUOTA_EXCEEDED = 40301;

interface Message {
  id: string;
  role: 'user' | 'ai';
  content: string;
  isDraft?: boolean;
  draftSaved?: boolean;
  // 历史消息创建时间（本地/远端历史均有；新发送的消息与草稿无此字段）
  created_at?: string;
  // 草稿关联的记忆 ID（保存 Reflection 时透传给后端）
  relatedMemoryIds?: string[] | null;
  // 引用依据（PRD §5.19.3）：正文复述了用户原话的 AI 消息可回溯到原话+日期
  memoryRefs?: MemoryRef[];
}

// 记忆依据折叠态（按消息 id 记录，会话内有效）
type RefsExpanded = Record<string, boolean>;

const MOOD_EMOJIS: MoodEmoji[] = ['😊', '😌', '😰', '😢', '😡', '😴'];

/** 合并本地与远端消息：按 id 去重，远端覆盖本地（同 id 以远端为准）。 */
function mergeMessages(remote: Message[], local: Message[]): Message[] {
  const map = new Map<string, Message>();
  local.forEach((m) => map.set(m.id, m));
  remote.forEach((m) => map.set(m.id, m));
  return Array.from(map.values());
}

/** 按创建时间升序排列（最旧在顶、最新在底，符合聊天 UI 惯例；本地 getRecent 为 DESC 需反转）。 */
function sortByCreatedAtAsc(msgs: Message[]): Message[] {
  return [...msgs].sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
}

export default function ReflectScreen() {
  const { t } = useTranslation();
  const navigation = useNavigation<any>();
  const { isLoggedIn } = useAuth();
  const requireAuth = useRequireAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [showMoodPanel, setShowMoodPanel] = useState(false);
  const [moodCheckedIn, setMoodCheckedIn] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [loading, setLoading] = useState(true);
  const [streak, setStreak] = useState(0);
  const [levelLabel, setLevelLabel] = useState('');
  const [consentVisible, setConsentVisible] = useState(false);
  // 草稿入库进行中（按消息 id 锁定）：无反馈时用户会以为按钮没反应而连点
  const [savingId, setSavingId] = useState<string | null>(null);
  // 消息中心未读数（本地 ai_messages 表，🔔 角标）
  const [unreadCount, setUnreadCount] = useState(0);
  const [refsExpanded, setRefsExpanded] = useState<RefsExpanded>({});
  const scrollRef = useRef<ScrollView>(null);
  const cancelStreamRef = useRef<(() => void) | null>(null);

  // 防止聚焦重入导致重复并发请求
  const loadingRef = useRef(false);

  const loadInitial = React.useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    // 首屏秒开：先读本地对话历史（Tier 1 本地拥有，离线可用）
    const local = await chatRepo.getRecent(20).catch(() => []);
    // 本地 getRecent 为 created_at DESC，统一升序（最旧在顶、最新在底）
    let msgs: Message[] = sortByCreatedAtAsc(local.map((h) => ({
      id: h.id,
      role: h.role === 'assistant' ? 'ai' : 'user',
      content: h.content_preview,
      created_at: h.created_at,
    })));
    setMessages(msgs);

    try {
      // 后台拉取远端最新数据，合并刷新（Tier 1 双向同步）
      const [dashboard, history, profile] = await Promise.all([
        getGrowthDashboard().catch(() => null),
        getChatHistory(20).catch(() => null),
        getUserProfile().catch(() => null),
      ]);
      // 合规 5.1.2(i)：未同意 AI 数据共享时先弹说明弹窗
      if (profile && !profile.ai_consent) {
        setConsentVisible(true);
      }
      if (dashboard) {
        setStreak(dashboard.streak_count);
        setLevelLabel(dashboard.relationship_level || dashboard.level_label || '');
        setMoodCheckedIn(!!dashboard.today_mood);
      }

      const remote: Message[] = (history || []).map((h) => ({
        id: h.id,
        role: h.role,
        content: h.content,
        created_at: h.created_at,
        memoryRefs: h.memory_refs ?? [],
      }));
      // 本地与远端按 id 去重合并；远端为真相源（覆盖本地旧内容，写入本地缓存）
      const merged = mergeMessages(remote, local.map((h) => ({
        id: h.id,
        role: h.role === 'assistant' ? 'ai' : 'user',
        content: h.content_preview,
        created_at: h.created_at,
      })));
      msgs = sortByCreatedAtAsc(merged);
      if (merged.length > 0) {
        await chatRepo.addAll(
          remote.map((m) => ({
            id: m.id,
            role: m.role === 'ai' ? 'assistant' : 'user',
            content_preview: m.content,
            full_content_url: null,
            // 透传远端创建时间，保持本地缓存时间轴与远端一致
            created_at: m.created_at ?? new Date().toISOString(),
          })),
        );
      }

      // 无历史对话且已同意数据共享时，预填今日 Reflection 草稿（P0-F04）
      if (msgs.length === 0 && profile?.ai_consent) {
        try {
          const draft = await generateReflectionDraft();
          if (draft.draft) {
            msgs.push({
              id: `draft-${Date.now()}`,
              role: 'ai',
              content: draft.draft,
              isDraft: true,
              relatedMemoryIds: draft.related_memory_ids,
            });
          }
        } catch {
          // 草稿生成失败不阻塞对话
        }
      }
      setMessages(msgs);
    } catch (e) {
      console.warn('Failed to load reflect screen:', e);
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, []);

  // 卸载时取消进行中的 SSE 流
  useEffect(() => () => cancelStreamRef.current?.(), []);

  // Tab 常驻不卸载：每次聚焦都重新加载（streak/签到提醒需刷新；mount 时也会触发一次）
  // 游客态跳过请求（401 噪声）
  useFocusEffect(
    React.useCallback(() => {
      // 每次聚焦刷新未读角标（从消息中心返回时清除已读掉的计数；纯本地查询，游客也可读）
      aiMessagesRepo.getUnreadCount().then(setUnreadCount).catch(() => {});
      if (isLoggedIn) {
        loadInitial();
      } else {
        setLoading(false);
      }
    }, [isLoggedIn, loadInitial])
  );

  // 同意：持久化并关闭弹窗，进入 AI 对话
  const onConsentAgree = async () => {
    try {
      await setAiConsent(true);
    } catch (e) {
      console.warn('Failed to save AI consent:', e);
    }
    setConsentVisible(false);
  };

  // 拒绝：持久化选择并回到成长 Tab（AI 功能停用，其他功能可用）
  const onConsentDecline = async () => {
    try {
      await setAiConsent(false);
    } catch (e) {
      console.warn('Failed to save AI consent:', e);
    }
    setConsentVisible(false);
    navigation.navigate('Growth');
  };

  // 配额耗尽（40301）：引导付费，可跳 Paywall
  const showQuotaAlert = () => {
    Alert.alert(
      t('reflect.quotaTitle'),
      t('reflect.quotaMessage'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        { text: t('reflect.quotaUpgrade'), onPress: () => navigation.navigate('Paywall') },
      ],
    );
  };

  const sendMessage = () => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
    const text = input.trim();
    if (!text || isStreaming) return;

    const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    // 立即写入本地对话历史（Tier 1 本地拥有，离线不丢失，在线后同步远端）
    chatRepo
      .add({
        id: userMsg.id,
        role: 'user',
        content_preview: text,
        full_content_url: null,
        created_at: new Date().toISOString(),
      })
      .catch(() => {});

    const aiMsgId = `ai-${Date.now()}`;
    setMessages((prev) => [...prev, { id: aiMsgId, role: 'ai', content: '' }]);
    setIsStreaming(true);
    // 行为事件：用户发送对话（仅行为元数据，无内容）
    track('chat_message_sent', { feature: 'chat', action: 'send' });

    cancelStreamRef.current = streamChat(
      { content: text, message_type: 'reply' },
      (token) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === aiMsgId ? { ...m, content: m.content + token } : m))
        );
      },
      () => setIsStreaming(false),
      (error, code) => {
        // 未同意 AI 数据共享：移除占位气泡并弹出 consent 弹窗（合规 5.1.2(i)）
        if (code === CODE_AI_CONSENT_REQUIRED) {
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          setConsentVisible(true);
          setIsStreaming(false);
          return;
        }
        // 配额耗尽：移除占位气泡并引导付费
        if (code === CODE_QUOTA_EXCEEDED) {
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          setIsStreaming(false);
          showQuotaAlert();
          return;
        }
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiMsgId ? { ...m, content: t('reflect.aiError', { error }) } : m
          )
        );
        setIsStreaming(false);
      }
    );
  };

  const quickMoodCheckin = async (emoji: MoodEmoji) => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
    try {
      const result = await moodCheckin(emoji);
      setMessages((prev) => [
        ...prev,
        { id: `mood-${Date.now()}`, role: 'ai', content: result.ai_interpretation },
        // 连续异常触发的深度解读作为后续一条 AI 消息展示
        ...(result.deep_interpretation
          ? [{ id: `mood-deep-${Date.now()}`, role: 'ai' as const, content: result.deep_interpretation }]
          : []),
      ]);
      setMoodCheckedIn(true);
      setShowMoodPanel(false);
      setStreak(result.streak_updated);
    } catch (e) {
      // 配额耗尽（签到解读也计入免费配额）：引导付费
      if (e instanceof ApiError && e.code === CODE_QUOTA_EXCEEDED) {
        showQuotaAlert();
        return;
      }
      const msg = e instanceof ApiError ? e.message : t('errors.checkinFailed');
      Alert.alert(t('common.error'), msg);
    }
  };

  const onSaveDraft = async (msg: Message) => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
    if (savingId) return; // 单条进行中：忽略连点，避免同一草稿重复入库
    setSavingId(msg.id);
    try {
      await saveReflection(msg.content, msg.relatedMemoryIds);
      setMessages((prev) =>
        prev.map((m) => (m.id === msg.id ? { ...m, draftSaved: true } : m))
      );
    } catch (e) {
      const text = e instanceof ApiError ? e.message : t('errors.saveFailed');
      Alert.alert(t('common.error'), text);
    } finally {
      setSavingId(null);
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
            <Text style={styles.streakText}>{streak}</Text>
          </View>
          {!!levelLabel && (
            <TouchableOpacity
              onPress={() => { if (requireAuth()) navigation.navigate('RelationshipTimeline'); }}
            >
              <Text style={styles.levelBadge}>✨ {levelLabel}</Text>
            </TouchableOpacity>
          )}
        </View>
        <Text style={styles.headerTitle}>ClosenAI</Text>
        <View style={styles.headerRight}>
          <TouchableOpacity onPress={() => navigation.navigate('Messages')}>
            <View>
              <Text style={styles.iconBtn}>🔔</Text>
              {unreadCount > 0 && (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>
                    {unreadCount > 99 ? '99+' : unreadCount}
                  </Text>
                </View>
              )}
            </View>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.navigate('Profile')}>
            <Text style={styles.iconBtn}>⚙️</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* 情绪签到提醒条 */}
      {!moodCheckedIn && !showMoodPanel && (
        <TouchableOpacity style={styles.moodReminder} onPress={() => setShowMoodPanel(true)}>
          <Text style={styles.moodReminderIcon}>💭</Text>
          <Text style={styles.moodReminderText}>{t('reflect.moodReminder')}</Text>
          <Text style={styles.moodReminderTime}>{t('reflect.moodReminderTime')}</Text>
        </TouchableOpacity>
      )}

      {/* 快捷签到面板 */}
      {showMoodPanel && (
        <View style={styles.moodPanel}>
          <Text style={styles.moodPanelTitle}>{t('reflect.moodPanelTitle')}</Text>
          <View style={styles.moodPanelRow}>
            {MOOD_EMOJIS.map((emoji) => (
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
        <Text style={styles.modeLabel}>{t('reflect.modeLabel')}</Text>
        {messages.map((msg) => (
          <View key={msg.id} style={[styles.msgRow, msg.role === 'user' && styles.msgRowUser]}>
            {msg.role === 'ai' && <Text style={styles.avatar}>✨</Text>}
            <View style={[styles.bubble, msg.role === 'user' ? styles.bubbleUser : styles.bubbleAi, msg.isDraft && styles.bubbleDraft]}>
              <Text style={[styles.bubbleText, msg.role === 'user' && styles.bubbleTextUser]}>
                {msg.content}
              </Text>
              {msg.role === 'ai' && <Text style={styles.aiTag}>{t('reflect.aiTag')}</Text>}
              {!!msg.memoryRefs?.length && (
                <TouchableOpacity
                  onPress={() =>
                    setRefsExpanded((prev) => ({ ...prev, [msg.id]: !prev[msg.id] }))
                  }
                >
                  <Text style={styles.memoryRefsToggle}>
                    {t('reflect.memoryRefs', {
                      n: msg.memoryRefs.length,
                      arrow: refsExpanded[msg.id] ? '▲' : '▾',
                    })}
                  </Text>
                </TouchableOpacity>
              )}
              {!!msg.memoryRefs?.length && refsExpanded[msg.id] && msg.memoryRefs.map((ref) => (
                <Text key={ref.memory_id} style={styles.memoryRefItem}>
                  「{ref.excerpt}」
                  {ref.created_at
                    ? ` · ${new Date(ref.created_at).toLocaleDateString()}`
                    : ''}
                </Text>
              ))}
              {msg.isDraft && !msg.draftSaved && (
                <TouchableOpacity
                  style={[styles.saveDraftBtn, savingId === msg.id && styles.saveDraftBtnPending]}
                  disabled={savingId === msg.id}
                  onPress={() => onSaveDraft(msg)}
                >
                  <Text style={styles.saveDraftText}>
                    {savingId === msg.id ? t('common.saving') : t('reflect.saveDraft')}
                  </Text>
                </TouchableOpacity>
              )}
              {msg.isDraft && msg.draftSaved && (
                <Text style={styles.draftSavedTag}>✓ {t('common.done')}</Text>
              )}
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
          placeholder={t('reflect.placeholder')}
          // placeholder 用 textTertiary（#71717A）：textMuted（#4A4A5A）在深色输入框上对比度不足
          placeholderTextColor={Colors.textTertiary}
          multiline
          onSubmitEditing={sendMessage}
        />
        <TouchableOpacity
          style={[styles.sendBtn, isStreaming && styles.sendBtnDisabled]}
          onPress={sendMessage}
          disabled={isStreaming}
        >
          <Text style={styles.sendBtnText}>→</Text>
        </TouchableOpacity>
      </View>

      {/* AI 数据共享同意弹窗（合规 5.1.2(i)） */}
      <AIConsentModal
        visible={consentVisible}
        onAgree={onConsentAgree}
        onDecline={onConsentDecline}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: Spacing.lg, paddingTop: 50, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  headerTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary },
  headerRight: { flexDirection: 'row', gap: 12 },
  iconBtn: { fontSize: 18 },
  badge: {
    position: 'absolute', top: -4, right: -8,
    minWidth: 14, height: 14, borderRadius: 7,
    backgroundColor: Colors.error, justifyContent: 'center', alignItems: 'center',
    paddingHorizontal: 3,
  },
  badgeText: { fontSize: 9, fontWeight: '700', color: Colors.textPrimary },
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
  bubbleText: { fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: LineHeight.body },
  bubbleTextUser: { color: Colors.textPrimary },
  memoryRefsToggle: { fontSize: FontSize.sm, color: Colors.gold400, opacity: 0.8, marginTop: 6 },
  memoryRefItem: {
    fontSize: FontSize.sm, color: Colors.textTertiary, fontStyle: 'italic',
    lineHeight: 18, marginTop: 4, paddingLeft: 8,
    borderLeftWidth: 2, borderLeftColor: 'rgba(255,215,0,0.25)',
  },
  aiTag: { fontSize: FontSize.xs, color: Colors.textTertiary, marginTop: 6, paddingTop: 4 },
  saveDraftBtn: {
    marginTop: 8, alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,215,0,0.15)', borderWidth: 1, borderColor: Colors.gold400,
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: BorderRadius.pill,
  },
  saveDraftText: { fontSize: FontSize.sm, color: Colors.gold400, fontWeight: '600' },
  saveDraftBtnPending: { opacity: 0.5 },
  draftSavedTag: { fontSize: FontSize.sm, color: Colors.success, marginTop: 8 },
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
  sendBtnDisabled: { opacity: 0.5 },
  sendBtnText: { fontSize: 20, color: Colors.textPrimary, fontWeight: '700' },
});
