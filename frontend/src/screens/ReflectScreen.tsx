import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

/**
 * Reflect Tab
 * Reflection Mode 入口，提供引导式反思对话。
 * TODO: 实现 SSE 流式对话界面
 */
export default function ReflectScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Reflection Mode</Text>
      <Text style={styles.subtitle}>开始一段反思对话</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    color: colors.primary,
    fontSize: 24,
    fontWeight: '700',
  },
  subtitle: {
    color: colors.textSecondary,
    fontSize: 16,
    marginTop: 8,
  },
});
