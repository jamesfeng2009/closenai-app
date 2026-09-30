/**
 * #34 洞察依据回链（读路径聚合）测试。
 *
 * 后端一行未动：/growth/insights 与 /reflections/generate 都不下发逐条引用，
 * 所以前端只能用 GET /memories 聚合。这类实现的失效方式有两种，
 * 测试就盯两种：① 把解析不到的 id 说成依据（编造），② 窗口边界把当期记录漏掉。
 */
import * as fs from 'fs';
import * as path from 'path';
import resources from '../src/i18n/locales';
import { resolveMemoryRefs, memoriesWithinDays } from '../src/services/evidence';

const listMemoriesMock = jest.fn();
jest.mock('../src/services/api', () => ({
  listMemories: (...args: unknown[]) => listMemoriesMock(...args),
}));

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const mem = (id: string, content: string, created_at: string) =>
  ({
    id,
    user_id: 'u1',
    content,
    category: 'values',
    status: 'current',
    source: 'self',
    conviction: 1,
    created_at,
  }) as never;

beforeEach(() => listMemoriesMock.mockReset());

describe('resolveMemoryRefs：id 级回链', () => {
  it('没有 id 时一次请求都不发（无依据就不该出现依据栏）', async () => {
    expect(await resolveMemoryRefs(undefined)).toEqual([]);
    expect(await resolveMemoryRefs([])).toEqual([]);
    expect(await resolveMemoryRefs(['', ''])).toEqual([]);
    expect(listMemoriesMock).not.toHaveBeenCalled();
  });

  it('按传入顺序返回，重复 id 去重，解析不到的直接丢弃而不是留空壳', async () => {
    listMemoriesMock.mockResolvedValue([
      mem('m2', '我周末通常一个人待着', '2026-09-20T02:00:00Z'),
      mem('m1', '我在意的是被记住', '2026-09-21T02:00:00Z'),
    ]);
    const refs = await resolveMemoryRefs(['m1', 'm2', 'm1', 'm-gone']);
    expect(refs.map((r) => r.memory_id)).toEqual(['m1', 'm2']);
    expect(refs[0].excerpt).toBe('我在意的是被记住');
    expect(refs.every((r) => !!r.created_at)).toBe(true);
  });

  it('原话里的换行与连续空白压成单空格（与 /chat/history 同口径）', async () => {
    listMemoriesMock.mockResolvedValue([mem('m1', ' 先说 A\n\t再说  B ', '2026-09-21T02:00:00Z')]);
    expect((await resolveMemoryRefs(['m1']))[0].excerpt).toBe('先说 A 再说 B');
  });

  it('取记忆用的是后端允许的最大条数', async () => {
    listMemoriesMock.mockResolvedValue([]);
    await resolveMemoryRefs(['m1']);
    expect(listMemoriesMock).toHaveBeenCalledWith(100);
  });
});

describe('memoriesWithinDays：期间级依据', () => {
  it('裸日期锚点右端进到次日 0 点，当天记录不会被自己排除', async () => {
    listMemoriesMock.mockResolvedValue([
      mem('same-day', '锚点当天的记录', '2026-09-27T10:00:00Z'),
      mem('in', '窗口内', '2026-09-22T00:00:00Z'),
      mem('out', '窗口外', '2026-09-19T23:00:00Z'),
    ]);
    const refs = await memoriesWithinDays(7, '2026-09-27');
    expect(refs.map((r) => r.memory_id).sort()).toEqual(['in', 'same-day']);
  });

  it('带时区的完整时间戳按原样当右端，未来记录不算依据', async () => {
    listMemoriesMock.mockResolvedValue([
      mem('past', '过去', '2026-09-26T00:00:00Z'),
      mem('future', '未来', '2026-10-05T00:00:00Z'),
    ]);
    const refs = await memoriesWithinDays(7, '2026-09-28T00:00:00Z');
    expect(refs.map((r) => r.memory_id)).toEqual(['past']);
  });

  it('锚点解析不出来 → 空数组（不把无关记录挂成依据）', async () => {
    listMemoriesMock.mockResolvedValue([mem('m1', '任意', '2026-09-21T02:00:00Z')]);
    expect(await memoriesWithinDays(7, 'not-a-date')).toEqual([]);
    expect(listMemoriesMock).not.toHaveBeenCalled();
  });

  it('无锚点退回当前时间', async () => {
    listMemoriesMock.mockResolvedValue([mem('now', '刚写的', new Date().toISOString())]);
    expect((await memoriesWithinDays(7)).map((r) => r.memory_id)).toEqual(['now']);
  });
});

describe('两屏接线在位（后端零改动的前提下）', () => {
  it('反思草稿：related_memory_ids 解析成 memoryRefs，交给已有「记忆依据 ▾」渲染', () => {
    const src = read('src/screens/ReflectScreen.tsx');
    expect(src).toContain("import { resolveMemoryRefs } from '../services/evidence'");
    expect(src).toMatch(/flatMap\(\(m\) => m\.relatedMemoryIds \?\? \[\]\)/);
    expect(src).toMatch(/resolveMemoryRefs\(pendingIds\)\.catch\(\(\) => \[\]\)/);
    // 只在解析成功时写入，且按 id 映射——没有「拿第一条记忆凑数」的写法
    expect(src).toMatch(/\.map\(\(id\) => byId\.get\(id\)\)[\s\S]*?\.filter\(\(r\): r is MemoryRef => !!r\)/);
    expect(src).toContain('refs.length > 0');
  });

  it('周洞察：以 period 为锚点聚合当期记忆，展开时带「不编造引用」说明', () => {
    const src = read('src/screens/InsightsScreen.tsx');
    expect(src).toContain("import { memoriesWithinDays } from '../services/evidence'");
    expect(src).toMatch(/memoriesWithinDays\(\s*7,\s*w\.period\s*\)\.catch\(\(\) => \[\]\)/);
    // 没有周报正文时不聚合（空态不该冒出依据栏）
    expect(src).toMatch(/w\?\.content \? await memoriesWithinDays/);
    expect(src).toContain("t('reflect.memoryRefs'");
    expect(src).toContain("t('journey.evidenceNote')");
    expect(src).toMatch(/\{!\!weeklyRefs\.length && \(/);
  });

  it('用到的两个文案键在 15 语言齐备且占位符不变', () => {
    const langs = Object.keys(resources);
    expect(langs).toHaveLength(15);
    langs.forEach((lang) => {
      const bundle = (resources as Record<string, { translation: Record<string, any> }>)[lang].translation;
      expect(bundle.reflect.memoryRefs).toMatch('{{n}}');
      expect(bundle.reflect.memoryRefs).toMatch('{{arrow}}');
      expect(typeof bundle.journey.evidenceNote).toBe('string');
      expect(bundle.journey.evidenceNote.length).toBeGreaterThan(0);
    });
  });
});
