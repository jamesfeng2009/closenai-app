/**
 * AI 数据共享同意弹窗（App Store 合规 5.1.2(i)）。
 *
 * 触发时机：
 * - 首次进入 AI 对话（Reflect Tab）且未做过选择
 * - 调用 AI 接口收到 403 AI_CONSENT_REQUIRED
 * - 我的 → 数据共享设置（随时可变更）
 *
 * 内容必须包含（合规）：发送内容 / 处理方 / 用途 / 存储方式 / 用户控制权。
 */
import React from 'react';
import { View, Text, StyleSheet, Modal, TouchableOpacity, ScrollView } from 'react-native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';

interface Props {
  visible: boolean;
  onAgree: () => void;
  onDecline: () => void;
}

const POINT_KEYS = ['point1', 'point2', 'point3', 'point4'] as const;

export default function AIConsentModal({ visible, onAgree, onDecline }: Props) {
  const { t } = useTranslation();
  return (
    <Modal visible={visible} transparent animationType="fade">
      <View style={styles.overlay}>
        <View style={styles.card}>
          <Text style={styles.title}>{t('consent.title')}</Text>
          {/* 小屏设备内容可滚动（按钮固定底部始终可见） */}
          <ScrollView style={styles.scroll}>
            {POINT_KEYS.map((key) => (
              <View key={key} style={styles.pointRow}>
                <Text style={styles.bullet}>•</Text>
                <Text style={styles.pointText}>{t(`consent.${key}`)}</Text>
              </View>
            ))}
            <Text style={styles.note}>{t('consent.declineNote')}</Text>
          </ScrollView>

          <TouchableOpacity style={styles.agreeBtn} onPress={onAgree}>
            <Text style={styles.agreeText}>{t('consent.agree')}</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.declineBtn} onPress={onDecline}>
            <Text style={styles.declineText}>{t('consent.decline')}</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'center', padding: Spacing.lg,
  },
  card: {
    backgroundColor: Colors.bgPrimary, borderRadius: BorderRadius.xl, padding: Spacing.xl,
    borderWidth: 1, borderColor: Colors.borderLight, maxHeight: '90%',
  },
  // RN 默认 flexShrink=0，需显式允许 ScrollView 收缩以触发滚动
  scroll: { flexGrow: 0, flexShrink: 1, marginBottom: Spacing.sm },
  title: {
    fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary,
    marginBottom: Spacing.lg, textAlign: 'center',
  },
  pointRow: { flexDirection: 'row', gap: 8, marginBottom: Spacing.md },
  bullet: { color: Colors.gold400, fontSize: FontSize.title },
  pointText: { flex: 1, fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 22 },
  note: {
    fontSize: FontSize.md, color: Colors.textTertiary, lineHeight: 20,
    marginTop: Spacing.xs, marginBottom: Spacing.lg,
  },
  agreeBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 14, alignItems: 'center',
  },
  agreeText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
  declineBtn: { paddingVertical: 12, alignItems: 'center' },
  declineText: { fontSize: FontSize.title, color: Colors.textTertiary },
});
