import React, { useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaView, StyleSheet, ActivityIndicator } from 'react-native';
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
import PersonalityScreen from './src/screens/PersonalityScreen';
import PaywallScreen from './src/screens/PaywallScreen';
import MemoriesScreen from './src/screens/MemoriesScreen';
import ShareCardScreen from './src/screens/ShareCardScreen';
import InsightsScreen from './src/screens/InsightsScreen';
import { Colors } from './src/theme/colors';
import { initI18n, default as i18n } from './src/i18n';
import { initApiUser, updateUserSettings } from './src/services/api';
import { onSessionReset } from './src/services/session';

const ONBOARDED_KEY = '@closenai/onboarded';

export type RootTabParamList = {
  Growth: undefined;
  Explore: undefined;
  Reflect: undefined;
  Profile: undefined;
};

export type RootStackParamList = {
  Tabs: undefined;
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
    (async () => {
      // 冷启动关键路径：i18n / 设备身份 / 引导标记三者无依赖，并行加载（串行会拉长启动）
      // 顺序保证：initApiUser 必须先于任何 API 调用完成（请求头依赖 cachedUserId），
      // 因此所有首屏网络请求只能在 ready 之后由屏幕组件发起。
      const [, , stored] = await Promise.all([
        initI18n(),
        initApiUser(),
        AsyncStorage.getItem(ONBOARDED_KEY).catch(() => null),
      ]);
      setOnboarded(stored === '1');
      setReady(true);
      // 时区上报为非关键路径：首帧渲染之后 fire-and-forget，不阻塞启动
      // （出海规范：后端按用户本地时区计算配额重置/streak/主动消息时间）
      try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (tz) updateUserSettings({ timezone: tz }).catch(() => {});
      } catch {
        // ignore
      }
    })();
    // 退出登录/注销后：回到引导页（新设备身份）
    return onSessionReset(() => setOnboarded(false));
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
        <NavigationContainer>
          <Stack.Navigator screenOptions={{ headerShown: false }}>
            <Stack.Screen name="Tabs" component={TabNavigator} />
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