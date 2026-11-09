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
  streamChat, moodCheckin, getGrowthDashboard,
  generateReflectionDraft, saveReflection, getUserProfile, setAiConsent, ApiError,
} from '../services/api';
import AIConsentModal from '../components/AIConsentModal';
import PromptLibrarySheet from '../components/PromptLibrarySheet';
import {
  saveableText,
  traceabilityFor,
  type PromptTraceability,
  type ReflectionPrompt,
} from '../services/reflectionPrompts';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import { chatRepo } from '../services/db/chatRepository';
import { chatOutboxRepo } from '../services/db/chatOutboxRepository';
import { flushChatOutbox, pullChatHistory } from '../services/chatSync';
import { resolveMemoryRefs } from '../services/evidence';
import { aiMessagesRepo } from '../services/db/aiMessagesRepository';
import { moodCheckinRepo } from '../services/db/moodCheckinRepository';
import { reflectionRepo } from '../services/db/reflectionRepository';
import { toLocalDate } from '../utils/datetime';
import { track } from '../services/analytics';
import { trackFunnelOnce } from '../services/funnel';
import type { MemoryRef, MoodEmoji } from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode.AI_CONSENT_REQUIRED 对齐
const CODE_AI_CONSENT_REQUIRED = 40303;
// 与后端 ErrorCode.QUOTA_EXCEEDED 对齐（免费配额耗尽 → 引导付费）
const CODE_QUOTA_EXCEEDED = 40301;
// 与后端 ErrorCode.INVALID_PARAMS 对齐（KG-26：提示库版本过期／溯源字段非法）
const CODE_INVALID_PARAMS = 40001;

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
// 签到附一句话的长度上限：与后端 MoodCheckinRequest.note 的 max_length（schemas.py:87）同值。
// 另设一个更小的数＝长出第二真源，界面上会被后端打回「too long」而不是这里先拦住。
const MOOD_NOTE_MAX_LENGTH = 1000;

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
  // 签到附一句话（D-G8）：只服务这一个弹层，签到成功即清空，不留成常驻草稿
  const [moodNote, setMoodNote] = useState('');
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
  // 提示库（KG-26）：面板开关、当前选中的提示与其库版本（版本必须一起上送，后端按它判过期）
  const [promptVisible, setPromptVisible] = useState(false);
  const [activePrompt, setActivePrompt] = useState<ReflectionPrompt | null>(null);
  const [promptVersion, setPromptVersion] = useState('');
  const [promptSaving, setPromptSaving] = useState(false);
  const scrollRef = useRef<ScrollView>(null);
  const cancelStreamRef = useRef<(() => void) | null>(null);
  // 签到请求的连点守卫（P2 #25）：状态更新要等下一次渲染，同一帧的第二次
  // 点击读到的还是旧值，必须用 ref 同步占位。
  const moodCheckinRef = useRef(false);
  const [moodCheckingIn, setMoodCheckingIn] = useState(false);

  // 防止聚焦重入导致重复并发请求
  const loadingRef = useRef(false);

  const loadInitial = React.useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    // P2 #28：先把离线攒下的用户消息补投出去、再按水位拉新，最后才读本地缓存。
    // 两步都失败也继续渲染本地历史（离线可用是 Tier 1 的承诺）。
    await flushChatOutbox().catch(() => {});
    const pulled = await pullChatHistory().catch(() => []);
    // 首屏秒开：本地对话历史（Tier 1 本地拥有，离线可用；pull 刚落库的新消息也在里面）
    const local = await chatRepo.getRecent(50).catch(() => []);
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
      const [dashboard, profile] = await Promise.all([
        getGrowthDashboard().catch(() => null),
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

      const remote: Message[] = pulled.map((h) => ({
        // 用户消息的本地 id 就是 client_message_id：靠它把远端行与本地缓存认成同一条，
        // 否则刷新后同一句话会显示两遍（服务端 uuid vs 本地 u-...）
        id: h.client_message_id || h.id,
        role: h.role,
        content: h.content,
        created_at: h.created_at,
        memoryRefs: h.memory_refs ?? [],
      }));
      // 主动消息同步进本地 ai_messages 表（消息中心数据源；同 id upsert 幂等）
      const proactive = pulled.filter((h) => h.message_type === 'proactive');
      for (const m of proactive) {
        await aiMessagesRepo.upsertFromRemote(m.id, m.content, m.created_at).catch(() => {});
      }
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

      // #34 洞察依据回链：/reflections/generate 只给 related_memory_ids，不给原话，
      // 这里按 id 聚合读路径补上「记忆依据 ▾」；解析不到的 id 丢弃而非猜测
      const pendingIds = msgs.flatMap((m) => m.relatedMemoryIds ?? []);
      if (pendingIds.length > 0) {
        const refs = await resolveMemoryRefs(pendingIds).catch(() => []);
        if (refs.length > 0) {
          const byId = new Map(refs.map((r) => [r.memory_id, r]));
          msgs = msgs.map((m) =>
            m.relatedMemoryIds?.length
              ? {
                  ...m,
                  memoryRefs: m.relatedMemoryIds
                    .map((id) => byId.get(id))
                    .filter((r): r is MemoryRef => !!r),
                }
              : m
          );
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
    const sentAt = new Date().toISOString();
    // 立即写入本地对话历史（Tier 1 本地拥有，离线不丢失），同时记进 outbox：
    // 在线流失败 / 纯离线时，页面下次聚焦会按 client_message_id 幂等补投（P2 #28）
    chatRepo
      .add({
        id: userMsg.id,
        role: 'user',
        content_preview: text,
        full_content_url: null,
        created_at: sentAt,
      })
      .catch(() => {});
    chatOutboxRepo
      .enqueue({
        client_message_id: userMsg.id,
        content: text,
        message_type: 'reply',
        created_at: sentAt,
      })
      .catch(() => {});

    const aiMsgId = `ai-${Date.now()}`;
    setMessages((prev) => [...prev, { id: aiMsgId, role: 'ai', content: '' }]);
    setIsStreaming(true);
    // 行为事件：用户发送对话（仅行为元数据，无内容）
    track('chat_message_sent', { feature: 'chat', action: 'send' });
    // 漏斗第二段：本设备第一条书写记录（trackFunnelOnce 内部门闸，只报一次）
    if (messages.length === 0) void trackFunnelOnce('first_record_created');

    cancelStreamRef.current = streamChat(
      { content: text, message_type: 'reply', client_message_id: userMsg.id },
      (token) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === aiMsgId ? { ...m, content: m.content + token } : m))
        );
      },
      () => {
        setIsStreaming(false);
        // 在线流已把这条用户消息落库：outbox 标 sent，避免下次聚焦重复补投
        chatOutboxRepo.markSent([userMsg.id]).catch(() => {});
      },
      (error, code) => {
        // 服务端永久拒绝的两类错误：留在 outbox 只会每次聚焦再撞一次同样的墙
        if (code === CODE_AI_CONSENT_REQUIRED || code === CODE_QUOTA_EXCEEDED) {
          chatOutboxRepo.markDiscarded([userMsg.id], error).catch(() => {});
        }
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
    if (moodCheckinRef.current) return; // 连点：上一次请求未回，忽略本次
    moodCheckinRef.current = true;
    setMoodCheckingIn(true);
    // 附一句话（D-G8）：trim 后为空 ⇒ 传 undefined，不给库里写空串（口径同睡前复盘 step1「可空=跳过」）
    const note = moodNote.trim() || undefined;
    try {
      const result = await moodCheckin(emoji, note);
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
      setMoodNote('');
      setStreak(result.streak_updated);
      // 本地签到记录落库（Tier 1 用户原创；热力图离线可用）。
      // note 写用户实际输入的那句（服务端响应里没有 note 字段，回读不了）；
      // 没输入时不要用 null 覆盖当天此前写过的备注，强度也保留本地既有值。
      moodCheckinRepo
        .mergeRemoteCheckin(result, {
          ...(note === undefined ? {} : { note }),
          ai_interpretation: result.deep_interpretation
            ? `${result.ai_interpretation}\n\n${result.deep_interpretation}`
            : result.ai_interpretation,
        })
        .catch(() => {});
    } catch (e) {
      // 配额耗尽（签到解读也计入免费配额）：引导付费
      if (e instanceof ApiError && e.code === CODE_QUOTA_EXCEEDED) {
        showQuotaAlert();
        return;
      }
      const msg = e instanceof ApiError ? e.message : t('errors.checkinFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      moodCheckinRef.current = false;
      setMoodCheckingIn(false);
    }
  };

  /** 本地反思记录落库（Tier 1 用户原创；探索页反思列表离线可用）。两条保存路径共用一份形状。 */
  const cacheReflectionLocally = (saved: { id: string; content: string; created_at: string }) => {
    reflectionRepo
      .save({
        id: saved.id,
        date: toLocalDate(saved.created_at),
        mood: null,
        mood_emoji: null,
        summary: saved.content,
        full_content_url: null,
        is_ai_generated: 0,
        created_at: saved.created_at,
        updated_at: saved.created_at,
      })
      .catch(() => {});
  };

  /**
   * 两条保存出口共用的那一段：存本地缓存 + 报漏斗节点（KG-26 把它从两处收成一处）。
   *
   * `first_reflection_saved` 只有一个发射点是**判据**，不只是整洁：草稿采纳和照提示自己写
   * 都算「第一条真的存进反思记录」，分两处写任何一处漏改，首日路径那一跳的转化率
   * 就只统计到一条路径——而这正是 KG-25 补这五格的理由。
   */
  const persistReflection = async (
    content: string,
    relatedMemoryIds?: string[] | null,
    promptMeta?: PromptTraceability | null
  ) => {
    const saved = await saveReflection(content, relatedMemoryIds ?? null, promptMeta);
    cacheReflectionLocally(saved);
    void trackFunnelOnce('first_reflection_saved');
    return saved;
  };

  /**
   * 选中／换一条提示（KG-26）。
   *
   * 非破坏性口径：输入框还停在上一条提示的原句（用户没动笔）才换成新句；
   * 用户已经写了字就**不覆盖正文**，只换溯源——「换一条」不该吞掉他刚打的字。
   */
  const applyPrompt = (prompt: ReflectionPrompt, version: string, action: 'select' | 'swap') => {
    setInput((prev) => {
      const base = prev.trim();
      if (action === 'swap' && activePrompt && base === activePrompt.question.trim()) {
        return prompt.question;
      }
      return base ? base : prompt.question;
    });
    setActivePrompt(prompt);
    setPromptVersion(version);
    track('prompt_selected', {
      feature: 'prompt_library',
      action,
      section: prompt.cluster,
    });
  };

  /**
   * 把输入框里的正文按当前提示存成反思。
   *
   * 空内容（只有空白）在本地就拦住、不发请求；后端同样有一条 40001 的闸
   * （`ReflectionCreate.content` 的 field_validator），这里是「立刻有反馈」那一层。
   */
  const onSaveFromPrompt = async () => {
    if (!requireAuth()) return;
    if (!activePrompt) return;
    const text = saveableText(input);
    if (!text) {
      Alert.alert(t('common.error'), t('reflect.promptBlank'));
      return;
    }
    const cluster = activePrompt.cluster;
    setPromptSaving(true);
    try {
      await persistReflection(text, null, traceabilityFor(activePrompt.id, promptVersion));
      track('prompt_saved', { feature: 'prompt_library', action: 'save', section: cluster, result: 'ok' });
      setInput('');
      setActivePrompt(null);
      setPromptVersion('');
      Alert.alert(t('common.done'), t('reflect.promptSaved'));
    } catch (e) {
      track('prompt_saved', { feature: 'prompt_library', action: 'save', section: cluster, result: 'error' });
      if (e instanceof ApiError && e.code === CODE_INVALID_PARAMS) {
        // 提示库版本过期：留着这条重试只会再撞一次 40001，所以清掉选中、让用户重选。
        setActivePrompt(null);
        setPromptVersion('');
        Alert.alert(t('common.error'), t('reflect.promptStale'));
      } else {
        const msg = e instanceof ApiError ? e.message : t('errors.saveFailed');
        Alert.alert(t('common.error'), msg);
      }
    } finally {
      setPromptSaving(false);
    }
  };

  const onSaveDraft = async (msg: Message) => {
    if (!requireAuth()) return; // 游客拦截 → 跳 AuthScreen
    if (savingId) return; // 单条进行中：忽略连点，避免同一草稿重复入库
    setSavingId(msg.id);
    try {
      // 草稿是 AI 写的，不带提示库溯源（KG-26 的溯源只服务「照提示自己写并保存」那条路）
      await persistReflection(msg.content, msg.relatedMemoryIds);
      setMessages((prev) =>
        prev.map((m) => (m.id === msg.id ? { ...m, draftSaved: true } : m))
      );
      // 漏斗节点在 persistReflection 里报——两条出口共用那一格发射点。
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
          {/* 附一句话（D-G8）：可选，点了表情才随签到一起上送；HomeScreen 的一键签到不加输入框 */}
          <TextInput
            style={styles.moodNoteInput}
            value={moodNote}
            onChangeText={setMoodNote}
            placeholder={t('reflect.moodNotePlaceholder')}
            placeholderTextColor={Colors.textTertiary}
            multiline
            maxLength={MOOD_NOTE_MAX_LENGTH}
            accessibilityLabel={t('reflect.moodNoteLabel')}
          />
          <View style={styles.moodPanelRow}>
            {MOOD_EMOJIS.map((emoji) => (
              <TouchableOpacity
                key={emoji}
                style={styles.moodQuickBtn}
                onPress={() => quickMoodCheckin(emoji)}
                disabled={moodCheckingIn}
                accessibilityState={{ disabled: moodCheckingIn, busy: moodCheckingIn }}
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

      {/* 提示库入口（KG-26）：面板只读静态内容，游客也能打开看；真正写库由「存为反思」拦 */}
      <View style={styles.promptBar}>
        <TouchableOpacity
          style={styles.promptEntry}
          onPress={() => {
            setPromptVisible(true);
            track('prompt_library_open', { feature: 'prompt_library', action: 'open' });
          }}
          accessibilityRole="button"
        >
          <Text style={styles.promptEntryText}>💡 {t('reflect.promptEntry')}</Text>
        </TouchableOpacity>
        {!!activePrompt && (
          <View style={styles.promptActiveRow}>
            <Text style={styles.promptActiveText} numberOfLines={1}>
              {activePrompt.title}
            </Text>
            <TouchableOpacity
              onPress={() => {
                setActivePrompt(null);
                setPromptVersion('');
              }}
              accessibilityRole="button"
              accessibilityLabel={t('common.close')}
            >
              <Text style={styles.promptClearText}>✕</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

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

      {/* 选中提示时的存为反思出口：正文取输入框里（可编辑过的）内容，溯源随请求一起上送 */}
      {!!activePrompt && (
        <TouchableOpacity
          style={[styles.promptSaveBtn, promptSaving && styles.promptSaveBtnPending]}
          disabled={promptSaving}
          onPress={() => void onSaveFromPrompt()}
          accessibilityRole="button"
        >
          <Text style={styles.promptSaveText}>
            {promptSaving ? t('common.saving') : t('reflect.promptSave')}
          </Text>
        </TouchableOpacity>
      )}

      {/* AI 数据共享同意弹窗（合规 5.1.2(i)） */}
      <AIConsentModal
        visible={consentVisible}
        onAgree={onConsentAgree}
        onDecline={onConsentDecline}
      />

      <PromptLibrarySheet
        visible={promptVisible}
        currentId={activePrompt?.id ?? null}
        onClose={() => setPromptVisible(false)}
        onUse={(prompt, version) => applyPrompt(prompt, version, 'select')}
        onSwap={(prompt, version) => applyPrompt(prompt, version, 'swap')}
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
  moodNoteInput: {
    minHeight: 44, maxHeight: 88, textAlignVertical: 'top',
    backgroundColor: Colors.bgTertiary, borderRadius: BorderRadius.lg,
    borderWidth: 1, borderColor: Colors.borderLight,
    paddingHorizontal: 12, paddingVertical: 8, marginBottom: 8,
    fontSize: FontSize.title, color: Colors.textPrimary, lineHeight: LineHeight.body,
  },
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
  // 提示库（KG-26）：入口行在输入框上方；选中后多一条「存为反思」的出口
  promptBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: Spacing.lg, paddingTop: Spacing.sm,
    backgroundColor: 'rgba(13,13,18,0.95)',
  },
  promptEntry: {
    borderRadius: BorderRadius.pill, borderWidth: 1, borderColor: Colors.borderLight,
    paddingHorizontal: 12, paddingVertical: 6,
  },
  promptEntryText: { fontSize: FontSize.xl, color: Colors.textSecondary },
  promptActiveRow: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: 8, borderRadius: BorderRadius.md,
    backgroundColor: 'rgba(255,215,0,0.10)', borderWidth: 1, borderColor: Colors.borderGold,
    paddingHorizontal: 10, paddingVertical: 6,
  },
  promptActiveText: { flex: 1, fontSize: FontSize.md, color: Colors.gold400 },
  promptClearText: { fontSize: FontSize.xl, color: Colors.textTertiary, paddingHorizontal: 2 },
  promptSaveBtn: {
    marginHorizontal: Spacing.lg, marginBottom: Spacing.md,
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 12, alignItems: 'center',
  },
  promptSaveBtnPending: { opacity: 0.6 },
  promptSaveText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
});
