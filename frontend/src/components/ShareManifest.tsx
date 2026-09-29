/**
 * 分享内容清单（I4 信任呈现）。
 *
 * 名片预览好看，但系统分享出去的是后端/本地拼装的纯文本——分享前必须让用户
 * 看到「实际会带走哪些字段」，而不是只能事后靠截图发现昵称也发出去了。
 * 默认折叠：只说有几项，点开逐项列出。
 */
import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { useTranslation } from 'react-i18next';

export default function ShareManifest({ items }: { items: string[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const list = items.filter((s) => !!s && !!s.trim());
  if (list.length === 0) return null;

  return (
    <View style={styles.box}>
      <TouchableOpacity
        style={styles.header}
        onPress={() => setOpen((v) => !v)}
        accessibilityRole="button"
      >
        <Text style={styles.title}>{t('share.manifestTitle')}</Text>
        <Text style={styles.count}>
          {list.length} {open ? '▾' : '▸'}
        </Text>
      </TouchableOpacity>

      {open && (
        <View style={styles.list}>
          {list.map((item, i) => (
            <Text key={i} style={styles.item} numberOfLines={3}>
              · {item}
            </Text>
          ))}
        </View>
      )}

      <Text style={styles.note}>{t('share.manifestTextNote')}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg,
    padding: Spacing.md,
    marginBottom: Spacing.md,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: FontSize.md, fontWeight: '600', color: Colors.textSecondary },
  count: { fontSize: FontSize.sm, color: Colors.textTertiary },
  list: { marginTop: Spacing.sm, gap: 4 },
  item: { fontSize: FontSize.sm, color: Colors.textTertiary, lineHeight: LineHeight.caption },
  note: {
    marginTop: Spacing.sm,
    fontSize: FontSize.xs,
    color: Colors.textTertiary,
    lineHeight: LineHeight.caption,
  },
});
