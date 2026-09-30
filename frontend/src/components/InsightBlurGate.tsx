/**
 * 洞察分级呈现闸门（PRD §5.20 G01）：锁「理解的呈现」，不锁原始数据。
 *
 * locked=true 时把 children 压在毛玻璃下面（PRD 设计规范 blur 4px 的原生
 * 对应：低强度 BlurView 覆盖 + 半透明蒙层），覆盖层整体可点 → 进 Paywall，
 * 并常驻「解锁高清」角标。免费态保留的聚合文字摘要由调用方放在闸门外。
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { BlurView } from 'expo-blur';
import { Colors, FontSize, BorderRadius, Spacing } from '../theme/colors';
import { useTranslation } from 'react-i18next';

// PRD「CSS blur 4px」的原生观感换算：intensity 0-100，低强度起步再叠一层
// 蒙层保证「看得见理解正在发生，但看不清细节」（§5.20 定位句）
const BLUR_INTENSITY = 30;

interface Props {
  locked: boolean;
  /** 覆盖层点击（进 Paywall 由调用方导航，组件不依赖路由） */
  onPressUnlock: () => void;
  children: React.ReactNode;
}

export default function InsightBlurGate({ locked, onPressUnlock, children }: Props) {
  const { t } = useTranslation();
  if (!locked) return <>{children}</>;
  return (
    <View>
      {children}
      <TouchableOpacity
        style={styles.overlay}
        activeOpacity={0.9}
        onPress={onPressUnlock}
        accessibilityRole="button"
        accessibilityLabel={t('insights.unlockHd')}
      >
        <BlurView intensity={BLUR_INTENSITY} tint="dark" style={StyleSheet.absoluteFill} />
        <View style={styles.badge}>
          <Text style={styles.badgeText}>🔒 {t('insights.unlockHd')}</Text>
        </View>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'flex-end',
    alignItems: 'center',
    paddingBottom: Spacing.md,
    overflow: 'hidden',
    borderRadius: BorderRadius.lg,
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.16)',
    borderWidth: 1,
    borderColor: Colors.gold400,
  },
  badgeText: { fontSize: FontSize.sm, color: Colors.gold400, fontWeight: '600' },
});
