/**
 * 我的/设置页面。
 *
 * 按 UI 原型 screen-profile 实现：
 * - 头像 + 姓名 + 性格类型
 * - AI 与记忆（记忆管理、对话风格、主动消息时间）
 * - 订阅（升级 Pro、使用情况）
 * - 其他设置（语言、隐私、帮助）
 * - 关于 AI（合规：免责声明、模型来源、数据共享）
 * - 退出登录
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getUserProfile } from '../services/api';
import type { UserProfile } from '../types';

export default function ProfileScreen({ navigation }: { navigation: any }) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      setLoading(true);
      const data = await getUserProfile();
      setProfile(data);
    } catch (e) {
      console.warn('Failed to load profile:', e);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* User Info */}
      <View style={styles.profileHeader}>
        <Text style={styles.avatar}>🧑</Text>
        <Text style={styles.name}>{profile?.name || 'Alex'}</Text>
        <Text style={styles.type}>{profile?.mbti_type || 'INTP'} · 逻辑学家</Text>
      </View>

      {/* AI 与记忆 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>AI 与记忆</Text>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🧠</Text>
          <Text style={styles.settingLabel}>记忆管理</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>💬</Text>
          <Text style={styles.settingLabel}>对话风格</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🔔</Text>
          <Text style={styles.settingLabel}>主动消息时间</Text>
          <Text style={styles.settingValue}>09:00</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 订阅 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>订阅</Text>
        <TouchableOpacity
          style={styles.settingItem}
          onPress={() => navigation.navigate('Paywall')}
        >
          <Text style={styles.settingIcon}>⭐</Text>
          <Text style={styles.settingLabel}>升级 Pro</Text>
          <Text style={styles.proBadge}>PRO</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>📊</Text>
          <Text style={styles.settingLabel}>使用情况</Text>
          <Text style={styles.settingValue}>
            {profile?.daily_quota_used || 0}/{profile?.daily_quota_total || 5} 今日
          </Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 其他设置 */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>其他</Text>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🌐</Text>
          <Text style={styles.settingLabel}>语言</Text>
          <Text style={styles.settingValue}>English</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🔒</Text>
          <Text style={styles.settingLabel}>隐私与安全</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>❓</Text>
          <Text style={styles.settingLabel}>帮助与反馈</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 关于 AI（合规） */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>关于 AI</Text>
        <View style={styles.complianceItem}>
          <Text style={styles.settingIcon}>⚠️</Text>
          <Text style={styles.complianceText}>
            AI 生成内容可能不准确，不用于医疗、法律或财务决策。如有需要，请咨询专业人士。
          </Text>
        </View>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>🤖</Text>
          <Text style={styles.settingLabel}>AI 模型来源</Text>
          <Text style={styles.settingValueSmall}>GPT 系列 API</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.settingItem}>
          <Text style={styles.settingIcon}>📋</Text>
          <Text style={styles.settingLabel}>数据共享设置</Text>
          <Text style={styles.settingValueGreen}>已同意</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>

      {/* 退出登录 */}
      <TouchableOpacity style={styles.logoutBtn}>
        <Text style={styles.logoutText}>退出登录</Text>
      </TouchableOpacity>

      {/* Version */}
      <Text style={styles.version}>ClosenAI v1.0.0{'\n'}Build 2026.08.03</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 80 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  profileHeader: { alignItems: 'center', paddingVertical: Spacing.xl },
  avatar: { fontSize: 48, marginBottom: Spacing.md },
  name: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary },
  type: { fontSize: FontSize.lg, color: Colors.textSecondary, marginTop: 4 },
  section: { marginBottom: Spacing.xl },
  sectionTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textTertiary, marginBottom: Spacing.md },
  settingItem: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  settingIcon: { fontSize: 18, width: 24 },
  settingLabel: { fontSize: FontSize.title, color: Colors.textPrimary, flex: 1 },
  settingValue: { fontSize: FontSize.xl, color: Colors.textSecondary, marginRight: 8 },
  settingValueSmall: { fontSize: FontSize.lg, color: Colors.textTertiary, marginRight: 8 },
  settingValueGreen: { fontSize: FontSize.lg, color: Colors.success, marginRight: 8 },
  arrow: { fontSize: 20, color: Colors.textTertiary },
  proBadge: {
    fontSize: FontSize.sm, fontWeight: '700', color: Colors.gold400,
    backgroundColor: 'rgba(255,215,0,0.15)', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  complianceItem: {
    flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  complianceText: { fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: 1.6, flex: 1 },
  logoutBtn: { justifyContent: 'center', alignItems: 'center', paddingVertical: 14 },
  logoutText: { fontSize: FontSize.title, color: Colors.error },
  version: { textAlign: 'center', fontSize: FontSize.lg, color: Colors.textMuted, paddingVertical: 16 },
});