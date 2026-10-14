/**
 * 性格名片页面 (P1-F04)。
 *
 * 性格画像详情（给自己看）vs 性格名片（给别人看）：
 * - 名片做"减法"：类型 + 标语 + 特质，可系统分享（微信/Ins/Twitter）
 * - 变体：classic 完整 / minimal 极简 / combo Pro 双维度组合（16 型 × 九型）
 * - combo 为 Pro 权益（40302 → 引导 Paywall）
 * - memory 回忆卡（§10.1 ① C4 第四模板）：日期大字 + 当年原文 + 情绪色环 + 水印。
 *   数据源是 GET /share/on-this-day 的候选（零 LLM、用户自己的原话），
 *   不走 getPersonalityCard、也不挂 Pro 门控——它和首页考古卡是同一条引流出口。
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, Share,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Colors, Spacing, FontSize, LineHeight, BorderRadius } from '../theme/colors';
import { getPersonalityCard, getOnThisDay, getShareLink, ApiError } from '../services/api';
import { trackFunnelShare } from '../services/funnel';
import ShareManifest from '../components/ShareManifest';
import MoodRing from '../components/charts/MoodRing';
import {
  memoryCardDate, memoryCardExcerpt, memoryCardShareMessage,
} from '../config/memoryRing';
import type { OnThisDayCandidate, PersonalityCard, ShareCardVariant } from '../types';
import { useTranslation } from 'react-i18next';

// 与后端 ErrorCode.PRO_REQUIRED 对齐（组合名片为 Pro 权益）
const CODE_PRO_REQUIRED = 40302;

const VARIANTS: ShareCardVariant[] = ['classic', 'minimal', 'combo', 'memory'];

export default function ShareCardScreen({
  navigation,
  route,
}: {
  navigation: any;
  route?: { params?: { variant?: ShareCardVariant; memory_id?: string } };
}) {
  const { t } = useTranslation();
  // 从首页考古卡带 memory_id 进来时直达回忆卡；手输/深链缺参也只回到 classic，不猜一条记忆
  const [variant, setVariant] = useState<ShareCardVariant>(
    route?.params?.variant === 'memory' ? 'memory' : 'classic',
  );
  const [card, setCard] = useState<PersonalityCard | null>(null);
  const [memory, setMemory] = useState<OnThisDayCandidate | null>(null);
  // 「今天」由服务端按用户时区下发，日期大字用它倒推年份，前端不自算周年
  const [memoryToday, setMemoryToday] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [sharing, setSharing] = useState(false);
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
        // 两条数据路各自清空：换 variant 时不能拿上一张卡的内容渲染新卡
        setCard(null);
        setMemory(null);
        if (variant === 'memory') {
          const data = await getOnThisDay();
          const wanted = route?.params?.memory_id;
          const picked =
            data.candidates.find((c) => c.memory_id === wanted)
            ?? data.candidates.find((c) => !!c.content?.trim())
            ?? null;
          if (!stale) {
            setMemory(picked);
            setMemoryToday(data.date);
          }
          return;
        }
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

  // 系统分享（性格名片走后端生成的 share_text；回忆卡走「原话 + 本人深链」两段文本）
  const onShare = async () => {
    if (variant === 'memory') {
      if (!memory || sharing) return; // 防连点：并发取链会白烧一个推荐码
      try {
        setSharing(true);
        // 深链只在真要分享时取（后端首次调用才惰性生成 referral_code）
        const { link } = await getShareLink();
        trackFunnelShare('share_tapped', 'memory');
        const res = await Share.share({
          message: memoryCardShareMessage(memory.content, link),
        });
        if (res.action === Share.sharedAction) {
          trackFunnelShare('share_completed', 'memory');
        }
      } catch (e) {
        // 走屏内错误出口，不开新的 Alert 站点（Alert 站点数由 trustPresentation 盯）
        setError(e instanceof ApiError ? e.message : t('share.memoryShareFailed'));
      } finally {
        setSharing(false);
      }
      return;
    }
    if (!card) return;
    try {
      trackFunnelShare('share_tapped', 'personality_card');
      const res = await Share.share({ message: card.share_text });
      if (res.action === Share.sharedAction) {
        trackFunnelShare('share_completed', 'personality_card');
      }
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
      ) : variant === 'memory' ? (
        !memory ? (
          <Text style={styles.stateText}>{t('share.memoryEmpty')}</Text>
        ) : (
          <>
            {/* 回忆卡：日期大字 + 当年原文 + 情绪色环 + App 水印（行动方案 §10.1 ①） */}
            <LinearGradient
              colors={Colors.gradCosmic}
              start={{ x: 0, y: 0 }}
              end={{ x: 0, y: 1 }}
              style={styles.card}
            >
              <Text style={styles.memoryDate}>
                {memoryCardDate(memoryToday, memory.years_ago) ?? '—'}
              </Text>
              <Text style={styles.memoryTitle}>
                {t('home.archiveTitle', { count: memory.years_ago, years: memory.years_ago })}
              </Text>
              <Text style={styles.memoryQuote}>「{memoryCardExcerpt(memory.content)}」</Text>
              <MoodRing
                value={memory.mood?.value ?? null}
                emoji={memory.mood?.emoji}
                accessibilityLabel={
                  memory.mood
                    ? t('share.memoryMoodA11y', {
                        emoji: memory.mood.emoji,
                        label: memory.mood.label,
                        value: memory.mood.value,
                      })
                    : t('share.memoryMoodNone')
                }
              />
              <Text style={styles.memoryMoodNote}>
                {memory.mood ? `${memory.mood.emoji} ${memory.mood.label}` : t('share.memoryMoodNone')}
              </Text>
              <View style={styles.memoryFootRow}>
                <Text style={styles.cardWatermark}>ClosenAI</Text>
              </View>
            </LinearGradient>

            {/* 清单与实际发出去的两段同源：原话 + 带推荐码的深链 */}
            <ShareManifest items={[t('home.archiveManifestQuote'), t('share.manifestLink')]} />

            <TouchableOpacity
              style={styles.shareBtn}
              disabled={sharing}
              onPress={onShare}
              accessibilityRole="button"
            >
              <Text style={styles.shareBtnText}>
                📤 {sharing ? t('common.saving') : t('share.shareBtn')}
              </Text>
            </TouchableOpacity>
          </>
        )
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
            {card.layout === 'combo' && !!(card.dimensions?.mbti || card.dimensions?.enneagram) && (
              <View style={styles.dimRow}>
                {!!card.dimensions?.mbti && (
                  <Text style={styles.dimChip}>16 {card.dimensions.mbti}</Text>
                )}
                {!!card.dimensions?.enneagram && (
                  <Text style={styles.dimChip}>
                    {t('personality.enneagramLabel')} {card.dimensions.enneagram}
                  </Text>
                )}
              </View>
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
  dimRow: { flexDirection: 'row', justifyContent: 'center', gap: Spacing.sm, marginTop: Spacing.md },
  dimChip: {
    fontSize: FontSize.sm, color: Colors.gold400, paddingHorizontal: Spacing.sm,
    paddingVertical: 2, borderWidth: 1, borderColor: Colors.borderGold, borderRadius: BorderRadius.sm,
  },
  cardFooter: {
    alignSelf: 'stretch', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginTop: Spacing.lg, paddingTop: Spacing.md,
    borderTopWidth: 1, borderTopColor: 'rgba(255,215,0,0.12)',
  },
  // 回忆卡（第四模板）
  memoryDate: {
    fontSize: 34, fontWeight: '800', color: Colors.textPrimary, letterSpacing: 1,
    lineHeight: 44,
  },
  memoryTitle: {
    fontSize: FontSize.md, color: Colors.gold400, marginTop: 4, letterSpacing: 1,
    lineHeight: LineHeight.caption,
  },
  memoryQuote: {
    fontSize: FontSize.xxl, color: Colors.textSecondary, fontStyle: 'italic', textAlign: 'center',
    marginTop: Spacing.md, marginBottom: Spacing.md, lineHeight: LineHeight.body,
  },
  memoryMoodNote: {
    fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: Spacing.sm,
    lineHeight: LineHeight.caption, textAlign: 'center',
  },
  memoryFootRow: {
    alignSelf: 'stretch', alignItems: 'flex-end', marginTop: Spacing.lg, paddingTop: Spacing.md,
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
