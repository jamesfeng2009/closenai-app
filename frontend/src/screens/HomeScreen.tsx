import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

/**
 * 首页 — Growth Tab
 * 展示 Today's Reflection 和 Growth Dimensions 概览。
 * TODO: 实现成长维度可视化
 */
export default function HomeScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Today's Reflection</Text>
      <Text style={styles.subtitle}>Growth Dimensions 概览</Text>
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
