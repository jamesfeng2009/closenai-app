/**
 * 性格画像页面 (P0-F06)。
 *
 * 按 UI 原型 screen-personality 实现：
 * - MBTI 类型 + 标签
 * - 冷启动进度（7 天五阶段：破冰/观察/探索/揭示/运用）
 * - AI 观察列表 + 独特特质
 * - 刷新画像（配额限制：免费 3 次总计 / Pro 5 次每月）
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert,
} from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getPersonalityProfile, refreshPersonality, ApiError } from '../services/api';
import type { PersonalityProfile } from '../types';
import { useTranslation } from 'react-i18next';

const STAGE_KEYS = ['stageIntro', 'stageObserve', 'stageExplore', 'stageReveal', 'stageApply'];

export default function PersonalityScreen() {
  const { t } = useTranslation();
  const [profile, setProfile] = useState<PersonalityProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      setLoading(true);
      setProfile(await getPersonalityProfile());
    } catch (e) {
      console.warn('Failed to load personality:', e);
    } finally {
      setLoading(false);
    }
  };

  const onRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      const res = await refreshPersonality();
      if (res.success && res.profile) {
        setProfile(res.profile);
      }
      if (res.message) {
        Alert.alert(t('common.error'), res.message);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'refresh failed';
      Alert.alert(t('common.error'), msg);
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  const progress = profile?.cold_start_progress ?? 1;
  const inColdStart = progress < 1;
  const stageIndex = Math.min(Math.floor(progress * STAGE_KEYS.length), STAGE_KEYS.length - 1);

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* 类型卡片 */}
      <View style={styles.typeCard}>
        <Text style={styles.typeEmoji}>🧬</Text>
        <Text style={styles.typeCode}>{profile?.mbti_type || '????'}</Text>
        <Text style={styles.typeLabel}>
          {profile?.mbti_label || t('explore.coldStartTitle')}
        </Text>
        <Text style={styles.version}>v{profile?.version || 1}</Text>
      </View>

      {/* 冷启动进度 */}
      {inColdStart && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>❄️ {t('explore.coldStartTitle')}</Text>
          <Text style={styles.coldStatus}>
            {t('explore.coldStartStatus', { day: Math.ceil(progress * 7) || 1 })}
          </Text>
          <View style={styles.stageRow}>
            {STAGE_KEYS.map((key, i) => (
              <View key={key} style={styles.stageItem}>
                <View style={[styles.stageDot, i <= stageIndex && styles.stageDotActive]} />
                <Text style={[styles.stageName, i <= stageIndex && styles.stageNameActive]}>
                  {t(`explore.${key}`)}
                </Text>
              </View>
            ))}
          </View>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progress * 100}%` }]} />
          </View>
        </View>
      )}

      {/* AI 观察 */}
      {!!profile?.observations?.length && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>🔍 {t('personality.observations')}</Text>
          {profile.observations.map((obs, i) => (
            <View key={i} style={styles.obsRow}>
              <Text style={styles.obsBullet}>•</Text>
              <Text style={styles.obsText}>{obs}</Text>
            </View>
          ))}
        </View>
      )}

      {/* 独特特质 */}
      {!!profile?.unique_traits && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>💎 {t('personality.uniqueTraits')}</Text>
          <Text style={styles.traitText}>{profile.unique_traits}</Text>
        </View>
      )}

      {/* 刷新 */}
      <TouchableOpacity
        style={[styles.refreshBtn, (refreshing || (profile?.refresh_quota_remaining ?? 0) <= 0) && styles.refreshBtnDisabled]}
        onPress={onRefresh}
        disabled={refreshing || (profile?.refresh_quota_remaining ?? 0) <= 0}
      >
        {refreshing ? (
          <ActivityIndicator color={Colors.bgPrimary} size="small" />
        ) : (
          <Text style={styles.refreshText}>
            {t('personality.refresh')} · {t('personality.quotaLeft', { count: profile?.refresh_quota_remaining ?? 0 })}
          </Text>
        )}
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  typeCard: {
    alignItems: 'center', padding: Spacing.xl, marginBottom: Spacing.md,
    backgroundColor: 'rgba(255,215,0,0.06)', borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl,
  },
  typeEmoji: { fontSize: 40, marginBottom: Spacing.sm },
  typeCode: { fontSize: 36, fontWeight: '700', color: Colors.gold400, letterSpacing: 4 },
  typeLabel: { fontSize: FontSize.xl, color: Colors.textSecondary, marginTop: 4 },
  version: { fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 8 },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.sm },
  coldStatus: { fontSize: FontSize.md, color: Colors.textSecondary, marginBottom: Spacing.md },
  stageRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: Spacing.sm },
  stageItem: { alignItems: 'center', flex: 1 },
  stageDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: 'rgba(255,255,255,0.12)' },
  stageDotActive: { backgroundColor: Colors.gold400 },
  stageName: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 4 },
  stageNameActive: { color: Colors.gold400 },
  progressTrack: { height: 4, backgroundColor: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: Colors.gold400 },
  obsRow: { flexDirection: 'row', gap: 8, marginBottom: 6 },
  obsBullet: { color: Colors.gold400, fontSize: FontSize.title },
  obsText: { flex: 1, fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 1.5 },
  traitText: { fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 1.6 },
  refreshBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 14, alignItems: 'center', marginTop: Spacing.sm,
  },
  refreshBtnDisabled: { opacity: 0.4 },
  refreshText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
});
