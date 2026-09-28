/**
 * 探索页面 (P0-F05)。
 *
 * 按 UI 原型 screen-explore 实现：
 * - 性格画像卡片（主体 70%）
 * - 冷启动进度条（新用户 Day 1-6）
 * - 更多探索（性格名片）
 */
import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getPersonalityProfile } from '../services/api';
import type { PersonalityProfile } from '../types';
import { personalityProfileRepo } from '../services/db/personalityProfileRepository';
import { useAuth, useRequireAuth } from '../context/AuthContext';
import { useTranslation } from 'react-i18next';

/** 本地画像快照 → 前端 Profile（缺省字段用安全默认值，其余待远端补齐）。 */
function profileRowToState(row: {
  id: string; mbti: string | null; zodiac_sign: string | null; enneagram: string | null; created_at: string;
}): PersonalityProfile {
  return {
    id: row.id,
    mbti_type: row.mbti,
    mbti_label: null,
    zodiac: row.zodiac_sign,
    enneagram_type: row.enneagram,
    observations: [],
    unique_traits: null,
    version: 1,
    refresh_quota_remaining: 0,
    cold_start_progress: null,
    cold_start_stage: null,
    created_at: row.created_at,
  };
}

function profileStateToRow(p: PersonalityProfile) {
  return {
    id: p.id,
    mbti: p.mbti_type,
    zodiac_sign: p.zodiac,
    enneagram: p.enneagram_type,
    summary: null,
    refreshed_at: new Date().toISOString(),
    created_at: p.created_at,
    updated_at: new Date().toISOString(),
  };
}

export default function ExploreScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const requireAuth = useRequireAuth();
  const [profile, setProfile] = useState<PersonalityProfile | null>(null);
  const [loading, setLoading] = useState(true);

  // Tab 常驻不卸载：每次聚焦都刷新画像（评估/刷新后返回不过期；mount 时也会触发一次）
  // 游客态跳过请求（401 噪声），展示空画像
  useFocusEffect(
    React.useCallback(() => {
      if (isLoggedIn) {
        loadProfile();
      } else {
        setProfile(null);
        setLoading(false);
      }
    }, [isLoggedIn])
  );

  const loadProfile = async () => {
    // 首屏秒开：先读本地画像缓存（Tier 2 展示缓存）
    const cached = await personalityProfileRepo.getLatest().catch(() => null);
    if (cached) setProfile(profileRowToState(cached));
    setLoading(false);

    try {
      // 后台拉取远端最新，覆盖本地缓存并刷新 UI
      const data = await getPersonalityProfile();
      setProfile(data);
      personalityProfileRepo.cache(profileStateToRow(data)).catch(() => {});
    } catch (e) {
      console.warn('Failed to load profile:', e);
    }
  };

  // MBTI 名称按类型查 i18n 表（不依赖后端中文 label，多语言安全）
  const mbtiLabel = profile?.mbti_type ? t(`mbti.${profile.mbti_type}`) : null;

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>{t('explore.title')}</Text>

      {/* 性格画像主体卡片 */}
      <TouchableOpacity
        style={styles.personalityCard}
        onPress={() => navigation.navigate('Personality')}
      >
        <Text style={styles.personalityIcon}>🧠</Text>
        <Text style={styles.mbtiType}>{profile?.mbti_type || '—'}</Text>
        <Text style={styles.mbtiLabel}>
          {mbtiLabel ? `${mbtiLabel} · ${t('explore.personalityLabel')}` : t('explore.personalityLabel')}
        </Text>
        {(profile?.observations || []).map((obs, i) => (
          <Text key={i} style={styles.observation}>• {obs}</Text>
        ))}
        <Text style={styles.viewFull}>{t('explore.viewFull')}</Text>
      </TouchableOpacity>

      {/* 冷启动进度（新用户） */}
      {profile?.cold_start_progress != null && profile.cold_start_progress < 1 && (
        <View style={styles.coldStartCard}>
          <View style={styles.coldStartHeader}>
            <Text style={styles.coldStartIcon}>🧠</Text>
            <View style={styles.flex1}>
              <Text style={styles.coldStartTitle}>{t('explore.coldStartTitle')}</Text>
              <Text style={styles.coldStartStatus}>
                {t('explore.coldStartStatus', { day: Math.ceil(profile.cold_start_progress * 7) || 1 })}
              </Text>
            </View>
          </View>
          <View style={styles.coldStartBar}>
            <View style={[styles.coldStartFill, { width: `${profile.cold_start_progress * 100}%` }]} />
          </View>
          <View style={styles.coldStartStages}>
            {['stageIntro', 'stageObserve', 'stageExplore', 'stageReveal', 'stageApply'].map((stageKey, i) => (
              <Text
                key={stageKey}
                style={[
                  styles.stageText,
                  profile.cold_start_stage === i && styles.stageActive,
                ]}
              >
                {i + 1} {t(`explore.${stageKey}`)}
              </Text>
            ))}
          </View>
        </View>
      )}

      {/* 更多探索 */}
      <View style={styles.moreSection}>
        <Text style={styles.sectionTitle}>{t('explore.moreTitle')}</Text>
        <View style={styles.exploreGrid}>
          <TouchableOpacity
            style={styles.exploreTile}
            onPress={() => navigation.navigate('ShareCard')}
          >
            <Text style={styles.tileIcon}>🃏</Text>
            <Text style={styles.tileName}>{t('explore.personCard')}</Text>
            <Text style={styles.tileDesc}>{t('explore.personCardDesc')}</Text>
          </TouchableOpacity>
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  flex1: { flex: 1 },
  content: { padding: Spacing.lg, paddingBottom: 80 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  title: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  personalityCard: {
    backgroundColor: 'rgba(255,215,0,0.06)',
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  personalityIcon: { fontSize: 32, textAlign: 'center', marginBottom: 8 },
  mbtiType: { fontSize: 28, fontWeight: '800', color: Colors.gold400, textAlign: 'center' },
  mbtiLabel: { fontSize: FontSize.lg, color: Colors.textSecondary, textAlign: 'center', marginBottom: 12 },
  observation: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: 1.6, marginBottom: 4 },
  viewFull: { fontSize: FontSize.md, color: Colors.gold400, marginTop: 8, textAlign: 'right' },
  coldStartCard: {
    backgroundColor: 'rgba(99,102,241,0.04)',
    borderWidth: 1, borderColor: 'rgba(99,102,241,0.25)', borderStyle: 'dashed',
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginBottom: Spacing.md,
  },
  coldStartHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  coldStartIcon: { fontSize: 20 },
  coldStartTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textSecondary },
  coldStartStatus: { fontSize: FontSize.md, color: Colors.textTertiary },
  coldStartBar: {
    height: 4, backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 2, overflow: 'hidden', marginBottom: 10,
  },
  coldStartFill: { height: '100%', backgroundColor: '#C084FC', borderRadius: 2 },
  coldStartStages: { flexDirection: 'row', justifyContent: 'space-between' },
  stageText: { fontSize: FontSize.xs, color: Colors.textTertiary },
  stageActive: { color: '#C084FC' },
  moreSection: { marginTop: Spacing.md },
  sectionTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  exploreGrid: { flexDirection: 'row', gap: Spacing.md },
  exploreTile: {
    flex: 1, alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: BorderRadius.xl, padding: Spacing.lg,
  },
  tileIcon: { fontSize: 28, marginBottom: 8 },
  tileName: { fontSize: FontSize.lg, fontWeight: '600', color: Colors.textPrimary, marginBottom: 4 },
  tileDesc: { fontSize: FontSize.sm, color: Colors.textTertiary },
});