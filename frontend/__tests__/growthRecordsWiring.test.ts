/**
 * 成长记录页 / 推荐话题（P0-F07 §6）的接线闸。
 *
 * RN 屏在 jest 的 node 环境渲染不了（同 memoryCardWiring.test.ts 的先例），所以这里钉的是
 * 「改了就不安全」的结构事实。这一批的失效方式全是静默的：
 * - 端点路径改一个字母 → 404，屏上只是「还没有记录」；
 * - 前端 tag 映射表与后端 _TOPIC_KEYS 漂移 → 非中文界面显中文规范名，或筛选档点不出东西；
 * - 自己造句替代 topics[].topic → 15 语言里只有前端那份，且后端本地化白做；
 * - created_at 用 slice(0,10) 分桶 → UTC 日，跨时区差一天。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';
import { FontSize, Spacing, BorderRadius, LineHeight } from '../src/theme/colors';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readBackend = (rel: string) => read(path.join('..', 'backend', rel));

const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const SCREEN = 'src/screens/GrowthRecordsScreen.tsx';
const EXPLORE = 'src/screens/ExploreScreen.tsx';
const APP = 'App.tsx';
const ROUTER = path.join('..', 'backend', 'routers', 'growth.py');

const NS_KEYS = ['title', 'empty', 'filterAll', 'topicsTitle', 'guestEmpty'];
const TAG_KEYS = ['mood', 'relationship', 'work', 'goal', 'communication', 'growth'];

describe('取数：两条 GET 的路径逐字，且不带任何前端自造的查询串', () => {
  it('api.ts 打的是 /growth/records 与 /growth/topics（后端那两条 GET 在册）', () => {
    const api = read(API);
    expect(api).toContain("return request<{ records: GrowthRecordItem[]; total: number }>('/growth/records');");
    expect(api).toContain("return request<{ topics: GrowthTopic[] }>('/growth/topics');");
    // 参数一律吃后端缺省：前端不许自己拼 tag/days/limit
    expect(api).not.toMatch(/\/growth\/records\?/);
    expect(api).not.toMatch(/\/growth\/topics\?/);
  });

  it('路由真在 backend/routers/growth.py 上（路径改了一边、另一边还绿＝最坏的那种绿）', () => {
    const router = fs.readFileSync(ROUTER, 'utf8');
    expect(router).toContain('@router.get("/growth/records")');
    expect(router).toContain('@router.get("/growth/topics")');
  });

  it('屏内各一处调用，且失败退 null 而不是抛出（错误态由屏自己渲染）', () => {
    const screen = read(SCREEN);
    expect(screen.match(/getGrowthRecords\(\)\.catch\(\(\) => null\)/g)).toHaveLength(1);
    expect(screen.match(/getRecommendedTopics\(\)\.catch\(\(\) => null\)/g)).toHaveLength(1);
  });
});

describe('跨仓字段对齐：tag 映射键集 == 后端 _TOPIC_KEYS，前端类型不自造字段', () => {
  /** 前端 TAG_KEYS 的键（值才是 i18n 后缀，键必须与后端中文规范名逐字相等）。 */
  function frontendTags(): string[] {
    const src = read(SCREEN);
    const i = src.indexOf('const TAG_KEYS: Record<string, string> = {');
    expect(i).toBeGreaterThanOrEqual(0);
    const block = src.slice(i, src.indexOf('};', i));
    return [...block.matchAll(/^\s{2}(\S+): '/gm)].map((m) => m[1]);
  }

  function backendTags(): string[] {
    const src = readBackend('services/growth_service.py');
    const i = src.indexOf('_TOPIC_KEYS = {');
    expect(i).toBeGreaterThanOrEqual(0);
    const block = src.slice(i, src.indexOf('}', i));
    return [...block.matchAll(/^\s{4}"([^"]+)":/gm)].map((m) => m[1]);
  }

  it('两侧键集逐字相等（少一个键→那个标签只能显中文；多一个键→那一档永远点空）', () => {
    expect(frontendTags().slice().sort()).toEqual(backendTags().slice().sort());
  });

  it('映射值恰好是文案表里那 6 个 tag.* 后缀（拼错的键只会静默回退成英文）', () => {
    const src = read(SCREEN);
    const i = src.indexOf('const TAG_KEYS: Record<string, string> = {');
    const block = src.slice(i, src.indexOf('};', i));
    const values = [...block.matchAll(/'([a-z]+)'/g)].map((m) => m[1]);
    expect(values.slice().sort()).toEqual([...TAG_KEYS].sort());
  });

  it('GrowthRecordItem / GrowthTopic 的字段与后端 list_records、get_recommended_topics 逐字同集', () => {
    const types = read(TYPES);
    const iface = (name: string) => {
      const i = types.indexOf(`export interface ${name} {`);
      expect(i).toBeGreaterThanOrEqual(0);
      return types.slice(i, types.indexOf('\n}', i));
    };
    const svc = readBackend('services/growth_service.py');
    for (const k of ['id', 'record_type', 'content', 'tags', 'created_at']) {
      expect(svc).toContain(`"${k}":`);
      expect(iface('GrowthRecordItem')).toMatch(new RegExp(`^  ${k}\\??:`, 'm'));
    }
    for (const k of ['tag', 'count', 'topic']) {
      expect(iface('GrowthTopic')).toMatch(new RegExp(`^  ${k}:`, 'm'));
    }
  });
});

describe('接线：屏在册、入口可点，且话题用的是后端整句而不是前端造句', () => {
  it('App.tsx 三处齐（导入、路由参数表、Stack.Screen）', () => {
    const app = read(APP);
    expect(app).toContain("import GrowthRecordsScreen from './src/screens/GrowthRecordsScreen';");
    expect(app).toContain('  GrowthRecords: undefined;');
    expect(app).toContain('name="GrowthRecords"');
    expect(app).toContain('component={GrowthRecordsScreen}');
  });

  it('探索页入口格在，且导航名与注册名同一个串', () => {
    expect(read(EXPLORE)).toContain("navigation.navigate('GrowthRecords')");
    expect(read(EXPLORE)).toContain("{t('explore.growthRecords')}");
  });

  it('话题正文取 tp.topic、数量取 tp.count，前端不拼「关于…」这类句子', () => {
    const screen = read(SCREEN);
    expect(screen).toContain('<Text style={styles.topicText}>{tp.topic}</Text>');
    expect(screen).toContain('<Text style={styles.topicCount}>{tp.count}</Text>');
    expect(screen).not.toMatch(/t\('growthRecords\.topic[A-Z]/);
  });

  it('消费面逐字：records / topics 两个信封字段都真的被读（读不到就整屏空态那种假象）', () => {
    const screen = read(SCREEN);
    expect(screen).toContain('setRecords(recordsRes.records);');
    expect(screen).toContain('setTopics(topicsRes?.topics || []);');
    expect(screen).toContain('r.tags.includes(tagFilter)');
    expect(screen).toContain('{r.content}');
  });
});

describe('日期与状态：本地日分桶、缺失不丢、失败与空态分开', () => {
  it('分桶走 toLocalDate(created_at)，不 slice(0,10)（那是 UTC 日，跨时区差一天）', () => {
    const screen = read(SCREEN);
    expect(screen).toContain("import { toLocalDate } from '../utils/datetime';");
    expect(screen).toContain('toLocalDate(r.created_at)');
    expect(screen).not.toMatch(/created_at\.slice\(/);
  });

  it("created_at 缺失的记录归到可见的「—」桶，而不是被 filter 掉", () => {
    expect(read(SCREEN)).toContain("r.created_at ? toLocalDate(r.created_at) : '—'");
  });

  it('拉取失败 ≠ 没有记录：error 态给重试按钮，空态只在 !error 时显文案', () => {
    const screen = read(SCREEN);
    expect(screen).toContain("setError(t('common.loadFailed'));");
    expect(screen).toContain("{t('common.retry')}");
    expect(screen).toContain('{!error && groups.length === 0 && (');
    expect(screen).toContain("{t('growthRecords.empty')}");
    // 未登录另走一态，不能把「没登录」显示成「你还没有记录」
    expect(screen).toContain("{t('growthRecords.guestEmpty')}");
  });
});

describe('15 语言文案齐备，且屏里每个 t() 键都在 en 表里', () => {
  const langs = Object.keys(resources);

  it('15 个语言都在册', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(langs)('%s：growthRecords 11 键 + explore 2 键存在且非空', (lng) => {
    const tr = (resources as any)[lng].translation;
    NS_KEYS.forEach((k) => {
      expect(typeof tr.growthRecords?.[k]).toBe('string');
      expect((tr.growthRecords?.[k] || '').trim()).not.toBe('');
    });
    TAG_KEYS.forEach((k) => {
      expect(typeof tr.growthRecords?.tag?.[k]).toBe('string');
      expect((tr.growthRecords?.tag?.[k] || '').trim()).not.toBe('');
    });
    ['growthRecords', 'growthRecordsDesc'].forEach((k) => {
      expect(typeof tr.explore?.[k]).toBe('string');
      expect((tr.explore?.[k] || '').trim()).not.toBe('');
    });
  });

  it('中文原生：标题就叫「成长记录」，六个标签是后端那六个规范名', () => {
    const zh = (resources as any).zh.translation;
    expect(zh.growthRecords.title).toBe('成长记录');
    expect(Object.values(zh.growthRecords.tag).slice().sort()).toEqual(
      ['情绪', '关系', '工作', '目标', '沟通', '成长'].slice().sort()
    );
  });

  it('屏里出现的每个字面量 t() 键都能在 en 表里查到（漏键只会在真机上显英文）', () => {
    const en = (resources as any).en.translation;
    const lookup = (key: string) =>
      key.split('.').reduce<unknown>(
        (acc, p) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[p] : undefined),
        en
      );
    const src = read(SCREEN) + read(EXPLORE);
    const literalKeys = [...new Set([...src.matchAll(/\bt\('([a-zA-Z0-9_.]+)'/g)].map((m) => m[1]))];
    expect(literalKeys.length).toBeGreaterThan(0);
    expect(literalKeys.filter((k) => lookup(k) === undefined)).toEqual([]);
    // 模板串键（t(`growthRecords.tag.${…}`)）单独查：六个后缀逐个必须在 en 表里
    TAG_KEYS.forEach((k) => expect(typeof lookup(`growthRecords.tag.${k}`)).toBe('string'));
  });
});

describe('样式口径：新屏的字体档位全在主题表里', () => {
  it('FontSize.<token> 引用的键真实存在（拼错 = 静默 undefined = 字号回落）', () => {
    const src = read(SCREEN);
    const tables: Record<string, any> = { FontSize, Spacing, BorderRadius, LineHeight };
    const bad = [...src.matchAll(/(FontSize|Spacing|BorderRadius|LineHeight)\.([a-zA-Z0-9]+)/g)]
      .filter((m) => !(m[2] in tables[m[1]]))
      .map((m) => `${m[1]}.${m[2]}`);
    expect(bad).toEqual([]);
  });
});
