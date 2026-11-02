/**
 * KG-28 三层次情绪分析 · 前端闸（2026-11-02）。
 *
 * 面板本身在本机 jest 的 node 环境里渲染不了（`.tsx` 只能做源码扫描），所以判据按
 * 「会各自漂移的地方」分组，不假装是行为测试：
 * ① 纯逻辑（入口开不开、四个业务码怎么答、额度读数、「这不像我」正文怎么拼、埋点能带哪些键）
 *    ——全在 `src/services/moodAnalysis.ts`，这里直接测函数。
 * ② 三档参数与后端字面量对齐——前端告诉用户「探索档看 7 天、要 1 条原话」，后端按自己的表判，
 *    两边各写各的就会出现「前端说给 2 条替代解释、后端只放 1 条」。这一组**两侧不同源**：
 *    一侧是 TS 常量，一侧是正则从 `backend/services/mood_analysis_service.py` 抠出来的 dict。
 * ③ 15 语言 × 30 键——漏一语言、留空、占位符丢一个、把 KG-23/KG-24 刚删掉的绝对化说法
 *    又写回来、`correctionPrefix` 的冒号分隔符没了，都要红。
 * ④ 契约字段名与 `backend/models/schemas.py` 逐字对（改名不报错，只会静默显空）。
 * ⑤ 面板与 HomeScreen 的接线（危机不给保存、重试复用幂等键、正文不上分析面）。
 *
 * 后端语义（逐字引用校验、降级、危机排配额之前、开关 40402）在
 * backend/tests/test_mood_analysis.py；路由落在 py-api 的取证在 backend/tests/test_k8s_manifests.py。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';
import {
  DEPTH_ORDER,
  DEPTH_SPECS,
  MOOD_ANALYSIS_EVENTS,
  analysisTrackProps,
  asDepth,
  canRetryAfter,
  correctionContent,
  depthSpec,
  errorKindForCode,
  isActionableElsewhere,
  isQuotaExhausted,
  quotaRemaining,
  resultOf,
  savableAnalysisText,
  showMoodAnalysisEntry,
} from '../src/services/moodAnalysis';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const SERVICE = 'src/services/moodAnalysis.ts';
const SHEET = 'src/components/MoodAnalysisSheet.tsx';
const SCREEN = 'src/screens/HomeScreen.tsx';
const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const FRONT_ANALYTICS = 'src/services/analytics.ts';
const BACKEND_SERVICE = 'backend/services/mood_analysis_service.py';
const BACKEND_SCHEMAS = 'backend/models/schemas.py';
const BACKEND_ERRORS = 'backend/core/errors.py';
const BACKEND_ANALYTICS = 'backend/services/analytics_service.py';
const OPENAPI = 'contracts/openapi.json';

const COPY_KEYS = [
  'entry', 'entryDesc', 'sheetTitle', 'sheetSubtitle',
  'light', 'lightDesc', 'explore', 'exploreDesc', 'deep', 'deepDesc',
  'focus', 'start', 'analyzing',
  'patternTitle', 'alternativesTitle', 'evidenceTitle', 'evidenceNote',
  'confidence', 'degradedNote', 'notLikeMe', 'correctionHint', 'correctionPrefix',
  'saveAsReflection', 'saved', 'remaining', 'quotaDone',
  'needConsent', 'needCheckin', 'failed', 'viewSources',
] as const;

const LANGUAGES = Object.keys(resources);

/** 阿拉伯数字与高棉数字（km 用 ៧/៣០）统一成 ASCII，否则「文案里写了 7 天」这条没法逐语言判。 */
function digitsOf(text: string): string {
  return text.replace(/[0-9\u17E0-\u17E9]/g, (ch) => {
    const khmer = '\u17E0\u17E1\u17E2\u17E3\u17E4\u17E5\u17E6\u17E7\u17E8\u17E9'.indexOf(ch);
    return khmer >= 0 ? String(khmer) : ch;
  });
}

// ═══ ① 纯逻辑 ═══

describe('① 纯逻辑：入口、四码、额度、纠正正文、埋点组包', () => {
  it('DEPTH_ORDER 就是后端 Literal 的三个值，且与 DEPTH_SPECS 的键一一对应', () => {
    expect(DEPTH_ORDER).toEqual(['light', 'explore', 'deep']);
    expect(Object.keys(DEPTH_SPECS).sort()).toEqual([...DEPTH_ORDER].sort());
    expect(depthSpec('deep')).toBe(DEPTH_SPECS.deep);
  });

  it('asDepth 只认那三个值，其余一律回落 light（含空串、大小写、旧值）', () => {
    expect(asDepth('light')).toBe('light');
    expect(asDepth('explore')).toBe('explore');
    expect(asDepth('deep')).toBe('deep');
    for (const junk of ['LIGHT', 'Deep', 'deep_dive', 'medium', '', '   ', undefined, null, 0, 3, {}, []]) {
      expect(asDepth(junk)).toBe('light');
    }
  });

  it('showMoodAnalysisEntry 只认真值：缺 features（升级前的本地缓存）与 false 都是关', () => {
    // 这一格判错的后果是「开关关着（端点 40402）时入口还在」——用户点了才拿到错误。
    expect(showMoodAnalysisEntry(undefined)).toBe(false);
    expect(showMoodAnalysisEntry(null)).toBe(false);
    expect(showMoodAnalysisEntry({})).toBe(false);
    expect(showMoodAnalysisEntry({ mood_analysis_depths: undefined })).toBe(false);
    expect(showMoodAnalysisEntry({ mood_analysis_depths: false })).toBe(false);
    // 正向证人：整组判据不是恒假
    expect(showMoodAnalysisEntry({ mood_analysis_depths: true })).toBe(true);
  });

  it('errorKindForCode：四个业务码各有答复，其余（含没有 code）都算网络', () => {
    expect(errorKindForCode(40001)).toBe('invalid');
    expect(errorKindForCode(40301)).toBe('quota');
    expect(errorKindForCode(40303)).toBe('consent');
    expect(errorKindForCode(40402)).toBe('no_checkin');
    for (const other of [500, 404, 0, undefined, null, '40301', NaN]) {
      expect(errorKindForCode(other)).toBe('network');
    }
  });

  it('四个业务码的字面量在后端 errors.py 里就是它（两侧不同源）', () => {
    const py = readRepo(BACKEND_ERRORS);
    const literal = (name: string) => new RegExp(`${name} = (\\d+)`).exec(py)![1];
    expect(literal('INVALID_PARAMS')).toBe('40001');
    expect(literal('QUOTA_EXCEEDED')).toBe('40301');
    expect(literal('AI_CONSENT_REQUIRED')).toBe('40303');
    expect(literal('RESOURCE_NOT_FOUND')).toBe('40402');
  });

  it('可重试的只有网络那一格；额度/同意/没签到重试是同一条拒绝', () => {
    expect(canRetryAfter('network')).toBe(true);
    for (const kind of ['invalid', 'consent', 'quota', 'no_checkin'] as const) {
      expect(canRetryAfter(kind)).toBe(false);
    }
  });

  it('要跳出面板去办的三格（去同意/去看额度/先去签到）', () => {
    expect(isActionableElsewhere('consent')).toBe(true);
    expect(isActionableElsewhere('quota')).toBe(true);
    expect(isActionableElsewhere('no_checkin')).toBe(true);
    expect(isActionableElsewhere('invalid')).toBe(false);
    expect(isActionableElsewhere('network')).toBe(false);
  });

  it('额度读数：没有计数器返回 null（危机出口不显示额度行），负数夹到 0', () => {
    expect(quotaRemaining(undefined)).toBeNull();
    expect(quotaRemaining(null)).toBeNull();
    expect(quotaRemaining({ used: 0, limit: 1 })).toBe(1);
    expect(quotaRemaining({ used: 1, limit: 1 })).toBe(0);
    expect(quotaRemaining({ used: 5, limit: 1 })).toBe(0);
    expect(isQuotaExhausted(null)).toBe(false);
    expect(isQuotaExhausted({ used: 0, limit: 1 })).toBe(false);
    expect(isQuotaExhausted({ used: 1, limit: 1 })).toBe(true);
  });

  it('correctionContent 保留文案自带的分隔符，正文裁空白；一个字没写就不产出', () => {
    // 分隔符是每个语言自己定的：拉丁系冒号后带一个空格，中日韩全角冒号不带。代码不猜。
    expect(correctionContent('This isn\'t me: ', '   其实是工作的事  ')).toBe('This isn\'t me: 其实是工作的事');
    expect(correctionContent('这不像我：', '不是这样的')).toBe('这不像我：不是这样的');
    expect(correctionContent('这不像我：', '   ')).toBeNull();
    expect(correctionContent('这不像我：', '')).toBeNull();
    expect(correctionContent('这不像我：', undefined as unknown as string)).toBeNull();
  });

  it('savableAnalysisText：空 summary 不存空壳反思', () => {
    expect(savableAnalysisText('  你今天两次写到「喘不过气」。  ')).toBe('你今天两次写到「喘不过气」。');
    for (const blank of ['', '   ', '\n\t', null, undefined]) {
      expect(savableAnalysisText(blank as string | null | undefined)).toBeNull();
    }
  });

  it('resultOf 与分析契约的 safety_status 同名，其余算 failed', () => {
    for (const s of ['ok', 'degraded', 'crisis']) expect(resultOf(s)).toBe(s);
    for (const other of [null, undefined, '', 'ok-ish', 'crisis ']) expect(resultOf(other)).toBe('failed');
  });

  it('analysisTrackProps 把内部键名翻成线上键名，且只带传了值的键', () => {
    expect(analysisTrackProps({ depth: 'explore', result: 'ok', durationMs: 812 }))
      .toEqual({ feature: 'mood_analysis', action: 'explore', result: 'ok', duration_ms: 812 });
    expect(analysisTrackProps({ depth: 'deep', count: 4 })).toEqual({ feature: 'mood_analysis', action: 'deep', count: 4 });
    expect(analysisTrackProps({ reason: 'network' })).toEqual({ feature: 'mood_analysis', reason: 'network' });
    expect(analysisTrackProps({})).toEqual({ feature: 'mood_analysis' });
    expect(analysisTrackProps({ depth: 'light' }, 'prompt_library')).toEqual({ feature: 'prompt_library', action: 'light' });
  });

  it('埋点组包函数本身不吃正文：值里出现 excerpt/focus 这种键名也组不出去', () => {
    const polluted = analysisTrackProps({
      depth: 'light',
      excerpt: '我今天很想哭',
      focus: '我老婆',
      content: '整段反思正文',
    } as never);
    expect(Object.keys(polluted)).toEqual(['feature', 'action']);
  });

  it('五个事件名都带 mood_analysis 前缀且互不相同', () => {
    const names = Object.values(MOOD_ANALYSIS_EVENTS);
    expect(new Set(names).size).toBe(names.length);
    for (const name of names) expect(name).toMatch(/^mood_analysis_[a-z_]+$/);
  });
});

// ═══ ② 三档参数与后端同表 ═══

describe('② DEPTH_SPECS 与后端三档表逐格对齐（改了后端没改前端当场红）', () => {
  const py = readRepo(BACKEND_SERVICE);

  function backendDict(decl: string): Record<string, number> {
    const line = new RegExp(`${decl}: Dict\\[str, int\\] = \\{([^}]*)\\}`).exec(py);
    expect(line).not.toBeNull();
    const out: Record<string, number> = {};
    for (const pair of line![1].matchAll(/"([a-z]+)": (\d+)/g)) out[pair[1]] = Number(pair[2]);
    return out;
  }

  it('窗口天数／最多取几段原文／最少几条独立来源／最多几条替代解释 四格全对', () => {
    const table: [string, keyof typeof DEPTH_SPECS.deep][] = [
      ['DEPTH_WINDOW_DAYS', 'windowDays'],
      ['DEPTH_MATERIAL_LIMIT', 'maxMaterials'],
      ['DEPTH_MIN_SOURCES', 'minEvidence'],
      ['DEPTH_MAX_ALTERNATIVES', 'maxAlternatives'],
    ];
    for (const [decl, field] of table) {
      const back = backendDict(decl);
      expect(Object.keys(back).sort()).toEqual([...DEPTH_ORDER].sort());
      for (const depth of DEPTH_ORDER) {
        expect(DEPTH_SPECS[depth][field] as number).toBe(back[depth]);
      }
    }
  });

  it('三档是由浅入深（等值即漂移）：窗口、材料数、最少来源、替代解释都不许倒退', () => {
    for (const field of ['windowDays', 'maxMaterials', 'minEvidence', 'maxAlternatives'] as const) {
      const [l, e, d] = DEPTH_ORDER.map((k) => DEPTH_SPECS[k][field]);
      expect(l <= e && e <= d).toBe(true);
    }
    // light 按契约不出结论：minEvidence/maxAlternatives 必须都是 0
    expect(DEPTH_SPECS.light.minEvidence).toBe(0);
    expect(DEPTH_SPECS.light.maxAlternatives).toBe(0);
    expect(DEPTH_SPECS.deep.minEvidence).toBeGreaterThanOrEqual(2);
  });

  it('后端引用长度／证据条数上限与前端展示假设不冲突（截断只发生在后端）', () => {
    const excerptMax = /EXCERPT_MAX_CHARS = (\d+)/.exec(py);
    const maxEvidence = /MAX_EVIDENCE_ITEMS = (\d+)/.exec(py);
    expect(excerptMax).not.toBeNull();
    expect(maxEvidence).not.toBeNull();
    // 面板折叠时切到 60 字符再展开：折叠长度必须小于服务端给的整段上限，
    // 否则「点开看原话」这一步就没有意义（展开的还是被裁过的句子）。
    expect(Number(excerptMax![1])).toBeGreaterThan(60);
    expect(DEPTH_SPECS.deep.maxMaterials).toBeGreaterThanOrEqual(Number(maxEvidence![1]));
  });
});

// ═══ ③ 15 语言 × 31 键 ═══

describe('③ 文案：15 语言都在、不留空、分隔符与占位符不丢、不写回绝对化承诺', () => {
  const flat: Record<string, string> = {};
  for (const lang of LANGUAGES) {
    const block = (resources as any)[lang].translation.moodAnalysis;
    expect(block).toBeDefined();
    for (const key of COPY_KEYS) flat[`${lang}.${key}`] = String(block[key] ?? '');
  }

  it('15 语言 × 30 键齐全且非空', () => {
    expect(LANGUAGES.length).toBe(15);
    expect(COPY_KEYS.length).toBe(30);
    // 证人另一侧：这份键清单就是 locale 里的那一份（加了键没进这张表 ⇒ 这里红）
    const enKeys = Object.keys((resources as any).en.translation.moodAnalysis);
    expect([...COPY_KEYS].sort()).toEqual([...enKeys].sort());
    for (const lang of LANGUAGES) {
      for (const key of COPY_KEYS) {
        expect(flat[`${lang}.${key}`].trim().length).toBeGreaterThan(0);
      }
    }
  });

  it('除 en 外没有一格是逐字照抄英文', () => {
    for (const lang of LANGUAGES) {
      if (lang === 'en') continue;
      for (const key of COPY_KEYS) expect(flat[`${lang}.${key}`]).not.toBe(flat[`en.${key}`]);
    }
  });

  it('两个带插值的键每语言都带着自己的占位符（漏一个界面上就是 {{n}}）', () => {
    for (const lang of LANGUAGES) {
      expect(`${lang} ${flat[`${lang}.remaining`]}`).toMatch(/\{\{n\}\}/);
      expect(`${lang} ${flat[`${lang}.confidence`]}`).toMatch(/\{\{percent\}\}/);
    }
  });

  it('correctionPrefix 一定以冒号收尾（半角或全角，后可跟一个空格），且不出现双尾空格', () => {
    // 这一格会被拼进存下来的反思正文：分隔符丢了就是「这不像我其实不是这样」那种读不通的记录。
    for (const lang of LANGUAGES) {
      const value = flat[`${lang}.correctionPrefix`];
      expect(`${lang} ${value}`).toMatch(/[:：] ?$/);
      expect(value.endsWith('  ')).toBe(false);
    }
  });

  it('三档说明里的天数与 DEPTH_SPECS 一致（文案与参数各自漂移就会指错窗口）', () => {
    for (const lang of LANGUAGES) {
      expect(`${lang} ${digitsOf(flat[`${lang}.exploreDesc`])}`).toMatch(/7/);
      expect(`${lang} ${digitsOf(flat[`${lang}.deepDesc`])}`).toMatch(/30/);
      const light = digitsOf(flat[`${lang}.lightDesc`]);
      expect(`${lang} lightDesc=${light}`).not.toMatch(/7|30/);
    }
  });

  it('KG-23/KG-24 的禁词不许从情绪分析这条路回来', () => {
    const banned = [
      /unlimited/i, /guarantee/i, /forever/i, /all data/i, /cure/i, /therapy/i, /diagnos/i,
      /治愈/, /根治/, /诊断/, /保证/, /一定能/, /永远/, /全部数据/, /无限/, /治疗/,
    ];
    for (const [where, value] of Object.entries(flat)) {
      for (const re of banned) expect(`${where} ${value}`).not.toMatch(re);
    }
  });

  it('降级那一格必须说「证据不够」，不能写成成功', () => {
    // 这是这条产品线的脸面：文案本身如果承诺了模式，降级就变成骗人。
    for (const lang of ['en', 'zh']) {
      const value = flat[`${lang}.degradedNote`];
      expect(value).toMatch(lang === 'en' ? /not enough/i : /不够|不足/);
    }
  });

  it('evidenceNote 说的是「不是 AI 写的」，与后端 source_type 只有 memory/reflection 同口径', () => {
    expect(flat['en.evidenceNote']).toMatch(/not written by the AI/i);
    expect(flat['zh.evidenceNote']).toMatch(/不是|并非/);
  });
});

// ═══ ④ 契约字段名两侧逐字 ═══

describe('④ 分析契约字段名与 schemas.py 逐字一致（改名不报错，只会静默显空）', () => {
  const py = readRepo(BACKEND_SCHEMAS);
  const ts = read(TYPES);

  function pyFields(className: string): string[] {
    const body = new RegExp(`class ${className}\\(BaseModel\\):([\\s\\S]*?)\\n(?:\\n)?class `).exec(py);
    expect(body).not.toBeNull();
    return [...body![1].matchAll(/^ {4}([a-z_]+):/gm)].map((m) => m[1]);
  }

  function tsFields(iface: string): string[] {
    const body = new RegExp(`export interface ${iface} \\{([\\s\\S]*?)\\n\\}`).exec(ts);
    expect(body).not.toBeNull();
    return [...body![1].matchAll(/^ {2}([a-z_]+)\??:/gm)].map((m) => m[1]);
  }

  it('MoodAnalysisResponse／Evidence／Pattern／NextStep／Quota／Request 字段名逐字相同、顺序相同', () => {
    for (const name of [
      'MoodAnalysisResponse', 'MoodAnalysisEvidence', 'MoodAnalysisPattern',
      'MoodAnalysisNextStep', 'MoodAnalysisQuota', 'MoodAnalysisRequest',
    ] as const) {
      expect(tsFields(name)).toEqual(pyFields(name));
    }
  });

  it('depth／safety_status／next_step.action 的枚举两侧同集合', () => {
    const depthEnum = /MoodAnalysisDepth = Literal\[([^\]]*)\]/.exec(py);
    expect(depthEnum).not.toBeNull();
    expect(depthEnum![1].replace(/"/g, '').split(',').map((s) => s.trim())).toEqual([...DEPTH_ORDER]);
    const tsUnion = /export type MoodAnalysisDepth = ([^;]*);/.exec(ts);
    expect(tsUnion).not.toBeNull();
    expect(tsUnion![1].replace(/'/g, '').split('|').map((s) => s.trim())).toEqual([...DEPTH_ORDER]);

    const safety = /MoodSafetyStatus = Literal\[([^\]]*)\]/.exec(py);
    expect(safety).not.toBeNull();
    const safetyValues = safety![1].replace(/"/g, '').split(',').map((s) => s.trim());
    expect(safetyValues).toEqual(['ok', 'degraded', 'crisis']);
    const tsSafety = /export type MoodAnalysisSafety = ([^;]*);/.exec(ts);
    expect(tsSafety![1].replace(/'/g, '').split('|').map((s) => s.trim())).toEqual(safetyValues);

    const actions = /class MoodAnalysisNextStep\(BaseModel\):[\s\S]*?action: Literal\[([^\]]*)\]/.exec(py);
    expect(actions).not.toBeNull();
    const tsActions = /export interface MoodAnalysisNextStep \{[\s\S]*?action: ([^;]*);/.exec(ts);
    expect(tsActions![1].replace(/'/g, '').split('|').map((s) => s.trim()))
      .toEqual(actions![1].replace(/"/g, '').split(',').map((s) => s.trim()));
  });

  it('请求体刻意没有 checkin_id（入口只分析今天那条签到）', () => {
    expect(pyFields('MoodAnalysisRequest')).not.toContain('checkin_id');
    expect(tsFields('MoodAnalysisRequest')).not.toContain('checkin_id');
    expect(read(API)).not.toMatch(/checkin_id/);
  });

  it('features.mood_analysis_depths 与仪表盘 schema 同字', () => {
    expect(py).toContain('mood_analysis_depths: bool');
    expect(tsFields('GrowthFeatures')).toEqual(['mood_analysis_depths']);
  });

  it('openapi.json 里有 POST /api/v1/mood/analysis（前端调的这条确实在契约上）', () => {
    const contract = JSON.parse(readRepo(OPENAPI));
    const item = contract.paths?.['/api/v1/mood/analysis'];
    expect(item).toBeDefined();
    expect(Object.keys(item)).toEqual(['post']);
  });
});

// ═══ ⑤ 面板与 HomeScreen 接线（源码扫描） ═══

describe('⑤ MoodAnalysisSheet：危机不给保存、重试复用幂等键、正文不上分析面', () => {
  const sheet = read(SHEET);

  it('取数只走 api.ts 的 analyzeMood，正文只走 saveReflection（面板不自己拼 fetch）', () => {
    expect(sheet).toMatch(/import \{ analyzeMood, ApiError, saveReflection \} from '\.\.\/services\/api'/);
    expect(sheet).not.toMatch(/fetch\(/);
    expect(sheet).not.toContain('XMLHttpRequest');
  });

  it('analyzeMood 用上送体的 request_id 就是这一次意图的键，重试复用、换深度才换新键', () => {
    const run = /const run = useCallback\(\s*async \(chosen: MoodAnalysisDepth, idempotencyKey: string \| null\) => \{[\s\S]*?\n  \},/.exec(sheet);
    expect(run).not.toBeNull();
    expect(run![0]).toContain('const requestIdUsed = idempotencyKey ?? newTraceId();');
    expect(run![0]).toContain('request_id: requestIdUsed');
    // 只有没带键的（新意图）才写回 state——重试不许把自己变成一次新意图
    expect(run![0]).toContain('if (!idempotencyKey) setRequestId(requestIdUsed);');
    expect(sheet).toMatch(/setRequestId\(null\)/);
    const starts = (sheet.match(/void run\(depth, requestId\)/g) || []).length;
    expect(starts).toBe(2); // 开始按钮 + 网络失败重试
  });

  it('危机与降级是两种呈现：危机那条路径没有额度行、也没有保存/纠正出口', () => {
    expect(sheet).toMatch(/const crisis = result\?\.safety_status === 'crisis';/);
    expect(sheet).toMatch(/const canSave = !!result && !crisis && !busy;/);
    // 额度行与操作行都被 !crisis 挡在前面
    expect(sheet).toMatch(/\{!crisis && remaining !== null && \(/);
    expect(sheet).toMatch(/\{!crisis && \(\s*<View style=\{styles\.actionRow\}/);
    // 反向证人：非危机路径真的渲染了这两格（整段被删掉的话 !crisis 就是空守卫）
    expect(sheet).toContain("t('moodAnalysis.saveAsReflection')");
    expect(sheet).toContain("t('moodAnalysis.notLikeMe')");
    expect(sheet).toContain("t('moodAnalysis.remaining', { n: remaining })");
  });

  it('降级给的是那句「证据不够」的说明，不是把 pattern 卡片照常显示', () => {
    expect(sheet).toMatch(/result\.safety_status === 'degraded'[\s\S]{0,120}t\('moodAnalysis\.degradedNote'\)/);
    expect(sheet).toMatch(/\{!!result\.pattern && \(/);
  });

  it('埋点全部经 analysisTrackProps：用户正文、focus、excerpt 一个字都不进事件属性', () => {
    const calls = [...sheet.matchAll(/track\(([^;]*?);/gs)].map((m) => m[1]);
    expect(calls.length).toBeGreaterThanOrEqual(5);
    for (const call of calls) {
      // 事件名要么是这里的常量，要么由 persist 的 `event` 形参带入（下一格单独钉）
      expect(call).toMatch(/MOOD_ANALYSIS_EVENTS\.[a-zA-Z]+|^event, analysisTrackProps\(/);
      expect(call).toMatch(/analysisTrackProps\(|\{ feature: 'mood_analysis', action: 'open' \}/);
      for (const forbidden of ['focus', 'excerpt', 'correctionText', 'result.summary', 'pattern.description', 'summary']) {
        expect(call).not.toContain(forbidden);
      }
    }
    // 那两条保存出口的 event 实参都是 MOOD_ANALYSIS_EVENTS 里的常量，没有裸字符串
    expect(sheet).toMatch(/persist\(content, MOOD_ANALYSIS_EVENTS\.saved\)/);
    expect(sheet).toMatch(/saveCorrection[\s\S]{0,400}MOOD_ANALYSIS_EVENTS\.saved/);
    expect(sheet).not.toMatch(/track\('mood_analysis[a-z_]*'/);
  });

  it('原话只按服务端给的 excerpt 展示，面板不改写、不拼接进别的句子', () => {
    // 展开态给整段原话，折叠态只截断不重写：`excerpt.replace(...)` 那种「顺手清理一下」
    // 会让用户对照不上的字，而这条功能唯一对外承诺的就是「指得回你写的原话」。
    expect(sheet).toMatch(/\{openEvidenceId === ev\.source_id \? ev\.excerpt : /);
    expect(sheet).toMatch(/ev\.excerpt\.slice\(0, 60\)/);
    expect(sheet).not.toMatch(/excerpt\.replace\(/);
    expect(sheet).not.toMatch(/excerpt\.(toLowerCase|toUpperCase|trim)\(/);
  });

  it('四码答复文案与错误码一一对应，额度耗尽用专用键而不是「还剩 0 次」', () => {
    const map = /const errorText: Record<MoodAnalysisErrorKind, string> = \{([\s\S]*?)\n  \};/.exec(sheet);
    expect(map).not.toBeNull();
    expect(map![1]).toMatch(/consent: t\('moodAnalysis\.needConsent'\)/);
    expect(map![1]).toMatch(/quota: t\('moodAnalysis\.quotaDone'\)/);
    expect(map![1]).toMatch(/no_checkin: t\('moodAnalysis\.needCheckin'\)/);
    expect(map![1]).toMatch(/invalid: t\('moodAnalysis\.failed'\)/);
    expect(map![1]).toMatch(/network: t\('moodAnalysis\.failed'\)/);
    expect(map![1]).not.toMatch(/remaining/);
  });

  it('未同意（40303）走宿主去设置页：面板不套第二层 Modal（RN 叠 Modal 在 Android 不可靠）', () => {
    expect((sheet.match(/<Modal\b/g) || []).length).toBe(1);
    expect(sheet).toMatch(/errorKind === 'consent'[\s\S]{0,200}onPress=\{onOpenSettings\}/);
    expect(sheet).toMatch(/canRetryAfter\(errorKind\)[\s\S]{0,200}t\('common\.retry'\)/);
  });

  it('请求期间禁用：三档卡片、输入框、开始按钮都吃 busy', () => {
    expect(sheet).toMatch(/disabled=\{busy\}/);
    expect(sheet).toMatch(/editable=\{!busy\}/);
    expect(sheet).toMatch(/busy \? t\('moodAnalysis\.analyzing'\) : t\('moodAnalysis\.start'\)/);
    expect(sheet).toMatch(/accessibilityState=\{\{ selected: active, disabled: busy \}\}/);
  });

  it('保存中禁用并改文案，两条保存出口都带 source=mood_analysis 的来源标记', () => {
    expect(sheet).toContain("await saveReflection(content, null, { source: 'mood_analysis' });");
    expect(sheet).toMatch(/disabled=\{!correctionDraft \|\| saving\}/);
    expect(sheet).toMatch(/saving \? t\('common\.saving'\) : t\('moodAnalysis\.saveAsReflection'\)/);
    // 空纠正不发请求：correctionContent 返回 null 时先 return
    const saveCorrection = /const saveCorrection = \(\) => \{[\s\S]*?\n  \};/.exec(sheet);
    expect(saveCorrection).not.toBeNull();
    expect(saveCorrection![0]).toMatch(/if \(!content\) return;/);
  });

  it('每条可交互控件都声明 accessibilityRole="button"', () => {
    const buttons = (sheet.match(/<TouchableOpacity\b/g) || []).length;
    const roles = (sheet.match(/accessibilityRole="button"/g) || []).length;
    expect(buttons).toBeGreaterThanOrEqual(8);
    expect(roles).toBe(buttons);
  });

  it('判定都在 moodAnalysis.ts：面板里没有第二套三档参数、也没有本地重算额度', () => {
    for (const fn of ['quotaRemaining', 'correctionContent', 'analysisTrackProps', 'errorKindForCode']) {
      expect(sheet).toContain(`from '../services/moodAnalysis'`);
      expect(sheet).toContain(fn);
      expect(new RegExp(`function ${fn}|const ${fn} =`).test(sheet)).toBe(false);
    }
    expect(sheet).not.toMatch(/windowDays|maxMaterials|maxAlternatives/);
  });
});

describe('⑥ HomeScreen：入口的两个条件与面板挂载', () => {
  const screen = read(SCREEN);

  it('入口同时要求「开关为真」和「今天已签到」，缺一个都不出现', () => {
    expect(screen).toMatch(/import \{ MOOD_ANALYSIS_EVENTS, showMoodAnalysisEntry \} from '\.\.\/services\/moodAnalysis'/);
    expect(screen).toMatch(/import MoodAnalysisSheet from '\.\.\/components\/MoodAnalysisSheet'/);
    expect(screen).toMatch(/showMoodAnalysisEntry\(dashboard\?\.features\) && !!dashboard\?\.today_mood && \(/);
    expect(screen).toContain("t('moodAnalysis.entry')");
    expect(screen).toContain("t('moodAnalysis.entryDesc')");
    // 反向证人：features 这一格不是恒真判据（本地旧缓存没有 features ⇒ 不显示）
    expect(read(SERVICE)).toMatch(/features\?\.mood_analysis_depths === true/);
  });

  it('入口点击只带 feature/action 两个白名单键，并打开面板', () => {
    expect(screen).toMatch(/track\(MOOD_ANALYSIS_EVENTS\.entry, \{ feature: 'mood_analysis', action: 'open' \}\)/);
    expect(screen).toMatch(/setAnalysisOpen\(true\)/);
    expect(screen).toMatch(/const \[analysisOpen, setAnalysisOpen\] = useState\(false\);/);
  });

  it('面板挂在屏上，两个跳出目标都是真实路由（Profile 是 Tab，Memories 是 Stack）', () => {
    expect(screen).toMatch(/<MoodAnalysisSheet[\s\S]*?visible=\{analysisOpen\}/);
    expect(screen).toMatch(/onClose=\{\(\) => setAnalysisOpen\(false\)\}/);
    expect(screen).toMatch(/onOpenSettings=\{\(\) => \{\s*setAnalysisOpen\(false\);\s*navigation\.navigate\('Tabs', \{ screen: 'Profile' \}\);/);
    expect(screen).toMatch(/onOpenMemories=\{\(\) => \{\s*setAnalysisOpen\(false\);\s*navigation\.navigate\('Memories'\);/);
    const app = read('App.tsx');
    expect(app).toMatch(/name="Memories"/);
    expect(app).toMatch(/<Tab\.Screen name="Profile"/);
  });

  it('面板里那两个跳出都先关面板（RN 里 Modal 盖着导航会看不见新页）', () => {
    expect(screen).toMatch(/onOpenSettings=\{\(\) => \{[\s\S]{0,120}setAnalysisOpen\(false\);[\s\S]{0,120}navigate/);
    expect(screen).toMatch(/onOpenMemories=\{\(\) => \{[\s\S]{0,120}setAnalysisOpen\(false\);[\s\S]{0,120}navigate/);
  });
});

describe('⑦ api.ts 调用点：路径字面量紧贴 request<>，方法是 POST', () => {
  const api = read(API);

  it('analyzeMood 打的是 /mood/analysis（N8 上这条留在 py-api，路径不许加前缀）', () => {
    const fn = /export async function analyzeMood\([\s\S]*?\n\}/.exec(api);
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/request<MoodAnalysisResponse>\(\s*'\/mood\/analysis'/);
    expect(fn![0]).toContain("method: 'POST'");
    expect(fn![0]).toContain('JSON.stringify(body)');
  });

  it('反思来源类型把 mood_analysis 也认作 AI 起草的一档（与后端排除集同表）', () => {
    expect(api).toMatch(/export type ReflectionOrigin = \{ source: 'draft' \| 'mood_analysis' \};/);
    expect(api).toMatch(/promptMeta\?: PromptTraceability \| ReflectionOrigin \| null/);
    // 另一侧从服务源码取，不在这里写死第二份排除集
    const py = readRepo(BACKEND_SERVICE);
    expect(py).toMatch(/_reflection_source\(r\) in \("draft", "mood_analysis"\)/);
  });
});

describe('⑧ 事件属性键在后端白名单里也有（否则出不了网）', () => {
  const py = readRepo(BACKEND_ANALYTICS);
  const frontList = /const ALLOWED_PROPERTY_KEYS = new Set\(\[([\s\S]*?)\]\)/.exec(read(FRONT_ANALYTICS));
  expect(frontList).not.toBeNull();
  const tsAllow = new Set([...frontList![1].matchAll(/'([a-z_]+)'/g)].map((m) => m[1]));

  it('analysisTrackProps 组出去的每个键都在两份白名单里', () => {
    const mapping = /const PROPERTY_BY_KEY: Record<keyof AnalysisTrackProps, string> = \{([\s\S]*?)\n\};/.exec(read(SERVICE));
    expect(mapping).not.toBeNull();
    const wireNames = [...mapping![1].matchAll(/: '([a-z_]+)'/g)].map((m) => m[1]);
    expect(wireNames.sort()).toEqual(['action', 'count', 'duration_ms', 'reason', 'result']);
    for (const name of [...wireNames, 'feature']) {
      expect(tsAllow.has(name)).toBe(true);
      expect(py).toContain(`"${name}"`);
    }
  });
});
