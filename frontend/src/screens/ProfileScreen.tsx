import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

/**
 * 我的 Tab
 * 展示 Personal Dataset 和用户设置。
 * TODO: 实现个人数据管理和设置
 */
export default function ProfileScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Personal Dataset</Text>
      <Text style={styles.subtitle}>个人设置与数据</Text>
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
