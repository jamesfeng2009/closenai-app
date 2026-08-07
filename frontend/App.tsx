import React, { useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaView, StyleSheet, ActivityIndicator, AppState, type AppStateStatus } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
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
import ShareCardScreen from './src/screens/ShareCardScreen';
import InsightsScreen from './src/screens/InsightsScreen';
import { Colors } from './src/theme/colors';
import { initI18n, default as i18n } from './src/i18n';
import { loadSession, updateUserSettings, isLoggedIn } from './src/services/api';
import { flushAnalytics } from './src/services/analytics';
import { onSessionReset } from './src/services/session';
import { AuthProvider } from './src/context/AuthContext';

const ONBOARDED_KEY = '@closenai/onboarded';

export type RootTabParamList = {
  Growth: undefined;
  Explore: undefined;
  Reflect: undefined;
  Profile: undefined;
};

export type RootStackParamList = {
  Tabs: undefined;
  Auth: undefined;
  ForgotPassword: undefined;
  Personality: undefined;
  Paywall: undefined;
  Memories: undefined;
  ShareCard: undefined;
  Insights: undefined;
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

export default function App() {
  const [ready, setReady] = useState(false);
  const [onboarded, setOnboarded] = useState(false);

  useEffect(() => {
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

    (async () => {
      // 冷启动关键路径：i18n / 登录态恢复 / 引导标记 三者无依赖，并行加载
      // loadSession 从 SecureStore 读 access/refresh token（替代旧 initApiUser 设备身份）
      const [, , stored] = await Promise.all([
        initI18n(),
        loadSession(),
        AsyncStorage.getItem(ONBOARDED_KEY).catch(() => null),
      ]);
      setOnboarded(stored === '1');
      setReady(true);
      // 首帧渲染之后上报时区（仅已登录用户；游客被守卫跳过）
      reportTimezone();
      // 首帧后上送本地分析事件队列（仅已登录；后端校验 consent + 匿名化转发 PostHog）
      if (isLoggedIn()) flushAnalytics().catch(() => {});
    })();

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

    return () => {
      subscription.remove();
      sessionCleanup();
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
          <NavigationContainer>
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
                name="ShareCard"
                component={ShareCardScreen}
                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}
              />
              <Stack.Screen
                name="Insights"
                component={InsightsScreen}
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
