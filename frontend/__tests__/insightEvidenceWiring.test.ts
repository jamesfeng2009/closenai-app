/**
 * T3-04 接线闸：V3 洞察面（逐条可回溯的依据 + 状态徽章）。
 *
 * 手法沿用本仓其它接线闸（读源码文本＋正则切片）：RN 屏在 jest 里跑不起来，
 * 而这一面的坏法恰恰都是「看起来能跑」的那一类。六条判据各自对应一句谎话：
 *
 * ① 封装指向的 path 逐字（写了 `listInsights` 不等于打的是 `/api/v1/insights`）；
 * ② **字段名与 Go 的 json tag 逐字对账**——这是本闸的核心。前端把 `excerpt` 写成
 *    `text` 时 TS 不会报错，只是那一栏永远空着，用户看到的是「依据消失了」，
 *    而服务端一切 200。工单 §验收 那句「防解析不到即静默丢弃」要的就是这一格。
 *    判据两侧不同源：TS 侧读 `types/index.ts`，Go 侧读 `httpapi/*.go` 的 tag 字面量。
 * ③ `evidence_count === 0` 那一格**不许出现 statement**（闸 G-05 的 UI 面）：
 *    「灰一下」和「留个空壳」都还算把结论铺给了用户；
 * ④ 三张枚举表的键集**跟着 Go 的词表**（claim_type／source_type／状态徽章），
 *    前端悄悄多一档或少一档都会在 15 语言那一格红；
 * ⑤ 加载/空/失败/重试四态分开，且 `truncated` 与 `evidence_summary.count` 都消费
 *    （只说「5 条」不说「5/23」＝替后端把裁剪藏起来）；
 * ⑥ 旧周报卡的期间级依据仍在（ADR-003：新面不替换旧卡），请求出口仍然唯一。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const SCREEN = 'src/screens/InsightsScreen.tsx';
const GO_INSIGHTS = 'go-core/internal/httpapi/v3_insights.go';
const GO_EVIDENCE = 'go-core/internal/httpapi/handlers_evidence.go';
const GO_FEEDBACK = 'go-core/internal/httpapi/v3_feedback.go';
const GO_POLICY = 'go-core/internal/domain/policy.go';
const GO_SCORE = 'go-core/internal/domain/evidence_score.go';
const GO_BROWSE = 'go-core/internal/domain/insight_browse.go';
const GO_READ = 'go-core/internal/domain/evidence_read.go';

/** 切出 Go 里某个 struct 的 json tag 名（按声明顺序）。 */
function goWire(...files: string[]): (structName: string) => string[] {
  const src = files.map(readRepo).join('\n');
  return (structName: string) => {
    const body = new RegExp(`type ${structName} struct \\{([\\s\\S]*?)\\n\\}`).exec(src);
    expect(body).not.toBeNull();
    return [...body![1].matchAll(/json:"([^",]+)/g)].map((m) => m[1]);
  };
}

/** 切出 TS 里某个 interface 的字段名（按声明顺序，忽略注释行）。 */
function tsFields(interfaceName: string): string[] {
  const body = new RegExp(
    `export interface ${interfaceName} \\{([\\s\\S]*?)\\n\\}`
  ).exec(read(TYPES));
  expect(body).not.toBeNull();
  return [...body![1].matchAll(/^\s{2}([a-z_][a-z0-9_]*):/gm)].map((m) => m[1]);
}

/** 抽取 Go 源里 `Name = []string{ "a", "b" }` 的字面量集合（按声明顺序）。 */
function goStringList(file: string, decl: string): string[] {
  const m = new RegExp(`${decl}\\s*=\\s*\\[\\]string\\{([^}]*)\\}`).exec(readRepo(file));
  expect(m).not.toBeNull();
  return [...m![1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

/**
 * 抽取读面「真能返回的状态集合」。
 * `InsightReturnableStatuses` 是 `append(append(...), "corrected", ...)` 写出来的
 * （insight_browse.go:32-34），不是字面量表，所以按「基础集合 ＋ 追加字面量」两段取。
 */
function goReturnableStatuses(): string[] {
  const src = readRepo(GO_BROWSE);
  const m = /var InsightReturnableStatuses = append\(\s*append\(\[\]string\{\}, (\w+)\.\.\.\),([^)]*)\)/.exec(src);
  expect(m).not.toBeNull();
  expect(m![1]).toBe('InsightVisibleStatuses');
  return [...goStringList(GO_READ, 'InsightVisibleStatuses'),
          ...[...m![2].matchAll(/"([^"]+)"/g)].map((x) => x[1])];
}

/** 抽取 SourceKind 常量的字面值（八类来源，03 §5）。 */
function goSourceKinds(): string[] {
  const src = readRepo(GO_SCORE);
  const m = /const \(([\s\S]*?)\n\)/.exec(src);
  expect(m).not.toBeNull();
  return [...m![1].matchAll(/SourceKind = "([^"]+)"/g)].map((x) => x[1]);
}

/** i18n 子表的键名（按 en 源表）。 */
function enSubTable(key: string): string[] {
  const tbl = (resources as any).en.translation.insights[key];
  expect(tbl).toBeInstanceOf(Object);
  return Object.keys(tbl);
}

const goWireOf = goWire(GO_INSIGHTS, GO_EVIDENCE, GO_FEEDBACK);

// ── ① 三条封装打的 path 逐字 ─────────────────────────────────────────────────

describe('① 三条新封装指向契约里那三条 path', () => {
  it('listInsights：GET /api/v1/insights，三个查询参数名逐字', () => {
    const src = read(API);
    const body = new RegExp(
      'export async function listInsights\\([\\s\\S]*?\\n}'
    ).exec(src);
    expect(body).not.toBeNull();
    for (const p of ['status=', 'limit=', 'cursor=']) expect(body![0]).toContain(p);
    // 路径字面量必须是 `('/insights' + …)` 这种「第一段是字面量」的写法：
    // 写成模板串 `/insights${tail}` 时契约扫描器归一不出这条端点，
    // 台账会把一个真在用的端点登记成「前端无调用点」（实测踩过）。
    expect(body![0]).toContain("request<InsightListPage>('/insights' + tail)");
    // GET 不带 method：写了 method:'GET' 不算错，但也不算这条判据——这里要的是
    // 「这条封装根本没有把 GET 变成 POST 的余地」
    expect(body![0]).not.toContain('method:');
  });

  it('getInsight：id 走 path 且编码（不编码时一颗带 ? 的 id 会打到另一条端点）', () => {
    expect(read(API)).toContain(
      'request<InsightDetail>(`/insights/${encodeURIComponent(insightId)}`)'
    );
  });

  it('postInsightFeedback：feedback 后缀逐字，正文字段名跟 Go 的 tag 同字', () => {
    const body = new RegExp(
      'export async function postInsightFeedback\\([\\s\\S]*?\\n}'
    ).exec(read(API));
    expect(body).not.toBeNull();
    expect(body![0]).toContain('`/insights/${encodeURIComponent(insightId)}/feedback`');
    expect(body![0]).toContain("method: 'POST'");
    expect(body![0]).toContain('correction_text:');
    expect(body![0]).toContain('operation_id:');
    expect(body![0]).not.toMatch(/user_id/);
  });

  it('三条都在客户端契约登记表里在册（前端不许指向一条契约外的线）', () => {
    const md = readRepo('contracts/endpoints.md');
    expect(md).toContain('| GET | `/api/v1/insights` |');
    expect(md).toContain('| GET | `/api/v1/insights/{insight_id}` |');
    expect(md).toContain('| POST | `/api/v1/insights/{insight_id}/feedback` |');
  });
});

// ── ② 字段名与 Go 的 wire tag 逐字对账 ──────────────────────────────────────

describe('② 六个形状与 Go 的 json tag 逐字同集同序', () => {
  const pairs: [string, string][] = [
    ['v3InsightListItemWire', 'InsightListItem'],
    ['v3InsightDetailPayload', 'InsightDetail'],
    ['v3InsightListPayload', 'InsightListPage'],
    ['evidenceWire', 'InsightEvidenceItem'],
    ['sourceWire', 'InsightEvidenceSource'],
    ['memoryRefWire', 'InsightEvidenceMemoryRef'],
    ['summaryWire', 'InsightEvidenceSummary'],
    ['timeRangeWire', 'InsightTimeRange'],
    ['patternGroupWire', 'InsightPatternGroup'],
    ['v3FeedbackPayload', 'InsightFeedbackReceipt'],
  ];

  it.each(pairs)('%s ↔ %s', (goStruct, tsName) => {
    expect(tsFields(tsName)).toEqual(goWireOf(goStruct));
  });

  it('工单点名的两个类型名真的存在（改名＝§怎么做.1 的交付物消失）', () => {
    expect(read(TYPES)).toContain('export interface InsightEvidenceItem {');
    expect(read(TYPES)).toContain('export interface InsightDetail {');
  });

  it('反馈六态类型跟着 Go 的 FeedbackTypes（02:156-163）', () => {
    const src = readRepo('go-core/internal/domain/outcome.go');
    const m = /var FeedbackTypes = \[\]FeedbackType\{([\s\S]*?)\n\}/.exec(src);
    expect(m).not.toBeNull();
    const goTypes = [...m![1].matchAll(/Feedback\w+/g)].map((x) => x[0]);
    const wire = [...src.matchAll(/\t(Feedback\w+)\s+FeedbackType = "([^"]+)"/g)];
    expect(wire.map((x) => x[2])).toEqual([
      'confirm', 'reject', 'partially_confirm', 'correct', 'outdated', 'forget_from_model',
    ]);
    expect(goTypes).toHaveLength(6);
    const tsUnion = /export type InsightFeedbackType =([\s\S]*?);\n/.exec(read(TYPES));
    expect(tsUnion).not.toBeNull();
    expect([...tsUnion![1].matchAll(/'([^']+)'/g)].map((x) => x[1]).sort())
      .toEqual(wire.map((x) => x[2]).sort());
  });
});

// ── ③ 空证据不渲染结论（G-05 的 UI 面）──────────────────────────────────────

describe('③ evidence_count === 0 那一格里没有结论', () => {
  const src = read(SCREEN);

  it('判据逐字存在，且用的是列表带回来的 evidence_count', () => {
    expect(src).toMatch(/item\.evidence_count === 0 \? \(/);
  });

  it('那一格只有一句「证据不足」，不出现 statement／徽章／依据栏', () => {
    const at = src.indexOf('item.evidence_count === 0 ? (');
    expect(at).toBeGreaterThan(-1);
    const branch = src.slice(at, src.indexOf(') : (', at));
    expect(branch).toContain("t('insights.evidenceInsufficient')");
    for (const forbidden of ['item.statement', 'insights.claim.', 'insights.status.',
                             'insights.evidenceToggle', 'onToggleEvidence']) {
      expect(branch).not.toContain(forbidden);
    }
  });

  it('结论与徽章各消费列表行的字段，不是从详情里另取一份', () => {
    for (const f of ['item.statement', 'item.claim_type', 'item.status', 'item.confidence']) {
      expect(src).toContain(f);
    }
    // confidence 是 *float64：null 时不显示，而不是显示成 0%（0% 是一条断言，不是缺数据）
    expect(src).toMatch(/item\.confidence != null/);
    expect(src).toMatch(/Math\.round\(item\.confidence \* 100\)/);
  });

  it('依据行逐字消费 excerpt / observed_at / source.source_type / strength', () => {
    for (const f of ['ev.excerpt', 'ev.memory_ref?.excerpt', 'ev.observed_at',
                     'ev.source.source_type', 'ev.strength != null', 'ev.evidence_id']) {
      expect(src).toContain(f);
    }
    expect(src).toMatch(/t\(`insights\.sourceType\.\$\{ev\.source\.source_type\}`\)/);
    expect(src).toMatch(/new Date\(ev\.observed_at\)\.toLocaleDateString\(\)/);
  });
});

// ── ④ 三张枚举表跟着 Go 的词表，15 语言齐备 ─────────────────────────────────

describe('④ 徽章与标签的键集 = Go 的词表（两侧不同源）', () => {
  it('claim_type 标签键 == domain.AllowedClaimTypes（03 §14）', () => {
    expect(enSubTable('claim')).toEqual(goStringList(GO_POLICY, 'AllowedClaimTypes'));
  });

  it('source_type 标签键 == SourceKind 八类（03 §5）', () => {
    expect(enSubTable('sourceType')).toEqual(goSourceKinds());
  });

  it('状态徽章键 == 这一面真能返回的状态集合（N9 三态 ∪ 用户表态三态）', () => {
    expect(goStringList(GO_READ, 'InsightVisibleStatuses'))
      .toEqual(['published', 'acknowledged', 'confirmed']);
    expect(goReturnableStatuses())
      .toEqual(['published', 'acknowledged', 'confirmed', 'corrected', 'expired', 'dismissed']);
    expect(enSubTable('status')).toEqual(goReturnableStatuses());
  });

  const LANGUAGES = Object.keys(resources);

  it('语言数就是 15，漏一门下面那条会假绿', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)('%s：五档徽章＋标签＋区文案全部有值', (lng) => {
    const ins = (resources as any)[lng].translation.insights;
    for (const k of ['canonicalTitle', 'canonicalEmpty', 'evidenceInsufficient',
                     'evidenceToggle', 'evidenceTruncated', 'confidence']) {
      expect(typeof ins[k]).toBe('string');
      expect(ins[k].trim()).not.toBe('');
    }
    const sub = ['claim', 'status', 'sourceType'];
    for (const s of sub) {
      expect(Object.keys(ins[s]).sort()).toEqual(
        Object.keys((resources as any).en.translation.insights[s]).sort()
      );
      for (const v of Object.values(ins[s])) {
        expect(typeof v).toBe('string');
        expect((v as string).trim()).not.toBe('');
      }
    }
    // 占位符个数逐语言一致（少一个就是屏上裸花括号）
    expect(ins.evidenceToggle).toMatch('{{n}}');
    expect(ins.evidenceToggle).toMatch('{{arrow}}');
    expect(ins.evidenceTruncated).toMatch('{{shown}}');
    expect(ins.evidenceTruncated).toMatch('{{total}}');
    expect(ins.confidence).toMatch('{{value}}');
  });

  it('屏上用到的 insights.* 键在 en 表里都有（漏键＝界面渲染出裸键名）', () => {
    const ins = (resources as any).en.translation.insights;
    const has = (key: string): boolean => {
      if (typeof ins[key] === 'string') return true;
      // 复数键（如 chatCount）在表里只有 `_one`/`_other` 变体，没有基础键——
      // i18next 按 count 选类别，这里按同一套类别认。
      return ['one', 'two', 'few', 'many', 'other'].some(
        (c) => typeof ins[`${key}_${c}`] === 'string'
      );
    };
    const used = [...read(SCREEN).matchAll(/t\('insights\.([A-Za-z]+)'/g)].map((m) => m[1]);
    expect(used.length).toBeGreaterThan(0);
    expect(used.filter((k) => !has(k))).toEqual([]);
  });
});

// ── ⑤ 四态分离、5 条上限与 truncated ────────────────────────────────────────

describe('⑤ 加载/空/失败/重试四态分离，且裁剪不藏', () => {
  const src = read(SCREEN);

  it('洞察面自己有一套三态位，不复用周报那一个', () => {
    expect(src).toMatch(/const \[insights, setInsights\] = useState<InsightListItem\[\] \| null>\(null\);/);
    expect(src).toMatch(/const \[insightsLoading, setInsightsLoading\] = useState\(true\);/);
    expect(src).toMatch(/const \[insightsFailed, setInsightsFailed\] = useState\(false\);/);
    expect(src).not.toMatch(/setWeeklyFailed\(wFailed\);[\s\S]{0,40}setInsightsFailed/);
  });

  it('失败态与空态是两句文案，重试一律走 common.retry', () => {
    expect(src).toContain("t('insights.canonicalEmpty')");
    expect(src).toContain("t('common.loadFailed')");
    // 三处 = 周报卡 1 ＋ 新面列表失败 1 ＋ 新面详情失败 1。
    // 数量钉死是为了「新增一处自己造的 Retry 文案」也会被拦下：那样等于把
    // 「重试」在 15 语言里做成只有英文的按钮。
    expect(src.match(/t\('common\.retry'\)/g)).toHaveLength(3);
    expect(src).toContain('onPress={loadInsights}');
    expect(src).toContain('onPress={() => fetchDetail(item.id)}');
  });

  it('游客态跳过请求（这一面要 JWT，硬发只会得到 401）', () => {
    const guard = /const loadInsights = useCallback\(async \(\) => \{([\s\S]*?)\n  \}, \[isLoggedIn\]\);/.exec(src);
    expect(guard).not.toBeNull();
    expect(guard![1]).toContain('if (!isLoggedIn) {');
    expect(guard![1]).toContain('return;');
  });

  it('依据最多 5 条是这一屏的常量，且 truncated 时给出裁剪前的总数', () => {
    expect(src).toMatch(/const EVIDENCE_MAX = 5;/);
    expect(src).toContain('slice(0, EVIDENCE_MAX)');
    expect(src).toContain('detailById[item.id].truncated');
    expect(src).toContain('detailById[item.id].evidence_summary.count');
    expect(src).toContain("t('insights.evidenceTruncated'");
  });

  it('详情只有 getInsight 一个调用点，且展开过就不再重复发请求', () => {
    expect(src.match(/await getInsight\(/g)).toHaveLength(1);
    expect(src).toMatch(/listInsights\(\)/g);
    expect(src.match(/await listInsights\(\)/g)).toHaveLength(1);
    expect(src).toContain('if (!detailById[item.id]) await fetchDetail(item.id);');
  });

  it('不发裸 fetch／XMLHttpRequest（请求出口只有 api.ts）', () => {
    expect(src).not.toMatch(/\bfetch\(/);
    expect(src).not.toMatch(/XMLHttpRequest/);
  });
});

// ── ⑥ 旧周报卡的期间级依据保留（ADR-003）───────────────────────────────────

describe('⑥ 新面不替换旧卡', () => {
  const src = read(SCREEN);

  it('周报卡与期间级聚合一行没动', () => {
    expect(src).toContain("import { memoriesWithinDays } from '../services/evidence'");
    expect(src).toMatch(/memoriesWithinDays\(\s*7,\s*w\.period\s*\)\.catch\(\(\) => \[\]\)/);
    expect(src).toContain('getWeeklyInsight()');
  });

  it('services/evidence.ts 仍在（工单 §怎么做.5：周报卡退役时才连文件一起删）', () => {
    expect(fs.existsSync(path.join(ROOT, 'src/services/evidence.ts'))).toBe(true);
  });

  it('两张卡各在页上：周报卡的 weeklyTitle 与新卡的 canonicalTitle', () => {
    expect(src).toContain("t('insights.weeklyTitle')");
    expect(src).toContain("t('insights.canonicalTitle')");
  });
});
