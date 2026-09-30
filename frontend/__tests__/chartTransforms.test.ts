/**
 * E2 图表纯变换：折线分段（无签到断线）+ GitHub 式热力矩阵（周一起始）。
 * 断言口径全部落在数据侧——SVG 渲染层在 node 环境不可测。
 */
import { toHeatMatrix, toLineSegments, type ChartDay } from '../src/components/charts/transforms';

const day = (date: string, value: number): ChartDay => ({ date, value });

describe('toLineSegments：折线分段', () => {
  it('连续有值日生成单段，两端留 pad', () => {
    const { segments, singles } = toLineSegments(
      [day('2026-09-21', 1), day('2026-09-22', 3), day('2026-09-23', 5)],
      100,
      40,
      2,
    );
    expect(segments).toHaveLength(1);
    expect(singles).toHaveLength(0);
    expect(segments[0]).toHaveLength(3);
    // 线性映射：v=1 → 底部（y=38），v=5 → 顶部（y=2）
    const [first, , last] = segments[0];
    expect(first.x).toBeCloseTo(2);
    expect(first.y).toBeCloseTo(38);
    expect(last.x).toBeCloseTo(98);
    expect(last.y).toBeCloseTo(2);
  });

  it('value=0（未签到）断线：不插值不编数据，裂成两段', () => {
    const { segments, singles } = toLineSegments(
      [day('2026-09-21', 2), day('2026-09-22', 4), day('2026-09-23', 0), day('2026-09-24', 3)],
      100,
      40,
    );
    expect(segments).toHaveLength(1); // 段1 两点可连线
    expect(segments[0]).toHaveLength(2);
    expect(singles).toHaveLength(1); // 断口后孤点单列，画圆点
  });

  it('全空序列 / 全未签到返回空段空点', () => {
    expect(toLineSegments([], 100, 40)).toEqual({ segments: [], singles: [] });
    expect(toLineSegments([day('2026-09-21', 0)], 100, 40)).toEqual({ segments: [], singles: [] });
  });

  it('断口前孤点进 singles，断口后连续两点成段', () => {
    const { segments, singles } = toLineSegments(
      [day('2026-09-21', 1), day('2026-09-22', 0), day('2026-09-23', 5), day('2026-09-24', 3)],
      102,
      42,
      1,
    );
    expect(segments).toHaveLength(1);
    expect(segments[0]).toHaveLength(2); // 09-23 → 09-24
    expect(singles).toHaveLength(1); // 09-21 孤点
    // x 全部按有值序列（09-21,09-23,09-24）索引均分：1, 51, 101 → 断口不挤压分布
    expect(singles[0].x).toBeCloseTo(1);
    expect(segments[0][0].x).toBeCloseTo(51);
    expect(segments[0][1].x).toBeCloseTo(101);
  });
});

describe('toHeatMatrix：GitHub 式热力矩阵', () => {
  it('首列按第一天星期对齐（周一为首行），后续自然日推进', () => {
    // 2026-09-21 是周一：首行即首列首格，无前导 null
    const m = toHeatMatrix([
      day('2026-09-21', 2),
      day('2026-09-22', 3),
      day('2026-09-23', 4),
    ]);
    expect(m[0][0]).toEqual(day('2026-09-21', 2));
    expect(m[1][0]).toEqual(day('2026-09-22', 3));
    expect(m[2][0]).toEqual(day('2026-09-23', 4));

    // 2026-09-23 是周三：前导 null 占周一、周二两行
    const m2 = toHeatMatrix([day('2026-09-23', 4)]);
    expect(m2[0][0]).toBeNull();
    expect(m2[1][0]).toBeNull();
    expect(m2[2][0]).toEqual(day('2026-09-23', 4));
  });

  it('输入缺失的日期落 null 格（不越行）', () => {
    // 09-21 周一、09-23 周三：09-22 缺失 → 周二行该列 null
    const m = toHeatMatrix([day('2026-09-21', 2), day('2026-09-23', 4)]);
    expect(m[0][0]).toEqual(day('2026-09-21', 2));
    expect(m[1][0]).toBeNull();
    expect(m[2][0]).toEqual(day('2026-09-23', 4));
  });

  it('跨周换列：周日是第 7 行，次周一开新列', () => {
    // 2026-09-27 周日（行 6）、2026-09-28 周一（行 0，新列）
    const m = toHeatMatrix([day('2026-09-27', 5), day('2026-09-28', 1)]);
    expect(m[6][0]).toEqual(day('2026-09-27', 5));
    expect(m[0][1]).toEqual(day('2026-09-28', 1));
  });

  it('空输入 / 非法日期返回 7 行空矩阵（渲染空图不崩溃）', () => {
    const m = toHeatMatrix([]);
    expect(m).toHaveLength(7);
    expect(m.every((row) => row.length === 0)).toBe(true);

    const bad = toHeatMatrix([day('not-a-date', 3)]);
    expect(bad).toHaveLength(7);
    expect(bad.every((row) => row.length === 0)).toBe(true);
  });
});
