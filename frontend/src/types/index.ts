/**
 * ClosenAI TypeScript 类型定义。
 *
 * 与后端 backend/models/schemas.py 保持同步。
 */

// ═══ Chat ═══

export interface ChatStreamRequest {
  content: string;
  message_type: 'reply' | 'mood_checkin';
}

export interface ChatHistoryItem {
  id: string;
  role: 'user' | 'ai';
  content: string;
  is_ai_generated?: boolean;
  created_at: string;
}

export interface SSEEvent {
  type: 'token' | 'memory_context' | 'done' | 'error';
  content?: string;
  memories?: Memory[];
  message?: string;
  code?: number;
}

// ═══ Memory ═══

export interface Memory {
  id: string;
  user_id: string;
  content: string;
  category: 'values' | 'preference' | 'relationship' | 'goal' | 'factual';
  status: 'current' | 'superseded' | 'reverted';
  source: 'self' | 'others' | 'hypothetical';
  conviction: number;
  created_at: string;
  superseded_at?: string;
  transition_reason?: string;
}

// ═══ Mood ═══

export type MoodEmoji = '😊' | '😌' | '😰' | '😢' | '😡' | '😴';

export interface MoodCheckinResponse {
  id: string;
  mood_emoji: MoodEmoji;
  mood_label: string;
  ai_interpretation: string;
  streak_updated: number;
  created_at: string;
  // 深度解读（连续 3 天同情绪 / 连续 2 天负面时由后端生成；未触发为 null/缺省）
  deep_interpretation?: string | null;
}

export interface MoodTrendData {
  labels: string[];
  values: number[];
  summary: string;
}

// ═══ Growth ═══

export interface GrowthMetrics {
  emotion: string;
  emotion_change: number;
  social: string;
  social_change: string;
  confidence: string;
  confidence_change: string;
  stress: string;
  stress_change: number;
  curiosity: string;
  curiosity_change: string;
  goals: string;
  goals_change: string;
}

export interface MilestoneItem {
  milestone_type: string; // streak_7 | streak_30 | streak_100 | streak_365
  days: number;
  achieved: boolean;
  achieved_at: string | null;
}

export interface GrowthDashboard {
  date: string;
  greeting: string;
  streak_count: number;
  relationship_level: string;
  level_label: string;
  level_progress: number;
  chat_count: number;
  today_mood: string | null;
  growth_metrics: GrowthMetrics;
  mood_timeline: string[];
  weekly_insight: string | null;
  yesterday_record: string | null;
  mbti_type: string | null;
  milestones: MilestoneItem[];
  streak_freeze_count: number;
}

export interface WeeklyInsight {
  level: string;
  content: string;
  period: string;
}

export interface GrowthTimelineDay {
  date: string;
  value: number;
}

export interface GrowthTimeline {
  days: GrowthTimelineDay[];
}

// ═══ Relationship (P0-F04：关系等级 / 徽章 / 时间线) ═══

/** 等级信息（/growth/level.current 与 relationship-timeline.level 共用）。 */
export interface RelationshipLevelInfo {
  level: number; // 1-4
  label: string; // 后端本地化等级名（如 熟悉 L2 / Familiar L2）
  progress: number; // 0-1，到下一级进度
  active_days: number;
  next_threshold: number | null;
  days_to_next: number;
  unlocks: string[]; // 后端本地化解锁能力列表（§5.15a：首位为该级情感回报）
  reward?: string | null; // §5.15a 情感回报文案；L1 无回报时为 null
}

export interface LevelDetail {
  current: RelationshipLevelInfo;
  levels: Array<{
    level: number;
    label: string;
    threshold: number;
    unlocks: string[];
    reward?: string | null;
    reached: boolean;
  }>;
  note: string; // 等级说明（名称无恋爱含义）
}

export interface BadgeItem {
  milestone_type: string; // streak_7 | streak_30 | streak_100 | streak_365
  days: number;
  name: string; // 后端本地化徽章名
  achieved: boolean;
  achieved_at: string | null;
}

export interface RelationshipTimelineEvent {
  type: 'milestone' | 'record';
  milestone_type?: string;
  record_type?: string;
  title: string; // 后端本地化标题
  content?: string;
  tags?: string[];
  date: string; // YYYY-MM-DD（用户本地日历日）
  created_at: string | null;
}

export interface RelationshipTimeline {
  days_known: number;
  chat_count: number;
  event_count: number;
  level: RelationshipLevelInfo;
  events: RelationshipTimelineEvent[];
}

export interface TimelineDaySummary {
  date: string;
  has_data: boolean;
  mood: { emoji: string; label: string; value: number; interpretation: string | null } | null;
  color: string; // 情绪色块 hex
  chat_count: number;
  records: Array<{ record_type: string; content: string; tags: string[] }>;
  milestones: Array<{ date: string; milestone_type: string; name: string }>;
}

// ═══ Reflection (P0-F04) ═══

export interface ReflectionDraft {
  draft: string;
  related_memory_ids: string[] | null;
}

export interface ReflectionItem {
  id: string;
  content: string;
  related_memory_ids: string[] | null;
  created_at: string;
}

// ═══ Personality ═══

/** 主要性格框架（P0-F05：画像页与解读以所选框架为主）。 */
export type PrimaryFramework = 'mbti' | 'enneagram' | 'astrology';

export interface PersonalityProfile {
  id: string;
  mbti_type: string | null;
  mbti_label: string | null;
  observations: string[];
  unique_traits: string | null;
  version: number;
  refresh_quota_remaining: number;
  cold_start_progress: number | null;
  cold_start_stage: number | null; // 阶段下标 0-4，前端按下标高亮
  zodiac: string | null;           // 太阳星座 key（aries 等，前端按 i18n 翻译）
  enneagram_type: string | null;   // 九型人格编号（1-9）
  created_at: string;
  // ── 对话式发现（P0-F05）渐进字段：后端逐步补齐，前端缺省降级到 cold_start_* ──
  stage?: string | null;                    // 阶段 key（icebreaking/observing/exploring/revealing/applying）
  signals_count?: number | null;            // 已采集的性格信号数
  estimated_completion_days?: number | null; // 预计画像完成所需天数
  dimension_signals?: Record<string, number> | null; // EI/SN/TF/JP 各维度倾向强度 0-1（0.5 为均衡）
  primary_framework?: PrimaryFramework | null;       // 用户选择的主要框架
}

export interface PersonalityRefreshResult {
  success: boolean;
  profile: PersonalityProfile | null;
  message: string;
}

// ── 对话式评估（PRD P0-F06：8 题问答）──

export interface AssessmentQuestion {
  index: number;
  total: number;
  dimension: string; // EI | SN | TF | JP
  text: string;
  options: Record<string, string>; // { a, b, neutral }
}

export interface AssessmentState {
  status: 'not_started' | 'in_progress' | 'completed';
  current_index: number;
  total: number;
  question: AssessmentQuestion | null;
}

export interface AssessmentAnswerResult {
  completed: boolean;
  next_question: AssessmentQuestion | null;
  profile: PersonalityProfile | null;
  message: string;
}

export interface ComboReport {
  content: string;
  cached: boolean;
  generated_at: string;
  personality_type: string | null;
  zodiac: string | null;
  enneagram_type: string | null;
}

// ── 九型人格渐进式评估（PRD P1-F03：36 题，可跨会话断点续答）──

export interface EnneagramQuestion {
  index: number;               // 0 起
  total: number;               // 总题数（36）
  text: string;
  options: Record<string, string>; // { a, b, neutral }，按用户语言下发
}

export interface EnneagramResult {
  enneagram_type: string;      // 1-9
  type_label: string;          // 按用户语言的类型名
  combo_name: string | null;   // MBTI×九型组合名（命中关联表时）
  combo_mbti: string | null;   // 组合对应的 16 型类型
}

export interface EnneagramState {
  status: 'not_started' | 'in_progress' | 'completed';
  current_index: number;       // 下一题下标（= 已答题数）
  total: number;
  question: EnneagramQuestion | null;
  result: EnneagramResult | null;
}

export interface EnneagramAnswerResult {
  completed: boolean;
  next_question: EnneagramQuestion | null;
  result: EnneagramResult | null; // 完成时返回
  message: string;
}

// ═══ User ═══

export type UserTier = 'free' | 'pro' | 'pro_plus';

/** AI 对话风格（P1-F04，后端三枚举校验）。 */
export type ConversationStyle = 'warm' | 'rational' | 'philosophical';

export interface UserProfile {
  id: string;
  name: string | null;
  username?: string | null; // 登录用户名（账号安全页展示）
  mbti_type: string | null;
  language: string | null;
  streak_count: number;
  relationship_level: string;
  chat_count: number;
  tier: UserTier;
  daily_quota_used: number;
  daily_quota_total: number;
  ai_consent: boolean;
  push_time?: string | null;
  conversation_style?: ConversationStyle | null;
  primary_framework?: PrimaryFramework | null; // 主要性格框架（P0-F05）
}

export interface UserSettings {
  name?: string;
  chat_style?: string;
  push_time?: string;
  timezone?: string;
  language?: string;
  push_frequency?: 'daily' | 'important' | 'off';
  push_enabled?: boolean;
  mbti_type?: string;
  conversation_style?: ConversationStyle;
  primary_framework?: PrimaryFramework;
}

// ═══ Discovery ═══

export interface DailyDiscovery {
  has_discovery: boolean;
  content: string | null;
  comparison_type: string | null;
  generated_at: string;
}

// ═══ Onboarding 性格初读卡 (P1-B01 / 架构方案 §二 M1) ═══

/** 快问答案：q = 题目 id，option = 选项 id（与 backend/data/first_read/{zh,en}.json 对齐） */
export interface FirstReadAnswer {
  q: 'conflict' | 'uncertainty' | 'night';
  option: string;
}

/** 初读卡内容（POST/GET /onboarding/first-read 的 data；后端 card_payload 展平而来） */
export interface FirstReadCard {
  id?: string;
  card_type?: string;
  assertions: string[];
  summary: string;
  closer: string;
  persona_type?: string | null;
  language?: string;
  share_url?: string;
  created_at?: string;
}

// ═══ Share (P1-F04) ═══

export type ShareCardVariant = 'classic' | 'minimal' | 'combo';
export type SharePeriod = 'weekly' | 'monthly' | 'quarterly' | 'yearly';

export interface PersonalityCard {
  card_type: string;
  variant: ShareCardVariant;
  layout: 'full' | 'compact' | 'combo';
  user_name: string;
  mbti_type: string | null;
  mbti_label: string | null;
  tagline: string;
  unique_traits: string | null;
  streak_count: number;
  relationship_level: string;
  share_url: string;
  exported_at: string;
  share_text: string;
  observations?: string[];
  dimensions?: { mbti: string | null; zodiac: string | null; enneagram: string | null };
  combo_insight?: string | null;
}

export interface ShareReport {
  period: string;
  period_label: string;
  range: string;
  insight_level: string;
  mood_distribution: Record<string, number>;
  mood_trend: string;
  content: string;
  chat_count?: number;
  share_url?: string;
}

// ═══ Growth Report / Pattern (P1-F03) ═══

export interface PatternDiscovery {
  content: string;
}

// ═══ IAP ═══

export interface SubscriptionStatus {
  tier: UserTier;
  is_active: boolean;
  expires_at: string | null;
  product_id: string | null;
  auto_renew: boolean;
  is_trial: boolean;
}

export type LifeArchiveScope = '1y' | 'all' | 'legacy';

export interface LifeArchiveEntitlement {
  product_id: string;
  scope: LifeArchiveScope;
  transaction_id: string;
  purchase_date: string | null;
  environment: string | null;
}

export interface ReceiptVerifyResult {
  valid: boolean;
  // 收据类型：subscription（自动续期订阅）/ entitlement（Life Archive 一次性买断）。
  // 买断响应里 tier 保持用户当前订阅档位，前端不得据此显示"订阅已生效"。
  kind: 'subscription' | 'entitlement';
  tier: UserTier | null;
  expires_at: string | null;
  is_trial: boolean;
  product_id?: string | null;
  scope?: LifeArchiveScope | null;
  entitlements?: LifeArchiveEntitlement[];
  message: string;
}

export interface EntitlementsResult {
  entitlements: LifeArchiveEntitlement[];
  owned_scopes: LifeArchiveScope[];
  best_scope: LifeArchiveScope | null;
}

export interface LifeArchiveChapter {
  key: string;
  title: string;
  aigc_generated: boolean;
  aigc_label: string | null;
  items: string[];
}

export interface LifeArchiveResult {
  archive_type: string;
  scope: LifeArchiveScope;
  gate: 'entitlement' | 'subscription';
  language: string;
  window_days: number | null;
  generated_at: string;
  from_cache?: boolean;
  stats: {
    active_days: number;
    chat_count: number;
    memory_count: number;
    checkin_count: number;
    message_count: number;
  };
  chapters: LifeArchiveChapter[];
  aigc_mark: string;
  aigc_notice: string;
  share_url: string;
}

// ═══ Auth ═══

export type CodePurpose = 'register' | 'login' | 'reset';

export interface AuthUser {
  id: string;
  email: string | null;
  username: string | null;
  name: string | null;
  tier: UserTier;
  email_verified: boolean;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

// ═══ Navigation ═══

export type TabName = 'home' | 'explore' | 'reflect' | 'profile';

export interface TabConfig {
  name: TabName;
  icon: string;
  label: string;
}