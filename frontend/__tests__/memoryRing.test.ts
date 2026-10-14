/**
 * 回忆卡（§10.1 ① 第四模板）的取数与换算闸：情绪色环 + 日期大字 + 分享正文。
 *
 * 为什么单独成文件：`MoodRing.tsx` 是 SVG 组件，jest 的 node 环境渲染不了
 * （沿 charts/transforms.ts + HeatGrid 的分工口径，见 dailyCards.test.ts 同口先例），
 * 而画错的失效方式恰好是「不报错」：段号错位、色相方向反了、无签到那天被画成
 * 有签到、日期按设备时区倒推差一天——全部只在屏幕上看得出来。所以把视觉规格
 * （2026-10-14 立项定稿：静态五段环 + 当前段高亮）里能被数字表达的部分逐条钉住。
 */
import {
  RING_SEGMENTS, SEGMENT_DEGREES, START_ANGLE, HUE_MIN, HUE_STEP,
  NEUTRAL_COLOR, DIM_OPACITY, MEMORY_CARD_EXCERPT_MAX,
  hueForValue, colorForValue, polarToCartesian, annularSectorPath,
  ringSegments, activeSegmentIndex, memoryCardDate, memoryCardExcerpt,
  memoryCardShareMessage,
} from '../src/config/memoryRing';

describe('五段几何：整环等分、12 点起顺时针、段序号＝情绪值', () => {
  it('五段 × 72° ＝ 满环，起始角 −90° 让第一段从 12 点方向开始', () => {
    expect(RING_SEGMENTS).toBe(5);
    expect(SEGMENT_DEGREES).toBeCloseTo(72);
    expect(START_ANGLE).toBe(-90);
    expect(RING_SEGMENTS * SEGMENT_DEGREES).toBeCloseTo(360);
  });

  it('每段的角度是首尾相接的 72° 步进，最后一段闭合回起点', () => {
    const segs = ringSegments(1);
    expect(segs).toHaveLength(RING_SEGMENTS);
    expect(segs.map((s) => s.fromAngle)).toEqual([-90, -18, 54, 126, 198]);
    segs.forEach((s, i) => {
      expect(s.toAngle - s.fromAngle).toBeCloseTo(SEGMENT_DEGREES);
      if (i > 0) expect(s.fromAngle).toBeCloseTo(segs[i - 1].toAngle);
    });
    expect(segs[segs.length - 1].toAngle - segs[0].fromAngle).toBeCloseTo(360);
  });

  it('段 value 从 1 到 5 连续，与后端 MOOD_MAP 的值域同口径', () => {
    expect(ringSegments(1).map((s) => s.value)).toEqual([1, 2, 3, 4, 5]);
  });

  it('极坐标：−90° 落在正上方、0° 落在正右（SVG y 轴向下的那套约定）', () => {
    const top = polarToCartesian(50, 50, 40, START_ANGLE);
    expect(top.x).toBeCloseTo(50);
    expect(top.y).toBeCloseTo(10);
    const right = polarToCartesian(50, 50, 40, 0);
    expect(right.x).toBeCloseTo(90);
    expect(right.y).toBeCloseTo(50);
  });
});

describe('色相映射：低落红→开心绿，与热力图/后端配色同向', () => {
  it('值 1→0°、值 5→120°，中间等步 30°', () => {
    expect(HUE_MIN).toBe(0);
    expect(HUE_STEP).toBe(30);
    [1, 2, 3, 4, 5].forEach((v, i) => {
      expect(hueForValue(v)).toBe(HUE_MIN + i * HUE_STEP);
    });
  });

  it('色相随情绪值单调不减（画成反向＝情绪与颜色不符，且不会报错）', () => {
    const hues = [1, 2, 3, 4, 5].map((v) => hueForValue(v) as number);
    hues.forEach((h, i) => {
      if (i > 0) expect(h).toBeGreaterThan(hues[i - 1]);
    });
  });

  it('值域外（0 / 6 / 小数 / null / undefined）不猜颜色：hue 为 null、色落中性', () => {
    [0, 6, 2.5, -1, null, undefined].forEach((v) => {
      expect(hueForValue(v as number)).toBeNull();
      expect(colorForValue(v as number)).toBe(NEUTRAL_COLOR);
    });
  });

  it('有效值出 hsl 且只让色相承载情绪值（饱和度/亮度固定）', () => {
    expect(colorForValue(1)).toBe('hsl(0, 72%, 55%)');
    expect(colorForValue(5)).toBe('hsl(120, 72%, 55%)');
    expect(colorForValue(3)).toBe(`hsl(${HUE_MIN + 2 * HUE_STEP}, 72%, 55%)`);
  });
});

describe('高亮口径：只亮一段', () => {
  it.each([1, 2, 3, 4, 5])('值 %i → 第 %i 段 active、其余四段降透明', (v) => {
    const segs = ringSegments(v);
    const actives = segs.filter((s) => s.active);
    expect(actives).toHaveLength(1);
    expect(actives[0].value).toBe(v);
    expect(activeSegmentIndex(v)).toBe(v - 1);
    segs.forEach((s) => {
      expect(s.opacity).toBe(s.value === v ? 1 : DIM_OPACITY);
    });
  });

  it('非高亮段的透明度确实低于高亮段（全亮＝看不出那天心情如何）', () => {
    expect(DIM_OPACITY).toBeGreaterThan(0);
    expect(DIM_OPACITY).toBeLessThan(1);
  });

  it('表外值不进高亮：activeSegmentIndex 返回 -1，调用方据此不放中心 emoji', () => {
    expect(activeSegmentIndex(0)).toBe(-1);
    expect(activeSegmentIndex(9)).toBe(-1);
    expect(activeSegmentIndex(null)).toBe(-1);
  });
});

describe('无数据降级：那天没签到就画空环，不冒充当天的情绪', () => {
  it.each([[null], [undefined], [0], [7]])('value=%p：五段全中性、全低透明、无一高亮', (v) => {
    const segs = ringSegments(v);
    expect(segs).toHaveLength(RING_SEGMENTS);
    segs.forEach((s) => {
      expect(s.color).toBe(NEUTRAL_COLOR);
      expect(s.opacity).toBe(DIM_OPACITY);
      expect(s.active).toBe(false);
    });
  });
});

describe('扇形路径：外弧顺时针、内弧逆时针闭合', () => {
  it('72° 段的 large-arc 取 0，外弧 sweep=1、内弧 sweep=0', () => {
    const d = annularSectorPath(50, 50, 46, 36, -90, -18);
    expect(d.startsWith('M ')).toBe(true);
    expect(d.endsWith('Z')).toBe(true);
    expect(d).toMatch(/A 46 46 0 0 1 /); // 外弧顺时针
    expect(d).toMatch(/A 36 36 0 0 0 /); // 内弧逆时针回起点
    expect((d.match(/A /g) ?? []).length).toBe(2);
    expect((d.match(/ L /g) ?? []).length).toBe(1);
  });

  it('夹角 >180° 时 large-arc 翻成 1（公式不写死，改分段数也不会画成补角）', () => {
    const small = annularSectorPath(50, 50, 46, 36, 0, 100);
    const big = annularSectorPath(50, 50, 46, 36, 0, 200);
    expect(small).toMatch(/A 46 46 0 0 1 /);
    expect(big).toMatch(/A 46 46 0 1 1 /);
    expect(big).toMatch(/A 36 36 0 1 0 /);
  });

  it('退化角（sweep ≤ 0）返回空串：不画出一条看不见却占位的路径', () => {
    expect(annularSectorPath(50, 50, 46, 36, 90, 90)).toBe('');
    expect(annularSectorPath(50, 50, 46, 36, 90, 10)).toBe('');
  });

  it('高亮段与相邻段共享的边界点坐标一致（缝隙/重叠＝五段拼不满一圈）', () => {
    const cx = 50, cy = 50, ro = 46, ri = 36;
    const a = annularSectorPath(cx, cy, ro, ri, -90, -18);
    const b = annularSectorPath(cx, cy, ro, ri, -18, 54);
    const outerEnd = /A 46 46 0 0 1 (-?[\d.]+) (-?[\d.]+)/.exec(a)!.slice(1, 3);
    const outerStart = /M (-?[\d.]+) (-?[\d.]+)/.exec(b)!.slice(1, 3);
    expect(outerEnd).toEqual(outerStart);
  });
});

describe('日期大字：年份只从服务端「今天」倒推，不碰 created_at 与设备时区', () => {
  it('今天 2026-11-01 ＋ years_ago=1/2 → 2025.11.01 / 2024.11.01（月日沿用今天）', () => {
    expect(memoryCardDate('2026-11-01', 1)).toBe('2025.11.01');
    expect(memoryCardDate('2026-11-01', 2)).toBe('2024.11.01');
    expect(memoryCardDate('2026-11-01', 3)).toBe('2023.11.01');
  });

  it('带时间的 ISO 串只取日期部分（服务端 isoformat 直出）', () => {
    expect(memoryCardDate('2026-11-01T04:32:00+00:00', 1)).toBe('2025.11.01');
  });

  it('月日补零保持两位，闰日也照今天月日倒推（候选本就按同一本地日界取的）', () => {
    expect(memoryCardDate('2026-01-05', 1)).toBe('2025.01.05');
    expect(memoryCardDate('2024-02-29', 4)).toBe('2020.02.29');
  });

  it('非法输入一律 null 而不是编一个日期：缺今天、年份差 <1、非整数都不画', () => {
    expect(memoryCardDate('', 1)).toBeNull();
    expect(memoryCardDate('11/01/2026', 1)).toBeNull();
    expect(memoryCardDate('2026-11-01', 0)).toBeNull();
    expect(memoryCardDate('2026-11-01', -1)).toBeNull();
    expect(memoryCardDate('2026-11-01', 1.5)).toBeNull();
  });
});

describe('原文摘录与分享正文', () => {
  it('不超长原样返回（含首尾空白收口）', () => {
    expect(memoryCardExcerpt('一年前的今天我在准备面试')).toBe('一年前的今天我在准备面试');
    expect(memoryCardExcerpt('  带空白  ')).toBe('带空白');
    expect(memoryCardExcerpt('')).toBe('');
    expect(memoryCardExcerpt(null)).toBe('');
  });

  it('超长截到上限并加省略号，且省略号前不留尾随空白', () => {
    const long = `${'字'.repeat(MEMORY_CARD_EXCERPT_MAX)}   还有更多`;
    const out = memoryCardExcerpt(long);
    expect(out.endsWith('…')).toBe(true);
    expect(out.length).toBeLessThanOrEqual(MEMORY_CARD_EXCERPT_MAX + 1);
    expect(/ \.\.$|\s…$/.test(out)).toBe(false);
  });

  it('分享正文＝原话 + 深链两段（与首页考古卡同一条口径，清单才敢只列两项）', () => {
    expect(memoryCardShareMessage('那年今日的原话', 'https://x/r/ABCDEFGH')).toBe(
      '那年今日的原话\n\nhttps://x/r/ABCDEFGH'
    );
  });

  it('空字段不留空行：原话为空只剩链接，两者皆空得到空串', () => {
    expect(memoryCardShareMessage('', 'https://x/r/ABCDEFGH')).toBe('https://x/r/ABCDEFGH');
    expect(memoryCardShareMessage('  ', '   ')).toBe('');
  });
});
