/**
 * 认证状态上下文。
 *
 * - 启动时从 SecureStore 恢复登录态（loadSession 已在 App.tsx 冷启动调用）
 * - 暴露 isLoggedIn / user / login / logout / deleteAccount / refreshUser
 * - 订阅 api.ts 的 onSessionReset：token 失效时自动置未登录（游客可继续浏览）
 * - 游客门控：各写操作屏用 useRequireAuth() 拦截未登录跳 AuthScreen
 */
import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { useNavigation } from '@react-navigation/native';
import { useTranslation } from 'react-i18next';
import { Alert } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  getAuthUser, getRefreshToken, deleteAccount, logoutApi,
  getMe, updateAuthUser, updateUserSettings,
} from '../services/api';
import { registerForPushNotifications } from '../services/push';
import { resetSession, resetSessionFull } from '../services/session';
import { onSessionReset } from '../services/api';
import type { AuthResponse, AuthUser } from '../types';

// 引导期游客选择的 MBTI 暂存 key（与 OnboardingScreen 共用）
const PENDING_MBTI_KEY = '@closenai/pending_mbti';

interface AuthContextValue {
  user: AuthUser | null;
  isLoggedIn: boolean;
  /** 登录/注册/验码登录/Apple 登录成功后写入登录态（token 已由 api.ts 持久化）。 */
  login: (resp: AuthResponse) => void;
  /** 登出：吊销 refresh + 清本机态，保留引导（游客浏览）。推送 token 不注销（后端 APNs 410 自清）。 */
  logout: () => Promise<void>;
  /** 注销账号：级联删除全部数据 + 全量重置，回到引导页。 */
  deleteMyAccount: () => Promise<void>;
  /** 重新拉取当前用户信息（token 有效时）。 */
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => getAuthUser());

  // 订阅 token 失效 / 登出 / 注销：同步清空内存登录态
  useEffect(() => {
    return onSessionReset(() => {
      setUser(null);
    });
  }, []);

  const login = useCallback((resp: AuthResponse) => {
    // api.ts 的 register/login/appleLogin 等已调 setAuth 持久化 token + user
    setUser(resp.user);
    // 游客→登录转换时 AppState/冷启动均不触发时区上报，此处补报一次（fire-and-forget）
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (tz) updateUserSettings({ timezone: tz }).catch(() => {});
    } catch {
      // ignore
    }
    // 登录成功后补报推送 token（fire-and-forget；权限/模拟器/失败在 push.ts 内静默处理）
    registerForPushNotifications().catch(() => {});
    // 引导期游客暂存的 MBTI：登录/注册成功后补写后端并清除暂存
    // （fire-and-forget + try/catch，不阻断登录主流程；失败保留暂存待下次登录重试）
    void (async () => {
      try {
        const pending = await AsyncStorage.getItem(PENDING_MBTI_KEY);
        if (!pending) return;
        await updateUserSettings({ mbti_type: pending });
        await AsyncStorage.removeItem(PENDING_MBTI_KEY);
      } catch {
        // ignore
      }
    })();
  }, []);

  const logout = useCallback(async () => {
    const rt = getRefreshToken();
    // 顺序：吊销 refresh → 清本机态
    // 推送 token 登出不注销（后端 APNs 410 自清；保留 token 使重新登录前也能收到主动消息）
    if (rt) {
      try {
        await logoutApi(rt);
      } catch {
        // 后端吊销失败不阻断本地登出
      }
    }
    await resetSession();
    setUser(null);
  }, []);

  const deleteMyAccount = useCallback(async () => {
    // 删除失败时原样上抛（保留 ApiError 的服务端文案），由调用方（ProfileScreen）兜底提示
    await deleteAccount();
    await resetSessionFull();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const u = await getMe();
      await updateAuthUser(u);
      setUser(u);
    } catch {
      // token 无效时 getMe 内部 401 已触发 session reset
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, isLoggedIn: !!user, login, logout, deleteMyAccount, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/**
 * 游客写操作拦截。
 *
 * 在写操作前调用 requireAuth()：未登录时弹提示并跳 AuthScreen，返回 false；
 * 已登录返回 true。必须在 NavigationContainer 内的屏幕组件中使用。
 */
export function useRequireAuth() {
  const { isLoggedIn } = useAuth();
  const navigation = useNavigation();
  const { t } = useTranslation();

  return useCallback((): boolean => {
    if (isLoggedIn) return true;
    Alert.alert(
      t('auth.loginRequiredTitle'),
      t('auth.loginRequiredMessage'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('auth.login'),
          onPress: () => navigation.navigate('Auth' as never),
        },
      ],
    );
    return false;
  }, [isLoggedIn, navigation, t]);
}
