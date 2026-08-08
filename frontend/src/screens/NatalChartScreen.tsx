/**
 * 本命星盘页面 (PRD P0)。
 *
 * - 无星盘：出生信息表单（日期 Modal 三列滚轮 + 时间可选「不知道」+ 地点文本输入）
 * - 有星盘：三大星座卡片（太阳/月亮/上升，符号文字卡片，非轮盘图）+ 行星位置列表
 *   + AI 解读 + 今日参考（评分/幸运项/短评）
 * - 出生时间未知：上升卡片显示「未知」态；tz_fallback 显示时区提示条
 * - 禁止项：圆形星盘轮盘图、爱情运势、合盘
 */
import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator,
  Alert, Modal, FlatList, TextInput,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import {
  createNatalChart, getNatalChart, getNatalChartDaily, ApiError,
} from '../services/api';
import { useAuth } from '../context/AuthContext';
import type { NatalChart, NatalChartDaily, NatalPlanet, ZodiacSign } from '../types';
import { useTranslation } from 'react-i18next';

// 星座符号（文字字形，替代 SVG 卡片绘制，视觉次要）
const ZODIAC_SYMBOLS: Record<ZodiacSign, string> = {
  aries: '♈', taurus: '♉', gemini: '♊', cancer: '♋',
  leo: '♌', virgo: '♍', libra: '♎', scorpio: '♏',
  sagittarius: '♐', capricorn: '♑', aquarius: '♒', pisces: '♓',
};

const PLANETS: NatalPlanet[] = ['mercury', 'venus', 'mars', 'jupiter', 'saturn'];

const CURRENT_YEAR = new Date().getFullYear();
const YEARS = Array.from({ length: CURRENT_YEAR - 1900 + 1 }, (_, i) => 1900 + i);
const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1);
const DAYS = Array.from({ length: 31 }, (_, i) => i + 1);

// 出生时间选项：「不知道」+ 每半小时一档（不引新依赖，FlatList 行选模式同 ProfileScreen 时间选择器）
const TIME_UNKNOWN = 'unknown';
const TIME_OPTIONS: string[] = [
  TIME_UNKNOWN,
  ...Array.from({ length: 48 }, (_, i) => {
    const h = String(Math.floor(i / 2)).padStart(2, '0');
    return `${h}:${i % 2 === 0 ? '00' : '30'}`;
  }),
];

const pad2 = (n: number) => String(n).padStart(2, '0');

interface BirthDate {
  year: number;
  month: number;
  day: number;
}

/** 1-5 分渲染为星标（今日参考评分）。 */
function ScoreStars({ score }: { score: number }) {
  const n = Math.max(0, Math.min(5, Math.round(score)));
  return <Text style={styles.stars}>{'★'.repeat(n)}{'☆'.repeat(5 - n)}</Text>;
}

export default function NatalChartScreen() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [loading, setLoading] = useState(true);
  const [chart, setChart] = useState<NatalChart | null>(null);
  const [daily, setDaily] = useState<NatalChartDaily | null>(null);
  const [editing, setEditing] = useState(false); // 「重新填写」强制展开表单
  const [submitting, setSubmitting] = useState(false);

  // 表单状态
  const [birthDate, setBirthDate] = useState<BirthDate | null>(null);
  const [birthTime, setBirthTime] = useState<string | null>(null); // null = 不知道/未提供
  const [birthPlace, setBirthPlace] = useState('');
  const [dateModalVisible, setDateModalVisible] = useState(false);
  const [timeModalVisible, setTimeModalVisible] = useState(false);
  const [draftDate, setDraftDate] = useState<BirthDate>({ year: 2000, month: 1, day: 1 });

  // Stack 页每次进入拉取（次级页从简，不做本地缓存）；游客被入口守卫拦截，此处兜底跳过
  useEffect(() => {
    if (isLoggedIn) {
      load();
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn]);

  const load = async () => {
    try {
      setLoading(true);
      const data = await getNatalChart();
      setChart(data);
      setEditing(!data);
      if (data) {
        // 今日参考为附属模块：失败/为空不阻断主星盘展示
        const d = await getNatalChartDaily().catch(() => null);
        setDaily(d);
      }
    } catch (e) {
      console.warn('Failed to load natal chart:', e);
    } finally {
      setLoading(false);
    }
  };

  const openDateModal = () => {
    setDraftDate(birthDate ?? { year: 2000, month: 1, day: 1 });
    setDateModalVisible(true);
  };

  const confirmDate = () => {
    // 按年月钳制日（如 2 月 30 日 → 28/29 日）
    const maxDay = new Date(draftDate.year, draftDate.month, 0).getDate();
    setBirthDate({ ...draftDate, day: Math.min(draftDate.day, maxDay) });
    setDateModalVisible(false);
  };

  const onSubmit = async () => {
    if (!birthDate || !birthPlace.trim()) {
      Alert.alert(t('common.error'), t('natal.fillRequired'));
      return;
    }
    try {
      setSubmitting(true);
      const data = await createNatalChart({
        birth_date: `${birthDate.year}-${pad2(birthDate.month)}-${pad2(birthDate.day)}`,
        ...(birthTime ? { birth_time: birthTime } : {}),
        birth_place: birthPlace.trim(),
      });
      setChart(data);
      setEditing(false);
      const d = await getNatalChartDaily().catch(() => null);
      setDaily(d);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('errors.submitFailed');
      Alert.alert(t('common.error'), msg);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.gold400} />
      </View>
    );
  }

  // ═══ 表单态（无星盘 / 重新填写）═══
  if (!chart || editing) {
    return (
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        <Text style={styles.title}>{t('natal.formTitle')}</Text>
        <Text style={styles.formSubtitle}>{t('natal.formSubtitle')}</Text>

        {/* 出生日期（必填，Modal 三列选择） */}
        <Text style={styles.fieldLabel}>{t('natal.birthDate')}</Text>
        <TouchableOpacity style={styles.fieldBtn} onPress={openDateModal}>
          <Text style={birthDate ? styles.fieldValue : styles.fieldPlaceholder}>
            {birthDate
              ? `${birthDate.year}-${pad2(birthDate.month)}-${pad2(birthDate.day)}`
              : t('natal.selectDate')}
          </Text>
        </TouchableOpacity>

        {/* 出生时间（可选，含「不知道」） */}
        <Text style={styles.fieldLabel}>{t('natal.birthTime')}</Text>
        <TouchableOpacity style={styles.fieldBtn} onPress={() => setTimeModalVisible(true)}>
          <Text style={birthTime ? styles.fieldValue : styles.fieldPlaceholder}>
            {birthTime ?? t('natal.timeUnknown')}
          </Text>
        </TouchableOpacity>

        {/* 出生地点（必填，文本输入） */}
        <Text style={styles.fieldLabel}>{t('natal.birthPlace')}</Text>
        <TextInput
          style={styles.input}
          value={birthPlace}
          onChangeText={setBirthPlace}
          placeholder={t('natal.birthPlacePlaceholder')}
          placeholderTextColor={Colors.textMuted}
        />

        <TouchableOpacity
          style={[styles.submitBtn, submitting && styles.submitBtnDisabled]}
          onPress={onSubmit}
          disabled={submitting}
        >
          {submitting ? (
            <ActivityIndicator color={Colors.bgPrimary} />
          ) : (
            <Text style={styles.submitText}>{t('natal.submit')}</Text>
          )}
        </TouchableOpacity>

        {editing && chart && (
          <TouchableOpacity style={styles.cancelEditBtn} onPress={() => setEditing(false)}>
            <Text style={styles.cancelEditText}>{t('common.cancel')}</Text>
          </TouchableOpacity>
        )}

        {/* 出生日期三列选择（年/月/日） */}
        <Modal
          visible={dateModalVisible}
          transparent
          animationType="slide"
          onRequestClose={() => setDateModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalCard}>
              <Text style={styles.modalTitle}>{t('natal.selectDate')}</Text>
              <View style={styles.wheelRow}>
                <FlatList
                  style={styles.wheelCol}
                  data={YEARS}
                  keyExtractor={(item) => String(item)}
                  initialScrollIndex={Math.max(0, draftDate.year - 1900)}
                  getItemLayout={(_, index) => ({ length: 44, offset: 44 * index, index })}
                  renderItem={({ item }) => (
                    <TouchableOpacity
                      style={[styles.wheelItem, item === draftDate.year && styles.wheelItemActive]}
                      onPress={() => setDraftDate((d) => ({ ...d, year: item }))}
                    >
                      <Text style={[styles.wheelText, item === draftDate.year && styles.wheelTextActive]}>
                        {item}
                      </Text>
                    </TouchableOpacity>
                  )}
                />
                <FlatList
                  style={styles.wheelCol}
                  data={MONTHS}
                  keyExtractor={(item) => String(item)}
                  initialScrollIndex={draftDate.month - 1}
                  getItemLayout={(_, index) => ({ length: 44, offset: 44 * index, index })}
                  renderItem={({ item }) => (
                    <TouchableOpacity
                      style={[styles.wheelItem, item === draftDate.month && styles.wheelItemActive]}
                      onPress={() => setDraftDate((d) => ({ ...d, month: item }))}
                    >
                      <Text style={[styles.wheelText, item === draftDate.month && styles.wheelTextActive]}>
                        {pad2(item)}
                      </Text>
                    </TouchableOpacity>
                  )}
                />
                <FlatList
                  style={styles.wheelCol}
                  data={DAYS}
                  keyExtractor={(item) => String(item)}
                  initialScrollIndex={draftDate.day - 1}
                  getItemLayout={(_, index) => ({ length: 44, offset: 44 * index, index })}
                  renderItem={({ item }) => (
                    <TouchableOpacity
                      style={[styles.wheelItem, item === draftDate.day && styles.wheelItemActive]}
                      onPress={() => setDraftDate((d) => ({ ...d, day: item }))}
                    >
                      <Text style={[styles.wheelText, item === draftDate.day && styles.wheelTextActive]}>
                        {pad2(item)}
                      </Text>
                    </TouchableOpacity>
                  )}
                />
              </View>
              <TouchableOpacity style={styles.confirmBtn} onPress={confirmDate}>
                <Text style={styles.confirmText}>{t('natal.confirm')}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>

        {/* 出生时间选择（含「不知道」） */}
        <Modal
          visible={timeModalVisible}
          transparent
          animationType="slide"
          onRequestClose={() => setTimeModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalCard}>
              <Text style={styles.modalTitle}>{t('natal.selectTime')}</Text>
              <FlatList
                data={TIME_OPTIONS}
                keyExtractor={(item) => item}
                renderItem={({ item }) => {
                  const value = item === TIME_UNKNOWN ? null : item;
                  const active = value === birthTime;
                  return (
                    <TouchableOpacity
                      style={[styles.optionRow, active && styles.optionRowActive]}
                      onPress={() => {
                        setBirthTime(value);
                        setTimeModalVisible(false);
                      }}
                    >
                      <Text style={styles.optionText}>
                        {item === TIME_UNKNOWN ? t('natal.timeUnknown') : item}
                      </Text>
                      {active && <Text style={styles.optionCheck}>✓</Text>}
                    </TouchableOpacity>
                  );
                }}
              />
              <TouchableOpacity style={styles.confirmBtn} onPress={() => setTimeModalVisible(false)}>
                <Text style={styles.confirmText}>{t('common.done')}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      </ScrollView>
    );
  }

  // ═══ 详情态 ═══
  const signCards: Array<{ key: 'sunSign' | 'moonSign' | 'risingSign'; descKey: 'sunDesc' | 'moonDesc' | 'risingDesc'; sign: ZodiacSign | null }> = [
    { key: 'sunSign', descKey: 'sunDesc', sign: chart.sun_sign },
    { key: 'moonSign', descKey: 'moonDesc', sign: chart.moon_sign },
    { key: 'risingSign', descKey: 'risingDesc', sign: chart.rising_sign },
  ];

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>{t('natal.title')}</Text>

      {/* 地点未识别提示条 */}
      {chart.tz_fallback && (
        <View style={styles.tzBanner}>
          <Text style={styles.tzBannerText}>{t('natal.tzFallback')}</Text>
        </View>
      )}

      {/* 三大星座卡片（微金渐变底 gradCard） */}
      <View style={styles.signRow}>
        {signCards.map(({ key, descKey, sign }) => (
          <LinearGradient
            key={key}
            colors={Colors.gradCard}
            start={{ x: 0, y: 0 }}
            end={{ x: 0, y: 1 }}
            style={styles.signCard}
          >
            <Text style={styles.signSymbol}>{sign ? ZODIAC_SYMBOLS[sign] : '—'}</Text>
            <Text style={styles.signLabel}>{t(`natal.${key}`)}</Text>
            <Text style={styles.signName}>
              {sign ? t(`zodiac.${sign}`) : t('natal.unknown')}
            </Text>
            <Text style={styles.signDesc}>{t(`natal.${descKey}`)}</Text>
          </LinearGradient>
        ))}
      </View>
      {chart.birth_time_unknown && (
        <Text style={styles.risingUnknownNote}>{t('natal.risingUnknownNote')}</Text>
      )}

      {/* 行星位置（纯文本列表） */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>{t('natal.planetsTitle')}</Text>
        {PLANETS.map((p) => (
          <View key={p} style={styles.planetRow}>
            <Text style={styles.planetName}>{t(`natal.planet.${p}`)}</Text>
            <Text style={styles.planetSign}>{t(`zodiac.${chart.planets[p]}`)}</Text>
          </View>
        ))}
      </View>

      {/* AI 解读 */}
      {!!chart.interpretation && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>{t('natal.interpretationTitle')}</Text>
          <Text style={styles.interpretation}>{chart.interpretation}</Text>
        </View>
      )}

      {/* 今日参考 */}
      {daily ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>{t('natal.dailyTitle')}</Text>
          <View style={styles.scoreRow}>
            <Text style={styles.scoreLabel}>{t('natal.overall')}</Text>
            <ScoreStars score={daily.overall_score} />
          </View>
          <View style={styles.scoreRow}>
            <Text style={styles.scoreLabel}>{t('natal.career')}</Text>
            <ScoreStars score={daily.career_score} />
          </View>
          <View style={styles.scoreRow}>
            <Text style={styles.scoreLabel}>{t('natal.wealth')}</Text>
            <ScoreStars score={daily.wealth_score} />
          </View>
          <View style={styles.luckyRow}>
            <Text style={styles.luckyItem}>
              {t('natal.luckyColor')} · {daily.lucky_color}
            </Text>
            <Text style={styles.luckyItem}>
              {t('natal.luckyNumber')} · {daily.lucky_number}
            </Text>
            <Text style={styles.luckyItem}>
              {t('natal.luckyTime')} · {daily.lucky_time}
            </Text>
          </View>
          {!!daily.comment && <Text style={styles.dailyComment}>{daily.comment}</Text>}
        </View>
      ) : (
        <Text style={styles.dailyEmpty}>{t('natal.dailyEmpty')}</Text>
      )}

      {/* 重新填写入口 */}
      <TouchableOpacity style={styles.refillBtn} onPress={() => setEditing(true)}>
        <Text style={styles.refillText}>{t('natal.refill')}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.lg, paddingBottom: 40 },
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.bgPrimary },
  title: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  formSubtitle: { fontSize: FontSize.md, color: Colors.textTertiary, marginBottom: Spacing.lg, lineHeight: 1.5 },
  fieldLabel: { fontSize: FontSize.md, color: Colors.textSecondary, marginBottom: Spacing.xs, marginTop: Spacing.sm },
  fieldBtn: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md,
  },
  fieldValue: { fontSize: FontSize.title, color: Colors.textPrimary },
  fieldPlaceholder: { fontSize: FontSize.title, color: Colors.textMuted },
  input: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.lg, padding: Spacing.md,
    fontSize: FontSize.title, color: Colors.textPrimary,
  },
  submitBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.lg,
    padding: Spacing.md, alignItems: 'center', marginTop: Spacing.xl,
  },
  submitBtnDisabled: { opacity: 0.6 },
  submitText: { fontSize: FontSize.title, fontWeight: '600', color: Colors.bgPrimary },
  cancelEditBtn: { alignItems: 'center', marginTop: Spacing.md, padding: Spacing.sm },
  cancelEditText: { fontSize: FontSize.md, color: Colors.textSecondary },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' },
  modalCard: {
    backgroundColor: Colors.bgSecondary, borderTopLeftRadius: BorderRadius.xl, borderTopRightRadius: BorderRadius.xl,
    padding: Spacing.lg, maxHeight: '70%',
  },
  modalTitle: { fontSize: FontSize.title, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  wheelRow: { flexDirection: 'row', gap: Spacing.sm, height: 220 },
  wheelCol: { flex: 1 },
  wheelItem: { height: 44, justifyContent: 'center', alignItems: 'center', borderRadius: BorderRadius.md },
  wheelItemActive: { backgroundColor: 'rgba(255,215,0,0.1)' },
  wheelText: { fontSize: FontSize.title, color: Colors.textSecondary },
  wheelTextActive: { color: Colors.gold400, fontWeight: '600' },
  optionRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    padding: Spacing.md, borderRadius: BorderRadius.md,
  },
  optionRowActive: { backgroundColor: 'rgba(255,215,0,0.1)' },
  optionText: { fontSize: FontSize.title, color: Colors.textPrimary },
  optionCheck: { fontSize: FontSize.title, color: Colors.gold400 },
  confirmBtn: {
    backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: BorderRadius.lg,
    padding: Spacing.md, alignItems: 'center', marginTop: Spacing.md,
  },
  confirmText: { fontSize: FontSize.title, color: Colors.textPrimary },
  tzBanner: {
    backgroundColor: 'rgba(245,158,11,0.08)', borderWidth: 1, borderColor: 'rgba(245,158,11,0.3)',
    borderRadius: BorderRadius.lg, padding: Spacing.md, marginBottom: Spacing.md,
  },
  tzBannerText: { fontSize: FontSize.md, color: Colors.warning, lineHeight: 1.5 },
  signRow: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.sm },
  signCard: {
    flex: 1, alignItems: 'center',
    borderWidth: 1, borderColor: 'rgba(255,215,0,0.15)',
    borderRadius: BorderRadius.xl, padding: Spacing.md,
  },
  signSymbol: { fontSize: 32, color: Colors.gold400, marginBottom: 4 },
  signLabel: { fontSize: FontSize.sm, color: Colors.textTertiary, marginBottom: 2 },
  signName: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: 4 },
  signDesc: { fontSize: FontSize.xs, color: Colors.textTertiary, textAlign: 'center', lineHeight: 1.4 },
  risingUnknownNote: { fontSize: FontSize.sm, color: Colors.textTertiary, marginBottom: Spacing.sm },
  card: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: Colors.borderLight,
    borderRadius: BorderRadius.xl, padding: Spacing.lg, marginTop: Spacing.md,
  },
  cardTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.textPrimary, marginBottom: Spacing.md },
  planetRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: Spacing.sm, borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.05)',
  },
  planetName: { fontSize: FontSize.title, color: Colors.textSecondary },
  planetSign: { fontSize: FontSize.title, color: Colors.textPrimary },
  interpretation: { fontSize: FontSize.title, color: Colors.textSecondary, lineHeight: 1.7 },
  scoreRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: Spacing.xs },
  scoreLabel: { fontSize: FontSize.title, color: Colors.textSecondary },
  stars: { fontSize: FontSize.title, color: Colors.gold400, letterSpacing: 2 },
  luckyRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.md, marginTop: Spacing.sm },
  luckyItem: { fontSize: FontSize.md, color: Colors.textTertiary },
  dailyComment: { fontSize: FontSize.md, color: Colors.textSecondary, lineHeight: 1.6, marginTop: Spacing.sm },
  dailyEmpty: { fontSize: FontSize.md, color: Colors.textTertiary, textAlign: 'center', marginTop: Spacing.lg },
  refillBtn: { alignItems: 'center', marginTop: Spacing.xl, padding: Spacing.sm },
  refillText: { fontSize: FontSize.md, color: Colors.gold400 },
});
