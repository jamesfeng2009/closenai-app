/**
 * 回忆卡情绪色环（§10.1 ① 第四模板；静态五段环 + 当前段高亮）。
 *
 * 换算全部在 src/config/memoryRing.ts（jest 的 node 环境渲染不了 SVG 组件，
 * 与 charts/transforms.ts + HeatGrid 的分工同一口径）；这里只把分段描述画出来。
 * 中心 emoji 用 RN Text 覆盖而不是 Svg Text：SDK 51 上 SVG 文本的字重/换行在
 * 部分机型不一致，而 emoji 不参与分享文本，覆盖层不影响导出观感。
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { annularSectorPath, ringSegments } from '../../config/memoryRing';

interface Props {
  /** 情绪值 1–5；null（当日无签到）→ 五段中性、中心不放 emoji。 */
  value: number | null | undefined;
  emoji?: string;
  size?: number;
  /** 无障碍朗读文案由调用方给（走 i18n，组件不自造文案）。 */
  accessibilityLabel?: string;
}

export default function MoodRing({ value, emoji, size = 96, accessibilityLabel }: Props) {
  const cx = size / 2;
  const cy = size / 2;
  const rOuterBase = size / 2 - 4;
  const rInner = size / 2 - 14;
  const segments = ringSegments(value);
  // ④：中心 emoji 只在真有高亮段时出现，表外值/无签到不放
  const showEmoji = segments.some((s) => s.active) && !!emoji;

  return (
    <View style={{ width: size, height: size }} accessibilityRole="image" accessibilityLabel={accessibilityLabel}>
      <Svg width={size} height={size}>
        {segments.map((s) => {
          // ③ 高亮段外半径 +2：只靠透明度区分在深色底上不够显眼
          const rOuter = s.active ? rOuterBase : rOuterBase - 2;
          return (
            <Path
              key={s.value}
              d={annularSectorPath(cx, cy, rOuter, rInner, s.fromAngle, s.toAngle)}
              fill={s.color}
              fillOpacity={s.opacity}
              stroke="rgba(0,0,0,0.35)"
              strokeWidth={0.5}
            />
          );
        })}
      </Svg>
      {showEmoji && (
        <View style={styles.center} pointerEvents="none">
          <Text style={styles.emoji}>{emoji}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  center: { position: 'absolute', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%' },
  emoji: { fontSize: 26, lineHeight: 34 },
});
