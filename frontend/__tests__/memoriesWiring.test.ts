/**
 * 批 2 · D-G4（记忆面新增＋详情/演化）的接线闸（2026-10-17）。
 *
 * 原状不是「后端没实现」：`backend/routers/memories.py` 六条端点齐全（:33/:45/:59/:80/:100/:122），
 * 而前端只有 list＋delete 两条封装（`api.ts` 的 Memory 段），于是**用户只能读和删，不能记、不能改**
 * ——一个写着「记忆管理」的屏其实只有「查看删除」。这类缺陷 jest 测不出来（渲染不了 RN 屏），
 * 所以手法沿用本仓其它接线闸：读源码文本＋正则切片，跨端一致（类别集合、长度上限）
 * 直接解析 `backend/models/schemas.py` 的 Literal/Field，前端悄悄加一档或多一档会在 CI 上红。
 *
 * 四条判据各自对应一条会骗人的实现：
 * ① 端点集合与请求形状（写了函数不等于指向同一条线）；
 * ② 提交后**回读**而不是乐观插一条（后端 ≥0.85 判重时根本不新建）；
 * ③ 空内容不提交（`new_content` 缺省时后端沿用原文新建一行＝同文字重复行，不是演变）；
 * ④ 15 语言键齐（漏一门会静默回退成英文整句，本仓踩过两次）。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const API = 'src/services/api.ts';
const SCREEN = 'src/screens/MemoriesScreen.tsx';
const TYPES = 'src/types/index.ts';
const SCHEMAS_PY = 'backend/models/schemas.py';

const CATEGORIES = ['values', 'preference', 'relationship', 'goal', 'factual'];
const STATUSES = ['current', 'superseded', 'reverted'];

/** 切出 api.ts 里某个导出封装的函数体（到本函数结束的 `\n}` 为止），并去掉注释行。 */
function wrapper(name: string): string {
  const src = read(API);
  const m = new RegExp(`export async function ${name}\\([^)]*\\)[^{]*\\{([\\s\\S]*?)\\n}`).exec(src);
  expect(m).not.toBeNull();
  // 注释里出现字段名是正常的（解释为什么不送），判据只看代码行
  return m![1]
    .split('\n')
    .filter((line) => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
    .join('\n');
}

/** 解析 schemas.py 里某类体内某字段的 Literal 成员（按声明顺序，逐字比）。 */
function literalMembers(className: string, field: string): string[] {
  const src = readRepo(SCHEMAS_PY);
  const body = new RegExp(`class ${className}\\(BaseModel\\):([\\s\\S]*?)\\n\\nclass `).exec(src);
  expect(body).not.toBeNull();
  const line = new RegExp(`${field}: ([^\\n]+)`).exec(body![1]);
  expect(line).not.toBeNull();
  const literal = /Literal\[([^\]]+)\]/.exec(line![1]);
  expect(literal).not.toBeNull();
  return literal![1].split(',').map((s) => s.trim().replace(/^"|"$/g, ''));
}

type Tree = Record<string, unknown>;
function flatten(obj: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') Object.assign(out, flatten(v as Tree, key));
    else out[key] = String(v);
  }
  return out;
}

describe('① 三条新封装真的指向后端那三条端点', () => {
  it('createMemory：POST /memories，只送 content＋category', () => {
    const body = wrapper('createMemory');
    expect(body).toContain("request<Memory>('/memories', {");
    expect(body).toContain("method: 'POST',");
    expect(body).toContain('body: JSON.stringify({ content: input.content, category: input.category }),');
    // source/conviction 是模型口径，不让用户替检索层打分（工作单 D-G4-2）
    expect(body).not.toMatch(/source/);
    expect(body).not.toMatch(/conviction/);
  });

  it('getMemory：GET /memories/{id}（详情面用的就是这条，不是从列表里捞）', () => {
    const body = wrapper('getMemory');
    expect(body).toContain('request<Memory>(`/memories/${memoryId}`)');
    expect(body).not.toContain('method:');
  });

  it('versionMemory：POST /memories/version，transition_type 有值且新信念落 new_content', () => {
    const body = wrapper('versionMemory');
    expect(body).toContain("request<{ memory_id: string }>('/memories/version', {");
    expect(body).toContain("method: 'POST',");
    expect(body).toContain('memory_id: input.memoryId,');
    expect(body).toContain("transition_type: input.transitionType ?? 'sudden',");
    expect(body).toContain('new_content: input.newContent,');
  });

  it('检索面不复活：api.ts 里没有 /memories/search（D-G4-1 登记不接）', () => {
    expect(read(API)).not.toContain('/memories/search');
  });

  it('后端契约里这三条都登记过（防止前端指向一条契约外的线）', () => {
    const md = readRepo('contracts/endpoints.md');
    // 只认「方法 + 路径在册」，不认序号：序号是全表排序的结果，T1-01 新增
    // GET /cron/export-jobs 一条就把这三行各往后推 1，把台账重导变成改测试没有意义。
    expect(md).toContain('| POST | `/api/v1/memories` |');
    expect(md).toContain('| POST | `/api/v1/memories/version` |');
    expect(md).toContain('| GET | `/api/v1/memories/{memory_id}` |');
  });
});

describe('② 类别集合与长度上限跟着后端，不是前端另立一套', () => {
  it('前端 MEMORY_CATEGORIES 与 MemoryCreate.category 的 Literal 同集同序', () => {
    expect(literalMembers('MemoryCreate', 'category')).toEqual(CATEGORIES);
    const src = read(SCREEN);
    const m = /const MEMORY_CATEGORIES: Memory\['category'\]\[\] = \[([^\]]+)\]/.exec(src);
    expect(m).not.toBeNull();
    expect(m![1].split(',').map((s) => s.trim().replace(/'/g, ''))).toEqual(CATEGORIES);
  });

  it('types 里 Memory 的 category 联合与后端同集（屏上取的是同一个类型）', () => {
    const body = /export interface Memory \{([\s\S]*?)\n\}/.exec(read(TYPES));
    expect(body).not.toBeNull();
    const line = /category: ([^;]+);/.exec(body![1]);
    expect(line).not.toBeNull();
    const got = [...line![1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
    expect(got).toEqual(CATEGORIES);
  });

  it('types 里 Memory 的 status 联合与界面三个状态键同集', () => {
    const body = /export interface Memory \{([\s\S]*?)\n\}/.exec(read(TYPES));
    const line = /status: ([^;]+);/.exec(body![1]);
    expect(line).not.toBeNull();
    expect([...line![1].matchAll(/'([^']+)'/g)].map((m) => m[1])).toEqual(STATUSES);
  });

  it('这三个状态的出处是后端 MemoryResponse 的行内口径（不是前端发明的枚举）', () => {
    const py = readRepo(SCHEMAS_PY);
    const line = /class MemoryResponse\(BaseModel\):[\s\S]*?\n    status: str\s+# ([^\n]+)/.exec(py);
    expect(line).not.toBeNull();
    expect(line![1].split('|').map((s) => s.trim())).toEqual(STATUSES);
  });

  it('MEMORY_MAX_LENGTH 逐字等于 MemoryCreate.content 的 max_length', () => {
    const py = readRepo(SCHEMAS_PY);
    const declared = /class MemoryCreate\(BaseModel\):[\s\S]*?content: str = Field\(\.\.\., max_length=(\d+)/.exec(py);
    expect(declared).not.toBeNull();
    const got = /const MEMORY_MAX_LENGTH = (\d+);/.exec(read(SCREEN));
    expect(got).not.toBeNull();
    expect(got![1]).toBe(declared![1]);
    expect(got![1]).toBe('4000');
  });
});

describe('③ 界面上两条入口都在，且口径不是「点了就算存上」', () => {
  const src = read(SCREEN);

  it('三条封装各只有一个调用点（新增/详情回读/演变）', () => {
    expect(src.match(/await createMemory\(\{ content, category: draftCategory \}\);/g)).toHaveLength(1);
    expect(src.match(/getMemory\(item\.id\)/g)).toHaveLength(1);
    expect(src.match(/await versionMemory\(\{ memoryId: detail\.id, newContent \}\);/g)).toHaveLength(1);
  });

  it('提交后回读列表，绝不乐观插一条（判重命中时后端压根没新建）', () => {
    expect(src).toContain('await load();');
    expect(src).not.toMatch(/\.\.\.prev,\s*\n?\s*(created|memory|item)/);
    expect(src).not.toMatch(/setMemories\(\(prev\) => \[[\s\S]{0,40}content/);
  });

  it('判重中间态：saving 时按钮文案是「正在检查是否重复」，不是普通 loading', () => {
    expect(src).toContain("{saving ? t('memories.checking') : t('memories.addSubmit')}");
    expect(src).toContain('disabled={!draft.trim() || saving}');
  });

  it('演变提交要非空内容（空提交＝后端沿用原文新建一行重复记忆）', () => {
    expect(src).toContain('disabled={!change.trim() || versioning}');
    const guard = /const onChange = async \(\) => \{([\s\S]*?)\n  \};/.exec(src);
    expect(guard).not.toBeNull();
    expect(guard![1]).toContain('if (!detail || !newContent || versioning) return;');
  });

  it('详情字段逐个来自 MemoryResponse，不自己编状态文案', () => {
    for (const field of ['detail.status', 'detail.conviction', 'detail.created_at', 'detail.superseded_at', 'detail.transition_reason']) {
      expect(src).toContain(field);
    }
    expect(src).toContain('t(`memories.status.${detail.status}`)');
  });

  it('memories.status 的三个子键与后端状态集合一字不差', () => {
    const flat = flatten((resources as any).en.translation as Tree);
    const got = Object.keys(flat)
      .filter((k) => k.startsWith('memories.status.'))
      .map((k) => k.split('.').pop()!);
    expect(got.sort()).toEqual([...STATUSES].sort());
  });

  it('游客点提交先过 requireAuth（与删除同一道闸）', () => {
    const add = /const onAdd = async \(\) => \{([\s\S]*?)\n  \};/.exec(src);
    expect(add).not.toBeNull();
    expect(add![1]).toContain('if (!requireAuth()) return;');
  });

  it('不发裸 fetch/XMLHttpRequest（请求出口只有 api.ts）', () => {
    expect(src).not.toMatch(/\bfetch\(/);
    expect(src).not.toMatch(/XMLHttpRequest/);
  });
});

describe('④ 15 语言新键齐备', () => {
  const locales = Object.keys(resources);
  const keys = [
    'memories.add',
    'memories.addPlaceholder',
    'memories.addSubmit',
    'memories.checking',
    'memories.status.current',
    'memories.status.superseded',
    'memories.status.reverted',
    'memories.conviction',
    'memories.changed',
    'memories.changedPlaceholder',
    'memories.changedSubmit',
    'memories.supersededAt',
  ];

  it('语言数就是 15，漏一门的话下面那条会假绿', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s：12 个键都有值', (locale) => {
    const flat = flatten((resources as any)[locale].translation as Tree);
    for (const key of keys) {
      expect(flat[key]).toBeTruthy();
    }
  });

  it('屏上用到的每个 memories.* 键都在 en 表里（漏键＝界面渲染出裸键名）', () => {
    const flat = flatten((resources as any).en.translation as Tree);
    const used = [...read(SCREEN).matchAll(/t\('memories\.([A-Za-z.]+)'/g)].map((m) => `memories.${m[1]}`);
    expect(used.length).toBeGreaterThan(0);
    for (const key of used) {
      expect(flat[key]).toBeTruthy();
    }
  });
});
