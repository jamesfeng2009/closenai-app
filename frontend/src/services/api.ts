/**
 * ClosenAI API 服务。
 *
 * 封装所有后端 API 调用。
 */
import { Platform } from 'react-native';
import type {
  ChatStreamRequest, ChatHistoryItem, SSEEvent,
  MoodCheckinResponse, MoodTrendData, MoodEmoji,
  GrowthDashboard, WeeklyInsight,
  PersonalityProfile,
  UserProfile, UserSettings,
  DailyDiscovery,
  SubscriptionStatus,
} from '../types';

// 开发环境 API 地址
const API_BASE = Platform.select({
  ios: 'http://localhost:8000/api/v1',
  android: 'http://10.0.2.2:8000/api/v1',
  default: 'http://localhost:8000/api/v1',
});

const headers = {
  'Content-Type': 'application/json',
};

// ═══ Chat (P0-F01) ═══

export async function streamChat(
  request: ChatStreamRequest,
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (error: string) => void,
): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    });

    const reader = response.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const event: SSEEvent = JSON.parse(line.slice(6));
          switch (event.type) {
            case 'token':
              onToken(event.content || '');
              break;
            case 'done':
              onDone();
              break;
            case 'error':
              onError(event.message || '未知错误');
              break;
          }
        }
      }
    }
  } catch (e) {
    onError((e as Error).message);
  }
}

export async function getChatHistory(limit: number = 20): Promise<ChatHistoryItem[]> {
  const res = await fetch(`${API_BASE}/chat/history?limit=${limit}`, { headers });
  return res.json();
}

// ═══ Mood (P0-F03) ═══

export async function moodCheckin(emoji: MoodEmoji, note?: string): Promise<MoodCheckinResponse> {
  const res = await fetch(`${API_BASE}/mood/checkin`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ emoji, note }),
  });
  return res.json();
}

export async function getMoodTrend(weeks: number = 4): Promise<MoodTrendData> {
  const res = await fetch(`${API_BASE}/mood/trend?weeks=${weeks}`, { headers });
  return res.json();
}

// ═══ Growth (P0-F07) ═══

export async function getGrowthDashboard(): Promise<GrowthDashboard> {
  const res = await fetch(`${API_BASE}/growth/dashboard`, { headers });
  return res.json();
}

export async function getWeeklyInsight(): Promise<WeeklyInsight> {
  const res = await fetch(`${API_BASE}/growth/insights`, { headers });
  return res.json();
}

export async function getTimeline(days: number = 365): Promise<{ days: number; data: unknown[] }> {
  const res = await fetch(`${API_BASE}/growth/timeline?days=${days}`, { headers });
  return res.json();
}

// ═══ Personality (P0-F05) ═══

export async function getPersonalityProfile(): Promise<PersonalityProfile> {
  const res = await fetch(`${API_BASE}/personality/profile`, { headers });
  return res.json();
}

export async function refreshPersonality(): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/personality/refresh`, {
    method: 'POST',
    headers,
  });
  return res.json();
}

// ═══ User (P0-F04) ═══

export async function getUserProfile(): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/user/profile`, { headers });
  return res.json();
}

export async function updateUserSettings(settings: UserSettings): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/user/settings`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(settings),
  });
  return res.json();
}

// ═══ Discovery (P0-F10) ═══

export async function getDailyDiscovery(): Promise<DailyDiscovery> {
  const res = await fetch(`${API_BASE}/discovery/daily`, { headers });
  return res.json();
}

// ═══ IAP ═══

export async function getSubscriptionStatus(): Promise<SubscriptionStatus> {
  const res = await fetch(`${API_BASE}/iap/status`, { headers });
  return res.json();
}