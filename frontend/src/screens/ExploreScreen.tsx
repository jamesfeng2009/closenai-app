import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

/**
 * 探索 Tab
 * 展示 Growth Timeline 和信念演变相关内容。
 * TODO: 实现时间线和信念演变可视化
 */
export default function ExploreScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Growth Timeline</Text>
      <Text style={styles.subtitle}>探索你的成长轨迹</Text>
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
