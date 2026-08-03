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
  created_at: string;
}

export interface SSEEvent {
  type: 'token' | 'memory_context' | 'done' | 'error';
  content?: string;
  memories?: Memory[];
  message?: string;
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
}

export interface WeeklyInsight {
  level: string;
  content: string;
  period: string;
}

// ═══ Personality ═══

export interface PersonalityProfile {
  id: string;
  mbti_type: string | null;
  mbti_label: string | null;
  observations: string[];
  unique_traits: string | null;
  version: number;
  refresh_quota_remaining: number;
  cold_start_progress: number | null;
  cold_start_stage: string | null;
  created_at: string;
}

// ═══ User ═══

export type UserTier = 'free' | 'pro' | 'pro_plus';

export interface UserProfile {
  id: string;
  name: string | null;
  mbti_type: string | null;
  streak_count: number;
  relationship_level: string;
  chat_count: number;
  tier: UserTier;
  daily_quota_used: number;
  daily_quota_total: number;
}

export interface UserSettings {
  name?: string;
  chat_style?: string;
  push_time?: string;
  timezone?: string;
}

// ═══ Discovery ═══

export interface DailyDiscovery {
  has_discovery: boolean;
  content: string | null;
  comparison_type: string | null;
  generated_at: string;
}

// ═══ IAP ═══

export interface SubscriptionStatus {
  tier: UserTier;
  is_active: boolean;
  expires_at: string | null;
  product_id: string | null;
  auto_renew: boolean;
}

// ═══ Navigation ═══

export type TabName = 'home' | 'explore' | 'reflect' | 'profile';

export interface TabConfig {
  name: TabName;
  icon: string;
  label: string;
}