import React, { useCallback, useEffect, useRef, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import {
  Linking,
  SafeAreaView,
  StyleSheet,
  ActivityIndicator,
  AppState,
  type AppStateStatus,
} from 'react-native';
import { NavigationContainer, useNavigationContainerRef } from '@react-navigation/native';
import type { NavigatorScreenParams } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { I18nextProvider, useTranslation } from 'react-i18next';
import AsyncStorage from '@react-native-async-storage/async-storage';

import HomeScreen from './src/screens/HomeScreen';
import ExploreScreen from './src/screens/ExploreScreen';
import ReflectScreen from './src/screens/ReflectScreen';
import ProfileScreen from './src/screens/ProfileScreen';
import OnboardingScreen from './src/screens/OnboardingScreen';
import AuthScreen from './src/screens/AuthScreen';
import ForgotPasswordScreen from './src/screens/ForgotPasswordScreen';
import PersonalityScreen from './src/screens/PersonalityScreen';
import PaywallScreen from './src/screens/PaywallScreen';
import MemoriesScreen from './src/screens/MemoriesScreen';
import RelationshipTimelineScreen from './src/screens/RelationshipTimelineScreen';
import ShareCardScreen from './src/screens/ShareCardScreen';
import FirstReadCardScreen from './src/screens/FirstReadCardScreen';
import Week1ReportScreen from './src/screens/Week1ReportScreen';
import InsightsScreen from './src/screens/InsightsScreen';
import MessagesScreen from './src/screens/MessagesScreen';
import PrivacySecurityScreen from './src/screens/PrivacySecurityScreen';
import HelpFeedbackScreen from './src/screens/HelpFeedbackScreen';
import { Colors } from './src/theme/colors';
import { initI18n, default as i18n } from './src/i18n';
import { loadSession, updateUserSettings, isLoggedIn } from './src/services/api';
import { parseReferralFromUrl, savePendingReferral } from './src/services/referral';
import { flushAnalytics, track } from './src/services/analytics';
import { getActiveRouteName, screenViewProperties } from './src/services/screenTracking';
import { registerForPushNotifications, subscribePushTokenChanges } from './src/services/push';
import { onSessionReset } from './src/services/session';
import { runColdStart } from './src/services/coldStart';
import { startMonitoring } from './src/services/monitoring';
import { AuthProvider } from './src/context/AuthContext';
import CrashBoundary from './src/components/CrashBoundary';

const ONBOARDED_KEY = '@closenai/onboarded';

// 崩溃监控须在首次渲染前初始化（DSN 留空时为 no-op，见 src/services/monitoring.ts）
startMonitoring();

export type RootTabParamList = {
  Growth: undefined;
  Explore: undefined;
  Reflect: undefined;
  // openDelete=1 表示进入本页后立即弹出注销确认（合规 5.1.1：隐私页可直达注销）
  Profile: { openDelete?: number } | undefined;
};

export type RootStackParamList = {
  // 支持 navigate('Tabs', { screen: 'Profile', params: {...} }) 跳到指定 Tab
  Tabs: NavigatorScreenParams<RootTabParamList> | undefined;
  Auth: undefined;
  ForgotPassword: undefined;
  Personality: undefined;
  Paywall: undefined;
  Memories: undefined;
  RelationshipTimeline: undefined;
  ShareCard: undefined;
  FirstReadCard: undefined;
  Week1Report: undefined;
  Insights: undefined;
  Messages: undefined;
  PrivacySecurity: undefined;
  HelpFeedback: undefined;
};

const Tab = createBottomTabNavigator<RootTabParamList>();
const Stack = createNativeStackNavigator<RootStackParamList>();

function TabNavigator() {
  const { t } = useTranslation();
  return (
    <Tab.Navigator
      screenOptions={{
        tabBarStyle: {
          backgroundColor: Colors.bgPrimary,
          borderTopColor: Colors.borderLight,
        },
        tabBarActiveTintColor: Colors.gold400,
        tabBarInactiveTintColor: Colors.textSecondary,
        headerShown: false,
      }}
    >
      <Tab.Screen name="Growth" component={HomeScreen} options={{ tabBarLabel: t('tabs.growth') }} />
      <Tab.Screen name="Explore" component={ExploreScreen} options={{ tabBarLabel: t('tabs.explore') }} />
      <Tab.Screen name="Reflect" component={ReflectScreen} options={{ tabBarLabel: t('tabs.reflect') }} />
      <Tab.Screen name="Profile" component={ProfileScreen} options={{ tabBarLabel: t('tabs.profile') }} />
    </Tab.Navigator>
  );
}

function AppRoot() {
  const [ready, setReady] = useState(false);
  const [onboarded, setOnboarded] = useState(false);

  // 页面埋点（H2）：导航就绪与状态变化各检一次，落到本地队列由 flushAnalytics 批量上送
  const navRef = useNavigationContainerRef();
  const lastScreenRef = useRef<string | null>(null);
  const emitScreenView = useCallback(() => {
    if (!navRef.isReady()) return;
    const props = screenViewProperties(
      lastScreenRef.current,
      getActiveRouteName(navRef.getRootState())
    );
    if (!props) return;
    lastScreenRef.current = props.screen;
    track('screen_view', props);
  }, [navRef]);

  useEffect(() => {
    // M4 Universal Links：已安装用户点别人分享的 closenai.app/r/{code} 会直接打开本 App，
    // 路由解析不了这条链接（它不属于任何 screen），所以这里只做一件事——把推荐码存成
    // 待消费引用，注册/首次登录时随 ref 上送。未安装时同一条链接落到 CF Pages 落地页。
    // 归因上限（App Store 一跳丢参数）见 src/services/referral.ts 头注释。
    const ingestReferral = (url: string | null): void => {
      const code = parseReferralFromUrl(url);
      if (code) savePendingReferral(code).catch(() => {});
    };
    Linking.getInitialURL()
      .then(ingestReferral)
      .catch(() => {});
    const linkSubscription = Linking.addEventListener('url', ({ url }) => ingestReferral(url));

    // 时区上报为非关键路径：fire-and-forget，不阻塞启动
    // （出海规范：后端按用户本地时区计算配额重置/streak/主动消息时间）
    // 游客守卫：未登录不上报——production 无 Bearer 必 401、development 匿名身份必 40401，
    // 纯噪音请求；登录成功后由 AuthContext.login 补报一次
    const reportTimezone = () => {
      try {
        if (!isLoggedIn()) return;
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (tz) updateUserSettings({ timezone: tz }).catch(() => {});
      } catch {
        // ignore
      }
    };

    // 冷启动关键路径：i18n / 登录态恢复 / 引导标记 三者无依赖并行加载，整体 4s 上限
    // （loadSession 从 SecureStore 读 access/refresh token；原生侧挂起时强制放行，
    //  详见 src/services/coldStart.ts 的超时与「向上修正」口径）
    const coldStart = runColdStart({
      initI18n,
      loadSession,
      readOnboarded: () => AsyncStorage.getItem(ONBOARDED_KEY),
      onReady: () => {
        setReady(true);
        // 首帧渲染之后上报时区（仅已登录用户；游客被守卫跳过）
        reportTimezone();
        // 首帧后注册推送 token（fire-and-forget，不阻塞 ready 门闸；
        // 游客/模拟器/权限被拒由 push.ts 内静默跳过；登录成功后由 AuthContext.login 补报）
        registerForPushNotifications().catch(() => {});
        // 首帧后上送本地分析事件队列（仅已登录；后端校验 consent + 匿名化转发 PostHog）
        if (isLoggedIn()) flushAnalytics().catch(() => {});
      },
      onOnboarded: setOnboarded,
    });

    // App 从后台回到前台时重新检测时区（跨时区旅行场景：streak 重置/push 时间需按新时区）
    const subscription = AppState.addEventListener('change', (nextAppState: AppStateStatus) => {
      if (nextAppState === 'active') {
        reportTimezone();
        // 回到前台时上送累积的分析事件（离线期间的事件延迟批量上送，口径按 occurred_at）
        if (isLoggedIn()) flushAnalytics().catch(() => {});
      }
    });

    // 会话重置（登出 / token 失效 / 注销）：重读引导标记
    // - 登出 / token 失效：引导标记保留（游客可继续浏览 Tabs）
    // - 注销账号：引导标记已清（resetSessionFull），回到引导页
    const sessionCleanup = onSessionReset(() => {
      AsyncStorage.getItem(ONBOARDED_KEY)
        .then((stored) => setOnboarded(stored === '1'))
        .catch(() => setOnboarded(false));
    });

    // APNs/FCM device token 轮换时重新上报后端（游客态在 push.ts 内守卫）
    const pushTokenCleanup = subscribePushTokenChanges();

    return () => {
      coldStart.cancel();
      linkSubscription.remove();
      subscription.remove();
      sessionCleanup();
      pushTokenCleanup();
    };
  }, []);

  if (!ready) {
    return (
      <SafeAreaView style={styles.container}>
        <StatusBar style="light" />
        <ActivityIndicator color={Colors.gold400} style={styles.loading} />
      </SafeAreaView>
    );
  }

  if (!onboarded) {
    return (
      <SafeAreaView style={styles.container}>
        <StatusBar style="light" />
        <I18nextProvider i18n={i18n}>
          <OnboardingScreen onComplete={() => setOnboarded(true)} />
        </I18nextProvider>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <I18nextProvider i18n={i18n}>
        <AuthProvider>
          <NavigationContainer ref={navRef} onReady={emitScreenView} onStateChange={emitScreenView}>
            <Stack.Navigator screenOptions={{ headerShown: false }}>
              <Stack.Screen name="Tabs" component={TabNavigator} />
              <Stack.Screen
                name="Auth"
                component={AuthScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="ForgotPassword"
                component={ForgotPasswordScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="Personality"
                component={PersonalityScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="Paywall"
                component={PaywallScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '', presentation: 'modal' }}
              />
              <Stack.Screen
                name="Memories"
                component={MemoriesScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="RelationshipTimeline"
                component={RelationshipTimelineScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="ShareCard"
                component={ShareCardScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="FirstReadCard"
                component={FirstReadCardScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="Week1Report"
                component={Week1ReportScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="Insights"
                component={InsightsScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="Messages"
                component={MessagesScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="PrivacySecurity"
                component={PrivacySecurityScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="HelpFeedback"
                component={HelpFeedbackScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
            </Stack.Navigator>
          </NavigationContainer>
        </AuthProvider>
      </I18nextProvider>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.bgPrimary,
  },
  loading: {
    flex: 1,
  },
});

/**
 * 根导出 = 崩溃兜底边界 + 真正的 App。
 * 边界在最外层，因此引导页/导航/任一屏的渲染异常都会落到本地化兜底页，
 * 而不是白屏或闪退（未捕获异常同时被 Sentry 捕获，前提是配了 DSN）。
 */
export default function App() {
  return (
    <CrashBoundary>
      <AppRoot />
    </CrashBoundary>
  );
}
