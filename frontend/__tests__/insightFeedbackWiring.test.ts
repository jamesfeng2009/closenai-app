/**
 * T3-05 接线闸：V3 洞察面的反馈条（六态）与纠错输入。
 *
 * 手法沿用本仓其它接线闸（读源码文本＋正则切片，两侧不同源）：RN 屏在 jest 里跑不起来，
 * 而这一面最坏的坏法是「屏上六个按钮都在、请求也发出去了，但表态落错了地方」。
 * 六组判据各自对应一句谎话：
 *
 * ① 出口唯一且形状逐字：屏上只从 `postInsightFeedback` 出去，body 三键跟着 Go 的 tag；
 *    「写了个 submitFeedback」不等于「打的是那条线」。
 * ② **六态词表与状态映射都跟 Go 对账**——这是本闸的核心。前端自己数一套
 *    「准确→已确认」的表，TS 一切正常，而权威表里落的是另一个状态；
 *    两侧不同源：TS 侧读屏上的字面量，Go 侧读 `outcome.go` 的常量与 `RefInsight` 那一格。
 * ③ 乐观更新只准长在这一张表上：徽章取值的优先级是「回执 > 本地推测」，
 *    `changed=false` 那一格必须回到读面的 `insights.status`（否则「部分准确」会被显示成
 *    一次改写）。屏上不许长出第二张映射表。
 * ④ 空纠错不可提交：判据与 `maxLength` 都钉回 Go（`maxCorrectionText`），
 *    而且不许把 `e.message` 弹给用户（工单 §怎么做.3 的那句「文案走 i18n」）。
 * ⑤ 失败必回滚：409 走「你已经答过了」，其余走通用失败；两分支都存在，
 *    且码值两侧不同源（Go 的 `CodeConflict` ＋ BFF 的 `int(code)` 原样透传）。
 * ⑥ 埋点只带行为元数据：事件名逐字，六态值走白名单里的 `action` 而不是 `type`，
 *    正文一律不进队列（`correctionText` 不许出现在 track 调用里）。
 *
 * 验收对照（工单 T3-05 §验收）：「六种反馈各一条 jest」＝②与各组里的 `it.each`；
 * 「失败回滚一条」＝⑤；「空纠错不可提交一条」＝④；「i18n 键集闸绿」＝⑦。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const SCREEN = 'src/screens/InsightsScreen.tsx';
const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const ANALYTICS = 'src/services/analytics.ts';
const GO_OUTCOME = 'go-core/internal/domain/outcome.go';
const GO_FEEDBACK = 'go-core/internal/httpapi/v3_feedback.go';
const GO_APIERR = 'go-core/internal/apierr/apierr.go';
const PY_PROXY = 'backend/services/v3_proxy.py';

/** 切出屏内某个 const 箭头函数的函数体（屏内所有处理函数都是两层缩进）。 */
function sliceScreenConst(name: string): string {
  const src = read(SCREEN);
  const at = src.indexOf(`const ${name} = `);
  expect(at).toBeGreaterThan(-1);
  const end = src.indexOf('\n  };', at);
  expect(end).toBeGreaterThan(at);
  return src.slice(at, end + 5);
}

/** Go 的 FeedbackType 常量 → wire 值（confirm / partially_confirm / …）。 */
function goFeedbackWire(): Record<string, string> {
  const src = readRepo(GO_OUTCOME);
  const out: Record<string, string> = {};
  for (const m of src.matchAll(/\t(Feedback\w+)\s+FeedbackType = "([^"]+)"/g)) {
    out[m[1]] = m[2];
  }
  expect(Object.keys(out)).toHaveLength(6);
  return out;
}

/** Go 的 `var FeedbackTypes` 里那六条（**声明顺序**，不是字母序）。 */
function goFeedbackOrder(): string[] {
  const wire = goFeedbackWire();
  const m = /var FeedbackTypes = \[\]FeedbackType\{([\s\S]*?)\n\}/.exec(readRepo(GO_OUTCOME));
  expect(m).not.toBeNull();
  return [...m![1].matchAll(/(Feedback\w+)/g)].map((x) => wire[x[1]]);
}

/**
 * Go 的 `StatusForDerivedTarget` 里 **RefInsight 那一格**：表态 → 洞察新状态。
 * 取「case RefInsight 到 case RefOutcome 之间」这段，是为了不在整个函数里乱抓
 * 到 RefFact 的 active/invalidated——那是另一张表，前端这屏一行都不该看见。
 */
function goInsightMatrix(): Record<string, string> {
  const src = readRepo(GO_OUTCOME);
  const at = src.indexOf('case RefInsight:');
  const until = src.indexOf('case RefOutcome:');
  expect(at).toBeGreaterThan(-1);
  expect(until).toBeGreaterThan(at);
  const wire = goFeedbackWire();
  const out: Record<string, string> = {};
  for (const m of src.slice(at, until).matchAll(/case (Feedback\w+):\s*\n\s*return "([a-z_]+)", true/g)) {
    out[wire[m[1]]] = m[2];
  }
  return out;
}

/** 屏上那张乐观表：键是六态里的五态，值是「我以为它会变成」。 */
function localStatusTable(): Record<string, string> {
  const m = /const LOCAL_TARGET_STATUS: Partial<Record<InsightFeedbackType, string>> = \{([\s\S]*?)\n\};/.exec(read(SCREEN));
  expect(m).not.toBeNull();
  const out: Record<string, string> = {};
  for (const pair of m![1].matchAll(/^\s*([a-z_]+): '([a-z_]+)',/gm)) out[pair[1]] = pair[2];
  return out;
}

/** 屏上按钮数组的字面量顺序。 */
function screenFeedbackTypes(): string[] {
  const m = /const FEEDBACK_TYPES: InsightFeedbackType\[\] = \[([\s\S]*?)\n\];/.exec(read(SCREEN));
  expect(m).not.toBeNull();
  return [...m![1].matchAll(/'([^']+)'/g)].map((x) => x[1]);
}

/** i18n 子表的键名（按 en 源表）。 */
function enSubTable(key: string): string[] {
  const tbl = (resources as any).en.translation.insights[key];
  expect(tbl).toBeInstanceOf(Object);
  return Object.keys(tbl);
}

/** 取 en 主表里某个扁平键的值（不存在时返回 undefined）。 */
function enFlat(key: string): string {
  const v = (resources as any).en.translation.insights[key];
  expect(typeof v).toBe('string');
  return v as string;
}

const WIRE = goFeedbackWire();
const GO_ORDER = goFeedbackOrder();
const MATRIX = goInsightMatrix();
const FLAT_KEYS = [
  'fbDone', 'fbAmend', 'fbSavedTitle', 'fbSavedMessage', 'fbDuplicate', 'fbFailed',
  'fbCorrectTitle', 'fbCorrectHint', 'fbCorrectPlaceholder', 'fbCorrectSubmit',
];

// ── ① 出口唯一，body 三键跟着 Go 的 tag ─────────────────────────────────────

describe('① 反馈只有一个出口，请求体三键与 Go 的 tag 逐字同集', () => {
  it('屏上只调用 postInsightFeedback 一处，且从 api.ts 导入', () => {
    const src = read(SCREEN);
    expect(src.match(/await postInsightFeedback\(/g)).toHaveLength(1);
    expect(src).toContain('postInsightFeedback,');
    expect(src.match(/\bfetch\(/g)).toBeNull();
    expect(src).not.toMatch(/XMLHttpRequest/);
  });

  it('封装的 body 三键 == Go 的 v3FeedbackRequest 的 json tag（同序）', () => {
    const goTags = [...readRepo(GO_FEEDBACK).matchAll(/json:"([^",]+)/g)].map((m) => m[1]);
    expect(goTags.slice(0, 3)).toEqual(['type', 'correction_text', 'operation_id']);
    const body = new RegExp(
      'export async function postInsightFeedback\\([\\s\\S]*?\\n}'
    ).exec(read(API));
    expect(body).not.toBeNull();
    for (const k of ['type,', 'correction_text:', 'operation_id:']) {
      expect(body![0]).toContain(k);
    }
    // 这条端点要 JWT 与 user_id（来自头），前端不许从 body 里塞身份
    expect(body![0]).not.toMatch(/user_id/);
  });

  it('这一屏真的把封装消费了（T3-04 交付的是形状，消费归 T3-05）', () => {
    const md = readRepo('contracts/endpoints.md');
    expect(md).toContain('| POST | `/api/v1/insights/{insight_id}/feedback` |');
    expect(read(SCREEN)).toContain('FEEDBACK_TYPES.map');
  });
});

// ── ② 六态词表与状态映射都跟 Go 对账 ────────────────────────────────────────

describe('② 六个按钮＝Go 的六态，映射表＝Go 的 RefInsight 那一格', () => {
  it('屏上六态与 Go 的词表同集（多一档少一档都红）', () => {
    expect([...screenFeedbackTypes()].sort()).toEqual([...GO_ORDER].sort());
    expect(screenFeedbackTypes()).toHaveLength(6);
  });

  it.each(GO_ORDER)('按钮 %s 在屏上有字面量，并且在 i18n 里有同名词', (type) => {
    expect(screenFeedbackTypes()).toContain(type);
    expect(enSubTable('fb')).toContain(type);
  });

  it.each(Object.entries(MATRIX))('%s → %s：乐观表的值逐字等于 Go 的矩阵', (type, status) => {
    expect(localStatusTable()[type]).toBe(status);
  });

  it('Go 的矩阵五格都在屏上那张表里，且没有第六格', () => {
    expect(Object.keys(MATRIX).sort()).toEqual(Object.keys(localStatusTable()).sort());
    expect(localStatusTable()['partially_confirm']).toBeUndefined();
  });

  it('partially_confirm 在 Go 里就是「不改写状态」（屏上不许替它猜一个）', () => {
    expect(MATRIX['partially_confirm']).toBeUndefined();
    expect(WIRE['FeedbackPartiallyConfirm']).toBe('partially_confirm');
    expect(sliceScreenConst('submitFeedback')).not.toMatch(/partially_confirm.*return\s*'"/);
  });

  it('类型联合也跟着 Go（改名先红这里，而不是红在屏上少一个按钮）', () => {
    const tsUnion = /export type InsightFeedbackType =([\s\S]*?);\n/.exec(read(TYPES));
    expect(tsUnion).not.toBeNull();
    expect([...tsUnion![1].matchAll(/'([^']+)'/g)].map((x) => x[1]).sort())
      .toEqual([...GO_ORDER].sort());
  });
});

// ── ③ 乐观更新只长在一张表上，回执优先级高于本地推测 ─────────────────────────

describe('③ 徽章取值：回执 > 本地推测，changed=false 回到读面表', () => {
  const fn = sliceScreenConst('statusBadgeKey');

  it('无表态时读列表带回来的 status（读面六档表）', () => {
    expect(fn).toContain('if (!mark) return `insights.status.${item.status}`;');
  });

  it('changed=false 那一格回 insights.status（不能显示成一次改写）', () => {
    expect(fn).toContain('if (mark.receipt && !mark.receipt.changed) return `insights.status.');
  });

  it('回执优先：receipt?.target_status 排在本地推测之前', () => {
    expect(fn).toContain('mark.receipt?.target_status ?? LOCAL_TARGET_STATUS[mark.type]');
  });

  it('全屏只有 LOCAL_TARGET_STATUS 这一张状态映射表（不许长出第二套状态机）', () => {
    expect(read(SCREEN).match(/target_status|targetStatus/g)!.length).toBeGreaterThanOrEqual(2);
    expect(read(SCREEN)).not.toMatch(/const (SECOND|ANOTHER)_STATUS/);
    // 「自己推一个状态」的写法只允许出现在这一张表的定义与消费两处
    expect(read(SCREEN).match(/LOCAL_TARGET_STATUS/g)).toHaveLength(3);
  });

  it('六枚按钮只长在「有结论」那一格里（evidence_count===0 没有结论可表态）', () => {
    const src = read(SCREEN);
    const emptyCell = src.indexOf("t('insights.evidenceInsufficient')");
    const elseBranch = src.indexOf(') : (', emptyCell);
    const buttons = src.indexOf('FEEDBACK_TYPES.map');
    expect(emptyCell).toBeGreaterThan(-1);
    expect(elseBranch).toBeGreaterThan(emptyCell);
    expect(buttons).toBeGreaterThan(elseBranch);
    // 卡片之后才是下一个 section（消息入口），所以按钮不可能落到卡外面
    expect(buttons).toBeLessThan(src.indexOf('{/* AI 消息历史入口'));
  });

  it('提交成功过就收起按钮，改口才重新铺出来', () => {
    const src = read(SCREEN);
    expect(src).toContain('feedbackById[item.id]?.receipt && amendId !== item.id ? (');
    expect(src).toContain('onPress={() => setAmendId(item.id)}');
    expect(src).toContain("t('insights.fbAmend')");
  });
});

// ── ④ 空纠错不可提交，上限与 Go 同值，且不弹 e.message ───────────────────────

describe('④ 「我要纠正」＝Modal 多行输入，非空才可提交', () => {
  it('只有 correct 这一档开弹层，其余五档直接提交', () => {
    const fn = sliceScreenConst('onFeedbackPress');
    expect(fn).toContain("if (type === 'correct') {");
    expect(fn).toContain('setCorrectionFor(item.id);');
    expect(fn).toContain('submitFeedback(item, type);');
  });

  it('上限逐字等于 Go 的 maxCorrectionText，并且真的挂到 maxLength 上', () => {
    const go = /const maxCorrectionText = (\d+)/.exec(readRepo(GO_FEEDBACK));
    expect(go).not.toBeNull();
    expect(read(SCREEN)).toContain(`const CORRECTION_MAX_LENGTH = ${go![1]};`);
    expect(read(SCREEN)).toContain('maxLength={CORRECTION_MAX_LENGTH}');
  });

  it('空正文不发请求：trim 之后判空即 return（工单 §怎么做.2「非空才可提交」）', () => {
    const fn = sliceScreenConst('submitCorrection');
    expect(fn).toContain('const text = correctionText.trim();');
    expect(fn).toContain('if (!correctionFor || !text || correcting) return;');
    expect(fn).toContain("submitFeedback(item, 'correct', text)");
  });

  it('提交按钮的 disabled 判据与上面同一条（灰掉而不是按下去没反应）', () => {
    const src = read(SCREEN);
    expect(src).toContain('disabled={!correctionText.trim() || correcting}');
  });

  it('弹层是 Modal 多行输入，关闭走 onRequestClose（安卓返回键）', () => {
    const src = read(SCREEN);
    expect(src).toContain('<Modal');
    expect(src).toContain('visible={correctionFor !== null}');
    expect(src).toContain('onRequestClose={closeCorrection}');
    expect(src).toContain('multiline');
  });

  it('不弹 e.message／error.message：服务端那条串是给运维看的', () => {
    const fn = sliceScreenConst('submitFeedback');
    // 这条判据是**归零型**的，所以正向证人在 ⑤：失败分支必须仍然只说两句 i18n 文案。
    expect(fn).not.toContain('.message');
    expect(fn).not.toMatch(/String\(e\)/);
    expect(read(SCREEN)).not.toMatch(/Alert\.alert\([^)]*\be\.message/);
  });
});

// ── ⑤ 失败必回滚，409 有专门一句 ────────────────────────────────────────────

describe('⑤ 乐观写的徽章在失败时必须收回去', () => {
  const fn = sliceScreenConst('submitFeedback');

  it('先乐观写、回执覆盖：setFeedbackById 两次（写／覆盖）加一次回滚', () => {
    expect(fn).toContain('if (predicted) setFeedbackById((p) => ({ ...p, [item.id]: { type } }));');
    expect(fn).toContain('setFeedbackById((p) => ({ ...p, [item.id]: { type, receipt } }));');
    expect(fn.match(/setFeedbackById/g)).toHaveLength(3);
  });

  it('回滚把整格收回 prev 或删掉（删不掉＝用户看到一次没发生的表态）', () => {
    const catchAt = fn.indexOf('} catch (e) {');
    expect(catchAt).toBeGreaterThan(-1);
    const branch = fn.slice(catchAt, fn.indexOf('} finally {', catchAt));
    expect(branch).toContain('setFeedbackById((p) => {');
    expect(branch).toContain('if (prev) {');
    expect(branch).toContain('delete next[item.id];');
  });

  it('忙位进入与退出各一次（防连点，也防卡在忙态）', () => {
    expect(fn).toContain('setFeedbackBusyId(item.id);');
    expect(fn).toContain('setFeedbackBusyId(null);');
    expect(fn.match(/setFeedbackBusyId/g)).toHaveLength(2);
  });

  it('409 的码值两侧不同源：Go 的 CodeConflict ＋ BFF 原样透传', () => {
    const go = /\tCodeConflict\s+=\s+(\d+)/.exec(readRepo(GO_APIERR));
    expect(go).not.toBeNull();
    expect(go![1]).toBe('409');
    expect(read(SCREEN)).toContain(`const CODE_DUPLICATE_FEEDBACK = ${go![1]};`);
    // Python 不重新折码：信封里的 code 直接进 AppException（409 到客户端还是 409）
    const py = readRepo(PY_PROXY);
    expect(py).toContain('raise AppException(int(code), str(message), data=data, http_status=resp.status_code)');
  });

  it('409 走「你已经答过了」，其余码走通用失败；两句都走 i18n', () => {
    expect(fn).toContain('e instanceof ApiError && e.code === CODE_DUPLICATE_FEEDBACK');
    expect(fn).toContain("t('insights.fbDuplicate')");
    expect(fn).toContain("t('insights.fbFailed')");
  });

  it('成功与失败都用 Alert 当面说清（工单 §怎么做.2 的「已记录，会用于改进后续判断」）', () => {
    expect(fn).toContain("Alert.alert(t('insights.fbSavedTitle'), t('insights.fbSavedMessage'));");
    // 工单那句「会用于改进后续判断」钉在中文源上（其余 14 门由 ⑦ 的齐备性判据管）
    expect((resources as any).zh.translation.insights.fbSavedMessage).toContain('改进后续判断');
  });
});

// ── ⑥ 埋点只带行为元数据 ────────────────────────────────────────────────────

describe('⑥ 埋点：事件名逐字，六态值走白名单里的 action', () => {
  it('事件名逐字等于工单 §怎么做.5 的 insight_feedback_submitted', () => {
    expect(sliceScreenConst('submitFeedback')).toContain(
      "track('insight_feedback_submitted', { feature: 'insights', action: type });"
    );
  });

  it('type 不在属性白名单里，所以六态值放进 action（本仓的隐私红线，闸自己会红）', () => {
    const keys = /const ALLOWED_PROPERTY_KEYS = new Set\(\[([\s\S]*?)\n\]\);/.exec(read(ANALYTICS));
    expect(keys).not.toBeNull();
    const listed = [...keys![1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
    expect(listed).toContain('action');
    expect(listed).toContain('feature');
    expect(listed).not.toContain('type');
  });

  it('正文不进埋点队列（纠错内容只走那一条 POST）', () => {
    const src = read(SCREEN);
    const call = /track\('insight_feedback_submitted'[^)]*\)/.exec(src);
    expect(call).not.toBeNull();
    expect(call![0]).not.toMatch(/correction/);
    expect(src.match(/track\(/g)).toHaveLength(1);
  });

  it('正文长度、正文本身都不写进本地 SQLite（这一屏不碰仓储）', () => {
    expect(read(SCREEN)).not.toMatch(/analyticsEventsRepo|enqueue\(/);
  });
});

// ── ⑦ i18n 键集：15 语言齐备，两张子表各钉一侧 ──────────────────────────────

describe('⑦ i18n：六态标签表＝Go 的词表，状态机表＝Go 的矩阵值', () => {
  it('insights.fb 的键集 == Go 的 FeedbackTypes（多一档少一档都红）', () => {
    // 比的是**集合**：对象里的书写序不是判据，屏上按钮序才是（那条在 ② 里钉）。
    expect(enSubTable('fb').sort()).toEqual([...GO_ORDER].sort());
  });

  it('insights.fbResult 的键集 == Go 的 RefInsight 矩阵值（含 retracted，不含 published）', () => {
    expect(enSubTable('fbResult').sort()).toEqual(Object.values(MATRIX).sort());
    expect(enSubTable('fbResult')).toContain('retracted');
    expect(enSubTable('fbResult')).not.toContain('published');
  });

  const LANGUAGES = Object.keys(resources);

  it('语言数就是 15，漏一门下面两条会假绿', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)('%s：六态＋五状态＋十句扁平文案全部有值，键集与 en 同', (lng) => {
    const ins = (resources as any)[lng].translation.insights;
    for (const sub of ['fb', 'fbResult']) {
      expect(Object.keys(ins[sub]).sort()).toEqual(Object.keys((resources as any).en.translation.insights[sub]).sort());
      for (const v of Object.values(ins[sub])) {
        expect(typeof v).toBe('string');
        expect((v as string).trim()).not.toBe('');
      }
    }
    for (const k of FLAT_KEYS) {
      expect(typeof ins[k]).toBe('string');
      expect(ins[k].trim()).not.toBe('');
    }
    // 占位符不许哪一门漏掉：漏了就是屏上裸花括号
    expect(ins.fbDone).toMatch('{{label}}');
  });

  it('扁平键的 {{label}} 只有一个，且 fbDone 真的带它（改口行嵌标签，不是再铺六个按钮）', () => {
    expect(enFlat('fbDone').match(/\{\{label\}\}/g)).toHaveLength(1);
    expect(sliceScreenConst('doneLine')).toContain("t('insights.fbDone', { label:");
    expect(sliceScreenConst('doneLine')).toContain('`insights.fb.${mark.type}`');
  });

  it('屏上用到的 insights.* 扁平键在 en 表里都有值（漏键＝界面渲染出裸键名）', () => {
    const used = [...read(SCREEN).matchAll(/t\('insights\.(fb[A-Za-z]*)'/g)].map((m) => m[1]);
    expect(used.length).toBeGreaterThan(0);
    for (const key of used) expect(typeof enFlat(key)).toBe('string');
    // 模板串那一侧（六态标签）也逐字钉住，否则改名会静默失联
    for (const literal of read(SCREEN).matchAll(/`insights\.(fb|fbResult)\.\$\{/g)) {
      expect(['fb', 'fbResult']).toContain(literal[1]);
    }
  });

  it('六枚按钮与「改口」都带 accessibilityLabel（工单：反馈条要能读出来）', () => {
    const src = read(SCREEN);
    const bar = src.slice(src.indexOf('FEEDBACK_TYPES.map'));
    expect(bar).toContain('accessibilityLabel={t(`insights.fb.${type}`)}');
    expect(src.match(/accessibilityLabel=\{t\('insights\.fbAmend'\)\}/g)).toHaveLength(1);
  });
});
