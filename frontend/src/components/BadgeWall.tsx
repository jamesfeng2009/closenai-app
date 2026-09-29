/**
 * 徽章墙小组件（PRD P0-F04）。
 *
 * - 横向滚动徽章图标列表（streak_7/30/100/365、level_up_L2/L3/L4、first_chat/first_mood）
 * - 未解锁灰色 + 🔒，已解锁金色高亮
 * - 点击弹出详情（名称、解锁条件、解锁日期）
 */
import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Modal,
} from 'react-native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { useTranslation } from 'react-i18next';

export interface BadgeWallItem {
  key: string; // badge_type（streak_7 / level_up_L2 / first_chat ...）
  icon: string;
  name: string;
  condition: string; // 解锁条件文案
  unlocked: boolean;
  unlockedAt?: string | null;
}

export default function BadgeWall({ badges }: { badges: BadgeWallItem[] }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<BadgeWallItem | null>(null);

  return (
    <View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
      >
        {badges.map((b) => (
          <TouchableOpacity
            key={b.key}
            style={[styles.badge, b.unlocked ? styles.badgeUnlocked : styles.badgeLocked]}
            onPress={() => setSelected(b)}
          >
            <Text style={[styles.icon, !b.unlocked && styles.iconDim]}>
              {b.unlocked ? b.icon : '🔒'}
            </Text>
            <Text
              style={[styles.name, b.unlocked ? styles.nameUnlocked : styles.nameLocked]}
              numberOfLines={1}
            >
              {b.name}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* 徽章详情弹窗 */}
      <Modal
        visible={!!selected}
        transparent
        animationType="fade"
        onRequestClose={() => setSelected(null)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalIcon}>
              {selected?.unlocked ? selected.icon : '🔒'}
            </Text>
            <Text style={styles.modalName}>{selected?.name}</Text>
            <Text style={styles.modalCondition}>{selected?.condition}</Text>
            <Text style={[styles.modalStatus, selected?.unlocked ? styles.statusUnlocked : styles.statusLocked]}>
              {selected?.unlocked
                ? t('relationship.badgeUnlockedAt', {
                    date: selected.unlockedAt
                      ? new Date(selected.unlockedAt).toLocaleDateString()
                      : '-',
                  })
                : t('relationship.badgeLocked')}
            </Text>
            <TouchableOpacity style={styles.modalClose} onPress={() => setSelected(null)}>
              <Text style={styles.modalCloseText}>{t('common.done')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { gap: Spacing.sm, paddingVertical: Spacing.xs },
  badge: {
    width: 76, alignItems: 'center', paddingVertical: 10, paddingHorizontal: 6,
    borderRadius: BorderRadius.lg, borderWidth: 1,
  },
  badgeUnlocked: {
    backgroundColor: 'rgba(255,215,0,0.08)', borderColor: 'rgba(255,215,0,0.3)',
  },
  badgeLocked: {
    backgroundColor: 'rgba(255,255,255,0.02)', borderColor: Colors.borderLight, opacity: 0.6,
  },
  icon: { fontSize: 22 },
  iconDim: { opacity: 0.5 },
  name: { fontSize: FontSize.xs, marginTop: 4, textAlign: 'center' },
  nameUnlocked: { color: Colors.gold400, fontWeight: '600' },
  nameLocked: { color: Colors.textTertiary },
  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: Spacing.xl,
  },
  modalCard: {
    backgroundColor: Colors.bgSecondary, borderRadius: BorderRadius.xl,
    padding: Spacing.xl, alignItems: 'center',
  },
  modalIcon: { fontSize: 44, marginBottom: Spacing.md },
  modalName: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary },
  modalCondition: {
    fontSize: FontSize.title, color: Colors.textSecondary,
    marginTop: Spacing.sm, textAlign: 'center', lineHeight: LineHeight.body,
  },
  modalStatus: { fontSize: FontSize.md, marginTop: Spacing.md },
  statusUnlocked: { color: Colors.gold400, fontWeight: '600' },
  statusLocked: { color: Colors.textTertiary },
  modalClose: { marginTop: Spacing.lg, paddingVertical: 10, paddingHorizontal: 32 },
  modalCloseText: { fontSize: FontSize.title, color: Colors.gold400, fontWeight: '600' },
});
