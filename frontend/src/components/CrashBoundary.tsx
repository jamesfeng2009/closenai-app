/**
 * 崩溃兜底边界（H1）。
 *
 * 为什么必须有：本项目没有原生 ErrorBoundary，JS 侧未捕获的渲染异常在开发期是红屏、
 * 在正式包里是白屏/闪退。用户当时正在写的反思文本会随进程一起消失，
 * 而这个 App 唯一的承诺就是「你说的话不会丢」。
 *
 * 两点实现说明：
 * - Sentry v5.24.3 的 `Sentry.wrap()` 只做 TouchEventBoundary + Profiler，**不含错误边界**
 *   （与新版文档不同，已核对 node_modules/@sentry/react-native/dist/js/sdk.js:76），
 *   所以错误边界必须显式用 `Sentry.ErrorBoundary`。
 * - 未配置 DSN 时 ErrorBoundary 依旧工作：只是 captureException 变 no-op、eventId 为空，
 *   兜底页与「重试」完全可用。
 */
import React from 'react';
import { SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import { ErrorBoundary } from '@sentry/react-native';

import { BorderRadius, Colors, FontSize, Spacing } from '../theme/colors';
import { flushMonitoring } from '../services/monitoring';

export default function CrashBoundary({ children }: { children: React.ReactNode }) {
  // useTranslation 走 initReactI18next 绑定的默认实例，无需位于 I18nextProvider 之下
  const { t } = useTranslation();
  return (
    <ErrorBoundary
      onError={() => flushMonitoring()}
      fallback={({ resetError }) => (
        <SafeAreaView style={styles.wrap}>
          <View style={styles.card}>
            <Text style={styles.title}>{t('errors.crashTitle')}</Text>
            <Text style={styles.body}>{t('errors.crashBody')}</Text>
            <TouchableOpacity style={styles.btn} onPress={resetError}>
              <Text style={styles.btnText}>{t('errors.crashRetry')}</Text>
            </TouchableOpacity>
          </View>
        </SafeAreaView>
      )}
    >
      {children}
    </ErrorBoundary>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: Colors.bgPrimary, justifyContent: 'center' },
  card: { padding: Spacing.xl, alignItems: 'center' },
  title: {
    fontSize: FontSize.h3,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: Spacing.md,
    textAlign: 'center',
  },
  body: {
    fontSize: FontSize.title,
    color: Colors.textSecondary,
    lineHeight: 22,
    marginBottom: Spacing.xl,
    textAlign: 'center',
  },
  btn: {
    backgroundColor: Colors.gold400,
    borderRadius: BorderRadius.lg,
    paddingVertical: 14,
    paddingHorizontal: Spacing.xl,
  },
  btnText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
});
