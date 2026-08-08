/**
 * 帮助与反馈页面。
 *
 * - 静态 FAQ（手风琴展开/收起，文案全部 i18n）
 * - 反馈邮箱行（mailto: 外链，失败降级 Alert 提示邮箱地址）
 */
import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert, Linking,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { Colors, Spacing, FontSize } from '../theme/colors';
import { SUPPORT_EMAIL } from '../config/links';

const FAQ_ITEMS = [
  { q: 'help.faqCheckinQ', a: 'help.faqCheckinA' },
  { q: 'help.faqQuotaQ', a: 'help.faqQuotaA' },
  { q: 'help.faqSubscriptionQ', a: 'help.faqSubscriptionA' },
  { q: 'help.faqDataQ', a: 'help.faqDataA' },
  { q: 'help.faqDeleteQ', a: 'help.faqDeleteA' },
  { q: 'help.faqContactQ', a: 'help.faqContactA' },
] as const;

export default function HelpFeedbackScreen() {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<number | null>(null);

  // mailto 失败降级（无邮件客户端）：提示用户手动发邮件到支持邮箱
  const onContact = async () => {
    try {
      await Linking.openURL(`mailto:${SUPPORT_EMAIL}`);
    } catch {
      Alert.alert(t('common.error'), t('help.mailFailed', { email: SUPPORT_EMAIL }));
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.pageTitle}>{t('profile.help')}</Text>

      {/* FAQ */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('help.sectionFaq')}</Text>
        {FAQ_ITEMS.map((item, index) => {
          const isOpen = expanded === index;
          return (
            <View key={item.q} style={styles.faqItem}>
              <TouchableOpacity
                style={styles.faqQuestion}
                onPress={() => setExpanded(isOpen ? null : index)}
              >
                <Text style={styles.faqQuestionText}>{t(item.q)}</Text>
                <Text style={styles.faqChevron}>{isOpen ? '▾' : '▸'}</Text>
              </TouchableOpacity>
              {isOpen && <Text style={styles.faqAnswer}>{t(item.a)}</Text>}
            </View>
          );
        })}
      </View>

      {/* 联系反馈 */}
      <View style={styles.section}>
        <TouchableOpacity style={styles.settingItem} onPress={onContact}>
          <Text style={styles.settingIcon}>✉️</Text>
          <Text style={styles.settingLabel}>{t('help.contact')}</Text>
          <Text style={styles.settingValue}>{SUPPORT_EMAIL}</Text>
          <Text style={styles.arrow}>›</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  pageTitle: {
    fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary,
    marginBottom: Spacing.lg,
  },
  section: { marginBottom: Spacing.xl },
  sectionTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textTertiary, marginBottom: Spacing.md },
  faqItem: {
    borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
    paddingVertical: 12,
  },
  faqQuestion: { flexDirection: 'row', alignItems: 'center', gap: Spacing.md },
  faqQuestionText: { fontSize: FontSize.title, color: Colors.textPrimary, flex: 1, lineHeight: 22 },
  faqChevron: { fontSize: FontSize.title, color: Colors.gold400 },
  faqAnswer: {
    fontSize: FontSize.lg, color: Colors.textSecondary, lineHeight: 20,
    marginTop: Spacing.sm,
  },
  settingItem: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderLight,
  },
  settingIcon: { fontSize: 18, width: 24 },
  settingLabel: { fontSize: FontSize.title, color: Colors.textPrimary, flex: 1 },
  settingValue: { fontSize: FontSize.lg, color: Colors.textTertiary, marginRight: 8 },
  arrow: { fontSize: 20, color: Colors.textTertiary },
});
