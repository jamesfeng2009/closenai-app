/**
 * ClosenAI API 服务。
 *
 * 封装所有后端 API 调用：
 * - 统一解包后端响应信封 {code, message, data, trace_id, user_id}
 * - 统一错误处理（ApiError 携带业务错误码）
 * - JWT 鉴权：access_token 注入 Authorization，401 自动用 refresh_token 刷新重试
 * - SSE 流式对话基于 XMLHttpRequest（React Native fetch 不支持流式 body）
 */
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import i18n from '../i18n';
import { newTraceId } from '../utils/traceId';
import { noteLastTrace } from './monitoring';
import { clearPendingReferral, peekPendingReferral } from './referral';
import type {
  ChatStreamRequest, ChatHistoryItem, SSEEvent,
  MoodCheckinResponse, MoodTrendData, MoodEmoji,
  GrowthDashboard, WeeklyInsight, GrowthTimeline,
  GrowthRecordItem, GrowthTopic,
  LevelDetail, BadgeItem, RelationshipTimeline, TimelineDaySummary,
  PersonalityProfile, PersonalityRefreshResult,
  AssessmentState, AssessmentAnswerResult, ComboReport,
  EnneagramState, EnneagramAnswerResult,
  UserProfile, UserSettings,
  ExportJob, ExportJobList,
  PersonalityCard, ShareCardVariant, SharePeriod, ShareReport, PatternDiscovery,
  ShareLink, YearInReviewCard,
  OnThisDayResponse,
  FirstReadAnswer, FirstReadCard,
  JourneyEventsResponse, MemoryRef,
  CurriculumWeekResponse,
  BedtimeTodayResponse, BedtimeStepResponse,
  SubscriptionStatus, ReceiptVerifyResult, EntitlementsResult,
  LifeArchiveScope,
  ReflectionDraft, ReflectionItem,
  Memory,
  AuthResponse, AuthUser, CodePurpose,
} from '../types';

// API 地址：生产用 EXPO_PUBLIC_API_BASE 注入（.env / EAS 环境变量），
// 未注入时回落到本机开发地址——回落脚本地而不是空串，是为了让「忘了配」在
// 开发机上表现正常、在生产包里立刻表现为请求失败（可观测），而不是静默打到 localhost。
const DEV_API_BASE = Platform.select({
  ios: 'http://localhost:8000/api/v1',
  android: 'http://10.0.2.2:8000/api/v1',
  default: 'http://localhost:8000/api/v1',
});
const API_BASE = (process.env.EXPO_PUBLIC_API_BASE || '').replace(/\/+$/, '') || DEV_API_BASE;

// ═══ 认证态：access/refresh token 存 SecureStore（iOS Keychain）═══

const ACCESS_TOKEN_KEY = '@closenai/access_token';
const REFRESH_TOKEN_KEY = '@closenai/refresh_token';
const USER_KEY = '@closenai/auth_user';

let accessToken: string | null = null;
let refreshToken: string | null = null;
let authUser: AuthUser | null = null;

/** 启动时加载持久化的登录态（App.tsx 冷启动关键路径调用）。 */
export async function loadSession(): Promise<void> {
  try {
    accessToken = await SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
    refreshToken = await SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
    const userJson = await SecureStore.getItemAsync(USER_KEY);
    authUser = userJson ? (JSON.parse(userJson) as AuthUser) : null;
  } catch {
    // ignore
  }
}

export function isLoggedIn(): boolean {
  return !!accessToken;
}

export function getAuthUser(): AuthUser | null {
  return authUser;
}

/** 更新内存 + SecureStore 中的用户摘要（getMe 刷新后调用，token 不变）。 */
export async function updateAuthUser(user: AuthUser): Promise<void> {
  authUser = user;
  try {
    await SecureStore.setItemAsync(USER_KEY, JSON.stringify(user));
  } catch {
    // ignore
  }
}

export function getRefreshToken(): string | null {
  return refreshToken;
}

async function setAuth(resp: AuthResponse): Promise<void> {
  accessToken = resp.access_token;
  refreshToken = resp.refresh_token;
  authUser = resp.user;
  try {
    await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, resp.access_token);
    await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, resp.refresh_token);
    await SecureStore.setItemAsync(USER_KEY, JSON.stringify(resp.user));
  } catch {
    // ignore
  }
}

/** 清除本机登录态（退出登录 / 注销 / refresh 失效时调用）。 */
export async function clearAuth(): Promise<void> {
  accessToken = null;
  refreshToken = null;
  authUser = null;
  try {
    await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
    await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
    await SecureStore.deleteItemAsync(USER_KEY);
  } catch {
    // ignore
  }
}

// ═══ 会话重置通知（401 刷新失败 / 退出登录 / 注销 → 回登录页）═══

const sessionListeners = new Set<() => void>();

/** 订阅会话重置事件（App 根组件挂载时订阅，返回取消函数）。 */
export function onSessionReset(fn: () => void): () => void {
  sessionListeners.add(fn);
  return () => {
    sessionListeners.delete(fn);
  };
}

export function notifySessionReset(): void {
  sessionListeners.forEach((fn) => fn());
}

// ═══ 统一请求封装（信封解包 + 错误处理 + 401 自动刷新）═══

export class ApiError extends Error {
  code: number;
  /** 服务端本次采用的 trace（来自响应回显头）；网络没通时没有这个值。 */
  traceId?: string;
  constructor(code: number, message: string, traceId?: string) {
    super(message);
    this.code = code;
    this.name = 'ApiError';
    this.traceId = traceId;
  }
}

// 动态请求头：Content-Type + 语言透传（X-Language）+ 鉴权（Authorization Bearer）
// 后端按「X-Language header → users.language → en」优先级解析 LLM 回复语言
function getHeaders(): Record<string, string> {
  // 批 T-D：每请求一个新 trace id（生成形状保证能被服务端 T-8 消毒闸原样放行）。
  // 落进 monitoring 的「最近一次」只为崩溃上报关联，只存内存。
  const traceId = newTraceId();
  noteLastTrace(traceId);
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Language': (i18n.language || 'en').slice(0, 2),
    'X-Trace-Id': traceId,
  };
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

/** 响应头里的采用值；拿不到（环境差异/头缺失）就当没有，不许造第二个真源。 */
function responseTrace(res: Response): string | undefined {
  try {
    return res.headers.get('X-Trace-Id') ?? undefined;
  } catch {
    return undefined;
  }
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

// refresh 单飞：并发 401 只触发一次刷新，共享结果
let refreshing: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    if (!refreshToken) return false;
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) return false;
      const body = await res.json();
      if (body?.code !== 200 || !body.data) return false;
      await setAuth(body.data as AuthResponse);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/** RequestInit + binary：为 true 时成功态按字节返回，不走 JSON 信封解包。 */
type RequestOptions = RequestInit & { binary?: boolean };

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { binary: wantsBinary, ...fetchOptions } = options;
  const doRequest = (): Promise<Response> =>
    fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      headers: { ...getHeaders(), ...(fetchOptions.headers as Record<string, string> | undefined) },
    });

  let res: Response;
  try {
    res = await doRequest();
  } catch {
    throw new ApiError(-1, i18n.t('errors.network'));
  }

  // 401 → 用 refresh_token 刷新一次后重试；refresh 也失败则清除登录态并回登录页
  if (res.status === 401 && accessToken) {
    const ok = await refreshAccessToken();
    if (ok) {
      try {
        res = await doRequest();
      } catch {
        throw new ApiError(-1, i18n.t('errors.network'));
      }
    } else {
      await clearAuth();
      notifySessionReset();
      throw new ApiError(40103, i18n.t('errors.sessionExpired'), responseTrace(res));
    }
  }

  // 回执里的采用值是「服务端到底认了哪个 trace」的唯一权威（我们发出去的可能被边缘层改写），
  // 用它覆盖「最近一次」，崩溃上报与报障话术都指到同一个可 grep 的值。
  noteLastTrace(responseTrace(res));

  let body: unknown = null;
  let pdfBytes: ArrayBuffer | null = null;
  if (wantsBinary && res.ok) {
    // PDF 成功态没有信封，只有字节流；失败态后端仍回 JSON 信封，落到下面统一的码/文案出口
    pdfBytes = await res.arrayBuffer();
  } else {
    try {
      body = await res.json();
    } catch {
      // 非 JSON 响应
    }
  }

  // USER_NOT_FOUND(40401) 在已登录态下意味着账号已注销（软删除）→ 清登录态回登录页。
  // 注销后 30 分钟 access_token 窗口期内，业务请求 get_user 过滤 deleted → 抛此码；
  // 游客态（无 accessToken）不拦截，避免误伤游客的正常 404。
  if (isEnvelope(body) && body.code === 40401 && accessToken) {
    await clearAuth();
    notifySessionReset();
    throw new ApiError(40401, i18n.t('errors.sessionExpired'), responseTrace(res));
  }

  if (!res.ok) {
    const msg =
      isEnvelope(body) && typeof body.message === 'string'
        ? body.message
        : i18n.t('errors.httpFailed', { status: res.status });
    const code = isEnvelope(body) ? body.code : res.status;
    throw new ApiError(code, msg, responseTrace(res));
  }

  if (pdfBytes) {
    return pdfBytes as unknown as T;
  }

  if (isEnvelope(body)) {
    if (body.code !== 200) {
      throw new ApiError(body.code, body.message || i18n.t('errors.requestFailed'), responseTrace(res));
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
 * 401 处理与 request() 对齐：access token 过期时自动 refresh 并重开流一次；
 * refresh 失败则清登录态并通知回登录页。
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
  let xhr: XMLHttpRequest | null = null;
  let cancelled = false;
  let settled = false;

  const finish = (fn: () => void) => {
    if (settled) return;
    settled = true;
    fn();
  };

  const startAttempt = (isRetry: boolean) => {
    const attempt = new XMLHttpRequest();
    xhr = attempt;
    attempt.open('POST', `${API_BASE}/chat/stream`);
    const headers = getHeaders();
    Object.entries(headers).forEach(([k, v]) => attempt.setRequestHeader(k, v));

    let processed = 0; // 已处理的 responseText 长度
    let buffer = '';

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
      buffer += attempt.responseText.slice(processed);
      processed = attempt.responseText.length;
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      lines.forEach(handleLine);
    };

    attempt.onprogress = pump;
    attempt.onload = () => {
      // 401：access token 过期 → refresh 后重开流一次（非 2xx 响应为 JSON 信封，不会被 pump 误解析）
      if (attempt.status === 401 && !isRetry && !cancelled) {
        void (async () => {
          const ok = accessToken ? await refreshAccessToken() : false;
          if (cancelled || settled) return;
          if (ok) {
            startAttempt(true);
          } else {
            await clearAuth();
            notifySessionReset();
            finish(() => onError(i18n.t('errors.sessionExpired'), 40103));
          }
        })();
        return;
      }
      // 非 2xx（如 403 AI_CONSENT_REQUIRED / 401 重试后仍失败）：响应为 JSON 信封而非 SSE 流，按错误处理
      if (attempt.status < 200 || attempt.status >= 300) {
        let message = i18n.t('errors.httpFailed', { status: attempt.status });
        let code: number = attempt.status;
        try {
          const body = JSON.parse(attempt.responseText);
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
    attempt.onerror = () => finish(() => onError(i18n.t('errors.network')));
    attempt.ontimeout = () => finish(() => onError(i18n.t('errors.timeout')));
    attempt.timeout = 60000;

    attempt.send(JSON.stringify(requestBody));
  };

  startAttempt(false);
  return () => {
    cancelled = true;
    xhr?.abort();
  };
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

/** 签到历史（最近 30 条，用于同步本地 mood_checkins 表）。 */
export async function getMoodHistory(): Promise<{ checkins: MoodCheckinResponse[] }> {
  return request<{ checkins: MoodCheckinResponse[] }>('/mood/history');
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

/**
 * 成长记录列表（P0-F07 §6 成长记录页）。
 *
 * 不传 tag/days/limit，三个参数都吃后端缺省（最近 30 天 / 上限 20），筛选在屏内本地做。
 * 原因是后端 tag 筛选的真实语义是「先取最近 100 条、逐条筛、凑满 limit 就停」——
 * 换个 tag 就重发一次请求，等于让每个筛选态各自截一次前 20 条，用户看到的
 * 「某标签下的记录」会随点击顺序而不同。一次拉全、本地筛，才是同一份数据。
 */
export async function getGrowthRecords(): Promise<{ records: GrowthRecordItem[]; total: number }> {
  return request<{ records: GrowthRecordItem[]; total: number }>('/growth/records');
}

/**
 * 推荐话题（P0-F07 §4）：近期记录的高频标签 → 话题方向。
 *
 * 文案取 topics[].topic（后端 growth.topic.* 已按 X-Language 本地化），前端不自造句；
 * count 只当数量徽章，不做复数文案，免得为此新增 15 语言复数键。
 */
export async function getRecommendedTopics(): Promise<{ topics: GrowthTopic[] }> {
  return request<{ topics: GrowthTopic[] }>('/growth/topics');
}

// ═══ Relationship (P0-F04：关系等级 / 徽章 / 时间线) ═══

/** 关系等级详情：当前等级 + 全部等级/解锁能力表 + 说明（名称无恋爱含义）。 */
export async function getLevelDetail(): Promise<LevelDetail> {
  return request<LevelDetail>('/growth/level');
}

/** 徽章列表（streak 7/30/100/365 四枚，含未解锁占位，名称按 X-Language 本地化）。 */
export async function getBadges(): Promise<{ badges: BadgeItem[]; total: number }> {
  return request<{ badges: BadgeItem[]; total: number }>('/growth/badges');
}

/** 关系时间线：认识天数/聊天数/事件数 + 倒序事件流（里程碑徽章 + 成长记录）。 */
export async function getRelationshipTimeline(): Promise<RelationshipTimeline> {
  return request<RelationshipTimeline>('/growth/relationship-timeline');
}

/** 单日摘要卡片：情绪 + 当日洞察 + 消息数 + 当日徽章（GET 只读）。 */
export async function getTimelineDay(day: string): Promise<TimelineDaySummary> {
  return request<TimelineDaySummary>(`/growth/timeline/day?day=${day}`);
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

/** 生成 Pro 组合报告（16 型人格 × 九型交叉解读；每月 1 份缓存）。 */
export async function generateComboReport(): Promise<ComboReport> {
  return request<ComboReport>('/personality/combo-report', { method: 'POST' });
}

// ── 九型人格渐进式评估（PRD P1-F03：36 题，可跨会话断点续答）──

/** 查询九型评估状态（not_started / in_progress + 下一题 / completed + 结果）。 */
export async function getEnneagram(): Promise<EnneagramState> {
  return request<EnneagramState>('/personality/enneagram');
}

/** 开始/续答 36 题九型评估，返回当前题。 */
export async function startEnneagram(): Promise<EnneagramState> {
  return request<EnneagramState>('/personality/enneagram/start', { method: 'POST' });
}

/** 提交九型当前题答案（复用 AssessmentAnswerRequest：option 或 answer 二选一）。 */
export async function answerEnneagram(
  payload: { option: 'a' | 'b' | 'neutral' } | { answer: string },
): Promise<EnneagramAnswerResult> {
  return request<EnneagramAnswerResult>('/personality/enneagram/answer', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
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

// ── 通用数据导出（T1-01 · Baseline §2.2-6「带走」／§14.2 数据可携）──────────
// 后端是异步 job：申请只入队，产物由每 5 分钟的 cron 生成，界面对用户说「排队中／
// 生成中／可下载／失败／已过期」五态（同一集合见 types.ExportJobStatus）。
// 字节走 binary:true 与 PDF 同一条出口：鉴权在 request 里，所以不用 Linking.openURL
// 打开 download_path（浏览器/系统打开 URL 带不上 Bearer，必然 401）。

/** 申请导出全部个人数据（scope 目前只有 all）；24h 内已有未失败的 job 回 42904。 */
export async function postUserExport(scope: string = 'all'): Promise<ExportJob> {
  return request<ExportJob>('/user/export', {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });
}

/**
 * 最近若干条导出任务（倒序）＋保存周期。
 *
 * 换设备后仍能找回未过期的 ready 产物；`retention_days` 由后端给，界面不自己写「7 天」。
 */
export async function listUserExports(): Promise<ExportJobList> {
  return request<ExportJobList>('/user/export');
}

/** 单个任务状态；前端轮询它，直到 ready／failed／expired。 */
export async function getUserExport(jobId: string): Promise<ExportJob> {
  return request<ExportJob>(`/user/export/${jobId}`);
}

/** 下载 zip 字节；非 ready 态后端回 409，所以调用方只在 download_ready 为真时进来。 */
export async function getUserExportDownload(jobId: string): Promise<ArrayBuffer> {
  return request<ArrayBuffer>(`/user/export/${jobId}/download`, { binary: true });
}

/** 设置 AI 数据共享同意状态（合规 5.1.2(i)）。 */
export async function setAiConsent(consent: boolean): Promise<boolean> {
  const res = await request<{ ai_consent: boolean }>('/user/ai-consent', {
    method: 'POST',
    body: JSON.stringify({ consent }),
  });
  return res.ai_consent;
}

/**
 * 设置匿名行为分析同意（独立于 AI 共享的第二项同意）。
 * posthog_erased：撤回时后端是否已把 PostHog 侧人物数据排进删除队列——
 * 界面要按这个值说话，不能一律宣称「已删除」（未配置 key 时后端只告警）。
 */
export async function setAnalyticsConsent(
  consent: boolean
): Promise<{ analytics_consent: boolean; posthog_erased: boolean }> {
  return request<{ analytics_consent: boolean; posthog_erased: boolean }>(
    '/user/analytics-consent',
    { method: 'POST', body: JSON.stringify({ consent }) }
  );
}

// ═══ Memory (P0-F02) ═══

export async function listMemories(limit: number = 50): Promise<Memory[]> {
  return request<Memory[]>(`/memories?limit=${limit}`);
}

/**
 * 用户手记一条记忆。
 *
 * 这不是一个瞬时写：后端先向量化，再按相似度分档（≥0.85 判为同一信念的重复表达，
 * **不新建**只加固旧条；0.75<s<0.85 走一次 lite LLM 矛盾检测，见
 * backend/services/memory_service.py:211-225）。所以调用方必须给中间态，
 * 且提交后要回读列表——本地插一条会与「没新建」那条分支必然分叉。
 */
export async function createMemory(input: {
  content: string;
  category: Memory['category'];
}): Promise<Memory> {
  return request<Memory>('/memories', {
    method: 'POST',
    // source/conviction 不传：那是模型口径（记忆来源与信念强度），不是让用户替检索层打分
    body: JSON.stringify({ content: input.content, category: input.category }),
  });
}

export async function getMemory(memoryId: string): Promise<Memory> {
  return request<Memory>(`/memories/${memoryId}`);
}

/**
 * 记录一次信念演变：旧行转 superseded，`new_content` 成为新的 current
 * （backend/services/memory_service.py:493-505）。
 *
 * `newContent` 必须由调用方保证非空：缺省时后端沿用原内容新建一行，
 * 产出的是一条同文字重复记忆而不是演变。
 */
export async function versionMemory(input: {
  memoryId: string;
  newContent: string;
  transitionType?: 'gradual' | 'sudden' | 'revert';
}): Promise<{ memory_id: string }> {
  return request<{ memory_id: string }>('/memories/version', {
    method: 'POST',
    body: JSON.stringify({
      memory_id: input.memoryId,
      transition_type: input.transitionType ?? 'sudden',
      new_content: input.newContent,
    }),
  });
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await request<{ deleted: boolean }>(`/memories/${memoryId}`, { method: 'DELETE' });
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

/**
 * 年度报告卡（M4 year_in_review）：一个自然年的落库事实计数 + 带本人推荐码的深链。
 *
 * 免费卡、不走 Pro 门控（当引流物料，AI 年度长文报告仍是 Pro 权益），所以这里
 * 没有 40302 分支；整年零记录后端回 40402 空态。缺省年份由后端定
 * （上一个完整自然年），前端不自己算——两端各算一份迟早分叉。
 */
export async function getYearInReview(year?: number): Promise<YearInReviewCard> {
  const suffix = year ? `&year=${year}` : '';
  return request<YearInReviewCard>(`/share/year-in-review?language=${langParam()}${suffix}`);
}

// ── PDF 导出（U2 · P0-F07 §6）──────────────────────────────────────────
// 四条都是 PDF 二进制出口：排版全在后端（share_service.py + reportlab 中文 CID 字体），
// 前端只拿字节。鉴权靠 request 里那条统一的 Authorization，所以不走 Linking.openURL
// ——浏览器/系统打开 URL 带不上 Bearer，必然 401。
// 失败码（40304 / 40402 / 40403 / 其它）由 pdfFailureKey 映射文案，文件名见 config/pdfFiles.ts。

/** 周期报告 PDF（Pro 周期仍由后端 40302 把关）。 */
export async function getReportPdf(period: SharePeriod): Promise<ArrayBuffer> {
  return request<ArrayBuffer>(`/share/report/${period}/pdf?language=${langParam()}`, { binary: true });
}

/** 年度报告 PDF：年份缺省交给后端（与 JSON 版同一口径，两端不各算一次年）。 */
export async function getYearInReviewPdf(year?: number): Promise<ArrayBuffer> {
  const suffix = year ? `&year=${year}` : '';
  return request<ArrayBuffer>(`/share/year-in-review/pdf?language=${langParam()}${suffix}`, { binary: true });
}

/** 回忆卡 PDF（memory_id 不属主回 40403）。 */
export async function postMemoryCardPdf(memoryId: string): Promise<ArrayBuffer> {
  return request<ArrayBuffer>('/share/memory-card', {
    method: 'POST',
    body: JSON.stringify({ memory_id: memoryId }),
    binary: true,
  });
}

/**
 * Life Archive PDF（M6 买断权益的「货」）。
 *
 * 权益判定全在后端（一次性买断，或订阅期内每月 1 次免费生成），App 不自己判：
 * 无权益回 40304 → `pdf.failed.entitlement`，界面上靠 Paywall 的导出按钮可见性兜住。
 * 同档位（订阅口径为同档位同自然月）命中后端缓存直接复用，`regenerate` 才重排。
 * 落盘名按 §七 模板拼接，见 `config/pdfFiles.ts` 的 `lifeArchivePdfFile`。
 */
export async function getLifeArchivePdf(
  scope: LifeArchiveScope,
  regenerate = false,
): Promise<ArrayBuffer> {
  return request<ArrayBuffer>(
    `/share/life-archive/pdf?scope=${scope}&language=${langParam()}&regenerate=${regenerate}`,
    { binary: true },
  );
}

/**
 * 取本人分享深链与推荐码（M4）。
 *
 * 后端首次调用才生成 referral_code（惰性），所以不要在任何非分享场景预取，
 * 否则会给从不分享的用户白占一个码。App 内分享文案一律用它，不要各自拼 URL。
 */
export async function getShareLink(): Promise<ShareLink> {
  return request<ShareLink>('/share/link');
}

/**
 * 记忆考古候选（§10.1 ① · C4 首页每日主位）：近 3 年「N 年前的今天」你说过的原话。
 *
 * 零 LLM、按年份升序（1 年前优先）、本地日界由服务端按用户时区判定，所以「今天」
 * 与周年都取自响应里的 date/years_ago，前端不自己算日期。
 * 不挂 require_ai_consent（内容是用户自己的历史原话，无 AI 加工），只要登录态。
 */
export async function getOnThisDay(): Promise<OnThisDayResponse> {
  return request<OnThisDayResponse>('/share/on-this-day');
}

// ═══ Onboarding 性格初读卡 (P1-B01 · 架构方案 §二 M1) ═══

/** 生成初读卡并落库（幂等 upsert；后端静态模板直出，零 LLM）。 */
export async function postFirstRead(
  answers: FirstReadAnswer[],
  personaType?: string | null,
): Promise<FirstReadCard> {
  return request<FirstReadCard>('/onboarding/first-read', {
    method: 'POST',
    body: JSON.stringify({
      answers,
      persona_type: personaType ?? null,
      language: langParam(),
    }),
  });
}

/** 重进 App 恢复初读卡（按生成时语言复现）；未生成 → 40402，由调用方兜底。 */
export async function getFirstRead(): Promise<FirstReadCard> {
  return request<FirstReadCard>('/onboarding/first-read');
}

// ═══ Journey 第一周剧本 (P1-B02 · 架构方案 §二 M2) ═══

/** D0–D7 全剧本状态 + 未读数（红点与 Week1ReportScreen 数据源）。 */
export async function getJourneyEvents(): Promise<JourneyEventsResponse> {
  return request<JourneyEventsResponse>('/journey/events');
}

/** 标记某条剧本事件已读（红点消费）。 */
export async function acknowledgeJourneyEvent(eventId: string): Promise<{ acknowledged: string }> {
  return request<{ acknowledged: string }>('/journey/events/acknowledge', {
    method: 'POST',
    body: JSON.stringify({ event_id: eventId }),
  });
}

// ═══ Growth Curriculum (P1-C2 · 架构方案 §二 C2) ═══

/**
 * 当周 7 张主题卡（首页次位数据源）。
 *
 * 生成在服务端 cron（周日 21 点本地窗口），本端点只读；未生成的天不返回行，
 * 所以 days 少于 7 不是错误而是「这周还没铺满」，由首页走兜底态。
 * 挂 require_ai_consent：未同意 → 403/40303。
 */
export async function getCurriculumWeek(): Promise<CurriculumWeekResponse> {
  return request<CurriculumWeekResponse>('/curriculum/week');
}

/** 点击主题卡 → 已读标记（服务端幂等；越权 40403）。 */
export async function markCurriculumRead(eventId: string): Promise<{ id: string; read: boolean }> {
  return request<{ id: string; read: boolean }>(`/curriculum/${eventId}/read`, { method: 'POST' });
}

// ═══ Bedtime Review (P1-C1 · §5.16 S1 / UI Screen 26) ═══

/**
 * 今日三步状态 + 当日摘要参考（零 LLM）+ 昨日归档。
 *
 * 挂 require_ai_consent：未同意 → 403/40303，由调用方弹同意窗（合规 5.1.2(i)）。
 */
export async function getBedtimeToday(): Promise<BedtimeTodayResponse> {
  return request<BedtimeTodayResponse>('/bedtime/today');
}

/**
 * 逐步提交复盘。step1 的 text 空/缺省 = 跳过（服务端记 skipped，零 LLM）；
 * step2/step3 不带用户输入，服务端幂等返回已生成内容，不重复消耗 LLM。
 * step1 命中危机触发词 → 返回 `{intercepted, crisis}`，此时未落任何复盘记录。
 */
export async function postBedtimeStep(
  step: 1 | 2 | 3,
  text?: string | null,
): Promise<BedtimeStepResponse> {
  return request<BedtimeStepResponse>('/bedtime/step', {
    method: 'POST',
    body: JSON.stringify({ step, text: text ?? null }),
  });
}

// ═══ Advanced Insights (P1-F03) ═══

/** 跨维度模式发现（LLM 实时生成，点击触发，勿自动轮询）。 */
export async function getPatternDiscovery(): Promise<PatternDiscovery> {
  return request<PatternDiscovery>('/growth/patterns');
}

// ═══ Analytics (分析事件队列批量上送) ═══

export interface AnalyticsBatchPayload {
  events: Array<{
    event: string;
    properties?: Record<string, unknown> | null;
    occurred_at: string;
  }>;
}

/** 批量上送前端本地分析事件队列（后端校验 consent + 匿名化后转发 PostHog）。 */
export async function postAnalyticsBatch(
  events: AnalyticsBatchPayload['events'],
): Promise<{ relayed: number; dropped: number }> {
  return request<{ relayed: number; dropped: number }>('/analytics/batch', {
    method: 'POST',
    body: JSON.stringify({ events } satisfies AnalyticsBatchPayload),
  });
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

/** Life Archive 买断权益（一次性内购，永久可用，与订阅状态互不影响）。 */
export async function getEntitlements(): Promise<EntitlementsResult> {
  return request<EntitlementsResult>('/iap/entitlements');
}

// ═══ Auth ═══
// 认证端点。登录/注册/验证码登录/Apple 登录成功后自动持久化 token。
// 公开端点不触发 401 刷新（登录态为空时 request 不走刷新分支）。

/**
 * M4 归因：把待消费的推荐码带到建号请求上（`ref`）。
 *
 * 三个会创建账号的端点都要带：密码注册、验证码登录（未注册自动建号）、Apple 首次
 * 登录。后端对已存在的账号忽略 `ref`，所以这里不必区分新老用户；成功后一律清除，
 * 避免残留码被下一个账号复用。归因上限见 services/referral.ts 头注释。
 */
async function referralBody(): Promise<{ ref?: string }> {
  const code = await peekPendingReferral();
  return code ? { ref: code } : {};
}

/** 邮箱 + 用户名 + 密码注册。 */
export async function register(
  email: string,
  username: string,
  password: string,
  language: string = 'en',
): Promise<AuthResponse> {
  const resp = await request<AuthResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({
      email,
      username,
      password,
      language,
      ...(await referralBody()),
    }),
  });
  await setAuth(resp);
  await clearPendingReferral();
  return resp;
}

/** 用户名/邮箱 + 密码登录。 */
export async function login(identifier: string, password: string): Promise<AuthResponse> {
  const resp = await request<AuthResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ identifier, password }),
  });
  await setAuth(resp);
  return resp;
}

/** 发送邮箱验证码（purpose: register|login|reset）。 */
export async function sendEmailCode(
  email: string,
  purpose: CodePurpose,
  language: string = 'en',
): Promise<void> {
  await request<{ status: string }>('/auth/send-code', {
    method: 'POST',
    body: JSON.stringify({ email, purpose, language }),
  });
}

/** 邮箱验证码登录（未注册自动建账号）。 */
export async function loginWithEmailCode(
  email: string,
  code: string,
  language: string = 'en',
): Promise<AuthResponse> {
  const resp = await request<AuthResponse>('/auth/login-code', {
    method: 'POST',
    body: JSON.stringify({ email, code, language, ...(await referralBody()) }),
  });
  await setAuth(resp);
  await clearPendingReferral();
  return resp;
}

/** Apple 登录（Sign in with Apple）：传 identityToken 到后端验签。 */
export async function appleLoginApi(
  identityToken: string,
  language: string = 'en',
): Promise<AuthResponse> {
  const resp = await request<AuthResponse>('/auth/apple', {
    method: 'POST',
    body: JSON.stringify({
      identity_token: identityToken,
      language,
      ...(await referralBody()),
    }),
  });
  await setAuth(resp);
  await clearPendingReferral();
  return resp;
}

/** 登出：吊销当前 refresh token（需传 refresh_token；登出后本地清态由 logout() 组合）。 */
export async function logoutApi(refreshToken: string): Promise<void> {
  await request<{ status: string }>('/auth/logout', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

/** 忘记密码：发送重置验证码（防枚举，统一返回成功）。 */
export async function forgotPassword(email: string, language: string = 'en'): Promise<void> {
  await request<{ status: string }>('/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email, language }),
  });
}

/** 重置密码：验证码 + 新密码。 */
export async function resetPassword(
  email: string,
  code: string,
  newPassword: string,
): Promise<void> {
  await request<{ status: string }>('/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });
}

/** 修改密码（旧 + 新；改密后吊销全部 refresh，需重新登录）。 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await request<{ status: string }>('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}

/** 修改用户名。 */
export async function updateUsername(username: string): Promise<void> {
  await request<{ status: string }>('/auth/username', {
    method: 'PATCH',
    body: JSON.stringify({ username }),
  });
}

/** 当前用户信息（校验 token 有效性 + 拉取最新资料）。 */
export async function getMe(): Promise<AuthUser> {
  return request<AuthUser>('/auth/me');
}
