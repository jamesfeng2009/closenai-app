/**
 * KG-26 反思提示库 · 前端闸（2026-11-01）。
 *
 * 这个面板的风险不在渲染，而在三处会各自漂移的地方，所以判据按这三处分组：
 * ① 纯逻辑（换一条／裁空／分组／回退提示）——`.tsx` 在本机 jest 的 node 环境里渲染不了，
 *    所以逻辑全在 `src/services/reflectionPrompts.ts`，这里直接测函数。
 * ② 溯源字段名——前端上送、Pydantic 接收、后端校验，三处必须同字。这里拿
 *    `TRACEABILITY_KEYS` 跟 `backend/models/schemas.py` 的 `ReflectionCreate` 逐字对，
 *    两侧不同源（一侧是 JS 常量，一侧是正则从 .py 里抠出来的字段名）。
 * ③ 15 语言 × 13 键的文案——漏一语言、或某格留空、或把「unlimited／保证／治愈」这类
 *    KG-23/KG-24 刚删掉的绝对化说法又写回来，都要红。
 *
 * 屏与组件（`.tsx`）那部分只能做源码扫描，口径写进断言里，不假装是行为测试。
 * 后端语义（提示内容校验、40001 矩阵、落库形状）在
 * backend/tests/test_prompt_library.py 与 backend/tests/test_reflection_prompt_save.py。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';
import {
  TRACEABILITY_KEYS,
  groupByCluster,
  isPromptWritable,
  nextPrompt,
  saveableText,
  showsFallbackNotice,
  traceabilityFor,
  type PromptLibraryPayload,
  type ReflectionPrompt,
} from '../src/services/reflectionPrompts';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const SCREEN = 'src/screens/ReflectScreen.tsx';
const SHEET = 'src/components/PromptLibrarySheet.tsx';
const MODULE = 'src/services/reflectionPrompts.ts';
const API = 'src/services/api.ts';
const FRONT_ANALYTICS = 'src/services/analytics.ts';
const SCHEMAS_PY = 'backend/models/schemas.py';
const LIBRARY_PY = 'backend/services/prompt_library.py';
const CONTENT_JSON = 'backend/content/reflection_prompts.json';

const COPY_KEYS = [
  'promptEntry', 'promptSheetTitle', 'promptSheetSubtitle', 'promptClusterAll',
  'promptUse', 'promptSwap', 'promptSave', 'promptSaved', 'promptStale',
  'promptBlank', 'promptEmpty', 'promptLoadFailed', 'promptUntranslated',
] as const;

const LANGUAGES = Object.keys(resources);

function prompt(id: string, cluster: string, question = `问题 ${id}`): ReflectionPrompt {
  return { id, cluster, function: 'reflect', title: `标题 ${id}`, question, why: '', follow_up: '' };
}

// ═══ ① 纯逻辑 ═══

describe('① 纯逻辑：换一条不许返回自己，也不许吞掉空条目', () => {
  it('isPromptWritable 把正文只剩空白的条目判为不可写', () => {
    expect(isPromptWritable(prompt('rp-001', 'a'))).toBe(true);
    expect(isPromptWritable(prompt('rp-002', 'a', '   '))).toBe(false);
    expect(isPromptWritable(prompt('rp-003', 'a', '\n\t '))).toBe(false);
    expect(isPromptWritable(null)).toBe(false);
    expect(isPromptWritable(undefined)).toBe(false);
  });

  it('saveableText 裁空白：空或全空白 → null（调用方据此不发请求）', () => {
    expect(saveableText('  今天很平静  ')).toBe('今天很平静');
    expect(saveableText('')).toBeNull();
    expect(saveableText('   \n  \t')).toBeNull();
  });

  it('nextPrompt 往后走一格，到尾回顶', () => {
    const list = [prompt('rp-001', 'a'), prompt('rp-002', 'a'), prompt('rp-003', 'a')];
    expect(nextPrompt(list, 'rp-001')!.id).toBe('rp-002');
    expect(nextPrompt(list, 'rp-002')!.id).toBe('rp-003');
    expect(nextPrompt(list, 'rp-003')!.id).toBe('rp-001');
  });

  it('nextPrompt 跳过不可写条目（点了「换一条」不能还是同一段字）', () => {
    const list = [prompt('rp-001', 'a'), prompt('rp-002', 'a', '  '), prompt('rp-003', 'a')];
    expect(nextPrompt(list, 'rp-001')!.id).toBe('rp-003');
  });

  it('nextPrompt：当前 id 不在库里（簇过滤后的陈旧 id）时给第一条可写的', () => {
    const list = [prompt('rp-001', 'a'), prompt('rp-002', 'a')];
    expect(nextPrompt(list, 'rp-999')!.id).toBe('rp-001');
  });

  it('nextPrompt 的退化两格：空库给 null，只剩一条可写的就给它自己', () => {
    expect(nextPrompt([], null)).toBeNull();
    expect(nextPrompt([], 'rp-001')).toBeNull();
    const only = [prompt('rp-001', 'a'), prompt('rp-002', 'a', ' ')];
    expect(nextPrompt(only, 'rp-001')!.id).toBe('rp-001');
    expect(nextPrompt(only, null)!.id).toBe('rp-001');
  });

  it('groupByCluster 的顺序跟 clusters 走，不跟 prompts 的出现顺序走', () => {
    const groups = groupByCluster({
      clusters: [
        { key: 'b', title: 'B 簇', hint: 'hb', count: 1 },
        { key: 'a', title: 'A 簇', hint: 'ha', count: 2 },
      ],
      prompts: [prompt('rp-001', 'a'), prompt('rp-002', 'b'), prompt('rp-003', 'a')],
    });
    expect(groups.map((g) => g.meta.key)).toEqual(['b', 'a']);
    expect(groups.map((g) => g.items.length)).toEqual([1, 2]);
  });

  it('groupByCluster 把未登记的簇单列出来，不丢行', () => {
    // 徽章写「30 条」而屏幕上数到 29＝这条闸的存在理由：簇改名后残留行不许静默消失。
    const groups = groupByCluster({
      clusters: [{ key: 'a', title: 'A 簇', hint: '', count: 1 }],
      prompts: [prompt('rp-001', 'a'), prompt('rp-002', 'ghost')],
    });
    expect(groups.map((g) => g.meta.key)).toEqual(['a', 'ghost']);
    expect(groups.reduce((n, g) => n + g.items.length, 0)).toBe(2);
  });

  it('groupByCluster：注册了但一条没有的簇仍出现在组里（tab 不塌），徽章用服务端 count', () => {
    const groups = groupByCluster({
      clusters: [
        { key: 'a', title: 'A 簇', hint: '', count: 8 },
        { key: 'empty', title: '空簇', hint: '', count: 0 },
      ],
      prompts: [prompt('rp-001', 'a')],
    });
    expect(groups.map((g) => g.items.length)).toEqual([1, 0]);
    expect(groups[0].meta.count).toBe(8);
  });

  it('showsFallbackNotice 只在「回退且回到的不是设备语言」时提示', () => {
    expect(showsFallbackNotice(true, 'en', 'fr')).toBe(true);
    expect(showsFallbackNotice(true, 'en', 'en')).toBe(false);
    expect(showsFallbackNotice(true, 'en', 'en-US')).toBe(false);
    expect(showsFallbackNotice(true, 'zh', 'zh-CN')).toBe(false);
    expect(showsFallbackNotice(true, 'zh', 'th')).toBe(true);
    expect(showsFallbackNotice(false, 'en', 'fr')).toBe(false);
    expect(showsFallbackNotice(true, '', '')).toBe(false); // 两侧都缺省 → 按 en 处理
  });

  it('traceabilityFor 的 shape 就是上送体', () => {
    expect(traceabilityFor('rp-007', '2026-11-1')).toEqual({
      prompt_id: 'rp-007',
      prompt_version: '2026-11-1',
      source: 'prompt',
    });
  });
});

// ═══ ② 溯源字段名两侧同字 ═══

describe('② 溯源三字段与后端 schema 同字（不同源取证）', () => {
  function schemaFieldNames(className: string): string[] {
    const src = readRepo(SCHEMAS_PY);
    const body = new RegExp(`class ${className}\\(BaseModel\\):([\\s\\S]*?)\\n(?:\\n)?class `).exec(src);
    expect(body).not.toBeNull();
    return [...body![1].matchAll(/^ {4}([a-z_]+):/gm)].map((m) => m[1]);
  }

  it('TRACEABILITY_KEYS 三个都在 ReflectionCreate 的字段里', () => {
    const fields = schemaFieldNames('ReflectionCreate');
    expect(TRACEABILITY_KEYS).toEqual(['prompt_id', 'prompt_version', 'source']);
    for (const key of TRACEABILITY_KEYS) expect(fields).toContain(key);
  });

  it('响应形状两侧都不带溯源字段，且字段序一致（KG-26 只动写侧）', () => {
    // ReflectionResponse 的顺序就是 Go reflectionPayload 的顺序（后端由
    // test_n8_batch5_contract 的字节对齐闸钉住）。前端类型「领先」两个字段，
    // 今天不会报错，切流那天就会读出 undefined——所以钉顺序，不钉包含。
    const fields = schemaFieldNames('ReflectionResponse');
    expect(fields).toEqual(['id', 'content', 'related_memory_ids', 'created_at']);
    const item = /interface ReflectionItem \{([\s\S]*?)\n\}/.exec(read('src/types/index.ts'));
    expect(item).not.toBeNull();
    expect([...item![1].matchAll(/^ {2}([a-z_]+)\??:/gm)].map((m) => m[1])).toEqual(fields);
    expect(item![1]).not.toMatch(/prompt_id|prompt_version/);
  });

  it('前端期望的响应字段都真的在 prompt_library.list_prompts 的返回里', () => {
    const py = readRepo(LIBRARY_PY);
    for (const key of ['version', 'language_used', 'fallback', 'total', 'clusters', 'prompts']) {
      expect(py).toContain(`"${key}":`);
    }
    for (const key of ['id', 'cluster', 'function', 'title', 'question', 'why', 'follow_up']) {
      expect(py).toContain(`"${key}":`);
    }
    for (const key of ['key', 'title', 'hint', 'count']) expect(py).toContain(`"${key}":`);
  });

  it('上送体把三字段带进去，不带时保持旧形状（不给后端塞 null 字段）', () => {
    const body = read(API);
    const fn = /export async function saveReflection\([\s\S]*?\n\}/.exec(body);
    expect(fn).not.toBeNull();
    // 第三参的类型在 KG-28（2026-11-02）扩成 `PromptTraceability | ReflectionOrigin | null`
    // 多出的那一档是情绪分析的来源标记；这条闸钉的是「可省 + 展开进上送体」，不是联合类型的宽度。
    expect(fn![0]).toMatch(/promptMeta\?: PromptTraceability \| ReflectionOrigin \| null/);
    expect(fn![0]).toMatch(/\.\.\.\(promptMeta \?\? \{\}\)/);
    // 反向证人：溯源不是无条件写死的 null
    expect(fn![0]).not.toMatch(/prompt_id: null/);
  });

  it('列表端点走 GET /reflection-prompts，簇名做 URL 编码后带上', () => {
    const fn = /export async function listReflectionPrompts\([\s\S]*?\n\}/.exec(read(API));
    expect(fn).not.toBeNull();
    expect(fn![0]).toContain('/reflection-prompts');
    expect(fn![0]).toContain('encodeURIComponent(cluster)');
  });

  it('提示库的路径不在 `/reflections` 前缀下（N8 上刀时子路径会跟着整段搬去 Go）', () => {
    // Go 侧没有这条内容面：写在 /reflections/prompts 会在放量那天静默 404。
    // 判据两侧取：前端调用点 + 后端路由声明，任何一侧被「顺手归位」都会红。
    expect(read(API)).not.toContain('/reflections/prompts?');
    expect(read(API)).not.toMatch(/request<[^)]*\(\s*[`']\/reflections\/prompts/);
    const routerSrc = readRepo('backend/routers/reflection_prompts.py');
    expect(routerSrc).toContain('@router.get("/reflection-prompts")');
    expect(readRepo('backend/main.py')).toMatch(
      /app\.include_router\(reflection_prompts\.router, prefix="\/api\/v1"/,
    );
    expect(readRepo('backend/routers/reflection.py')).not.toContain('/reflections/prompts');
  });
});

// ═══ ③ 15 语言 × 13 键 ═══

describe('③ 提示库文案：15 语言都在、不留空、不写回绝对化承诺', () => {
  const flat: Record<string, string> = {};
  for (const lang of LANGUAGES) {
    const block = (resources as any)[lang].translation.reflect;
    expect(block).toBeDefined();
    for (const key of COPY_KEYS) flat[`${lang}.${key}`] = String(block[key] ?? '');
  }

  it('每语言 13 键齐全且非空', () => {
    expect(LANGUAGES.length).toBe(15);
    for (const lang of LANGUAGES) {
      for (const key of COPY_KEYS) {
        expect(flat[`${lang}.${key}`].trim().length).toBeGreaterThan(0);
      }
    }
  });

  it('除 en 外没有一格是逐字照抄英文', () => {
    for (const lang of LANGUAGES) {
      if (lang === 'en') continue;
      for (const key of COPY_KEYS) {
        expect(flat[`${lang}.${key}`]).not.toBe(flat[`en.${key}`]);
      }
    }
  });

  it('非中日文案里不许残留别的语言的正文', () => {
    // ko 这一格曾经写成「英语显示가 아니라…」：混排没人看得见，只能靠闸。
    // ja 排除在外——它的正文本来就含汉字，这条判据对它没有区分度。
    for (const lang of LANGUAGES) {
      if (lang === 'en' || lang === 'zh' || lang === 'ja') continue;
      for (const key of COPY_KEYS) {
        expect(flat[`${lang}.${key}`]).toMatch(/^[^㐀-鿿]*$/);
      }
    }
  });

  it('KG-23/KG-24 的禁词不许从提示库这条路回来', () => {
    const banned = [
      /unlimited/i, /guarantee/i, /forever/i, /all data/i,
      /治愈/, /根治/, /诊断/, /保证/, /一定能/, /永远/, /全部数据/, /无限/,
    ];
    for (const [where, value] of Object.entries(flat)) {
      for (const re of banned) expect(`${where} ${value}`).not.toMatch(re);
    }
  });

  it('提示库正文只有中英两版：这条限制要在界面上说清楚（回退说明键存在）', () => {
    const py = readRepo(LIBRARY_PY);
    const languages = /LIBRARY_LANGUAGES = \("([^"]*)", "([^"]*)"\)/.exec(py);
    expect(languages).not.toBeNull();
    expect(languages![1]).toBe('en');
    expect(languages![2]).toBe('zh');
    // 内容文件里每条提示都带这两版（不然 fallback 的口径就是空的）
    const content = JSON.parse(readRepo(CONTENT_JSON));
    expect(content.prompts.length).toBeGreaterThan(0);
    for (const p of content.prompts) {
      for (const field of ['title', 'question', 'why', 'follow_up']) {
        expect(String(p[field].en).trim().length).toBeGreaterThan(0);
        expect(String(p[field].zh).trim().length).toBeGreaterThan(0);
      }
    }
  });
});

// ═══ ④ 屏与组件的接线（源码扫描，不是行为测试） ═══

describe('④ ReflectScreen：入口、本地拦空、版本过期、草稿路不带溯源', () => {
  const screen = read(SCREEN);

  it('入口按钮存在且可点（accessibilityRole），点开面板并记一次 open', () => {
    expect(screen).toMatch(/import PromptLibrarySheet from '\.\.\/components\/PromptLibrarySheet'/);
    expect(screen).toContain("t('reflect.promptEntry')");
    expect(screen).toMatch(/promptBar[\s\S]{0,400}accessibilityRole="button"/);
    expect(screen).toContain("track('prompt_library_open'");
    expect(screen).toMatch(/setPromptVisible\(true\)/);
  });

  it('空白正文在本地拦住、不发请求（先 Alert 再 return）', () => {
    const fn = /const onSaveFromPrompt = async \(\) => \{[\s\S]*?\n  \};/.exec(screen);
    expect(fn).not.toBeNull();
    const body = fn![0];
    const guard = body.indexOf('saveableText(input)');
    const request = body.indexOf('persistReflection(');
    expect(guard).toBeGreaterThanOrEqual(0);
    expect(request).toBeGreaterThan(guard); // 判空在请求之前
    expect(body).toContain("t('reflect.promptBlank')");
    expect(body).toMatch(/if \(!text\) \{\s*Alert\.alert\([\s\S]*?\);\s*return;\s*\}/);
  });

  it('两条保存出口收成 persistReflection，漏斗节点只有一个发射点', () => {
    // KG-26 把重复的那段（存本地 + 报节点）收进一个 helper。发射点分两处时，
    // 任何一处漏改就只统计到一条路径，而这一格数的是「第一条真的存进反思记录」。
    const fromPrompt = /const onSaveFromPrompt = async \(\) => \{[\s\S]*?\n  \};/.exec(screen)![0];
    const fromDraft = /const onSaveDraft = async \(msg: Message\) => \{[\s\S]*?\n  \};/.exec(screen)![0];
    expect(fromPrompt).toContain('traceabilityFor(activePrompt.id, promptVersion)');
    expect(fromPrompt).toContain("track('prompt_saved'");
    expect(fromPrompt).toContain('persistReflection(text, null, traceabilityFor(');
    expect(fromDraft).toContain('persistReflection(msg.content, msg.relatedMemoryIds)');
    expect((screen.match(/trackFunnelOnce\('first_reflection_saved'\)/g) || []).length).toBe(1);
    const helper = /const persistReflection = async \([\s\S]*?\n  \};/.exec(screen);
    expect(helper).not.toBeNull();
    expect(helper![0]).toContain('await saveReflection(');
    expect(helper![0]).toContain('cacheReflectionLocally(saved)');
  });

  it('40001（提示库版本过期）清掉选中，不让用户对着同一条重试', () => {
    const body = /const onSaveFromPrompt = async \(\) => \{[\s\S]*?\n  \};/.exec(screen)![0];
    expect(screen).toMatch(/const CODE_INVALID_PARAMS = 40001;/);
    // 另一侧从后端 errors.py 取，不在这里写死第二个 40001
    const py = readRepo('backend/core/errors.py');
    expect(/INVALID_PARAMS = (\d+)/.exec(py)![1]).toBe('40001');
    expect(body).toContain('e.code === CODE_INVALID_PARAMS');
    expect(body).toContain("t('reflect.promptStale')");
    const clearBranch = body.slice(body.indexOf('CODE_INVALID_PARAMS'));
    expect(clearBranch).toMatch(/setActivePrompt\(null\)[\s\S]*setPromptVersion\(''\)/);
  });

  it('AI 草稿那条路不吃提示溯源（草稿不是照提示写的）', () => {
    const body = /const onSaveDraft = async \(msg: Message\) => \{[\s\S]*?\n  \};/.exec(screen)![0];
    // 只传两个位置参数：第三个（promptMeta）缺席，上送体就不会带 prompt_id/version。
    expect(body).toContain('persistReflection(msg.content, msg.relatedMemoryIds)');
    expect(body).not.toMatch(/persistReflection\([^)]*,[^)]*,/);
    expect(body).not.toContain('traceabilityFor');
    // 证人另一侧：这条路上真的有人调用了带溯源的版本，否则「第三参缺席」只是整条链没接。
    expect(screen).toMatch(/persistReflection\(text, null, traceabilityFor\(/);
  });

  it('换提示不覆盖用户已经动过的正文：只在原句还是上一条提示时替换', () => {
    const body = /const applyPrompt = \(prompt: ReflectionPrompt[\s\S]*?\n  \};/.exec(screen)![0];
    expect(body).toContain('base === activePrompt.question.trim()');
    expect(body).toMatch(/return base \? base : prompt\.question;/);
    expect(body).toContain("track('prompt_selected'");
  });

  it('面板挂在屏上，currentId 与 version 都从屏传给面板', () => {
    expect(screen).toMatch(/<PromptLibrarySheet[\s\S]*?visible=\{promptVisible\}/);
    expect(screen).toMatch(/currentId=\{activePrompt\?\.id \?\? null\}/);
    expect(screen).toMatch(/onUse=\{\(prompt, version\) => applyPrompt\(prompt, version, 'select'\)\}/);
    expect(screen).toMatch(/onSwap=\{\(prompt, version\) => applyPrompt\(prompt, version, 'swap'\)\}/);
  });

  it('「存为反思」按钮在保存中禁用并改文案（连点不重复入库）', () => {
    expect(screen).toMatch(/disabled=\{promptSaving\}/);
    expect(screen).toMatch(/promptSaving \? t\('common\.saving'\) : t\('reflect\.promptSave'\)/);
  });
});

describe('⑤ PromptLibrarySheet：三态、可访问性、逻辑归模块', () => {
  const sheet = read(SHEET);

  it('三态齐全：spinner / 失败+重试 / 空态，各有各的文案键', () => {
    expect(sheet).toContain('<ActivityIndicator');
    expect(sheet).toContain("t('reflect.promptLoadFailed')");
    expect(sheet).toContain("t('common.retry')");
    expect(sheet).toContain('onPress={() => void load()}');
    expect(sheet).toContain("t('reflect.promptEmpty')");
  });

  it('每条可交互控件都声明 accessibilityRole="button"', () => {
    const buttons = (sheet.match(/<TouchableOpacity\b/g) || []).length;
    const roles = (sheet.match(/accessibilityRole="button"/g) || []).length;
    expect(buttons).toBeGreaterThanOrEqual(5);
    expect(roles).toBe(buttons);
  });

  it('簇 tab 用 accessibilityState.selected 表达选中态', () => {
    expect(sheet).toContain('accessibilityState={{ selected: cluster === null }}');
    expect(sheet).toMatch(/accessibilityState=\{\{ selected: cluster === c\.key \}\}/);
  });

  it('面板自己不算换一条、不分组：逻辑都在 reflectionPrompts.ts', () => {
    for (const fnName of ['groupByCluster', 'nextPrompt', 'showsFallbackNotice']) {
      expect(sheet).toContain(`from '../services/reflectionPrompts'`);
      expect(sheet).toContain(fnName);
      const defined = new RegExp(`function ${fnName}|const ${fnName} =`).exec(sheet);
      expect(defined).toBeNull();
    }
    expect(read(MODULE)).toMatch(/export function nextPrompt/);
  });

  it('库里不许出现硬编码的提示 id（内容只从服务端取）', () => {
    expect(sheet).not.toMatch(/rp-\d{3}/);
    expect(read(SCREEN)).not.toMatch(/rp-\d{3}/);
    expect(read(API)).not.toMatch(/rp-\d{3}/);
  });

  it('取数只在首次打开时发生，切簇走本地分组', () => {
    expect(sheet).toMatch(/if \(visible && !payload && !loading\) void load\(\);/);
    expect(sheet).toMatch(/const shown = cluster \? groups\.filter\(\(g\) => g\.meta\.key === cluster\) : groups;/);
  });
});

describe('⑥ 新事件的属性键在后端白名单里也有（否则出不了网）', () => {
  const screen = read(SCREEN);
  const py = readRepo('backend/services/analytics_service.py');
  const frontList = /const ALLOWED_PROPERTY_KEYS = new Set\(\[([\s\S]*?)\]\)/.exec(read(FRONT_ANALYTICS));
  expect(frontList).not.toBeNull();
  const tsAllow = new Set([...frontList![1].matchAll(/'([a-z_]+)'/g)].map((m) => m[1]));

  function keysOf(callText: string): string[] {
    return [...callText.matchAll(/([a-z_]+):/g)].map((m) => m[1]);
  }

  it('prompt_library_open / prompt_selected / prompt_saved 用的键都在两份白名单里', () => {
    const calls = [...screen.matchAll(/track\('(prompt_[a-z_]+)', (\{[^}]*\})\)/g)];
    const names = calls.map((m) => m[1]);
    expect(names).toContain('prompt_library_open');
    expect(names).toContain('prompt_selected');
    expect(names).toContain('prompt_saved');
    for (const call of calls) {
      for (const key of keysOf(call[2])) {
        expect(tsAllow.has(key)).toBe(true);
        expect(py).toContain(`"${key}"`);
      }
    }
  });

  it('feature 值固定为 prompt_library（分析平台按它切这一条路）', () => {
    expect((screen.match(/feature: 'prompt_library'/g) || []).length).toBeGreaterThanOrEqual(3);
  });
});
