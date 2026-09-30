/**
 * GitHub 式成长热力图（E2 · PRD Growth Timeline Logo Feature）。
 *
 * 列 = 自然周（周一起始），行 = 星期几；输入缺失日落 null 格。
 * 变换在 transforms.ts（可 jest），组件只做 SVG Rect 渲染。
 */
import React from 'react';
import Svg, { Rect } from 'react-native-svg';
import { toHeatMatrix, HEAT_COLORS, type ChartDay } from './transforms';

interface Props {
  days: ChartDay[];
  cell?: number;
  gap?: number;
}

export default function HeatGrid({ days, cell = 11, gap = 3 }: Props) {
  const matrix = toHeatMatrix(days);
  const cols = Math.max(...matrix.map((row) => row.length), 0);
  const width = cols * (cell + gap);
  const height = 7 * (cell + gap);
  return (
    <Svg width={width} height={height}>
      {matrix.map((row, r) =>
        row.map((d, c) => (
          <Rect
            key={`${r}-${c}`}
            x={c * (cell + gap)}
            y={r * (cell + gap)}
            width={cell}
            height={cell}
            rx={3}
            fill={HEAT_COLORS[d?.value ?? 0] ?? HEAT_COLORS[0]}
          />
        )),
      )}
    </Svg>
  );
}
