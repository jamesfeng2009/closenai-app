/**
 * 性格初读卡页面（P1-B01 · UI 原型 Screen 29 · 架构方案 §二 M1）。
 *
 * 第 0 天的「它懂我」证据卡：断言全部来自用户在引导快问里自己选的选项（静态模板直出），
 * 不是预测、不是测评结论，因此合规上不触发 4.3(b) 与医疗化红线（PRD §5.19.2 合规条）。
 *
 * 数据来源优先级：本地缓存（AuthContext 登录补写时写入）→ GET /onboarding/first-read。
 * 游客无 token 时 GET 必失败，展示空态并引导完成引导。
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, Share,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';

import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { getFirstRead, ApiError } from '../services/api';
import { loadFirstReadCard, markFirstReadSeen, saveFirstReadCard } from '../services/firstRead';
import type { FirstReadCard } from '../types';

export default function FirstReadCardScreen({ navigation }: { navigation: any }) {
  const { t } = useTranslation();
  const [card, setCard] = useState<FirstReadCard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stale = false;
    // 打开即视为已读：成长首页的初读卡入口只在未读时展示
    markFirstReadSeen().catch(() => {});
    (async () => {
      try {
        const cached = await loadFirstReadCard();
        if (cached) {
          if (!stale) setCard(cached);
          return;
        }
        const data = await getFirstRead();
        if (stale) return;
        setCard(data);
        await saveFirstReadCard(data);
      } catch (e) {
        // ApiError（未登录 / 40402 未生成）走空态文案；只有非预期错误才提示
        if (!stale && !(e instanceof ApiError)) {
          Alert.alert(t('common.error'), t('firstRead.loadFailed'));
        }
      } finally {
        if (!stale) setLoading(false);
      }
    })();
    return () => { stale = true; };
  }, []);

  const onShare = async () => {
    if (!card) return;
    // 分享文案本地拼装：断言是后端模板产出的纯文本，PDF/深链归因卡随 M4 分享卡体系（B4）
    const body = [
      t('firstRead.cardTitle'),
      ...card.assertions.map((a) => `· ${a}`),
      card.summary,
      `#ClosenAI ${card.share_url ?? ''}`,
    ].filter(Boolean).join('\n');
    try {
      await Share.share({ message: body });
    } catch {
      // 用户取消分享不提示
    }
  };

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator color={Colors.gold400} style={styles.loading} />
      </View>
    );
  }

  if (!card) {
    return (
      <View style={styles.container}>
        <View style={styles.emptyWrap}>
          <Text style={styles.emptyText}>{t('firstRead.empty')}</Text>
          <TouchableOpacity
            style={styles.primaryBtn}
            onPress={() => navigation.navigate('Tabs', { screen: 'Reflect' })}
          >
            <Text style={styles.primaryBtnText}>{t('firstRead.cta')}</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.headerTitle}>{t('firstRead.title')}</Text>
      <Text style={styles.headerSubtitle}>{t('firstRead.subtitle')}</Text>

      <LinearGradient
        colors={Colors.gradCosmic}
        start={{ x: 0, y: 0 }}
        end={{ x: 0, y: 1 }}
        style={styles.card}
      >
        <Text style={styles.cardEmoji}>🌱</Text>
        <Text style={styles.cardTitle}>{t('firstRead.cardTitle')}</Text>
        <Text style={styles.cardNote}>{t('firstRead.subtitle')}</Text>

        <View style={styles.assertionList}>
          {card.assertions.map((text, i) => (
            <Text key={i} style={styles.assertionText}>· {text}</Text>
          ))}
        </View>

        {!!card.summary && <Text style={styles.summaryText}>{card.summary}</Text>}
        {!!card.closer && <Text style={styles.closerText}>{card.closer}</Text>}

        <View style={styles.cardFooter}>
          <Text style={styles.cardWatermark}>ClosenAI</Text>
        </View>
      </LinearGradient>

      {/* CTA「开始书写你自己的」→ 首条记录（PRD §5.19.2 漏斗第一环：初读卡 → 首条记录） */}
      <TouchableOpacity
        style={styles.primaryBtn}
        onPress={() => navigation.navigate('Tabs', { screen: 'Reflect' })}
      >
        <Text style={styles.primaryBtnText}>{t('firstRead.cta')}</Text>
      </TouchableOpacity>

      <TouchableOpacity style={styles.shareBtn} onPress={onShare}>
        <Text style={styles.shareBtnText}>📤 {t('firstRead.share')}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 48 },
  loading: { marginTop: 80 },
  headerTitle: {
    fontSize: FontSize.h3, fontWeight: '700', color: Colors.textPrimary, marginTop: Spacing.md,
  },
  headerSubtitle: {
    fontSize: FontSize.sm, color: Colors.textTertiary, marginTop: 4, marginBottom: Spacing.lg,
  },
  card: {
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.2)',
    borderRadius: BorderRadius.xl, padding: Spacing.xl, alignItems: 'center',
    marginBottom: Spacing.lg,
  },
  cardEmoji: { fontSize: 32, marginBottom: Spacing.sm },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '700', color: Colors.gold400 },
  cardNote: {
    fontSize: FontSize.sm, color: Colors.textTertiary, textAlign: 'center',
    marginTop: 4, marginBottom: Spacing.lg, lineHeight: 1.5,
  },
  assertionList: { alignSelf: 'stretch' },
  assertionText: {
    fontSize: FontSize.md, color: Colors.textPrimary, lineHeight: 1.7, marginBottom: Spacing.sm,
  },
  summaryText: {
    alignSelf: 'stretch', fontSize: FontSize.md, color: Colors.textSecondary,
    fontStyle: 'italic', lineHeight: 1.6, marginTop: Spacing.xs,
  },
  closerText: {
    alignSelf: 'stretch', fontSize: FontSize.sm, color: Colors.textTertiary,
    lineHeight: 1.6, marginTop: Spacing.md, paddingTop: Spacing.md,
    borderTopWidth: 1, borderTopColor: 'rgba(255,215,0,0.15)',
  },
  cardFooter: {
    alignSelf: 'stretch', flexDirection: 'row', justifyContent: 'flex-end',
    marginTop: Spacing.md,
  },
  cardWatermark: { fontSize: FontSize.sm, fontWeight: '700', color: Colors.gold400, opacity: 0.7 },
  primaryBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.pill,
    paddingVertical: 14, alignItems: 'center', marginBottom: Spacing.md,
  },
  primaryBtnText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  shareBtn: {
    borderRadius: BorderRadius.pill, borderWidth: 1, borderColor: Colors.borderLight,
    paddingVertical: 12, alignItems: 'center',
  },
  shareBtnText: { fontSize: FontSize.md, color: Colors.textSecondary },
  emptyWrap: { paddingTop: 80, paddingHorizontal: Spacing.xl },
  emptyText: {
    fontSize: FontSize.md, color: Colors.textSecondary, textAlign: 'center', marginBottom: Spacing.xl,
  },
});
