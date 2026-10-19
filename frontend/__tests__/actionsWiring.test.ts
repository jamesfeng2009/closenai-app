/**
 * T4-04 接线闸：洞察 → 行动 → 结果那条闭环（六条出口 + 两个屏落点）。
 *
 * 手法沿用本仓其它接线闸（读源码文本 + 正则切片，判据两侧不同源）：RN 屏在 jest 里
 * 挂不起来，而这一面最坏的坏法不是崩，是**谎话**——
 * 「屏上有三个分区，但其中一个把 cancelled 的行吃掉了」「行动立好了，其实没落」
 * 「今天记过一条，屏上又说记上了」。八组判据各自对着一句谎：
 *
 * ① 出口逐字回台账行：六条封装的 path 必须在 `contracts/endpoints.md` 有对应行，
 *    且那一行「前端调用」列指着的 api.ts 行号真指着这条路径。
 *    ② 请求体键名逐字＝Go 的 `v3*Request`：多一格、少一格、改名都先在 Go 那边 400，
 *    而 400 的中文说法用户读不懂。身份（user_id）绝不进 body（ADR-002）。
 * ③ 响应类型键名逐字＝Go 的 `v3*Wire`／`v3*Payload`：TS 这边把 `goal_id` 写成 `goalId`
 *    一切编译正常，只有屏上那片空白会告诉你。
 * ④ 词表跟 Go 对账：三个生命周期动词、六个 result 值、九类 outcome_type 里这一面唯一
 *    能用的那一类。前端自己数一套词表＝同一个行动在两侧是两个状态。
 * ⑤ 分区、三态与截断：三个分区各有一套 loading/failed/empty，记录区不再整屏早退，
 *    `truncated` 必须说出口（不说就是「行动变少」这种最难归因的假象）。
 * ⑥ 行内两枚按钮的语义：动词逐字、`changed=false` 换第二句话、忙位防抖、
 *    结果必须显式提交、写失败不许关弹层。
 * ⑦ 洞察入口的两步写：目标建成而行动没落时重试不再建第二颗目标；空标题不发请求；
 *    截止日按本地日 23:59 换算，不自己重述服务端的合法性。
 * ⑧ 15 语言键集齐（缺一个键 = 那一门语言整屏显英文）。
 *
 * 验收对照（工单 T4-04 §验收）：「三步各一条」＝①②③④；「失败恢复一条」＝⑥⑦；
 * 「i18n 键集闸绿」＝⑧。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');
const countIn = (src: string, re: RegExp) => (src.match(re) ?? []).length;

const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const GROWTH = 'src/screens/GrowthRecordsScreen.tsx';
const INSIGHTS = 'src/screens/InsightsScreen.tsx';
const ENDPOINTS_MD = 'contracts/endpoints.md';
const PY_ROUTER = 'backend/routers/v3.py';
const GO_HTTP = 'go-core/internal/httpapi/v3_goals.go';
const GO_OUTCOME_HTTP = 'go-core/internal/httpapi/v3_outcomes.go';
const GO_GOAL = 'go-core/internal/domain/goal.go';
const GO_OUTCOME = 'go-core/internal/domain/outcome.go';

/** Go 结构体的 json tag 键名（逐字，不含 omitempty 处理：这七个结构体都没有可选键）。 */
function goJsonKeys(file: string, structName: string): string[] {
  const src = readRepo(file);
  const at = src.indexOf(`type ${structName} struct {`);
  expect(at).toBeGreaterThan(-1);
  const end = src.indexOf('\n}', at);
  expect(end).toBeGreaterThan(at);
  return [...src.slice(at, end).matchAll(/json:"([a-z_]+)"/g)].map((m) => m[1]);
}

/** TS interface 的顶层键名。 */
function tsInterfaceKeys(iface: string): string[] {
  const src = read(TYPES);
  const at = src.indexOf(`export interface ${iface} {`);
  expect(at).toBeGreaterThan(-1);
  const end = src.indexOf('\n}', at);
  expect(end).toBeGreaterThan(at);
  return [...src.slice(at, end).matchAll(/^ {2}([a-z_]+)\??:/gm)].map((m) => m[1]);
}

/** 切出 `export async function name(...)` 到下一个顶层 `}` 之间的封装体。 */
function wrapperBody(name: string): string {
  const api = read(API);
  const at = api.indexOf(`export async function ${name}`);
  expect(at).toBeGreaterThan(-1);
  const end = api.indexOf('\n}', at);
  expect(end).toBeGreaterThan(at);
  return api.slice(at, end);
}

/** 切出屏内某个 const 处理函数的函数体（含 useCallback 包着的那种收尾）。 */
function screenFn(src: string, name: string): string {
  const start = src.indexOf(`  const ${name} = `);
  expect(start).toBeGreaterThan(-1);
  const rest = src.slice(start);
  const end = /\n {2}(\};|}, [\s\S]*?\);)/.exec(rest);
  expect(end).not.toBeNull();
  return rest.slice(0, end!.index + end![0].length);
}

/** 台账主表里 `/goals`、`/actions` 面的登记行（含方法、路径与「前端调用」列）。 */
function commitmentRows(): { method: string; path: string; used: string }[] {
  const rows: { method: string; path: string; used: string }[] = [];
  for (const line of readRepo(ENDPOINTS_MD).split('\n')) {
    const m = /^\| \d+ \| (GET|POST|PUT|PATCH|DELETE) \| `(\/api\/v1\/(?:goals|actions)[^`]*)` \|[^|]*\|[^|]*\| ([^|]*) \|/.exec(line);
    if (m) rows.push({ method: m[1], path: m[2], used: m[3].trim() });
  }
  return rows;
}

// ── ① 出口逐字回台账行 ─────────────────────────────────────────────────────

describe('① 六条出口逐字回台账行', () => {
  const rows = commitmentRows();

  it('台账过滤器不是扫空集：/goals 与 /actions 面恰好七条（含屏上不接的那条目标 PATCH）', () => {
    expect(rows.map((r) => `${r.method} ${r.path}`).sort()).toEqual([
      'GET /api/v1/actions',
      'GET /api/v1/goals',
      'PATCH /api/v1/actions/{action_id}',
      'PATCH /api/v1/goals/{goal_id}',
      'POST /api/v1/actions/{action_id}/outcomes',
      'POST /api/v1/goals',
      'POST /api/v1/goals/{goal_id}/actions',
    ]);
  });

  const cases: [string, string, string, string][] = [
    ['listGoals', 'GET', '/api/v1/goals', `'/goals'`],
    ['createGoal', 'POST', '/api/v1/goals', `'/goals'`],
    ['listActions', 'GET', '/api/v1/actions', `'/actions'`],
    ['createAction', 'POST', '/api/v1/goals/{goal_id}/actions', '/actions`'],
    ['transitionAction', 'PATCH', '/api/v1/actions/{action_id}', '/actions/${encodeURIComponent(actionId)}`'],
    ['postActionOutcome', 'POST', '/api/v1/actions/{action_id}/outcomes', '/outcomes`'],
  ];

  it.each(cases)('%s：封装在册、台账有对应行、出处列那一行真指着这条路径', (name, method, p, frag) => {
    expect(countIn(read(API), new RegExp(`export async function ${name}\\b`, 'g'))).toBe(1);
    const row = rows.find((r) => r.method === method && r.path === p);
    expect(row).toBeDefined();
    // 出处列写 `—` 就是「封装没接线」；写行号就得行号此刻真指着这条 path
    const ref = /api\.ts:(\d+)/.exec(row!.used);
    expect(ref).not.toBeNull();
    expect(read(API).split('\n')[Number(ref![1]) - 1]).toContain(frag);
  });

  it('BFF 那六条转发装饰器在册（屏上发的每一条都有人在服务端接）', () => {
    const py = readRepo(PY_ROUTER);
    for (const deco of [
      '@router.get("/goals")',
      '@router.post("/goals")',
      '@router.post("/goals/{goal_id}/actions")',
      '@router.get("/actions")',
      '@router.patch("/actions/{action_id}")',
      '@router.post("/actions/{action_id}/outcomes")',
    ]) {
      expect(countIn(py, new RegExp(deco.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&'), 'g'))).toBe(1);
    }
  });

  it('列表查询只有 status/limit 两格：这一面没有游标（Go 用 truncated 表达还有更多）', () => {
    expect(read(API)).toMatch(/export interface CommitmentListQuery \{\n {2}status\?: string;\n {2}limit\?: number;\n\}/);
    expect(wrapperBody('listGoals')).toContain('listTail(query)');
    expect(wrapperBody('listActions')).toContain('listTail(query)');
    expect(readRepo(GO_HTTP)).toContain('这一面**没有游标**');
  });
});

// ── ② 请求体键名逐字＝Go ───────────────────────────────────────────────────

describe('② 请求体键名逐字等于 Go 的 v3*Request', () => {
  /**
   * 从 `JSON.stringify({ ... })` 里取属性名。两种写法都要认：
   * `{ title, description }` 的简写与 `{ target_at: targetAt }` 的显式写法——
   * 简写漏掉就是「键在册但闸看不见」，那比漏键更坏。
   */
  const bodyKeys = (name: string): string[] => {
    const m = /JSON\.stringify\(\{([\s\S]*?)\}\)/.exec(wrapperBody(name));
    expect(m).not.toBeNull();
    return m![1]
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const at = part.indexOf(':');
        return (at === -1 ? part : part.slice(0, at)).trim();
      });
  };

  it('createGoal 的三格逐字＝v3GoalCreateRequest', () => {
    expect(bodyKeys('createGoal')).toEqual(goJsonKeys(GO_HTTP, 'v3GoalCreateRequest'));
  });

  it('createAction 的三格逐字＝v3ActionCreateRequest（body 里没有 goal_id：目标由 path 给）', () => {
    expect(bodyKeys('createAction')).toEqual(goJsonKeys(GO_HTTP, 'v3ActionCreateRequest'));
    expect(bodyKeys('createAction')).not.toContain('goal_id');
  });

  it('transitionAction 只带动词一个键＝v3TransitionRequest', () => {
    expect(bodyKeys('transitionAction')).toEqual(goJsonKeys(GO_HTTP, 'v3TransitionRequest'));
  });

  it('postActionOutcome 的五格逐字＝v3OutcomeRequest', () => {
    expect(bodyKeys('postActionOutcome')).toEqual(goJsonKeys(GO_OUTCOME_HTTP, 'v3OutcomeRequest'));
  });

  it('六条封装一个都不带身份：身份由服务端从令牌解出，前端无从替用户声明「这是谁」', () => {
    for (const name of ['listGoals', 'listActions', 'createGoal', 'createAction', 'transitionAction', 'postActionOutcome']) {
      const body = wrapperBody(name);
      expect(body).not.toContain('user_id');
      // 也不许自己拼头：身份头与鉴权头都归 request() 一处（api.ts:558 那条注释是这一格的登记）
      expect(body).not.toMatch(/[Hh]eaders/);
    }
  });
});

// ── ③ 响应类型键名逐字＝Go ─────────────────────────────────────────────────

describe('③ 响应类型逐字等于 Go 的 v3*Wire／v3*Payload', () => {
  const pairs: [string, string, string][] = [
    ['GoalItem', GO_HTTP, 'v3GoalItemWire'],
    ['GoalListPage', GO_HTTP, 'v3GoalListPayload'],
    ['ActionItem', GO_HTTP, 'v3ActionItemWire'],
    ['ActionListPage', GO_HTTP, 'v3ActionListPayload'],
    ['GoalReceipt', GO_HTTP, 'v3GoalWritePayload'],
    ['ActionWriteReceipt', GO_HTTP, 'v3ActionWritePayload'],
    ['ActionOutcomeReceipt', GO_OUTCOME_HTTP, 'v3OutcomePayload'],
  ];

  // 标题里两侧名字都要写全：it.each 的 `%s` 按参数顺序落，只写两个 %s 时打出来的
  // 是「TS 名 ↔ Go 文件名」，变异自证那条「红点必须落在自己那格判据」就没法按标题定位。
  it.each(pairs)('%s ↔ %s（Go 的 %s）：键集与顺序都逐字对齐', (iface, goFile, struct) => {
    expect(tsInterfaceKeys(iface)).toEqual(goJsonKeys(goFile, struct));
  });

  it('可空时间在 TS 侧是 string | null 而不是 string（Go 是 *time.Time，线上就是 null）', () => {
    const items = [read(TYPES).indexOf('export interface GoalItem'), read(TYPES).indexOf('export interface ActionItem')];
    expect(items.every((i) => i > -1)).toBe(true);
    const goalBlock = read(TYPES).slice(read(TYPES).indexOf('export interface GoalItem {'));
    const nullables = [...goalBlock.slice(0, goalBlock.indexOf('\n}')).matchAll(/(\w+): string \| null;/g)].map((m) => m[1]);
    // 与 Go 侧的 *time.Time 同一格：哪一侧多一格就是两侧对「空」的理解不一样了
    expect(nullables.sort()).toEqual(
      ['completed_at', 'created_at', 'started_at', 'target_at', 'updated_at'].sort(),
    );
    expect(read(TYPES)).toMatch(/  goal_id: string \| null;/);
  });
});

// ── ④ 词表与 Go 对账 ───────────────────────────────────────────────────────

describe('④ 词表全部指回 Go 的 domain，屏上不重述合法性', () => {
  it('三个生命周期动词逐字＝Go 的 lifecycleVerbs', () => {
    const go = readRepo(GO_GOAL);
    const verbs = [...go.matchAll(/Verb\w+\s+LifecycleVerb = "(\w+)"/g)].map((m) => m[1]);
    expect(verbs).toEqual(['complete', 'skip', 'cancel']);
    const ts = /export type ActionTransition = ([^;]+);/.exec(read(TYPES))![1];
    expect(ts).toMatch(/'complete'/);
    expect([...ts.matchAll(/'(\w+)'/g)].map((m) => m[1]).sort()).toEqual([...verbs].sort());
  });

  it('屏上只发 complete 与 skip：cancel 在这一面没有入口，也不自己造第四枚按钮', () => {
    const src = read(GROWTH);
    expect(src).toContain("transitionAction(item.id, 'complete')");
    expect(src).toContain("transitionAction(item.id, 'skip')");
    expect(src).not.toContain("transitionAction(item.id, 'cancel')");
    expect(countIn(src, /transitionAction\(/g)).toBe(2);
  });

  it('四选一逐字取 Go 六值里的四格，且 mixed/not_relevant 确实不在屏上', () => {
    const go = readRepo(GO_OUTCOME);
    const all = [...go.matchAll(/OutcomeResult\w+\s+OutcomeResult = "(\w+)"/g)].map((m) => m[1]);
    expect(all).toEqual(['better', 'same', 'worse', 'mixed', 'not_relevant', 'unknown']);
    const shown = /const RESULT_CHOICES: ActionResultChoice\[\] = \[([^\]]+)\]/.exec(read(GROWTH))![1];
    const choices = [...shown.matchAll(/'(\w+)'/g)].map((m) => m[1]);
    expect(choices).toEqual(['better', 'same', 'worse', 'unknown']);
    expect(choices.every((c) => all.includes(c))).toBe(true);
    // 正向证人：这一档确实长在弹层里（不是只在常量表里挂着）
    expect(read(GROWTH)).toContain('RESULT_CHOICES.map((choice) =>');
  });

  it('outcome_type 只有一个字面量且住在 api.ts：屏上无从漂进九类词表', () => {
    expect(wrapperBody('postActionOutcome')).toContain("outcome_type: 'commitment_completed'");
    expect(countIn(read(API), /outcome_type: '/g)).toBe(1);
    for (const f of [GROWTH, INSIGHTS]) expect(read(f)).not.toContain('outcome_type');
    // 这一类唯一能挂在行动上：Go 的 OutcomeTargetRequirement 指回 RefCommitment
    const req = readRepo(GO_OUTCOME).slice(
      readRepo(GO_OUTCOME).indexOf('func OutcomeTargetRequirement'),
    );
    expect(req).toContain('case OutcomeCommitmentCompleted');
    expect(req.slice(req.indexOf('case OutcomeCommitmentCompleted'), req.indexOf('case OutcomeCommitmentCompleted') + 120))
      .toContain('RefCommitment');
  });

  it('标题上限一格不两处：屏上的 maxLength 与 Go 的 maxGoalTitle 同值', () => {
    const goMax = Number(/const maxGoalTitle = (\d+)/.exec(readRepo(GO_GOAL))![1]);
    expect(goMax).toBe(200);
    const insights = read(INSIGHTS);
    expect(insights).toMatch(/const ACTION_TITLE_MAX = 200;/);
    expect(countIn(insights, /maxLength=\{ACTION_TITLE_MAX\}/g)).toBe(2);
  });

  it('屏上不发带 status 的行动列表，也不自己判日期合法性（服务端词表是唯一判据）', () => {
    // 正向证人先立起来：一次全量取回确实发生了，下面两条归零才不是扫空集
    expect(read(GROWTH)).toContain('await listActions()');
    expect(read(GROWTH)).toContain('toLocalDate(item.due_at)');
    expect(read(GROWTH)).not.toMatch(/listActions\(\{[^}]*status/);
    expect(read(GROWTH)).not.toMatch(/due_at[^)\n]*< Date\.now\(\)/);
    expect(read(INSIGHTS)).not.toMatch(/listGoals\(\{ status: '(?!active)/);
  });
});

// ── ⑤ 分区、三态与截断 ─────────────────────────────────────────────────────

describe('⑤ 三个分区、各一套三态、截断不藏', () => {
  const src = read(GROWTH);

  it('分区状态取自 Go 的 ActionStatuses，且 cancelled 那一格是登记过的缺口而不是漏写', () => {
    const buckets = [...src.matchAll(/\{ key: '(\w+)', status: '(\w+)' \}/g)].map((m) => m[2]);
    expect(buckets).toEqual(['active', 'completed', 'skipped']);
    const go = readRepo(GO_GOAL);
    const all = [...go.matchAll(/Status(\w+) += "(\w+)"/g)].map((m) => m[2]);
    expect(all).toEqual(expect.arrayContaining(['active', 'completed', 'skipped', 'cancelled']));
    // 缺陷在册：那一屏自己写明「cancelled 无分区」，否则这看起来像漏了一格
    expect(src).toContain('`cancelled` 不在这里');
  });

  it('行动面三态各一份，且不与记录面共用一个 flag', () => {
    expect(src).toMatch(/const \[actions, setActions\] = useState<ActionItem\[\] \| null>\(null\);/);
    expect(src).toMatch(/const \[actionsLoading, setActionsLoading\] = useState\(true\);/);
    expect(src).toMatch(/const \[actionsFailed, setActionsFailed\] = useState\(false\);/);
    expect(src).not.toMatch(/setActionsFailed\(!!?error\)/);
    expect(src).not.toMatch(/setActionsFailed\(loading\)/);
  });

  it('空态只在「不加载、不失败、确实零行」时说出口', () => {
    expect(src).toMatch(/!actionsLoading && !actionsFailed && \(actions \?\? \[\]\)\.length === 0 && \(\n {10}<Text style=\{styles\.emptyText\}>\{t\('actions\.empty'\)\}<\/Text>/);
    expect(src).toContain("t('common.loadFailed')");
  });

  it('记录区不再整屏早退：行动区不等记录加载完就能用（记录区自己有 spinner）', () => {
    expect(src).not.toMatch(/if \(loading\) \{\n {4}return/);
    expect(src).toContain('{loading && (');
  });

  it('两个列表的 truncated 都被消费：一句「更早的没显示」而不是把截断讲成没有', () => {
    expect(tsInterfaceKeys('GoalListPage')).toContain('truncated');
    expect(tsInterfaceKeys('ActionListPage')).toContain('truncated');
    expect(read(GROWTH)).toContain('setActionsTruncated(page.truncated)');
    expect(read(GROWTH)).toContain("{!actionsLoading && !actionsFailed && actionsTruncated && (");
    expect(read(GROWTH)).toContain("t('actions.truncated')");
    expect(read(INSIGHTS)).toContain('setActionGoalsTruncated(page.truncated)');
    expect(read(INSIGHTS)).toContain("t('actions.truncated')");
    // 失败时不许留着上一条的截断说法
    expect(read(GROWTH)).toContain('setActionsFailed(true);\n      setActionsTruncated(false)');
    expect(read(INSIGHTS)).toContain('setActionGoalsFailed(true);\n      setActionGoalsTruncated(false)');
  });

  it('截断那句文案与 Go 的 ORDER BY created_at DESC 同口径：说的是「更早的没显示」', () => {
    const en = (resources as Record<string, any>).en.translation.actions.truncated as string;
    expect(en).toMatch(/older/i);
    expect(readRepo('go-core/db/queries/canonical.sql')).toMatch(
      /name: ListCommitments :many[\s\S]{0,400}ORDER BY created_at DESC/,
    );
  });
});

// ── ⑥ 行内两枚按钮的语义 ───────────────────────────────────────────────────

describe('⑥ 完成／跳过／记结果：幂等两句话与防抖', () => {
  const src = read(GROWTH);
  const submitBody = screenFn(src, 'submitResult');

  it('完成之后先重取分区再开结果弹层（顺序反了就把行动留在 active 上问结果）', () => {
    const doneBody = screenFn(src, 'markDone');
    expect(doneBody.indexOf("transitionAction(item.id, 'complete')")).toBeLessThan(
      doneBody.indexOf('loadActions()'),
    );
    expect(doneBody.indexOf('loadActions()')).toBeLessThan(doneBody.indexOf('setResultFor(item)'));
  });

  it('changed=false 是「今天已经记过了」而不是失败：两句话都在，且按回执选', () => {
    expect(submitBody).toContain('receipt.changed ? t(\'actions.resultSaved\') : t(\'actions.resultSameDay\')');
  });

  it('忙位防抖：两枚行内按钮共用一个 busy，结果提交另有自己一个', () => {
    expect(countIn(src, /if \(busyActionId\) return;/g)).toBe(2);
    expect(countIn(src, /disabled=\{busyActionId !== null\}/g)).toBe(2);
    expect(submitBody).toMatch(/if \(!resultFor \|\| !resultChoice \|\| resultBusy\) return;/);
    expect(src).toContain('disabled={!resultChoice || resultBusy}');
  });

  it('结果必须显式提交：四枚档位只选中，submitResult 只长在最后一枚按钮上', () => {
    expect(countIn(src, /onPress=\{submitResult\}/g)).toBe(1);
    expect(src).toContain('onPress={() => setResultChoice(choice)}');
    expect(src).not.toMatch(/onPress=\{\(\) => \{[^}]*setResultChoice\(choice\);[^}]*submitResult\(\)/);
  });

  it('写结果失败不关弹层：关掉就是让用户以为记过了', () => {
    const catchBlock = /catch \{([\s\S]*?)\n {4}\} finally/.exec(submitBody);
    expect(catchBlock).not.toBeNull();
    expect(catchBlock![1]).toContain("t('actions.failed')");
    expect(catchBlock![1]).not.toContain('setResultFor(null)');
    expect(catchBlock![1]).not.toContain('closeResult()');
    // 反向证人：成功路径确实会关（否则上面那条 not.toContain 是恒真）
    expect(submitBody).toContain('setResultFor(null);');
  });

  it('operation_id 每次尝试都是新的一条（同日幂等按 UTC 日判，它只负责把重试对上自己那次点击）', () => {
    expect(submitBody).toContain('newTraceId()');
    expect(src).toContain("import { newTraceId } from '../utils/traceId'");
    expect(wrapperBody('postActionOutcome')).toContain('operation_id: operationId');
  });
});

// ── ⑦ 洞察入口与两步写的失败恢复 ───────────────────────────────────────────

describe('⑦ 洞察 → 行动入口：预填、两步写与不重复建目标', () => {
  const src = read(INSIGHTS);

  it('每张洞察卡有一枚入口按钮，文案走 actions.cta（不是屏上自造英文）', () => {
    expect(src).toContain('onPress={() => openActionModal(item)}');
    expect(src).toContain("t('actions.cta')");
    expect(countIn(src, /accessibilityLabel=\{t\('actions\.cta'\)\}/g)).toBe(1);
  });

  it('预填按 rune 截断到 Go 的上限，不用 slice（slice 会把 emoji 切成半个字符）', () => {
    expect(src).toMatch(/const truncateRunes[\s\S]*?Array\.from\(text\)\.slice\(0, n\)/);
    expect(src).toContain('truncateRunes(item.statement, ACTION_TITLE_MAX)');
    expect(src).not.toMatch(/item\.statement\.slice\(0, ACTION_TITLE_MAX\)/);
  });

  it('空标题不发请求，忙时不重发', () => {
    expect(screenFn(src, 'submitAction')).toMatch(/if \(!actionFor \|\| !title \|\| actionBusy\) return;/);
    expect(src).toContain('disabled={!actionTitle.trim() || actionBusy}');
  });

  it('两步写之间留住已建成的目标 id：重试只补行动那一步，不再建第二颗目标', () => {
    const body = screenFn(src, 'submitAction');
    expect(body).toContain('actionGoalId === NEW_GOAL ? createdGoalId : actionGoalId');
    expect(body).toContain('const goal = await createGoal(');
    expect(body.indexOf('await createGoal(')).toBeLessThan(body.indexOf('setCreatedGoalId(goalId)'));
    expect(body).toContain('await createAction(goalId, title');
    // 成功后才作废那份留存的 id（失败时它还得留着给重试）
    expect(body.indexOf('setCreatedGoalId(goalId)')).toBeLessThan(body.indexOf('setCreatedGoalId(null)'));
  });

  it('NEW_GOAL 只是本地键，绝不进请求体', () => {
    expect(src).toMatch(/const NEW_GOAL = '__new__';/);
    expect(read(API)).not.toContain('__new__');
    const body = screenFn(src, 'submitAction');
    expect(body).not.toMatch(/create(Goal|Action)\([^)]*NEW_GOAL/);
  });

  it('截止日取本地日的 23:59 再转 RFC3339：UTC 零点会切掉那天的最后一整天', () => {
    const body = screenFn(src, 'submitAction');
    expect(body).toContain('T23:59:00');
    expect(body).toContain('toISOString()');
    expect(body).not.toMatch(/new Date\(due\)/);
    expect(body).toContain('DUE_DATE_PATTERN.test(due)');
  });

  it('两步写失败说一句「什么都没加」，不弹服务端双语串', () => {
    const body = screenFn(src, 'submitAction');
    const catchBlock = /catch \{([\s\S]*?)\n {4}\} finally/.exec(body);
    expect(catchBlock).not.toBeNull();
    expect(catchBlock![1]).toContain("t('actions.failed')");
    // 注释里可以提 e.message（那是「为什么不弹它」的登记），Alert 里不许
    expect(catchBlock![1]).not.toMatch(/Alert\.alert\([^\n]*message/);
    expect(body).not.toMatch(/Alert\.alert\([^\n]*message/);
  });

  it('目标候选拉不到时退成「新建一颗」而不是整块坏掉，且给重试', () => {
    const body = screenFn(src, 'reloadActionGoals');
    expect(body).toContain('setActionGoalsFailed(true)');
    expect(body).toContain('setActionGoalId(NEW_GOAL)');
    expect(src).toContain('onPress={() => void reloadActionGoals()}');
  });
});

// ── ⑧ 15 语言键集 ──────────────────────────────────────────────────────────

describe('⑧ actions.* 在 15 个 locale 里键集齐', () => {
  const LANGS = Object.keys(resources);
  const actionsOf = (lng: string) => (resources as Record<string, any>)[lng].translation.actions as Record<string, unknown>;
  const en = actionsOf('en');

  it('语言表恰好 15 门（少了就是这一闸在扫空集）', () => {
    expect(LANGS).toHaveLength(15);
  });

  it('en 那一格 29 个扁平键 + result 四键，且屏上用的每个键都在册', () => {
    const flat = Object.keys(en).filter((k) => k !== 'result');
    expect(flat).toHaveLength(29);
    expect(Object.keys(en.result as object)).toEqual(['better', 'same', 'worse', 'unknown']);
    const used = [
      ...read(GROWTH).matchAll(/t\('actions\.([A-Za-z]+)'\)/g),
      ...read(INSIGHTS).matchAll(/t\('actions\.([A-Za-z]+)'\)/g),
    ].map((m) => m[1]);
    expect(used.length).toBeGreaterThan(20);
    expect([...new Set(used)].filter((k) => !(k in en))).toEqual([]);
  });

  // 与 i18nLocales.test.ts 的全表键集闸重叠但不同判据：那一闸比的是「两侧一致」，
  // 15 份同时少同一格它照样绿；这一条钉的是 actions 面**应有的**键数与键名。
  it.each(LANGS.filter((l) => l !== 'en'))('%s：actions 段与 en 同键集（含 result 四键）', (lng) => {
    const table = actionsOf(lng);
    expect(Object.keys(table).sort()).toEqual(Object.keys(en).sort());
    expect(Object.keys(table.result as object).sort()).toEqual(Object.keys(en.result as object).sort());
  });

  it('占位符只长在 due 那一格，且每一门语言都带着它', () => {
    const holder = (o: Record<string, unknown>): string[] =>
      Object.entries(o).filter(([k, v]) => k !== 'result' && String(v).includes('{{date}}')).map(([k]) => k);
    for (const lng of LANGS) expect(holder(actionsOf(lng))).toEqual(['due']);
    expect(read(GROWTH)).toContain("t('actions.due', {\n                      date: toLocalDate(item.due_at),\n                    })");
  });
});
