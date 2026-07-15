# App 启动时序规范

> 来源：ClosenAI 技术方案设计 v1.0 + PRD v2.0
> 目标：尽可能丝滑加载，响应时间尽可能快
> 状态：P0 阶段执行标准

---

## 1. 启动时序总览

### 1.1 全流程 ASCII 时序图

```
用户点击 App 图标
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  iOS 系统层                                                              │
│                                                                         │
│  冷启动: fork 进程 → 加载 dyld → 链接框架 → 加载 main bundle             │
│  热启动: 进程已驻留 → 唤醒 → 恢复 JS 引擎                                │
└───────────────────────┬─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  React Native / Expo 层                                                  │
│                                                                         │
│  加载 JS Bundle → 初始化 JS 引擎 → 执行 App Entry                        │
│  ↓ (同步)                                                               │
│  显示 Native Splash Screen (expo-splash-screen)                          │
└───────────────────────┬─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  应用初始化层 (App Entry → App.tsx)                                      │
│                                                                         │
│  ┌─────────────┐   ┌──────────────────┐   ┌────────────────────────┐    │
│  │ 打开 SQLite  │   │ Keychain 读取     │   │ Navigation 初始化       │    │
│  │ (expo-sqlite)│   │ (expo-secure-store│   │ (React Navigation)     │    │
│  └──────┬──────┘   └────────┬─────────┘   └───────────┬────────────┘    │
│         │                   │                          │                  │
└─────────┼───────────────────┼──────────────────────────┼─────────────────┘
          │                   │                          │
          ▼                   ▼                          │
┌─────────────────┐  ┌─────────────────┐                 │
│ SQLite 读取缓存  │  │ Auth Token 验证  │                 │
│ (首屏数据)       │  │ (本地 JWT 解码)  │                 │
│ < 50ms          │  │ < 20ms          │                 │
└────────┬────────┘  └────────┬────────┘                 │
         │                    │                          │
         │  有缓存数据?        │  Token 有效?              │
         │                    │                          │
         ▼                    ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  首屏渲染 (Growth Tab)                                                   │
│                                                                         │
│  有缓存 → 立即渲染缓存数据 (离线优先)                                     │
│  无缓存 → 显示骨架屏 / Splash 直到 API 返回                               │
└───────────────────────┬─────────────────────────────────────────────────┘
                        │
                        ▼ (首屏已可见，不阻塞)
┌─────────────────────────────────────────────────────────────────────────┐
│  后台静默同步层                                                          │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ GET /profile  │  │ GET /growth/  │  │ GET /mood-   │   (并行)         │
│  │ (用户配置)    │  │  insights     │  │  checkins/   │                   │
│  │              │  │ (Reflection   │  │  today       │                   │
│  │              │  │  状态)        │  │ (Streak)     │                   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                   │
│         │                 │                  │                            │
│         └─────────────────┼──────────────────┘                            │
│                           ▼                                               │
│              拿到新数据 → 静默更新 UI                                      │
│              写入 SQLite 缓存                                             │
└───────────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心设计原则

| 原则 | 说明 |
|------|------|
| **离线优先** | 首屏数据先从本地 SQLite 读取并渲染，不等网络 |
| **并行化** | Auth 验证与 SQLite 读取并行；首屏 API 请求之间并行 |
| **不阻塞渲染** | 后台 API 同步在首屏渲染之后异步执行，绝不阻塞首屏 |
| **静默更新** | API 返回新数据后静默更新 UI，无 loading 闪烁 |
| **渐进式加载** | 首屏只加载必要数据，非首屏内容延迟/按需加载 |

---

## 2. 详细时序（毫秒级）

### 2.1 冷启动（首次安装 / 杀进程后）

**目标：首屏可见 < 2s**

```
时间轴 (ms)    事件
───────────────────────────────────────────────────────────────────
0              用户点击 App 图标
               │
               ├── [iOS 系统] fork 进程 + 加载 dyld + 链接框架
0    ─ 200     │   耗时 ~200ms
               │
200            ├── [Expo] Native Splash Screen 显示（静态图片，原生渲染）
               │   Splash 在 JS 加载前就已显示，用户感知"已打开"
               │
200  ─ 600     ├── [RN] 加载 JS Bundle + 初始化 JS 引擎 (Hermes)
               │   耗时 ~400ms
               │
600            ├── [JS] App Entry 执行
               │
               ├── ┌──────────────────────────────────────────┐
               │   │ 并行执行 (Promise.all):                   │
600  ─ 660     │   │  ① 打开 SQLite 连接         ~50ms        │
600  ─ 620     │   │  ② Keychain 读取 JWT        ~20ms        │
               │   └──────────────────────────────────────────┘
               │
660            ├── [SQLite] 读取首屏缓存数据
660  ─ 720     │   耗时 ~50ms (索引查询，目标 < 50ms)
               │
720            ├── [Auth] 本地 JWT 解码验证 (exp 字段检查)
720  ─ 740     │   耗时 ~20ms (纯本地，不请求网络)
               │
740            ├── 判断是否有缓存数据
               │   │
               │   ├── 有缓存 ──────────────────────────────┐
               │   │                                        │
740  ─ 900     │   │   [RN] 渲染首屏 (Growth Tab)            │
               │   │   耗时 ~150ms (从缓存数据渲染)          │
               │   │                                        │
900            │   │   ✅ 首屏可见（缓存数据）                │
               │   │                                        │
               │   │   ┌────────────────────────────────┐   │
900  ─ 1200    │   │   │ 后台并行 API 请求 (不阻塞)      │   │
               │   │   │  GET /profile                   │   │
               │   │   │  GET /growth/insights           │   │
               │   │   │  GET /mood-checkins/today       │   │
               │   │   │  网络耗时 ~300ms (P95)          │   │
               │   │   └─────────────┬──────────────────┘   │
               │   │                 │                      │
1200 ─ 1280    │   │   [静默更新] 写入 SQLite + 刷新 UI     │
               │   │   耗时 ~50ms                          │
               │   │   ✅ 首屏数据已为最新                   │
               │   │                                        │
               │   └────────────────────────────────────────┘
               │
               │   └── 无缓存（首次安装）────────────────────┐
               │                                            │
740  ─ 900     │       [RN] 渲染骨架屏 (Skeleton Screen)    │
               │       ✅ 骨架屏可见                         │
               │                                            │
900  ─ 1400    │       [API] 等待首屏 API 返回               │
               │       网络耗时 ~400ms (首次无 ETag)         │
               │                                            │
1400 ─ 1500   │       [RN] 渲染首屏真实数据                  │
               │       ✅ 首屏可见（真实数据）                │
               │                                            │
               └────────────────────────────────────────────┘
```

**冷启动关键路径耗时汇总（有缓存）：**

| 阶段 | 耗时 | 累计 |
|------|------|------|
| iOS 系统启动 | ~200ms | 200ms |
| JS Bundle 加载 | ~400ms | 600ms |
| SQLite 打开 + Keychain 读取（并行） | ~60ms | 660ms |
| SQLite 读取首屏缓存 | ~50ms | 710ms |
| JWT 本地验证 | ~20ms | 730ms |
| 首屏渲染（缓存数据） | ~150ms | 880ms |
| **首屏可见（缓存）** | - | **~880ms** |
| 后台 API 同步（不阻塞） | ~300ms | 1180ms |
| 静默 UI 更新 | ~50ms | 1230ms |

### 2.2 热启动（从后台返回）

**目标：首屏可见 < 500ms**

```
时间轴 (ms)    事件
───────────────────────────────────────────────────────────────────
0              用户从后台切回 App
               │
               ├── [iOS] 唤醒已驻留进程
0    ─ 50      │   耗时 ~50ms
               │
50             ├── [RN] JS 引擎已温热，恢复执行上下文
50   ─ 80      │   耗时 ~30ms
               │
80             ├── [内存] 读取内存缓存（AppState 监听触发）
80   ─ 100     │   耗时 ~20ms (内存读取，无需 SQLite)
               │
100            ├── [Auth] 内存中 Token 仍有效，跳过验证
               │   (仅检查距上次同步是否 > 30s)
               │
100  ─ 200     ├── [RN] 渲染首屏（内存缓存数据）
               │   耗时 ~100ms
               │
200            ✅ 首屏可见（内存缓存）
               │
               ├── ┌──────────────────────────────────────────┐
200  ─ 450     │   │ 后台并行 API 请求 (条件触发)              │
               │   │  仅当距上次同步 > 30s 时发起               │
               │   │  网络耗时 ~250ms (热连接)                 │
               │   └─────────────┬──────────────────────────┘
               │                 │
450  ─ 500     │   [静默更新] 刷新 UI（如有变化）              │
               │   ✅ 首屏数据已为最新                          │
               └──────────────────────────────────────────────
```

**热启动关键路径耗时汇总：**

| 阶段 | 耗时 | 累计 |
|------|------|------|
| iOS 唤醒进程 | ~50ms | 50ms |
| JS 引擎恢复 | ~30ms | 80ms |
| 内存缓存读取 | ~20ms | 100ms |
| 首屏渲染（内存缓存） | ~100ms | 200ms |
| **首屏可见** | - | **~200ms** |
| 后台 API 同步（条件触发，不阻塞） | ~250ms | 450ms |

### 2.3 冷启动 vs 热启动对比

| 维度 | 冷启动 | 热启动 |
|------|--------|--------|
| 进程状态 | 从零创建 | 已驻留内存 |
| JS 引擎 | 冷初始化 | 已温热 |
| 数据来源 | SQLite 磁盘缓存 | 内存缓存 |
| Auth 验证 | Keychain 读取 + JWT 解码 | 内存 Token 直接可用 |
| 首屏可见目标 | < 2s | < 500ms |
| 实际预估 | ~880ms（有缓存） | ~200ms |
| API 同步 | 总是发起 | 仅距上次同步 > 30s 时发起 |

---

## 3. 离线优先策略

### 3.1 三层数据读取策略

```
┌─────────────────────────────────────────────────────┐
│                    用户打开 App                       │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  1. 内存缓存     │  ← 命中? 立即渲染 (< 5ms)
              │  (Memory Cache) │
              └────────┬────────┘
                       │ 未命中
                       ▼
              ┌─────────────────┐
              │  2. SQLite 缓存  │  ← 命中? 渲染缓存 + 后台同步 (< 50ms)
              │  (Local Disk)   │
              └────────┬────────┘
                       │ 未命中（首次安装 / 清除数据）
                       ▼
              ┌─────────────────┐
              │  3. 骨架屏 + API │  ← 显示骨架屏，等待 API 返回
              │  (Network)      │
              └─────────────────┘
```

### 3.2 离线优先渲染流程

```typescript
// src/services/offline-first.ts

import * as SQLite from 'expo-sqlite';
import * as SecureStore from 'expo-secure-store';

const DB_NAME = 'closenai.db';
const LAST_SYNC_KEY = 'last_sync_timestamp';
const SYNC_INTERVAL_MS = 30 * 1000; // 30 秒内不重复同步

export interface CacheEntry<T> {
  data: T;
  cached_at: number; // 缓存写入时间戳 (ms)
  etag: string | null;
}

/**
 * 离线优先数据读取器。
 * 1. 先从内存缓存读取 → 2. 再从 SQLite 读取 → 3. 都没有则返回 null
 */
export class OfflineFirstStore {
  private memoryCache = new Map<string, CacheEntry<unknown>>();
  private db: SQLite.SQLiteDatabase | null = null;

  /** 初始化 SQLite 连接（App 启动时调用一次） */
  async init(): Promise<void> {
    this.db = await SQLite.openDatabaseAsync(DB_NAME);
    await this.db.execAsync(`
      CREATE TABLE IF NOT EXISTS cache (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        cached_at   INTEGER NOT NULL,
        etag        TEXT,
        ttl         INTEGER NOT NULL DEFAULT 86400000
      );
    `);
  }

  /**
   * 读取数据（离线优先）。
   * @param key   缓存键（如 'home:profile'）
   * @returns     缓存数据 or null
   */
  async read<T>(key: string): Promise<CacheEntry<T> | null> {
    // 1. 内存缓存
    const memHit = this.memoryCache.get(key) as CacheEntry<T> | undefined;
    if (memHit) {
      return memHit;
    }

    // 2. SQLite 缓存
    if (!this.db) return null;
    const row = await this.db.getFirstAsync<{
      value: string;
      cached_at: number;
      etag: string | null;
    }>('SELECT value, cached_at, etag FROM cache WHERE key = ?', [key]);

    if (!row) return null;

    const entry: CacheEntry<T> = {
      data: JSON.parse(row.value) as T,
      cached_at: row.cached_at,
      etag: row.etag,
    };

    // 回填内存缓存
    this.memoryCache.set(key, entry as CacheEntry<unknown>);
    return entry;
  }

  /**
   * 写入缓存（内存 + SQLite 双写）。
   */
  async write<T>(key: string, data: T, etag: string | null = null): Promise<void> {
    const entry: CacheEntry<T> = { data, cached_at: Date.now(), etag };

    // 内存缓存
    this.memoryCache.set(key, entry as CacheEntry<unknown>);

    // SQLite 缓存
    if (!this.db) return;
    await this.db.runAsync(
      `INSERT OR REPLACE INTO cache (key, value, cached_at, etag)
       VALUES (?, ?, ?, ?)`,
      [key, JSON.stringify(data), entry.cached_at, etag],
    );
  }

  /** 获取缓存 ETag（用于条件请求 If-None-Match） */
  async getEtag(key: string): Promise<string | null> {
    const entry = await this.read<unknown>(key);
    return entry?.etag ?? null;
  }

  /** 判断是否需要后台同步（距上次同步 > 30s） */
  async shouldSync(): Promise<boolean> {
    const last = await SecureStore.getItemAsync(LAST_SYNC_KEY);
    if (!last) return true;
    return Date.now() - parseInt(last, 10) > SYNC_INTERVAL_MS;
  }

  /** 记录同步时间 */
  async markSynced(): Promise<void> {
    await SecureStore.setItemAsync(LAST_SYNC_KEY, Date.now().toString());
  }
}

export const offlineStore = new OfflineFirstStore();
```

### 3.3 首屏离线优先渲染（React Hook）

```typescript
// src/hooks/useOfflineFirst.ts

import { useState, useEffect, useCallback } from 'react';
import { offlineStore } from '../services/offline-first';

interface OfflineFirstState<T> {
  data: T | null;
  loading: boolean;       // 仅在无缓存且 API 未返回时为 true
  isStale: boolean;       // true = 当前显示的是缓存数据，后台正在更新
  error: Error | null;
}

/**
 * 离线优先数据加载 Hook。
 *
 * 行为：
 * 1. 立即从缓存读取并渲染（loading = false if cache hit）
 * 2. 后台并行请求 API
 * 3. API 返回后静默更新（isStale = false）
 * 4. 首次无缓存时显示 loading（骨架屏）
 */
export function useOfflineFirst<T>(
  cacheKey: string,
  fetcher: () => Promise<{ data: T; etag?: string }>,
): OfflineFirstState<T> {
  const [state, setState] = useState<OfflineFirstState<T>>({
    data: null,
    loading: true,
    isStale: false,
    error: null,
  });

  const load = useCallback(async () => {
    // 1. 先读缓存
    const cached = await offlineStore.read<T>(cacheKey);

    if (cached) {
      // 有缓存 → 立即渲染，标记为 stale（后台将更新）
      setState({
        data: cached.data,
        loading: false,
        isStale: true,
        error: null,
      });
    }
    // 无缓存 → 保持 loading = true（显示骨架屏）

    // 2. 后台请求 API
    try {
      const { data, etag } = await fetcher();

      // 3. 写入缓存
      await offlineStore.write(cacheKey, data, etag ?? null);

      // 4. 静默更新 UI
      setState({
        data,
        loading: false,
        isStale: false,
        error: null,
      });
    } catch (err) {
      // API 失败：如果已有缓存数据，保持缓存数据不变
      if (cached) {
        setState((prev) => ({ ...prev, isStale: false }));
      } else {
        // 无缓存且 API 失败 → 显示错误
        setState({
          data: null,
          loading: false,
          isStale: false,
          error: err as Error,
        });
      }
    }
  }, [cacheKey, fetcher]);

  useEffect(() => {
    load();
  }, [load]);

  return state;
}
```

### 3.4 首次安装无缓存处理

```typescript
// src/screens/HomeScreen.tsx (片段)

import { useOfflineFirst } from '../hooks/useOfflineFirst';
import { SkeletonCard } from '../components/SkeletonCard';
import { api } from '../services/api';

export default function HomeScreen() {
  const { data: homeData, loading, isStale } = useOfflineFirst(
    'home:dashboard',
    () => api.getHomeDashboard(),
  );

  // 首次安装无缓存 → 显示骨架屏
  if (loading && !homeData) {
    return <HomeSkeletonScreen />;
  }

  return (
    <View>
      {/* isStale = true 时可显示一个不明显的"更新中"指示器 */}
      <HomeDashboard data={homeData} isRefreshing={isStale} />
    </View>
  );
}
```

```typescript
// src/components/SkeletonCard.tsx
// 骨架屏组件——首屏无缓存时展示，视觉上占位

import React from 'react';
import { View, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

export function SkeletonCard() {
  return (
    <View style={styles.card}>
      <View style={styles.lineLarge} />
      <View style={styles.lineMedium} />
      <View style={styles.lineSmall} />
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    marginVertical: 8,
  },
  lineLarge: {
    height: 24,
    width: '60%',
    backgroundColor: colors.surfaceVariant,
    borderRadius: 6,
    marginBottom: 12,
  },
  lineMedium: {
    height: 16,
    width: '90%',
    backgroundColor: colors.surfaceVariant,
    borderRadius: 6,
    marginBottom: 8,
  },
  lineSmall: {
    height: 16,
    width: '40%',
    backgroundColor: colors.surfaceVariant,
    borderRadius: 6,
  },
});
```

---

## 4. API 请求时序

### 4.1 首屏 API 请求依赖图

```
                    ┌──────────────────────┐
                    │  Auth Token 验证      │
                    │  (本地 Keychain)      │
                    │  ~20ms                │
                    └──────────┬───────────┘
                               │
                    Token 有效?
                    ├── 是 ────┐
                    │          │
                    │    ┌─────▼──────────────────────────────────────┐
                    │    │  并行组 1 (Promise.all)                     │
                    │    │                                            │
                    │    │  ┌─────────────┐ ┌──────────┐ ┌──────────┐ │
                    │    │  │ GET /profile │ │GET/growth│ │GET/mood- │ │
                    │    │  │             │ │/insights │ │checkins/ │ │
                    │    │  │ 用户配置     │ │Reflection│ │today     │ │
                    │    │  │             │ │状态      │ │Streak    │ │
                    │    │  │ ~100ms      │ │~150ms    │ │~100ms    │ │
                    │    │  └─────────────┘ └──────────┘ └──────────┘ │
                    │    │                                            │
                    │    │  最慢请求决定总耗时 ~150ms (P95)            │
                    │    └────────────────────────────────────────────┘
                    │
                    └── 否 (Token 过期)
                         │
                    ┌────▼───────────────┐
                    │ POST /auth/refresh  │  ← 串行依赖
                    │ (Refresh Token)     │
                    │ ~200ms              │
                    └────┬───────────────┘
                         │ 拿到新 Access Token
                         │
                    ┌────▼───────────────┐
                    │ 并行组 1 (同上)     │
                    └────────────────────┘

                    ─────────────────────────────────

                    ┌──────────────────────────────────────────────┐
                    │  延迟加载（用户滚动到时才请求）                  │
                    │                                            │
                    │  GET /growth/timeline     (Growth Timeline) │
                    │  GET /personality         (Personality Profile)│
                    │  → 不在首屏加载，进入对应区域时触发            │
                    └──────────────────────────────────────────────┘
```

### 4.2 首屏 API 请求清单

| 优先级 | API 端点 | 用途 | 缓存键 | 依赖 | 延迟 |
|--------|---------|------|--------|------|------|
| P0 | `GET /api/v1/profile` | 用户配置（昵称、时区、偏好） | `profile` | Auth | 首屏 |
| P0 | `GET /api/v1/growth/insights` | 今日 Reflection 状态 | `growth:insights` | Auth | 首屏 |
| P0 | `GET /api/v1/mood-checkins/today` | 今日情绪签到 + Streak | `mood:today` | Auth | 首屏 |
| P1 | `GET /api/v1/growth/timeline` | 成长时间线 | `growth:timeline` | Auth | 延迟 |
| P1 | `GET /api/v1/personality` | 性格画像 | `personality` | Auth | 延迟 |
| P1 | `GET /api/v1/chat/messages?limit=20` | 最近对话消息 | `chat:recent` | Auth | Reflect Tab |

### 4.3 串行依赖：Auth Token 验证

```typescript
// src/services/auth.ts

import * as SecureStore from 'expo-secure-store';
import { jwtDecode } from 'jwt-decode';

const ACCESS_TOKEN_KEY = 'auth_access_token';
const REFRESH_TOKEN_KEY = 'auth_refresh_token';

interface JWTPayload {
  sub: string;
  timezone: string;
  iat: number;
  exp: number;
}

/**
 * 获取有效的 Access Token。
 * 1. 先从 Keychain 读取本地 Token
 * 2. 检查 exp 是否过期
 * 3. 过期则用 Refresh Token 静默刷新
 * 4. 刷新失败则跳转登录页
 */
export async function getValidAccessToken(): Promise<string | null> {
  const accessToken = await SecureStore.getItemAsync(ACCESS_TOKEN_KEY);

  if (!accessToken) {
    return null; // 未登录
  }

  // 本地解码验证（不请求网络，~1ms）
  try {
    const payload = jwtDecode<JWTPayload>(accessToken);
    const now = Math.floor(Date.now() / 1000);

    // 提前 60s 判定过期，避免请求时刚好过期
    if (payload.exp - now > 60) {
      return accessToken; // 仍然有效
    }
  } catch {
    return null; // Token 格式错误
  }

  // Token 过期 → 静默刷新
  return await refreshAccessToken();
}

/**
 * 静默刷新 Access Token。
 * 用户无感知，不中断当前操作。
 */
async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = await SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
  if (!refreshToken) {
    return null; // 无 Refresh Token → 跳转登录
  }

  try {
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!response.ok) {
      await clearTokens();
      return null; // Refresh Token 也过期 → 跳转登录
    }

    const { data } = await response.json();

    // 写回 Keychain
    await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, data.access_token);
    if (data.refresh_token) {
      await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, data.refresh_token);
    }

    return data.access_token;
  } catch {
    return null; // 网络错误 → 后续请求会走离线缓存
  }
}

async function clearTokens(): Promise<void> {
  await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
}
```

### 4.4 并行 API 请求实现

```typescript
// src/services/api.ts

import { getValidAccessToken } from './auth';
import { offlineStore } from './offline-first';

const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

/** 带 Auth + ETag 条件请求的 fetch 封装 */
async function authedFetch<T>(
  path: string,
  cacheKey: string,
  options: RequestInit = {},
): Promise<{ data: T; etag: string | null }> {
  const token = await getValidAccessToken();
  if (!token) {
    throw new Error('NOT_AUTHENTICATED');
  }

  const etag = await offlineStore.getEtag(cacheKey);

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Accept-Language': 'zh',
      'X-Client-Version': '1.0.0',
      'X-Device-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
      ...(etag ? { 'If-None-Match': etag } : {}),
      ...options.headers,
    },
  });

  // 304 Not Modified → 返回缓存
  if (response.status === 304) {
    const cached = await offlineStore.read<T>(cacheKey);
    if (cached) {
      return { data: cached.data, etag: cached.etag };
    }
  }

  if (!response.ok) {
    throw new Error(`API_ERROR: ${response.status}`);
  }

  const json = await response.json();
  const newEtag = response.headers.get('ETag');

  // 写入缓存
  await offlineStore.write(cacheKey, json.data, newEtag);

  return { data: json.data, etag: newEtag };
}

/**
 * 首屏并行加载——一次发起 3 个并行请求。
 * 总耗时 = max(单个请求)，而非 sum。
 */
export const api = {
  /** 并行组 1：首屏数据 */
  async getHomeDashboard(): Promise<{ data: HomeDashboard; etag?: string }> {
    const [profile, insights, moodToday] = await Promise.all([
      authedFetch<UserProfile>('/profile', 'profile'),
      authedFetch<GrowthInsights>('/growth/insights', 'growth:insights'),
      authedFetch<MoodToday>('/mood-checkins/today', 'mood:today'),
    ]);

    return {
      data: {
        profile: profile.data,
        insights: insights.data,
        moodToday: moodToday.data,
      },
      etag: `${profile.etag}|${insights.etag}|${moodToday.etag}`,
    };
  },

  /** 延迟加载：Growth Timeline（用户滚动到时触发） */
  async getGrowthTimeline(): Promise<{ data: GrowthTimeline; etag?: string }> {
    return authedFetch<GrowthTimeline>(
      '/growth/timeline?limit=30',
      'growth:timeline',
    );
  },

  /** 延迟加载：Personality Profile（进入 Profile Tab 时触发） */
  async getPersonality(): Promise<{ data: PersonalityProfile; etag?: string }> {
    return authedFetch<PersonalityProfile>('/personality', 'personality');
  },

  /** Reflect Tab：加载历史对话 */
  async getRecentMessages(): Promise<{ data: ChatMessage[]; etag?: string }> {
    return authedFetch<ChatMessage[]>(
      '/chat/messages?limit=20',
      'chat:recent',
    );
  },
};
```

### 4.5 延迟加载实现

```typescript
// src/hooks/useLazyLoad.ts

import { useState, useCallback, useRef } from 'react';

/**
 * 延迟加载 Hook——组件进入可视区域时才触发数据加载。
 * 用于 Growth Timeline、Personality Profile 等非首屏内容。
 */
export function useLazyLoad<T>(
  fetcher: () => Promise<{ data: T; etag?: string }>,
  cacheKey: string,
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const triggered = useRef(false);

  const trigger = useCallback(async () => {
    if (triggered.current) return; // 防止重复触发
    triggered.current = true;

    // 先读缓存
    const cached = await offlineStore.read<T>(cacheKey);
    if (cached) {
      setData(cached.data);
    }

    setLoading(true);
    try {
      const { data: fresh } = await fetcher();
      setData(fresh);
    } catch {
      // 失败时保持缓存数据
    } finally {
      setLoading(false);
    }
  }, [fetcher, cacheKey]);

  return { data, loading, trigger };
}
```

---

## 5. 缓存策略

### 5.1 本地 SQLite 缓存有效期（TTL）

不同数据类型使用不同的 TTL，平衡新鲜度与离线可用性：

| 缓存键 | 数据类型 | TTL | 过期行为 | 原因 |
|--------|---------|-----|---------|------|
| `profile` | 用户配置 | 7 天 | 过期后后台同步，仍显示旧缓存 | 配置极少变更 |
| `growth:insights` | 成长洞察 | 1 小时 | 过期后后台同步 | 每日更新，小时内不变 |
| `mood:today` | 今日情绪签到 | 5 分钟 | 过期后后台同步 | 用户可能随时签到 |
| `growth:timeline` | 成长时间线 | 24 小时 | 过期后后台同步 | 每日 23:00 生成新洞察 |
| `personality` | 性格画像 | 7 天 | 过期后后台同步 | 性格类型稳定 |
| `chat:recent` | 最近对话 | 即时（不缓存） | 每次进入 Reflect Tab 请求 | 对话实时性强 |
| `memories:*` | 记忆列表 | 24 小时 | 过期后后台同步 | 记忆每日摘要生成 |

```typescript
// src/services/offline-first.ts (TTL 扩展)

const CACHE_TTL: Record<string, number> = {
  'profile':           7 * 24 * 60 * 60 * 1000,  // 7 天
  'growth:insights':        60 * 60 * 1000,       // 1 小时
  'mood:today':          5 * 60 * 1000,           // 5 分钟
  'growth:timeline':   24 * 60 * 60 * 1000,       // 24 小时
  'personality':       7 * 24 * 60 * 60 * 1000,   // 7 天
  'chat:recent':                     0,           // 不缓存（即时）
};

/**
 * 判断缓存是否过期。
 * 注意：过期不代表清除，仍可用于离线渲染，仅标记需要后台同步。
 */
function isCacheExpired(cacheKey: string, cachedAt: number): boolean {
  const ttl = CACHE_TTL[cacheKey] ?? 24 * 60 * 60 * 1000; // 默认 24h
  if (ttl === 0) return true;
  return Date.now() - cachedAt > ttl;
}
```

### 5.2 ETag / If-None-Match 条件请求

```typescript
// src/services/offline-first.ts (ETag 请求逻辑)

/**
 * 条件请求流程：
 * 1. 首次请求：无 ETag → 后端返回完整数据 + ETag → 缓存数据和 ETag
 * 2. 后续请求：携带 If-None-Match: {etag}
 *    - 数据未变 → 后端返回 304 Not Modified（无 body，极快）→ 使用缓存
 *    - 数据已变 → 后端返回 200 + 新数据 + 新 ETag → 更新缓存
 */

async function conditionalFetch<T>(
  path: string,
  cacheKey: string,
): Promise<{ data: T; fromCache: boolean; etag: string | null }> {
  const token = await getValidAccessToken();
  const cached = await offlineStore.read<T>(cacheKey);
  const etag = cached?.etag ?? null;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Authorization': `Bearer ${token}`,
      ...(etag ? { 'If-None-Match': etag } : {}),
    },
  });

  if (response.status === 304) {
    // 数据未变，304 响应无 body，直接用缓存
    return {
      data: cached!.data,
      fromCache: true,
      etag: etag,
    };
  }

  if (!response.ok) {
    throw new Error(`API_ERROR: ${response.status}`);
  }

  const json = await response.json();
  const newEtag = response.headers.get('ETag');

  // 更新缓存
  await offlineStore.write(cacheKey, json.data, newEtag);

  return {
    data: json.data,
    fromCache: false,
    etag: newEtag,
  };
}
```

```python
# 后端 ETag 支持（FastAPI 示例）
# app/api/v1/profile.py

from fastapi import APIRouter, Depends, Request, Response
import hashlib, json

router = APIRouter(prefix="/api/v1", tags=["profile"])

@router.get("/profile")
async def get_profile(
    request: Request,
    user: dict = Depends(get_current_user),
):
    profile = await profile_service.get(user["sub"])

    # 生成 ETag（基于数据内容 hash）
    etag = f'"{hashlib.md5(json.dumps(profile, sort_keys=True).encode()).hexdigest()}"'

    # 检查 If-None-Match
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=json.dumps({"data": profile}),
        media_type="application/json",
        headers={"ETag": etag},
    )
```

### 5.3 下拉刷新策略

```typescript
// src/hooks/usePullToRefresh.ts

import { useState, useCallback } from 'react';
import { api } from '../services/api';
import { offlineStore } from '../services/offline-first';

/**
 * 下拉刷新策略：
 * 1. 强制跳过缓存，直接请求 API（携带 ETag 条件请求）
 * 2. API 返回新数据 → 更新 UI + 写入缓存
 * 3. API 返回 304 → 提示"已是最新"
 * 4. API 失败 → 提示"网络异常，显示缓存数据"
 */
export function usePullToRefresh<T>(
  cacheKey: string,
  fetcher: () => Promise<{ data: T; etag?: string }>,
) {
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const { data, etag } = await fetcher();
      await offlineStore.write(cacheKey, data, etag ?? null);
      // 数据通过 useOfflineFirst 的 fetcher 回调自动更新 UI
    } catch (err) {
      // 静默处理：保持缓存数据，可选显示 toast 提示
      console.warn('Pull to refresh failed, showing cached data:', err);
    } finally {
      setRefreshing(false);
    }
  }, [cacheKey, fetcher]);

  return { refreshing, onRefresh };
}
```

### 5.4 数据冲突解决（Last-Write-Wins）

```typescript
// src/services/conflict-resolver.ts

/**
 * 数据冲突解决策略：Last-Write-Wins（LWW）
 *
 * 规则：
 * 1. 每条缓存数据记录 cached_at 时间戳
 * 2. 当本地写入与远端同步发生冲突时，以时间戳较新的为准
 * 3. 远端数据始终带有 updated_at 字段，与本地 cached_at 比较
 * 4. 如果本地写入后未同步成功（离线写入），标记为 pending_sync
 *    下次联网时优先提交本地 pending_sync 数据
 */

export interface SyncableData {
  id: string;
  updated_at: string;      // ISO 8601 时间戳
  pending_sync?: boolean;  // 本地未同步标记
  data: unknown;
}

/**
 * 合并本地与远端数据（LWW）。
 */
export function resolveConflict<T extends SyncableData>(
  local: T | null,
  remote: T | null,
): { winner: T | null; source: 'local' | 'remote' | 'none' } {
  if (!local && !remote) return { winner: null, source: 'none' };
  if (!local) return { winner: remote, source: 'remote' };
  if (!remote) return { winner: local, source: 'local' };

  // 本地有 pending_sync（离线写入未同步）→ 本地优先
  if (local.pending_sync) {
    return { winner: local, source: 'local' };
  }

  // 比较 updated_at 时间戳
  const localTime = new Date(local.updated_at).getTime();
  const remoteTime = new Date(remote.updated_at).getTime();

  return localTime >= remoteTime
    ? { winner: local, source: 'local' }
    : { winner: remote, source: 'remote' };
}
```

---

## 6. 各 Tab 首次加载时序

### 6.1 Tab 加载策略总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Bottom Tab Navigator                          │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │  成长     │  │  探索     │  │  Reflect │  │  我的     │             │
│  │ (Growth)  │  │(Explore)  │  │          │  │(Profile)  │             │
│  │          │  │          │  │          │  │          │             │
│  │ 离线优先  │  │ 延迟加载  │  │ 进入时    │  │ 本地配置  │             │
│  │ 缓存优先  │  │ 进入时请求│  │ 加载历史  │  │ 优先      │             │
│  │          │  │          │  │ 对话      │  │          │             │
│  │ ★ 默认Tab │  │          │  │          │  │          │             │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘             │
│                                                                      │
│  加载时机:        加载时机:        加载时机:        加载时机:          │
│  App 启动时      Tab focus 时     Tab focus 时     Tab focus 时       │
│  (首屏)          (lazy)           (lazy)           (lazy)             │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.2 成长 Tab（首页）—— 离线优先，缓存优先

```
加载时机：App 启动时立即加载（默认 Tab）

时序：
  App 启动
    ├── SQLite 读取缓存 → 立即渲染 (~880ms 首屏可见)
    ├── 后台并行 API:
    │     GET /profile + GET /growth/insights + GET /mood-checkins/today
    └── API 返回 → 静默更新 UI

数据源：内存缓存 → SQLite 缓存 → API（三层降级）
刷新方式：下拉刷新（强制请求 API）
```

```typescript
// src/screens/HomeScreen.tsx

import React, { useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, RefreshControl,
} from 'react-native';
import { useOfflineFirst } from '../hooks/useOfflineFirst';
import { usePullToRefresh } from '../hooks/usePullToRefresh';
import { api } from '../services/api';
import { SkeletonCard } from '../components/SkeletonCard';
import { colors } from '../theme/colors';

export default function HomeScreen() {
  const { data: homeData, loading, isStale } = useOfflineFirst(
    'home:dashboard',
    () => api.getHomeDashboard(),
  );

  const { refreshing, onRefresh } = usePullToRefresh(
    'home:dashboard',
    () => api.getHomeDashboard(),
  );

  // 首次安装无缓存 → 骨架屏
  if (loading && !homeData) {
    return (
      <View style={styles.container}>
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </View>
    );
  }

  return (
    <FlatList
      data={homeData ? [homeData] : []}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
      }
      renderItem={() => (
        <View>
          <HomeHeader profile={homeData!.profile} />
          <ReflectionCard insights={homeData!.insights} isStale={isStale} />
          <MoodStreakCard moodToday={homeData!.moodToday} />
        </View>
      )}
    />
  );
}
```

### 6.3 探索 Tab —— 延迟加载，进入时才请求

```
加载时机：用户切换到 Explore Tab 时首次加载（Tab focus 触发）

时序：
  Tab focus (首次)
    ├── SQLite 读取缓存 → 有缓存则立即渲染
    ├── 无缓存 → 骨架屏
    ├── API: GET /growth/timeline?limit=30
    └── API 返回 → 渲染时间线 + 写入缓存

数据源：SQLite 缓存 → API
刷新方式：下拉刷新
```

```typescript
// src/screens/ExploreScreen.tsx

import React, { useEffect } from 'react';
import { View, FlatList } from 'react-native';
import { useOfflineFirst } from '../hooks/useOfflineFirst';
import { usePullToRefresh } from '../hooks/usePullToRefresh';
import { api } from '../services/api';
import { SkeletonCard } from '../components/SkeletonCard';

export default function ExploreScreen() {
  // 延迟加载：仅在此 Tab 首次被 focus 时才触发
  const { data: timeline, loading } = useOfflineFirst(
    'growth:timeline',
    () => api.getGrowthTimeline(),
  );

  const { refreshing, onRefresh } = usePullToRefresh(
    'growth:timeline',
    () => api.getGrowthTimeline(),
  );

  if (loading && !timeline) {
    return (
      <View>
        <SkeletonCard />
        <SkeletonCard />
      </View>
    );
  }

  return (
    <FlatList
      data={timeline?.items ?? []}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
      }
      renderItem={({ item }) => <TimelineItem item={item} />}
    />
  );
}
```

### 6.4 Reflect Tab —— 进入时加载历史对话

```
加载时机：用户切换到 Reflect Tab 时加载最近对话历史

时序：
  Tab focus (首次)
    ├── SQLite 读取缓存 → 有缓存则立即渲染历史消息
    ├── 无缓存 → 空对话界面（无骨架屏，直接显示输入框）
    ├── API: GET /chat/messages?limit=20
    └── API 返回 → 渲染消息列表 + 写入缓存

  用户发送消息时:
    ├── POST /chat/messages/stream (SSE 流式)
    ├── 首 token < 1.5s
    └── 逐 token 渲染

数据源：API（对话实时性强，缓存仅用于离线查看历史）
刷新方式：滚动到顶部加载更多（分页）
```

```typescript
// src/screens/ReflectScreen.tsx

import React, { useState, useCallback, useEffect } from 'react';
import { View, FlatList, TextInput, Button } from 'react-native';
import { api } from '../services/api';
import { offlineStore } from '../services/offline-first';

export default function ReflectScreen() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);

  // 进入 Tab 时加载历史对话
  useEffect(() => {
    (async () => {
      // 1. 先读缓存（离线查看历史）
      const cached = await offlineStore.read<ChatMessage[]>('chat:recent');
      if (cached) {
        setMessages(cached.data);
      }

      // 2. 请求最新消息
      try {
        const { data } = await api.getRecentMessages();
        setMessages(data);
        await offlineStore.write('chat:recent', data, null);
      } catch {
        // 离线时保持缓存数据
      }
    })();
  }, []);

  /** 发送消息（SSE 流式接收） */
  const sendMessage = useCallback(async () => {
    if (!input.trim() || streaming) return;

    const userMessage: ChatMessage = {
      id: `temp-${Date.now()}`,
      content: input,
      role: 'user',
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setStreaming(true);

    // SSE 流式接收
    const aiMessage: ChatMessage = {
      id: `temp-ai-${Date.now()}`,
      content: '',
      role: 'assistant',
      is_ai_generated: true,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, aiMessage]);

    try {
      await api.streamChat(input, (token: string) => {
        // 逐 token 追加到 AI 消息
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: updated[updated.length - 1].content + token,
          };
          return updated;
        });
      });
    } catch {
      // 流式失败 → 显示 "稍等，我想想..."
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content: '稍等，我想想...',
        };
        return updated;
      });
    } finally {
      setStreaming(false);
    }
  }, [input, streaming]);

  return (
    <View style={{ flex: 1 }}>
      <FlatList
        data={messages}
        renderItem={({ item }) => <MessageBubble message={item} />}
      />
      <TextInput
        value={input}
        onChangeText={setInput}
        placeholder="说点什么..."
        editable={!streaming}
      />
      <Button title="发送" onPress={sendMessage} disabled={streaming} />
    </View>
  );
}
```

### 6.5 我的 Tab —— 本地配置优先

```
加载时机：用户切换到 Profile Tab 时加载

时序：
  Tab focus (首次)
    ├── 本地配置立即渲染（通知开关、语言、时区等——存于 SecureStore / SQLite）
    ├── API: GET /profile + GET /personality
    └── API 返回 → 更新远端配置部分

数据源：本地配置（即时）→ API（远端数据）
刷新方式：配置变更时自动提交（PUT /profile）
```

```typescript
// src/screens/ProfileScreen.tsx

import React, { useEffect, useState } from 'react';
import { View, Switch, Text } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { api } from '../services/api';
import { offlineStore } from '../services/offline-first';

export default function ProfileScreen() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [pushEnabled, setPushEnabled] = useState(false); // 本地配置即时可用

  useEffect(() => {
    (async () => {
      // 1. 本地配置即时读取（无需等待 API）
      const localPush = await SecureStore.getItemAsync('pref_push_enabled');
      setPushEnabled(localPush === 'true');

      // 2. 远端配置异步加载
      const cached = await offlineStore.read<UserProfile>('profile');
      if (cached) setProfile(cached.data);

      try {
        const { data } = await api.getProfile();
        setProfile(data);
      } catch {
        // 离线时使用缓存
      }
    })();
  }, []);

  /** 配置变更 → 本地即时更新 + 后台提交远端 */
  const togglePush = async (value: boolean) => {
    setPushEnabled(value); // 本地即时生效
    await SecureStore.setItemAsync('pref_push_enabled', value.toString());

    // 后台提交（不阻塞 UI）
    if (profile) {
      api.updateProfile({ push_enabled: value }).catch(() => {
        // 提交失败 → 标记 pending_sync，下次联网时重试
      });
    }
  };

  return (
    <View>
      <Text>推送通知</Text>
      <Switch value={pushEnabled} onValueChange={togglePush} />
      {profile && <ProfileInfo profile={profile} />}
    </View>
  );
}
```

### 6.6 各 Tab 加载策略对比

| Tab | 加载时机 | 数据源优先级 | 骨架屏 | 下拉刷新 | 延迟加载项 |
|-----|---------|-------------|--------|---------|-----------|
| 成长 (首页) | App 启动时 | 内存 > SQLite > API | 是（无缓存时） | 是 | Personality Profile |
| 探索 | Tab focus 时 | SQLite > API | 是（无缓存时） | 是 | - |
| Reflect | Tab focus 时 | API > SQLite（缓存仅离线） | 否（空对话界面） | 否（分页加载） | - |
| 我的 | Tab focus 时 | 本地配置 > API | 否（本地即时） | 否（变更即提交） | Personality |

---

## 7. 性能指标

### 7.1 性能指标总表

| 指标 | 目标 | 测量方式 | 对应章节 |
|------|------|---------|---------|
| **首屏渲染（冷启动）** | < 2s | 用户点击图标 → 首屏内容可见 | 2.1 |
| **首屏渲染（热启动）** | < 500ms | 后台切回 → 首屏内容可见 | 2.2 |
| **API 响应（P95）** | < 300ms | 请求发出 → 响应完成（不含 LLM 调用） | 4 |
| **本地 SQLite 查询** | < 50ms | 查询开始 → 结果返回 | 3 |
| **流式对话首 token** | < 1.5s | 用户发送 → 首个 token 渲染 | 6.4 |
| **Auth Token 验证** | < 20ms（本地） | Keychain 读取 + JWT 解码 | 4.3 |
| **Auth Token 刷新** | < 300ms | 请求 /auth/refresh → 新 Token | 4.3 |
| **骨架屏显示** | < 100ms | 无缓存场景下骨架屏可见 | 3.4 |
| **静默 UI 更新** | < 50ms | API 返回 → UI 刷新完成 | 3.3 |
| **JS Bundle 加载** | < 600ms | 冷启动 JS 引擎初始化 | 2.1 |
| **SQLite 打开** | < 50ms | 数据库连接建立 | 2.1 |

### 7.2 性能监控埋点

```typescript
// src/services/performance.ts

/**
 * 性能埋点——记录启动各阶段耗时。
 * 数据上报至 Axiom / Sentry 用于监控 P95 表现。
 */

interface TimingMark {
  name: string;
  timestamp: number;
}

const marks: TimingMark[] = [];

/** 记录时间点 */
export function mark(name: string): void {
  marks.push({ name, timestamp: Date.now() });
}

/** 计算两个时间点之间的耗时 */
export function measure(start: string, end: string): number {
  const s = marks.find((m) => m.name === start);
  const e = marks.find((m) => m.name === end);
  if (!s || !e) return -1;
  return e.timestamp - s.timestamp;
}

/** 上报启动性能数据 */
export async function reportStartupMetrics(): Promise<void> {
  const coldStartTotal = measure('app_launch', 'first_screen_visible');
  const jsBundleLoad = measure('app_launch', 'js_bundle_ready');
  const sqliteRead = measure('sqlite_open', 'cache_read_done');
  const apiSync = measure('api_request_start', 'api_request_done');

  const metrics = {
    cold_start_total_ms: coldStartTotal,
    js_bundle_load_ms: jsBundleLoad,
    sqlite_read_ms: sqliteRead,
    api_sync_ms: apiSync,
    is_cold_start: marks.length > 0,
    timestamp: new Date().toISOString(),
  };

  // 上报至 Axiom / Sentry
  await fetch(`${API_BASE}/metrics/app-startup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(metrics),
  }).catch(() => {
    // 上报失败不影响用户体验
  });
}
```

```typescript
// App.tsx 中使用性能埋点

import { mark, reportStartupMetrics } from './services/performance';

export default function App() {
  mark('app_launch');

  useEffect(() => {
    (async () => {
      mark('js_bundle_ready');

      await offlineStore.init();
      mark('sqlite_open');

      // 先渲染缓存数据
      // ... 渲染逻辑 ...

      mark('cache_read_done');
      mark('first_screen_visible');

      // 上报性能数据（后台，不阻塞）
      reportStartupMetrics();

      // 后台同步
      mark('api_request_start');
      await api.getHomeDashboard();
      mark('api_request_done');
    })();
  }, []);

  return <NavigationContainer>...</NavigationContainer>;
}
```

### 7.3 性能预算红线

以下为各阶段性能红线，超过即需告警排查：

| 阶段 | 红线（P95） | 超线处理 |
|------|------------|---------|
| 冷启动首屏可见 | 2000ms | 检查 JS Bundle 体积、SQLite 查询慢日志 |
| 热启动首屏可见 | 500ms | 检查 AppState 恢复逻辑、内存缓存命中率 |
| API 响应（非 LLM） | 300ms | 检查后端慢查询、Vercel 冷启动 |
| SQLite 查询 | 50ms | 检查索引、查询计划 (EXPLAIN) |
| 流式首 token | 1500ms | 检查 LLM API 延迟、记忆注入耗时 |

---

## 8. 错误处理

### 8.1 错误处理总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                        错误处理决策树                                 │
└──────────────────────────────────────────────────────────────────────┘

  API 请求失败
      │
      ├── 网络超时 / 无网络
      │     ├── 有本地缓存 → 显示缓存数据 + 离线提示 banner
      │     └── 无本地缓存 → 显示错误页 + 重试按钮
      │
      ├── Auth Token 过期 (401 TOKEN_EXPIRED)
      │     ├── 有 Refresh Token → 静默刷新 → 重试原请求
      │     │     ├── 刷新成功 → 用户无感知
      │     │     └── 刷新失败 → 跳转登录页
      │     └── 无 Refresh Token → 跳转登录页
      │
      ├── API 返回错误 (4xx / 5xx)
      │     ├── 有本地缓存 → 显示缓存数据 + 错误 toast
      │     └── 无本地缓存 → 显示错误页 + 重试按钮
      │
      └── SSE 流式对话中断
            ├── 已有部分 token → 显示已接收内容 + "连接中断"提示
            └── 无 token → 显示 "稍等，我想想..." + 自动重试 1 次
```

### 8.2 网络超时：显示本地缓存 + 离线提示

```typescript
// src/components/OfflineBanner.tsx

import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

interface OfflineBannerProps {
  visible: boolean;
}

/** 顶部离线提示条——显示缓存数据时在顶部提示 */
export function OfflineBanner({ visible }: OfflineBannerProps) {
  if (!visible) return null;

  return (
    <View style={styles.banner}>
      <Text style={styles.text}>当前离线，显示缓存数据</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: colors.warning,
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  text: {
    color: colors.textPrimary,
    fontSize: 13,
    textAlign: 'center',
  },
});
```

```typescript
// src/hooks/useOfflineFirst.ts (错误处理扩展)

// 在 load() 函数中的 catch 块：
} catch (err) {
  const isNetworkError = err instanceof TypeError
    || (err as Error).message.includes('network');

  if (cached) {
    // 有缓存 → 显示缓存 + 离线提示
    setState({
      data: cached.data,
      loading: false,
      isStale: false,
      error: isNetworkError ? new Error('OFFLINE') : (err as Error),
    });
  } else {
    // 无缓存 → 显示错误页
    setState({
      data: null,
      loading: false,
      isStale: false,
      error: err as Error,
    });
  }
}
```

### 8.3 Auth Token 过期：静默刷新

```typescript
// src/services/auth-interceptor.ts

import { getValidAccessToken } from './auth';

/**
 * Auth 拦截器——自动处理 Token 过期。
 *
 * 流程：
 * 1. 请求前自动注入有效 Token
 * 2. 收到 401 TOKEN_EXPIRED → 静默刷新 → 重试原请求
 * 3. 刷新失败 → 触发登出（跳转登录页）
 * 4. 全程用户无感知（除非 Refresh Token 也过期）
 */

let onAuthFailed: (() => void) | null = null;

export function setAuthFailedHandler(handler: () => void): void {
  onAuthFailed = handler;
}

/** 带自动刷新的请求封装 */
export async function authedRequest(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const token = await getValidAccessToken();
  if (!token) {
    onAuthFailed?.();
    throw new Error('NOT_AUTHENTICATED');
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${token}`,
      ...options.headers,
    },
  });

  // 401 → 尝试刷新 Token 后重试一次
  if (response.status === 401) {
    const errorBody = await response.clone().json();
    if (errorBody?.error?.code === 'TOKEN_EXPIRED') {
      // 静默刷新
      const newToken = await getValidAccessToken(); // 内部会触发 refresh
      if (newToken) {
        // 用新 Token 重试
        return fetch(`${API_BASE_URL}${path}`, {
          ...options,
          headers: {
            'Authorization': `Bearer ${newToken}`,
            ...options.headers,
          },
        });
      }
      // 刷新失败 → 跳转登录
      onAuthFailed?.();
    }
  }

  return response;
}
```

### 8.4 API 返回错误：显示缓存 + 重试按钮

```typescript
// src/components/ErrorState.tsx

import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

interface ErrorStateProps {
  message: string;
  onRetry: () => void;
}

/** 错误状态页——无缓存且 API 失败时显示 */
export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <View style={styles.container}>
      <Text style={styles.icon}>:(</Text>
      <Text style={styles.message}>{message}</Text>
      <TouchableOpacity style={styles.retryButton} onPress={onRetry}>
        <Text style={styles.retryText}>重试</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 40,
  },
  icon: {
    fontSize: 48,
    color: colors.textSecondary,
    marginBottom: 16,
  },
  message: {
    fontSize: 16,
    color: colors.textSecondary,
    textAlign: 'center',
    marginBottom: 24,
  },
  retryButton: {
    backgroundColor: colors.primary,
    paddingHorizontal: 32,
    paddingVertical: 12,
    borderRadius: 24,
  },
  retryText: {
    color: colors.background,
    fontSize: 16,
    fontWeight: '600',
  },
});
```

```typescript
// src/screens/HomeScreen.tsx (错误处理完整版)

import { ErrorState } from '../components/ErrorState';
import { OfflineBanner } from '../components/OfflineBanner';

export default function HomeScreen() {
  const { data, loading, isStale, error } = useOfflineFirst(
    'home:dashboard',
    () => api.getHomeDashboard(),
  );

  const { onRefresh } = usePullToRefresh(
    'home:dashboard',
    () => api.getHomeDashboard(),
  );

  // 场景 1: 首次加载中，无缓存 → 骨架屏
  if (loading && !data) {
    return <HomeSkeletonScreen />;
  }

  // 场景 2: API 失败，无缓存 → 错误页 + 重试
  if (error && !data) {
    return (
      <ErrorState
        message="加载失败，请检查网络后重试"
        onRetry={onRefresh}
      />
    );
  }

  // 场景 3: API 失败，有缓存 → 缓存数据 + 离线提示
  const isOffline = error?.message === 'OFFLINE';

  return (
    <View style={{ flex: 1 }}>
      {isOffline && <OfflineBanner visible />}
      <HomeDashboard data={data} isRefreshing={isStale} />
    </View>
  );
}
```

### 8.5 错误处理场景汇总

| 错误场景 | 有缓存 | 无缓存 | 用户感知 | 恢复方式 |
|---------|--------|--------|---------|---------|
| 网络超时 | 显示缓存 + 离线 banner | 错误页 + 重试按钮 | 轻微（看到离线提示） | 网络恢复后自动同步 |
| Token 过期 | 静默刷新，无感知 | 静默刷新，无感知 | 无 | 自动（Refresh Token） |
| Token + Refresh 均过期 | 缓存数据 + 跳转登录 | 直接跳转登录 | 需重新登录 | 用户重新登录 |
| API 4xx（如配额用完） | 显示缓存 + toast 提示 | 错误页 + 提示 | 看到具体错误提示 | 按提示操作（如升级 Pro） |
| API 5xx（服务器错误） | 显示缓存 + toast 提示 | 错误页 + 重试按钮 | 看到错误提示 | 服务器恢复后重试 |
| SSE 流式中断 | N/A | N/A | 显示已接收内容 + "连接中断" | 自动重试 1 次 |
| SQLite 损坏 | 尝试重建数据库 | 尝试重建数据库 | 短暂延迟 | 重建后从 API 全量同步 |

### 8.6 SQLite 损坏恢复

```typescript
// src/services/offline-first.ts (损坏恢复)

import * as FileSystem from 'expo-file-system';

/**
 * SQLite 损坏恢复策略：
 * 1. 检测到数据库异常 → 删除本地数据库文件
 * 2. 重新创建空数据库
 * 3. 标记所有缓存为"需要全量同步"
 * 4. 后台 API 请求重新填充缓存
 */
export async function recoverCorruptedDB(): Promise<void> {
  try {
    await this.db?.closeAsync();
  } catch {
    // 忽略关闭错误
  }

  // 删除损坏的数据库文件
  await FileSystem.deleteAsync(
    `${FileSystem.documentDirectory}SQLite/${DB_NAME}`,
  );

  // 重新初始化
  this.db = await SQLite.openDatabaseAsync(DB_NAME);
  await this.db.execAsync(`
    CREATE TABLE IF NOT EXISTS cache (
      key         TEXT PRIMARY KEY,
      value       TEXT NOT NULL,
      cached_at   INTEGER NOT NULL,
      etag        TEXT,
      ttl         INTEGER NOT NULL DEFAULT 86400000
    );
  `);

  // 清空内存缓存
  this.memoryCache.clear();

  // 标记需要全量同步
  await SecureStore.setItemAsync('need_full_sync', 'true');
}
```

---

## 附录 A：启动流程代码集成

### App.tsx 完整启动流程

```typescript
// App.tsx

import React, { useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import {
  SafeAreaView, StyleSheet, View, ActivityIndicator,
} from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import * as SplashScreen from 'expo-splash-screen';

import HomeScreen from './src/screens/HomeScreen';
import ExploreScreen from './src/screens/ExploreScreen';
import ReflectScreen from './src/screens/ReflectScreen';
import ProfileScreen from './src/screens/ProfileScreen';
import { colors } from './src/theme/colors';
import { offlineStore } from './src/services/offline-first';
import { getValidAccessToken, setAuthFailedHandler } from './src/services/auth';
import { mark, reportStartupMetrics } from './src/services/performance';

const Tab = createBottomTabNavigator();

// 阻止 Splash 自动隐藏，直到首屏准备好
SplashScreen.preventAutoHideAsync().catch(() => {});

export default function App() {
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    mark('app_launch');

    (async () => {
      // 1. 初始化 SQLite（与 Keychain 读取并行）
      mark('js_bundle_ready');
      const [, token] = await Promise.all([
        offlineStore.init(),
        getValidAccessToken(),
      ]);

      mark('sqlite_open');

      // 2. 设置 Auth 失败处理（跳转登录）
      setAuthFailedHandler(() => {
        // TODO: 导航到登录页
        console.log('Auth failed, navigate to login');
      });

      // 3. 首屏已准备好 → 隐藏 Splash
      await SplashScreen.hideAsync();
      mark('first_screen_visible');

      // 4. 上报性能数据（后台）
      reportStartupMetrics();

      // 5. 触发首屏后台同步（不阻塞 UI）
      if (token) {
        mark('api_request_start');
        const should = await offlineStore.shouldSync();
        if (should) {
          try {
            const { api } = await import('./src/services/api');
            await api.getHomeDashboard();
            await offlineStore.markSynced();
            mark('api_request_done');
          } catch {
            // 同步失败不影响首屏，下次启动重试
          }
        }
      }

      setIsReady(true);
    })();
  }, []);

  if (!isReady) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <NavigationContainer>
        <Tab.Navigator
          screenOptions={{
            tabBarStyle: {
              backgroundColor: colors.background,
              borderTopColor: colors.surface,
            },
            tabBarActiveTintColor: colors.primary,
            tabBarInactiveTintColor: colors.textSecondary,
            headerShown: false,
          }}
        >
          <Tab.Screen name="Growth" component={HomeScreen} />
          <Tab.Screen name="Explore" component={ExploreScreen} />
          <Tab.Screen name="Reflect" component={ReflectScreen} />
          <Tab.Screen name="Profile" component={ProfileScreen} />
        </Tab.Navigator>
      </NavigationContainer>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  loading: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
```

### 附录 B：依赖库清单

| 库 | 用途 | 安装 |
|----|------|------|
| `expo-sqlite` | 本地 SQLite 缓存 | `npx expo install expo-sqlite` |
| `expo-secure-store` | Keychain 存储 JWT | `npx expo install expo-secure-store` |
| `expo-splash-screen` | Native Splash 控制 | `npx expo install expo-splash-screen` |
| `expo-file-system` | 数据库文件操作（损坏恢复） | `npx expo install expo-file-system` |
| `jwt-decode` | 本地 JWT 解码验证 | `npm install jwt-decode` |

> 注意：`expo-sqlite` 和 `expo-secure-store` 需在 `app.json` 的 `plugins` 中注册，构建时自动配置原生权限。
