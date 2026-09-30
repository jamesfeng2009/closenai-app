/**
 * 图表纯数据变换（E2：react-native-svg 图表的数据侧）。
 *
 * SVG 组件在 jest node 环境不可渲染（testMatch 只收 .test.ts），所以坐标/
 * 矩阵的全部计算拆在这里做纯函数，组件层只做 <Svg> 渲染。测试见
 * __tests__/chartTransforms.test.ts。
 */

export interface ChartDay {
  date: string;
  /** 情绪值：0 = 无签到，1（低落）–5（开心），与后端 MOOD_COLORS 同语义 */
  value: number;
}

export interface Point {
  x: number;
  y: number;
}

/** 热力图配色（自 InsightsScreen.HEAT_COLORS 上收共享；0 = 无数据） */
export const HEAT_COLORS: Record<number, string> = {
  0: 'rgba(255,255,255,0.06)',
  1: '#EF9A9A',
  2: '#FFCC80',
  3: '#FFF59D',
  4: '#A5D6A7',
  5: '#66BB6A',
};

/** 情绪值域（折线 Y 轴映射）：1 低落 – 5 开心 */
const VALUE_MIN = 1;
const VALUE_MAX = 5;

/**
 * 情绪折线分段：把按日升序的序列映射为 Svg 坐标点。
 *
 * value=0（当日未签到）不参与连线也不插值——断线比编数据诚实。
 * 返回 segments（每段 ≥2 点，画 polyline）与 singles（断口后落单的孤点，
 * 画圆点标注），两者 x 都按「有值序列」的索引均分。
 */
export function toLineSegments(
  days: ChartDay[],
  width: number,
  height: number,
  pad = 2,
): { segments: Point[][]; singles: Point[] } {
  const plotted = days.filter((d) => d.value >= VALUE_MIN);
  if (plotted.length === 0) return { segments: [], singles: [] };
  const spanX = Math.max(width - pad * 2, 0);
  const spanY = Math.max(height - pad * 2, 0);
  const xOf = (i: number) =>
    plotted.length === 1 ? width / 2 : pad + (spanX * i) / (plotted.length - 1);
  const yOf = (v: number) =>
    height - pad - ((v - VALUE_MIN) / (VALUE_MAX - VALUE_MIN)) * spanY;

  const segments: Point[][] = [];
  const singles: Point[] = [];
  let current: Point[] = [];
  days.forEach((day) => {
    if (day.value < VALUE_MIN) {
      if (current.length > 1) segments.push(current);
      else if (current.length === 1) singles.push(current[0]);
      current = [];
      return;
    }
    current.push({ x: xOf(plotted.indexOf(day)), y: yOf(day.value) });
  });
  if (current.length > 1) segments.push(current);
  else if (current.length === 1) singles.push(current[0]);
  return { segments, singles };
}

/**
 * GitHub 式热力矩阵：列 = 自然周（周一起始），行 = 星期几（0=周一 … 6=周日）。
 *
 * 输入按日期升序；输入缺失的日期（未拉到/未签到且无行）落 null 格，渲染
 * 用 HEAT_COLORS[0]。首列按第一天的星期补前导 null 对齐。
 */
export function toHeatMatrix(days: ChartDay[]): (ChartDay | null)[][] {
  const byDate = new Map(days.map((d) => [d.date, d]));
  const matrix: (ChartDay | null)[][] = Array.from({ length: 7 }, () => []);
  if (days.length === 0) return matrix;

  const first = new Date(`${days[0].date}T00:00:00Z`);
  if (Number.isNaN(first.getTime())) return matrix;
  // JS getDay(): 0=周日；换算为周一起始行序
  const firstRow = (first.getUTCDay() + 6) % 7;
  for (let row = 0; row < firstRow; row++) matrix[row].push(null);

  const cursor = new Date(first);
  let guard = 0;
  while (cursor <= new Date(`${days[days.length - 1].date}T00:00:00Z`)) {
    const row = (cursor.getUTCDay() + 6) % 7;
    const key = cursor.toISOString().slice(0, 10);
    matrix[row].push(byDate.get(key) ?? null);
    cursor.setUTCDate(cursor.getUTCDate() + 1);
    if (++guard > 400 * 7) break; // 防御性上限（约 400 年），正常 365 天远达不到
  }
  return matrix;
}
