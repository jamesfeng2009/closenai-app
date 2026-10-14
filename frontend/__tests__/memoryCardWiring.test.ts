/**
 * 回忆卡（§10.1 ① 第四模板）的接线闸：屏与屏的接线点、跨仓字段对齐、清单同源。
 *
 * RN/SVG 组件在 jest 的 node 环境渲染不了（见 dailyCardWiring.test.ts 同口先例），
 * 所以这里钉的是「改了就不安全」的结构事实：入口按钮→导航参数→取数路径→渲染分支→
 * 分享正文。这一批的失效方式全是静默的：memory 分支误走 getPersonalityCard 会撞上
 * 40302 Pro 门（回忆卡是免费引流出口）、清单与实际发出去的内容脱钩会虚报分享面、
 * 前端自算周年日期会差一天。换算本身在 memoryRing.test.ts 里断言。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readBackend = (rel: string) => read(path.join('..', 'backend', rel));

const SHARE = 'src/screens/ShareCardScreen.tsx';
const HOME = 'src/screens/HomeScreen.tsx';
const RING = 'src/components/charts/MoodRing.tsx';
const CFG = 'src/config/memoryRing.ts';
const TYPES = 'src/types/index.ts';

/** 从 needle 起取一段（回忆卡分支整体到它自己的 return，避免匹配到名片分支的代码）。 */
function from(rel: string, needle: string, span: number): string {
  const src = read(rel);
  const i = src.indexOf(needle);
  expect(i).toBeGreaterThanOrEqual(0);
  return src.slice(i, i + span);
}

function count(rel: string, re: RegExp): number {
  return (read(rel).match(re) || []).length;
}

/**
 * 两个锚点之间的一段。卡面/组件的「尾部要素」用这种切法：固定字符窗在有人往中间
 * 加注释或加行时会把尾部挤出窗口（317b390 就是这么造成假红的），锚点不会。
 */
function between(rel: string, start: string, end: string): string {
  const src = read(rel);
  const i = src.indexOf(start);
  expect(i).toBeGreaterThanOrEqual(0);
  const j = src.indexOf(end, i);
  expect(j).toBeGreaterThan(i);
  return src.slice(i, j);
}

describe('第四模板在册：类型、变体表与导航参数三处齐', () => {
  it("ShareCardVariant 含 'memory'（类型缺一项，屏里的分支就是类型错误现场）", () => {
    expect(read(TYPES)).toContain(
      "export type ShareCardVariant = 'classic' | 'minimal' | 'combo' | 'memory';"
    );
  });

  it('变体表四个都在（只在 route 参数里支持 memory，用户自己点不进去）', () => {
    expect(read(SHARE)).toContain(
      "const VARIANTS: ShareCardVariant[] = ['classic', 'minimal', 'combo', 'memory'];"
    );
  });

  it('导航参数表带 variant 与 memory_id，且仍是可选（旧调用点不传参也得能进名片）', () => {
    expect(read('App.tsx')).toContain(
      "ShareCard: { variant?: ShareCardVariant; memory_id?: string } | undefined;"
    );
  });

  it('首页考古卡→回忆卡入口：带 memory_id 跳转并埋点，按钮在展开态的每条候选里', () => {
    const home = read(HOME);
    expect(home).toContain("navigation.navigate('ShareCard', { variant: 'memory', memory_id: c.memory_id })");
    expect(home).toContain("track('memory_card_opened', { feature: 'memory', action: 'open' })");
    expect(home).toContain("{t('home.archiveRecallCard')}");
  });

  it('route 缺参只回 classic，不猜一条记忆', () => {
    expect(from(SHARE, "useState<ShareCardVariant>(", 200)).toContain(
      "route?.params?.variant === 'memory' ? 'memory' : 'classic'"
    );
  });
});

describe('取数路径：memory 走 on-this-day，不碰性格名片与 Pro 门', () => {
  const branch = from(SHARE, "if (variant === 'memory') {", 620);

  it('memory 分支调 getOnThisDay()，且在自己的 return 之前不调 getPersonalityCard', () => {
    expect(branch).toContain('await getOnThisDay()');
    expect(branch.split('return;')[0]).not.toContain('getPersonalityCard');
    expect(branch.split('return;')[0]).not.toContain('CODE_PRO_REQUIRED');
    // 名片请求仍在（三张性格卡没被拆掉），只是不在 memory 分支里
    expect(read(SHARE)).toContain('await getPersonalityCard(variant)');
  });

  it('按 memory_id 精确挑，挑不到退首条有原话的候选，全都没有才是空态', () => {
    expect(branch).toContain('data.candidates.find((c) => c.memory_id === wanted)');
    expect(branch).toContain('data.candidates.find((c) => !!c.content?.trim())');
    expect(branch).toContain('?? null;');
  });

  it('换 variant 先清空两条数据路，不拿上一张卡的内容渲染新卡', () => {
    const effect = from(SHARE, 'setLoading(true);', 300);
    expect(effect).toContain('setCard(null);');
    expect(effect).toContain('setMemory(null);');
  });

  it('空态与失败态分开：无候选走 memoryEmpty 文案，不是转圈也不是报错', () => {
    expect(read(SHARE)).toContain("{t('share.memoryEmpty')}");
    expect(read(SHARE)).toContain("{t('share.empty')}"); // 名片空态没被挤掉
  });

  it('屏内只有一处 getOnThisDay 调用（多处就是重复烧一次考古查询）', () => {
    expect(count(SHARE, /getOnThisDay\(\)/g)).toBe(1);
  });
});

describe('日期大字与原文：年份只从服务端「今天」倒推', () => {
  it('用 memoryCardDate(服务端今天的 date, years_ago)，不用 created_at、不用设备时钟', () => {
    const src = read(SHARE);
    expect(src).toContain("{memoryCardDate(memoryToday, memory.years_ago) ?? '—'}");
    expect(src).toContain('setMemoryToday(data.date)');
    expect(src).not.toMatch(/new Date\(/);
    expect(src).not.toContain('memory.created_at');
  });

  it('标题走 home.archiveTitle 的 CLDR 复数键（count 交给 i18next 选档）', () => {
    expect(read(SHARE)).toContain(
      "t('home.archiveTitle', { count: memory.years_ago, years: memory.years_ago })"
    );
  });

  it('正文只显摘录而不是整段原话（卡片不是列表页），且带引号包边', () => {
    expect(read(SHARE)).toContain('{memoryCardExcerpt(memory.content)}');
  });

  it('卡面四要素齐备：日期大字 + 当年原文 + 情绪色环 + App 水印', () => {
    const cardBlock = between(SHARE, '{/* 回忆卡：', '<ShareManifest');
    expect(cardBlock).toContain('<LinearGradient');
    expect(cardBlock).toContain('styles.memoryDate');
    expect(cardBlock).toContain('styles.memoryQuote');
    expect(cardBlock).toContain('<MoodRing');
    expect(cardBlock).toContain('<Text style={styles.cardWatermark}>ClosenAI</Text>');
  });
});

describe('情绪色环接线：值来自候选自带 mood，缺数据只降级不冒充', () => {
  it('MoodRing 收到 value 与 emoji，无障碍文案两态各自走 i18n', () => {
    const ring = between(SHARE, '<MoodRing', '<Text style={styles.memoryMoodNote}>');
    expect(ring).toContain('value={memory.mood?.value ?? null}');
    expect(ring).toContain('emoji={memory.mood?.emoji}');
    expect(ring).toContain("t('share.memoryMoodA11y', {");
    expect(ring).toContain("t('share.memoryMoodNone')");
  });

  it('环下有一行可读的情绪注记（色环是图，读屏与截图都得有文字出口）', () => {
    expect(read(SHARE)).toContain('${memory.mood.emoji} ${memory.mood.label}');
    expect(read(SHARE)).toContain("{memory.mood ? `");
    expect(read(SHARE)).toContain("styles.memoryMoodNote}");
  });

  it('MoodRing 组件不自算几何：角度/颜色/路径全部来自 config 纯函数', () => {
    const src = read(RING);
    expect(src).toContain("import { annularSectorPath, ringSegments } from '../../config/memoryRing'");
    expect(src).not.toMatch(/Math\.PI/);
    expect(src).not.toMatch(/fromAngle:\s*-?\d/);
    expect(src).toContain('<Svg');
    expect(src).toContain('<Path');
    expect(src).toContain('d={annularSectorPath(');
    expect(src).toContain('fill={s.color}');
    expect(src).toContain('fillOpacity={s.opacity}');
  });

  it('中心 emoji 只在真有情绪时出现，且覆盖层不吃触摸（挡住分享按钮就是事故）', () => {
    const src = read(RING);
    expect(src).toContain('const showEmoji = segments.some((s) => s.active) && !!emoji;');
    expect(src).toContain('{showEmoji && (');
    expect(src).toContain('pointerEvents="none"');
  });

  it('环本体带 image 角色与 label（色环无文字，读屏至少要说出是哪段）', () => {
    const src = read(RING);
    expect(src).toContain('accessibilityRole="image"');
    expect(src).toContain('accessibilityLabel={accessibilityLabel}');
  });

  it('静态环：不引 Animated（分享卡要能被截图，补间帧不确定＝观感不可复现）', () => {
    expect(read(RING)).not.toContain('Animated');
    expect(read(CFG)).not.toContain('Animated');
  });
});

describe('跨仓字段对齐：候选与 mood 的键集，前端类型不许自造', () => {
  const backend = readBackend('services/share_service.py');

  /** 取 TS 里某个 interface 的字段块（整份文件匹配会让别处的同名字段冒充通过）。 */
  function tsBlock(name: string): string {
    const src = read(TYPES);
    const i = src.indexOf(`export interface ${name} {`);
    expect(i).toBeGreaterThanOrEqual(0);
    return src.slice(i, src.indexOf('\n}', i));
  }

  /** 字段在册的判据：块内以该名字起头声明（可选标记 ? 不算改名）。 */
  function hasField(block: string, key: string): boolean {
    return new RegExp(`^  ${key}\\??:`, 'm').test(block);
  }

  it('候选五个键与 OnThisDayCandidate 逐字一致（多一个键前端拿不到，少一个键色环永空）', () => {
    const block = backend.slice(
      backend.indexOf('"memory_id": r.id,'),
      backend.indexOf('"memory_id": r.id,') + 320
    );
    const iface = tsBlock('OnThisDayCandidate');
    ['memory_id', 'content', 'years_ago', 'created_at', 'mood'].forEach((k) => {
      expect(block).toContain(`"${k}"`);
      expect(hasField(iface, k)).toBe(true);
    });
  });

  it('mood 三键 emoji/label/value 与前端 MemoryDayMood 一致', () => {
    const fn = backend.slice(backend.indexOf('def _day_mood'));
    const iface = tsBlock('MemoryDayMood');
    ['emoji', 'label', 'value'].forEach((k) => {
      expect(fn.slice(0, 1400)).toContain(`"${k}"`);
      expect(hasField(iface, k)).toBe(true);
    });
  });

  it('情绪分值复用 MOOD_MAP 的同一张表与同一个折中口径（表外 emoji 记 3）', () => {
    const fn = backend.slice(backend.indexOf('def _day_mood'));
    expect(fn).toContain('MOOD_MAP.get(first.mood_emoji, (None, 3))[1]');
  });

  it('当日无签到返回 None，不拿别日签到冒充（前端据此走中性环）', () => {
    const fn = backend.slice(backend.indexOf('def _day_mood'));
    expect(fn).toMatch(/if not checkins:\s*\n\s*return None/);
  });

  it('色环与记忆共用同一条本地日界窗口（两次查询用同一对 start/end）', () => {
    const loop = backend.slice(
      backend.indexOf('start = local_day_start_utc(tz_name, local_day)'),
      backend.indexOf('start = local_day_start_utc(tz_name, local_day)') + 700
    );
    expect(loop).toContain('memory_repository.list_between(db, user_id, start, end');
    expect(loop).toContain('mood_repository.list_between(db, user_id, start, end)');
  });
});

describe('分享出口：正文两段、清单两项、深链一条路', () => {
  it('清单两项＝原话 + 深链，与实际发出去的 memoryCardShareMessage(原话, link) 同源', () => {
    const src = read(SHARE);
    expect(src).toContain("<ShareManifest items={[t('home.archiveManifestQuote'), t('share.manifestLink')]} />");
    expect(src).toContain('memoryCardShareMessage(memory.content, link)');
  });

  it('深链只在真要分享时取，全屏一处调用（预取会白烧一次推荐码）', () => {
    expect(count(SHARE, /getShareLink\(\)/g)).toBe(1);
    expect(from(SHARE, 'const { link } = await getShareLink();', 260)).toContain(
      'trackFunnelShare('
    );
  });

  it('分享两拍埋点齐全，source 用 memory（与首页考古卡同一口径）', () => {
    const src = read(SHARE);
    expect(src).toContain("trackFunnelShare('share_tapped', 'memory')");
    expect(src).toContain("trackFunnelShare('share_completed', 'memory')");
    expect(src).toContain('if (res.action === Share.sharedAction)');
  });

  it('PDF 出口本批仍不接（负向闸，与首页同口径；接上要同时改这条）', () => {
    expect(read(SHARE)).not.toContain('/share/memory-card');
    expect(read(SHARE)).not.toContain('exportMemoryCard');
  });

  it('防连点：sharing 闸门 + 按钮 disabled + finally 复位', () => {
    const src = read(SHARE);
    expect(src).toContain('if (!memory || sharing) return;');
    expect(src).toContain('setSharing(true);');
    expect(src).toContain('setSharing(false);');
    expect(src).toContain('disabled={sharing}');
    expect(count(SHARE, /const \[sharing, setSharing\] = useState\(false\);/g)).toBe(1);
  });

  it('失败走屏内 error 而不是开新 Alert 站点（Alert 站点数仍是 proGate 那一处）', () => {
    expect(count(SHARE, /Alert\.alert\(/g)).toBe(1);
    expect(from(SHARE, '      } catch (e) {\n        // 走屏内错误出口', 260)).toContain(
      "setError(e instanceof ApiError ? e.message : t('share.memoryShareFailed'));"
    );
  });
});

describe('新增文案：6 个键 × 15 语言齐备，占位符不许掉', () => {
  const langs = Object.keys(resources);
  const KEYS: [string, string][] = [
    ['home', 'archiveRecallCard'],
    ['share', 'memory'],
    ['share', 'memoryEmpty'],
    ['share', 'memoryMoodNone'],
    ['share', 'memoryMoodA11y'],
    ['share', 'memoryShareFailed'],
  ];

  it('15 个语言都在册', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(langs)('%s：六个新键存在且非空', (lng) => {
    KEYS.forEach(([b, k]) => {
      const v = (resources as any)[lng].translation[b]?.[k];
      expect(typeof v).toBe('string');
      expect((v || '').trim().length).toBeGreaterThan(0);
    });
  });

  it('无障碍句的 {{emoji}} {{label}} {{value}} 三个占位符逐语言齐备（少一个就是读屏念出裸花括号）', () => {
    langs.forEach((lng) => {
      const v = (resources as any)[lng].translation.share.memoryMoodA11y as string;
      ['{{emoji}}', '{{label}}', '{{value}}'].forEach((p) => expect(v).toContain(p));
    });
  });

  it('中文原生：入口叫「回忆卡」、空态说清为什么空', () => {
    const zh = (resources as any).zh.translation;
    expect(zh.home.archiveRecallCard).toMatch(/回忆卡/);
    expect(zh.share.memory).toBe('回忆');
    expect(zh.share.memoryEmpty).toMatch(/原话/);
    expect(zh.share.memoryMoodNone).toMatch(/情绪/);
  });

  it('回忆卡用到的每个字面量 t() 键都在 en 表里（漏键只真机显英文）', () => {
    const en = (resources as any).en.translation;
    const lookup = (key: string) =>
      key.split('.').reduce<unknown>(
        (acc, p) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[p] : undefined),
        en
      );
    const missing = [...new Set([...read(SHARE).matchAll(/\bt\('([a-zA-Z0-9_.]+)'/g)].map((m) => m[1]))]
      .filter((k) => lookup(k) === undefined && lookup(`${k}_other`) === undefined);
    expect(missing).toEqual([]);
  });
});

describe('样式口径：回忆卡的行高与名片共用主题档位（长原文最容易挤成一坨）', () => {
  it('正文与标题行高取 LineHeight token，日期大字给足 pt', () => {
    const styles = read(SHARE).slice(read(SHARE).indexOf('const styles = StyleSheet.create'));
    ['memoryTitle', 'memoryQuote', 'memoryMoodNote'].forEach((k) => {
      const line = styles.split('\n').find((l) => l.trim().startsWith(`${k}:`));
      expect(line).toBeDefined();
    });
    expect(styles).toContain('lineHeight: LineHeight.body');
    expect(styles).toContain('lineHeight: LineHeight.caption');
    expect(styles).toMatch(/memoryDate: \{[\s\S]*lineHeight: 44/);
  });
});
