/**
 * 周情绪折线图（E2 · PRD §P0 情绪趋势可视化：X=日期，Y=情绪值）。
 *
 * 数据/坐标变换在 transforms.ts 纯函数（可 jest），组件只做 SVG 渲染：
 * 连续有值段画 Polyline，未签到断口用 Circle 标孤点——断线比编数据诚实。
 */
import React from 'react';
import Svg, { Circle, Polyline } from 'react-native-svg';
import { Colors } from '../../theme/colors';
import { toLineSegments, type ChartDay } from './transforms';

interface Props {
  days: ChartDay[];
  width: number;
  height: number;
  color?: string;
}

export default function MoodLineChart({ days, width, height, color = Colors.gold400 }: Props) {
  const { segments, singles } = toLineSegments(days, width, height);
  return (
    <Svg width={width} height={height}>
      {segments.map((seg, i) => (
        <Polyline
          key={`seg-${i}`}
          points={seg.map((p) => `${p.x},${p.y}`).join(' ')}
          fill="none"
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
      {singles.map((p, i) => (
        <Circle key={`dot-${i}`} cx={p.x} cy={p.y} r={3} fill={color} />
      ))}
    </Svg>
  );
}
