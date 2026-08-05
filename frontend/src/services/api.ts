/**
 * ClosenAI API 服务。
 *
 * 封装所有后端 API 调用：
 * - 统一解包后端响应信封 {code, message, data, trace_id, user_id}
 * - 统一错误处理（ApiError 携带业务错误码）
 * - SSE 流式对话基于 XMLHttpRequest（React Native fetch 不支持流式 body）
 */
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import i18n from '../i18n';
import type {
  ChatStreamRequest, ChatHistoryItem, SSEEvent,
  MoodCheckinResponse, MoodTrendData, MoodEmoji,
  GrowthDashboard, WeeklyInsight, GrowthTimeline,
  PersonalityProfile, PersonalityRefreshResult,
  AssessmentState, AssessmentAnswerResult, ComboReport,
  UserProfile, UserSettings,
  DailyDiscovery,
  PersonalityCard, ShareCardVariant, SharePeriod, ShareReport, PatternDiscovery,
  SubscriptionStatus, ReceiptVerifyResult,
  ReflectionDraft, ReflectionItem,
  Memory,
} from '../types';

// 开发环境 API 地址
const API_BASE = Platform.select({
  ios: 'http://localhost:8000/api/v1',
  android: 'http://10.0.2.2:8000/api/v1',
  default: 'http://localhost:8000/api/v1',
});

// ═══ 设备级用户标识（P0 阶段：X-User-Id 透传，正式鉴权上线后替换为 JWT）═══

const USER_ID_KEY = '@closenai/device_user_id';
let cachedUserId: string | null = null;

function generateUuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/** 初始化/获取设备级用户 ID（App 启动时调用，保证数据按设备隔离）。 */
export async function initApiUser(): Promise<string> {
  if (cachedUserId) return cachedUserId;
  let id: string | null = null;
  try {
    id = await AsyncStorage.getItem(USER_ID_KEY);
  } catch {
    // ignore
  }
  if (!id) {
    id = generateUuid();
    try {
      await AsyncStorage.setItem(USER_ID_KEY, id);
    } catch {
      // ignore
    }
  }
  cachedUserId = id;
  return id;
}

/** 重置设备身份（退出登录/注销后调用）：清除本地用户 ID 并生成新身份。 */
export async function resetApiUser(): Promise<void> {
  cachedUserId = null;
  try {
    await AsyncStorage.removeItem(USER_ID_KEY);
  } catch {
    // ignore
  }
  await initApiUser();
}

// ═══ 统一请求封装（信封解包 + 错误处理）═══

export class ApiError extends Error {
  code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
    this.name = 'ApiError';
  }
}

// 动态请求头：Content-Type + 语言透传（X-Language）+ 用户标识（X-User-Id）
// 后端按「X-Language header → users.language → en」优先级解析 LLM 回复语言
function getHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Language': (i18n.language || 'en').slice(0, 2),
  };
  if (cachedUserId) {
    headers['X-User-Id'] = cachedUserId;
  }
  return headers;
}

interface Envelope<T> {
  code: number;
  message: string;
  data: T;
}

function isEnvelope(body: unknown): body is Envelope<unknown> {
  return (
    typeof body === 'object' &&
    body !== null &&
    'code' in body &&
    'data' in body &&
    typeof (body as { code: unknown }).code === 'number'
  );
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { ...getHeaders(), ...(options.headers as Record<string, string> | undefined) },
    });
  } catch {
    throw new ApiError(-1, i18n.t('errors.network'));
  }

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // 非 JSON 响应
  }

  if (!res.ok) {
    const msg =
      isEnvelope(body) && typeof body.message === 'string'
        ? body.message
        : i18n.t('errors.httpFailed', { status: res.status });
    const code = isEnvelope(body) ? body.code : res.status;
    throw new ApiError(code, msg);
  }

  if (isEnvelope(body)) {
    if (body.code !== 200) {
      throw new ApiError(body.code, body.message || i18n.t('errors.requestFailed'));
    }
    return body.data as T;
  }
  return body as T;
}

// ═══ Chat (P0-F01) ═══

/**
 * SSE 流式对话。
 *
 * React Native 的 fetch 不支持流式响应体（response.body 为 null），
 * 这里用 XMLHttpRequest 的 onprogress 增量读取 responseText 实现 SSE 解析。
 *
 * @returns 取消函数（调用可中断流）
 */
export function streamChat(
  requestBody: ChatStreamRequest,
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (error: string, code?: number) => void,
  onMemoryContext?: (memories: SSEEvent['memories']) => void,
): () => void {
  const xhr = new XMLHttpRequest();
  xhr.open('POST', `${API_BASE}/chat/stream`);
  const headers = getHeaders();
  Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));

  let processed = 0; // 已处理的 responseText 长度
  let buffer = '';
  let settled = false;

  const finish = (fn: () => void) => {
    if (settled) return;
    settled = true;
    fn();
  };

  const handleLine = (line: string) => {
    if (!line.startsWith('data: ')) return;
    let event: SSEEvent;
    try {
      event = JSON.parse(line.slice(6));
    } catch {
      return;
    }
    switch (event.type) {
      case 'token':
        onToken(event.content || '');
        break;
      case 'memory_context':
        onMemoryContext?.(event.memories);
        break;
      case 'done':
        finish(onDone);
        break;
      case 'error':
        finish(() => onError(event.message || i18n.t('errors.unknown'), event.code));
        break;
    }
  };

  const pump = () => {
    buffer += xhr.responseText.slice(processed);
    processed = xhr.responseText.length;
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    lines.forEach(handleLine);
  };

  xhr.onprogress = pump;
  xhr.onload = () => {
    // 非 2xx（如 403 AI_CONSENT_REQUIRED）：响应为 JSON 信封而非 SSE 流，按错误处理
    if (xhr.status < 200 || xhr.status >= 300) {
      let message = i18n.t('errors.httpFailed', { status: xhr.status });
      let code: number = xhr.status;
      try {
        const body = JSON.parse(xhr.responseText);
        if (typeof body?.message === 'string') message = body.message;
        if (typeof body?.code === 'number') code = body.code;
      } catch {
        // 非 JSON 响应，保留默认错误
      }
      finish(() => onError(message, code));
      return;
    }
    pump();
    if (buffer.trim()) handleLine(buffer.trim());
    // 流正常结束但未收到 done 事件时，兜底完成
    finish(onDone);
  };
  xhr.onerror = () => finish(() => onError(i18n.t('errors.network')));
  xhr.ontimeout = () => finish(() => onError(i18n.t('errors.timeout')));
  xhr.timeout = 60000;

  xhr.send(JSON.stringify(requestBody));
  return () => xhr.abort();
}

export async function getChatHistory(limit: number = 20): Promise<ChatHistoryItem[]> {
  return request<ChatHistoryItem[]>(`/chat/history?limit=${limit}`);
}

// ═══ Mood (P0-F03) ═══

export async function moodCheckin(emoji: MoodEmoji, note?: string): Promise<MoodCheckinResponse> {
  return request<MoodCheckinResponse>('/mood/checkin', {
    method: 'POST',
    body: JSON.stringify({ emoji, note }),
  });
}

export async function getMoodTrend(days: number = 14): Promise<MoodTrendData> {
  return request<MoodTrendData>(`/mood/trend?days=${days}`);
}

// ═══ Growth (P0-F07) ═══

export async function getGrowthDashboard(): Promise<GrowthDashboard> {
  return request<GrowthDashboard>('/growth/dashboard');
}

export async function getWeeklyInsight(): Promise<WeeklyInsight> {
  return request<WeeklyInsight>('/growth/insights');
}

export async function getTimeline(days: number = 365): Promise<GrowthTimeline> {
  return request<GrowthTimeline>(`/growth/timeline?days=${days}`);
}

// ═══ Reflection (P0-F04) ═══

export async function generateReflectionDraft(context?: string): Promise<ReflectionDraft> {
  return request<ReflectionDraft>('/reflections/generate', {
    method: 'POST',
    body: JSON.stringify({ context: context ?? null }),
  });
}

export async function saveReflection(
  content: string,
  relatedMemoryIds?: string[] | null,
): Promise<ReflectionItem> {
  return request<ReflectionItem>('/reflections', {
    method: 'POST',
    body: JSON.stringify({ content, related_memory_ids: relatedMemoryIds ?? null }),
  });
}

export async function listReflections(limit: number = 20): Promise<ReflectionItem[]> {
  return request<ReflectionItem[]>(`/reflections?limit=${limit}`);
}

// ═══ Personality (P0-F06) ═══

export async function getPersonalityProfile(): Promise<PersonalityProfile> {
  return request<PersonalityProfile>('/personality/profile');
}

export async function refreshPersonality(): Promise<PersonalityRefreshResult> {
  return request<PersonalityRefreshResult>('/personality/refresh', { method: 'POST' });
}

/** 查询对话式评估状态（not_started / in_progress / completed + 下一题）。 */
export async function getAssessment(): Promise<AssessmentState> {
  return request<AssessmentState>('/personality/assessment');
}

/** 开始/续答 8 题对话式评估，返回当前题。 */
export async function startAssessment(): Promise<AssessmentState> {
  return request<AssessmentState>('/personality/assessment/start', { method: 'POST' });
}

/** 提交当前题答案：快捷选项（option）或自由输入（answer）二选一。 */
export async function answerAssessment(
  payload: { option: 'a' | 'b' | 'neutral' } | { answer: string },
): Promise<AssessmentAnswerResult> {
  return request<AssessmentAnswerResult>('/personality/assessment/answer', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** 生成 Pro 组合报告（16 型人格 × 星座 × 九型交叉解读；每月 1 份缓存）。 */
export async function generateComboReport(): Promise<ComboReport> {
  return request<ComboReport>('/personality/combo-report', { method: 'POST' });
}

// ═══ User (P0-F04) ═══

export async function getUserProfile(): Promise<UserProfile> {
  return request<UserProfile>('/user/profile');
}

export async function updateUserSettings(settings: UserSettings): Promise<UserProfile> {
  return request<UserProfile>('/user/settings', {
    method: 'PATCH',
    body: JSON.stringify(settings),
  });
}

export async function registerPushToken(token: string, platform: 'ios' | 'android'): Promise<void> {
  await request<{ status: string }>('/user/push-token', {
    method: 'POST',
    body: JSON.stringify({ token, platform }),
  });
}

/** 登出时清除推送 Token（此后不再收到推送）。 */
export async function clearPushToken(): Promise<void> {
  await request<{ status: string }>('/user/push-token', { method: 'DELETE' });
}

/** 注销账号：级联删除全部数据，不可恢复。 */
export async function deleteAccount(): Promise<void> {
  await request<{ deleted: boolean }>('/user/account', { method: 'DELETE' });
}

/** 设置 AI 数据共享同意状态（合规 5.1.2(i)）。 */
export async function setAiConsent(consent: boolean): Promise<boolean> {
  const res = await request<{ ai_consent: boolean }>('/user/ai-consent', {
    method: 'POST',
    body: JSON.stringify({ consent }),
  });
  return res.ai_consent;
}

// ═══ Memory (P0-F02) ═══

export async function listMemories(limit: number = 50): Promise<Memory[]> {
  return request<Memory[]>(`/memories?limit=${limit}`);
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await request<{ deleted: boolean }>(`/memories/${memoryId}`, { method: 'DELETE' });
}

// ═══ Discovery (P0-F10) ═══

export async function getDailyDiscovery(): Promise<DailyDiscovery> {
  return request<DailyDiscovery>('/discovery/daily');
}

// ═══ Share (P1-F04) ═══

// 后端 share 端点 language 为显式 Query 参数（默认 en），必须按当前 UI 语言透传
function langParam(): string {
  return (i18n.language || 'en').slice(0, 2);
}

/** 导出性格名片（classic/minimal 免费，combo 为 Pro 权益 40302）。 */
export async function getPersonalityCard(variant: ShareCardVariant): Promise<PersonalityCard> {
  return request<PersonalityCard>(`/share/personality-card?variant=${variant}&language=${langParam()}`);
}

/** 导出周期报告（weekly 免费；monthly/quarterly/yearly 为 Pro 权益 40302；同周期后端缓存）。 */
export async function getShareReport(period: SharePeriod): Promise<ShareReport> {
  return request<ShareReport>(`/share/report/${period}?language=${langParam()}`);
}

// ═══ Advanced Insights (P1-F03) ═══

/** 跨维度模式发现（LLM 实时生成，点击触发，勿自动轮询）。 */
export async function getPatternDiscovery(): Promise<PatternDiscovery> {
  return request<PatternDiscovery>('/growth/patterns');
}

// ═══ IAP (P1-F02) ═══

export async function getSubscriptionStatus(): Promise<SubscriptionStatus> {
  return request<SubscriptionStatus>('/iap/status');
}

export async function verifyReceipt(
  receiptData: string,
  productId: string,
): Promise<ReceiptVerifyResult> {
  // 不再传 is_sandbox：前端无法可靠判断环境（TestFlight 为 release 构建但走沙箱）。
  // 后端 iap_service.verify 的 is_sandbox 默认 False，并依据 App Store 状态码
  // 21007/21008 自动切换沙箱/生产端点重试，可正确覆盖 TestFlight 与生产。
  return request<ReceiptVerifyResult>('/iap/verify', {
    method: 'POST',
    body: JSON.stringify({
      receipt_data: receiptData,
      product_id: productId,
    }),
  });
}
