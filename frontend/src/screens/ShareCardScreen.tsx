/**
 * 性格名片页面 (P1-F04)。
 *
 * 性格画像详情（给自己看）vs 性格名片（给别人看）：
 * - 名片做"减法"：类型 + 标语 + 特质，可系统分享（微信/Ins/Twitter）
 * - 变体：classic 完整 / minimal 极简 / combo Pro 三维度组合
 * - combo 为 Pro 权益（40302 → 引导 Paywall）
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, Share,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { getPersonalityCard, ApiError } from '../services/api';
import ShareManifest from '../components/ShareManifest';
import type { PersonalityCard, ShareCardVariant } from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode.PRO_REQUIRED 对齐（组合名片为 Pro 权益）
const CODE_PRO_REQUIRED = 40302;

const VARIANTS: ShareCardVariant[] = ['classic', 'minimal', 'combo'];

export default function ShareCardScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const [variant, setVariant] = useState<ShareCardVariant>('classic');
  const [card, setCard] = useState<PersonalityCard | null>(null);
  const [loading, setLoading] = useState(true);
  // 失败态必须与「还在加载」「没有数据」分开呈现：旧写法 loading||!card 让请求
  // 失败后永远停在转圈，用户既不知道出错也没有重试入口。
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  // variant 变化时发起新请求；用 stale 闭包标志防止竞态（旧请求结果覆盖新状态）
  useEffect(() => {
    let stale = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await getPersonalityCard(variant);
        if (!stale) setCard(data);
      } catch (e) {
        if (stale) return;
        if (e instanceof ApiError && e.code === CODE_PRO_REQUIRED) {
          Alert.alert(
            t('share.proGateTitle'),
            t('share.proGateMessage'),
            [
              { text: t('common.cancel'), style: 'cancel', onPress: () => setVariant('classic') },
              { text: t('share.upgrade'), onPress: () => navigation.navigate('Paywall') },
            ],
          );
          return;
        }
        if (!stale) setError(e instanceof ApiError ? e.message : t('common.loadFailed'));
      } finally {
        if (!stale) setLoading(false);
      }
    })();
    return () => { stale = true; };
  }, [variant, reloadTick]);

  // 系统分享（分享文案由后端按语言生成，含 App Store 落地链接）
  const onShare = async () => {
    if (!card) return;
    try {
      await Share.share({ message: card.share_text });
    } catch {
      // 用户取消分享不提示
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* 变体选择（PRD §6：可生成多张名片挑选分享） */}
      <View style={styles.variantRow}>
        {VARIANTS.map((v) => (
          <TouchableOpacity
            key={v}
            style={[styles.variantBtn, variant === v && styles.variantBtnActive, loading && styles.variantBtnDisabled]}
            disabled={loading}
            onPress={() => setVariant(v)}
          >
            <Text style={[styles.variantText, variant === v && styles.variantTextActive]}>
              {t(`share.${v}`)}
            </Text>
            {v === 'combo' && <Text style={styles.proBadge}>PRO</Text>}
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <ActivityIndicator color={Colors.gold400} style={styles.loading} />
      ) : error ? (
        <View style={styles.stateBox}>
          <Text style={styles.stateText}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={() => setReloadTick((n) => n + 1)}>
            <Text style={styles.retryText}>{t('common.retry')}</Text>
          </TouchableOpacity>
        </View>
      ) : !card ? (
        <Text style={styles.stateText}>{t('share.empty')}</Text>
      ) : (
        <>
          {/* 名片预览（布局随 variant：full / compact / combo；宇宙渐变底 + 金边） */}
          <LinearGradient
            colors={Colors.gradCosmic}
            start={{ x: 0, y: 0 }}
            end={{ x: 0, y: 1 }}
            style={[styles.card, card.layout === 'compact' && styles.cardCompact]}
          >
            <Text style={styles.cardEmoji}>🧬</Text>
            <Text style={styles.cardName}>{card.user_name}</Text>
            {!!card.mbti_type && (
              <Text style={styles.cardType}>
                {card.mbti_type}{card.mbti_label ? ` · ${card.mbti_label}` : ''}
              </Text>
            )}
            <Text style={styles.cardTagline}>{card.tagline}</Text>

            {card.layout === 'full' && !!card.observations?.length && (
              <View style={styles.cardSection}>
                {card.observations.map((obs, i) => (
                  <Text key={i} style={styles.cardObs}>• {obs}</Text>
                ))}
              </View>
            )}
            {card.layout !== 'compact' && !!card.unique_traits && (
              <Text style={styles.cardTraits}>💎 {card.unique_traits}</Text>
            )}
            {card.layout === 'combo' && !!card.combo_insight && (
              <Text style={styles.cardCombo}>{card.combo_insight}</Text>
            )}

            <View style={styles.cardFooter}>
              <Text style={styles.cardFooterText}>
                🔥 {t('share.streak', { count: card.streak_count })} · ✨ {card.relationship_level}
              </Text>
              <Text style={styles.cardWatermark}>ClosenAI</Text>
            </View>
          </LinearGradient>

          {/* 分享内容清单：名片是图片预览，分享出去的是后端拼的纯文本，逐项对齐 */}
          <ShareManifest
            items={[
              card.user_name,
              card.mbti_type
                ? `${card.mbti_type}${card.mbti_label ? ` · ${card.mbti_label}` : ''}`
                : '',
              card.tagline,
              t('share.manifestLink'),
            ]}
          />

          {/* 系统分享 */}
          <TouchableOpacity style={styles.shareBtn} onPress={onShare}>
            <Text style={styles.shareBtnText}>📤 {t('share.shareBtn')}</Text>
          </TouchableOpacity>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { marginTop: 80 },
  stateBox: { alignItems: 'center', marginTop: 60, gap: Spacing.md },
  stateText: {
    color: Colors.textTertiary, textAlign: 'center', marginTop: 60,
    fontSize: FontSize.md, lineHeight: LineHeight.caption,
  },
  retryBtn: {
    paddingHorizontal: 20, paddingVertical: 8, borderRadius: BorderRadius.pill,
    backgroundColor: 'rgba(255,215,0,0.12)', borderWidth: 1, borderColor: Colors.gold400,
  },
  retryText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  variantRow: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.lg },
  variantBtn: {
    flex: 1, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 4,
    paddingVertical: 10, borderRadius: BorderRadius.lg,
    backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: Colors.borderLight,
  },
  variantBtnActive: { backgroundColor: 'rgba(255,215,0,0.12)', borderColor: Colors.gold400 },
  variantBtnDisabled: { opacity: 0.5 },
  variantText: { fontSize: FontSize.md, color: Colors.textSecondary },
  variantTextActive: { color: Colors.gold400, fontWeight: '600' },
  proBadge: {
    fontSize: FontSize.xs, fontWeight: '700', color: Colors.gold400,
    backgroundColor: 'rgba(255,215,0,0.15)', paddingHorizontal: 4, paddingVertical: 1, borderRadius: 4,
  },
  card: {
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.2)',
    borderRadius: BorderRadius.xl, padding: Spacing.xl, alignItems: 'center', marginBottom: Spacing.lg,
  },
  cardCompact: { paddingVertical: Spacing.lg },
  cardEmoji: { fontSize: 36, marginBottom: Spacing.sm },
  cardName: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary },
  cardType: { fontSize: 24, fontWeight: '800', color: Colors.gold400, letterSpacing: 2, marginTop: 4 },
  cardTagline: {
    fontSize: FontSize.md, color: Colors.textSecondary, fontStyle: 'italic',
    textAlign: 'center', marginTop: Spacing.sm, lineHeight: LineHeight.caption,
  },
  cardSection: { alignSelf: 'stretch', marginTop: Spacing.md },
  cardObs: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: LineHeight.caption, marginBottom: 4 },
  cardTraits: { fontSize: FontSize.md, color: Colors.textSecondary, marginTop: Spacing.md, textAlign: 'center' },
  cardCombo: {
    fontSize: FontSize.md, color: Colors.textTertiary, marginTop: Spacing.md,
    textAlign: 'center', lineHeight: LineHeight.caption,
  },
  cardFooter: {
    alignSelf: 'stretch', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginTop: Spacing.lg, paddingTop: Spacing.md,
    borderTopWidth: 1, borderTopColor: 'rgba(255,215,0,0.12)',
  },
  cardFooterText: { fontSize: FontSize.sm, color: Colors.textTertiary },
  cardWatermark: { fontSize: FontSize.sm, fontWeight: '700', color: Colors.gold400, opacity: 0.7 },
  shareBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    paddingVertical: 14, alignItems: 'center',
  },
  shareBtnText: { fontSize: FontSize.title, fontWeight: '700', color: Colors.bgPrimary },
});
