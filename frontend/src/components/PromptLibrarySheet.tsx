/**
 * 反思提示库面板（KG-26）。
 *
 * 只做两件事：**列**（按簇）和**选**。选中之后填进输入框、编辑、保存都归 `ReflectScreen`，
 * 因为「保存成一条成长记录」这件事的真值只在那条链路上（配额、鉴权、漏斗事件都在那儿）。
 *
 * 三态齐全（加载／失败可重试／空）不是形式：这是 2026-11-01 定下的入口口径——
 * 一个只有 spinner 的面板在网络失败时就是把用户关在外面。
 *
 * 正文不翻译、不在本地留副本：`GET /reflection-prompts` 返回 en/zh，其余语言回退 en
 * 并给 `fallback=true`，这里据此挂一条「未翻译」说明（`showsFallbackNotice`），
 * 让用户知道看到的是英文而不是界面坏了。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, Modal, TouchableOpacity, ScrollView, ActivityIndicator } from 'react-native';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { listReflectionPrompts } from '../services/api';
import {
  groupByCluster,
  nextPrompt,
  showsFallbackNotice,
  type PromptLibraryPayload,
  type ReflectionPrompt,
} from '../services/reflectionPrompts';

interface Props {
  visible: boolean;
  /** 当前已选中的提示 id（用于高亮，以及「换一条」知道该往后走哪一格）。 */
  currentId: string | null;
  onClose: () => void;
  /** 用户选中一条：由调用方填进输入框并记录溯源。 */
  onUse: (prompt: ReflectionPrompt, version: string) => void;
  /** 换一条：与选中同一条路径，只是动作记成 swap（调用方决定要不要覆盖输入框原句）。 */
  onSwap: (prompt: ReflectionPrompt, version: string) => void;
}

export default function PromptLibrarySheet({ visible, currentId, onClose, onUse, onSwap }: Props) {
  const { t } = useTranslation();
  const [payload, setPayload] = useState<PromptLibraryPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [cluster, setCluster] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setFailed(false);
      setPayload(await listReflectionPrompts());
    } catch {
      // 整批提示取不到才叫失败；簇过滤取不到不该把已拿到的库扔掉，所以带 cluster 的
      // 分支不走这里（现在也只请求一次全库，切簇是本地分组）。
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible && !payload && !loading) void load();
  }, [visible, payload, loading, load]);

  const groups = payload ? groupByCluster(payload) : [];
  const shown = cluster ? groups.filter((g) => g.meta.key === cluster) : groups;
  const promptCount = shown.reduce((n, g) => n + g.items.length, 0);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>{t('reflect.promptSheetTitle')}</Text>
            <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel={t('common.close')}>
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.subtitle}>{t('reflect.promptSheetSubtitle')}</Text>

          {loading && (
            <View style={styles.state}>
              <ActivityIndicator color={Colors.gold400} />
            </View>
          )}

          {!loading && failed && (
            <View style={styles.state}>
              <Text style={styles.stateTitle}>{t('reflect.promptLoadFailed')}</Text>
              <TouchableOpacity style={styles.retryBtn} onPress={() => void load()} accessibilityRole="button">
                <Text style={styles.retryText}>{t('common.retry')}</Text>
              </TouchableOpacity>
            </View>
          )}

          {!loading && !failed && payload && promptCount === 0 && (
            <Text style={styles.stateTitle}>{t('reflect.promptEmpty')}</Text>
          )}

          {!loading && !failed && payload && promptCount > 0 && (
            <>
              <TouchableOpacity
                style={styles.swapBtn}
                onPress={() => {
                  const next = nextPrompt(payload.prompts, currentId);
                  if (next) onSwap(next, payload.version);
                }}
                accessibilityRole="button"
              >
                <Text style={styles.swapText}>🔄 {t('reflect.promptSwap')}</Text>
              </TouchableOpacity>

              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
                <TouchableOpacity
                  style={[styles.chip, cluster === null && styles.chipActive]}
                  onPress={() => setCluster(null)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: cluster === null }}
                >
                  <Text style={[styles.chipText, cluster === null && styles.chipTextActive]}>
                    {t('reflect.promptClusterAll')}
                  </Text>
                </TouchableOpacity>
                {payload.clusters.map((c) => (
                  <TouchableOpacity
                    key={c.key}
                    style={[styles.chip, cluster === c.key && styles.chipActive]}
                    onPress={() => setCluster(c.key)}
                    accessibilityRole="button"
                    accessibilityState={{ selected: cluster === c.key }}
                  >
                    <Text style={[styles.chipText, cluster === c.key && styles.chipTextActive]}>
                      {c.title} {c.count}
                    </Text>
                  </TouchableOpacity>
                ))}
              </ScrollView>

              {showsFallbackNotice(payload.fallback, payload.language_used, i18n.language) && (
                <Text style={styles.fallbackNote}>{t('reflect.promptUntranslated')}</Text>
              )}

              <ScrollView style={styles.list}>
                {shown.map((group) => (
                  <View key={group.meta.key}>
                    <Text style={styles.groupTitle}>{group.meta.title}</Text>
                    {!!group.meta.hint && <Text style={styles.groupHint}>{group.meta.hint}</Text>}
                    {group.items.map((prompt) => (
                      <View
                        key={prompt.id}
                        style={[styles.item, prompt.id === currentId && styles.itemActive]}
                      >
                        <Text style={styles.itemTitle}>{prompt.title}</Text>
                        <Text style={styles.itemQuestion}>{prompt.question}</Text>
                        <Text style={styles.itemWhy}>{prompt.why}</Text>
                        {!!prompt.follow_up && (
                          <Text style={styles.itemFollowUp}>↳ {prompt.follow_up}</Text>
                        )}
                        <TouchableOpacity
                          style={styles.useBtn}
                          onPress={() => onUse(prompt, payload.version)}
                          accessibilityRole="button"
                        >
                          <Text style={styles.useText}>
                            {prompt.id === currentId ? `✓ ${t('reflect.promptUse')}` : t('reflect.promptUse')}
                          </Text>
                        </TouchableOpacity>
                      </View>
                    ))}
                  </View>
                ))}
              </ScrollView>
            </>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: Colors.bgPrimary,
    borderTopLeftRadius: BorderRadius.xl, borderTopRightRadius: BorderRadius.xl,
    padding: Spacing.lg, paddingTop: Spacing.xl, maxHeight: '82%', minHeight: 260,
    borderWidth: 1, borderColor: Colors.borderLight,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary },
  closeText: { fontSize: FontSize.xl, color: Colors.textTertiary, paddingHorizontal: Spacing.sm },
  subtitle: {
    fontSize: FontSize.md, color: Colors.textTertiary, lineHeight: LineHeight.body,
    marginTop: Spacing.xs, marginBottom: Spacing.md,
  },
  state: { paddingVertical: Spacing.xl, alignItems: 'center', gap: Spacing.md },
  stateTitle: { fontSize: FontSize.title, color: Colors.textSecondary, textAlign: 'center', lineHeight: LineHeight.body },
  retryBtn: {
    backgroundColor: Colors.bgElevated, borderRadius: BorderRadius.pill,
    paddingHorizontal: Spacing.lg, paddingVertical: 8, borderWidth: 1, borderColor: Colors.borderLight,
  },
  retryText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  chips: { gap: 8, paddingBottom: Spacing.md },
  chip: {
    borderRadius: BorderRadius.pill, paddingHorizontal: 12, paddingVertical: 6,
    borderWidth: 1, borderColor: Colors.borderLight, backgroundColor: Colors.bgElevated,
  },
  chipActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.12)' },
  chipText: { fontSize: FontSize.sm, color: Colors.textTertiary },
  chipTextActive: { color: Colors.gold400, fontWeight: '600' },
  fallbackNote: {
    fontSize: FontSize.sm, color: Colors.textTertiary, fontStyle: 'italic', marginBottom: Spacing.sm,
  },
  // RN 默认 flexShrink=0：不显式允许收缩，ScrollView 就不会滚（同 AIConsentModal.tsx:63）
  list: { flexGrow: 0, flexShrink: 1 },
  groupTitle: {
    fontSize: FontSize.md, fontWeight: '700', color: Colors.gold400,
    marginTop: Spacing.md, marginBottom: Spacing.xs,
  },
  groupHint: { fontSize: FontSize.sm, color: Colors.textTertiary, marginBottom: Spacing.sm },
  item: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    padding: Spacing.md, marginBottom: Spacing.sm, backgroundColor: Colors.bgSecondary,
  },
  itemActive: { borderColor: Colors.gold400, backgroundColor: 'rgba(255,215,0,0.08)' },
  swapBtn: {
    alignSelf: 'flex-start', marginBottom: Spacing.sm,
    borderRadius: BorderRadius.pill, borderWidth: 1, borderColor: Colors.borderLight,
    paddingHorizontal: Spacing.md, paddingVertical: 6,
  },
  swapText: { fontSize: FontSize.md, color: Colors.textSecondary },
  itemTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary },
  itemQuestion: {
    fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.body, marginTop: 4,
  },
  itemWhy: { fontSize: FontSize.sm, color: Colors.textTertiary, lineHeight: LineHeight.body, marginTop: 4 },
  itemFollowUp: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4 },
  useBtn: {
    alignSelf: 'flex-start', marginTop: Spacing.sm,
    borderRadius: BorderRadius.pill, borderWidth: 1, borderColor: Colors.gold400,
    paddingHorizontal: Spacing.lg, paddingVertical: 6,
  },
  useText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
});
