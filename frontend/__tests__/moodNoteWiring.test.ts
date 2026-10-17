/**
 * 批 2 · D-G8（签到附一句话没输入框）的接线闸（2026-10-17）。
 *
 * 缺陷原状：`api.ts:409` 的 `moodCheckin(emoji, note?)` 有参数、后端 `schemas.py:87` 有 max_length=1000
 * 的字段、`mood_service.py` 也真的吃这句（拼进解读 prompt + 沉淀成记忆），唯独两个调用点只传 emoji
 * ⇒ 用户没有地方打这句，后端那条线一次也没被喂过。
 *
 * 判据按工作单三条默认值来：
 * - D-G8-1 位置：只有 `ReflectScreen` 的 mood 弹层加输入框；`HomeScreen` 的情绪行保持一键签到
 *   （那是今日卡片的一跳，加输入框等于把签到变成表单）⇒ 本闸要**反向**钉住 HomeScreen 没被顺手接上。
 * - D-G8-2 空值：trim 后为空 ⇒ 传 undefined（不给库里写空串），本地行写输入值或 null。
 * - D-G8-3 长度：maxLength 取后端同值 1000，不另设更小的数（另设＝第二真源）。
 * 后端语义（空⇒不沉淀、备注进 prompt、未同意不上送、1000 的收放）在
 * backend/tests/test_mood_checkin_note.py。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const REFLECT = 'src/screens/ReflectScreen.tsx';
const HOME = 'src/screens/HomeScreen.tsx';
const API = 'src/services/api.ts';
const SCHEMAS_PY = 'backend/models/schemas.py';
const MOOD_SERVICE_PY = 'backend/services/mood_service.py';
const KEYS = ['moodNotePlaceholder', 'moodNoteLabel'];

const reflect = read(REFLECT);
const home = read(HOME);

/** 解析 schemas.py 里某类体某字段的 Field(..., max_length=N)。 */
function backendMaxLength(className: string, field: string): number {
  const src = readRepo(SCHEMAS_PY);
  const body = new RegExp(`class ${className}\\(BaseModel\\):([\\s\\S]*?)\\n\\nclass `).exec(src);
  expect(body).not.toBeNull();
  const line = new RegExp(`${field}: [^\\n]+`).exec(body![1]);
  expect(line).not.toBeNull();
  const n = /max_length=(\d+)/.exec(line![0]);
  expect(n).not.toBeNull();
  return Number(n![1]);
}

/** 切出某个函数/块的源码（按花括号配平，从签名处开始）。 */
function block(src: string, signature: string): string {
  const start = src.indexOf(signature);
  expect(start).toBeGreaterThanOrEqual(0);
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error('花括号没配平');
}

describe('① 请求出口本来就带 note（这条缺陷不在 api.ts）', () => {
  const body = block(read(API), 'export async function moodCheckin');

  it('签名有可选 note，body 真的把它序列化进请求', () => {
    expect(body).toMatch(/moodCheckin\(emoji: MoodEmoji, note\?: string\)/);
    expect(body).toContain('JSON.stringify({ emoji, note })');
    expect(body).toContain("'/mood/checkin'");
  });
});

describe('② 长度上限取后端同值，不长出第二真源', () => {
  it('前端常量 === 后端 max_length', () => {
    const decl = /const MOOD_NOTE_MAX_LENGTH = (\d+);/.exec(reflect);
    expect(decl).not.toBeNull();
    expect(Number(decl![1])).toBe(backendMaxLength('MoodCheckinRequest', 'note'));
  });

  it('界面上用的是常量，不是把 1000 抄在 JSX 里', () => {
    expect(reflect).toContain('maxLength={MOOD_NOTE_MAX_LENGTH}');
    expect(reflect).not.toMatch(/maxLength=\{1000\}/);
  });

  it('常量只声明一次', () => {
    expect(reflect.match(/const MOOD_NOTE_MAX_LENGTH =/g)).toHaveLength(1);
  });
});

describe('③ 输入框只长在 ReflectScreen 的 mood 弹层里（D-G8-1）', () => {
  it('弹层块里有受控 TextInput，绑的是 moodNote', () => {
    const panel = /\{showMoodPanel && \(([\s\S]*?)\n      \)\}/.exec(reflect);
    expect(panel).not.toBeNull();
    expect(panel![1]).toContain('<TextInput');
    expect(panel![1]).toContain('value={moodNote}');
    expect(panel![1]).toContain('onChangeText={setMoodNote}');
    expect(panel![1]).toContain("placeholder={t('reflect.moodNotePlaceholder')}");
    expect(panel![1]).toContain("accessibilityLabel={t('reflect.moodNoteLabel')}");
  });

  it('state 只有这一处，且签到成功即清空', () => {
    expect(reflect.match(/const \[moodNote, setMoodNote\] = useState\(''\);/g)).toHaveLength(1);
    const handler = block(reflect, 'const quickMoodCheckin = async (emoji: MoodEmoji)');
    expect(handler).toContain('setMoodNote(\'\');');
  });

  it('HomeScreen 保持一键签到：不传 note、没有 moodNote 输入框', () => {
    expect(home).not.toMatch(/moodNote/);
    for (const call of home.match(/moodCheckin\([^)]*\)/g) || []) {
      expect(call).not.toMatch(/,/);
    }
    expect(home.match(/moodCheckin\(/g) || []).toHaveLength(1);
  });
});

describe('④ 空值口径：空 ⇒ 不写库、界面上也不留空串（D-G8-2）', () => {
  const handler = block(reflect, 'const quickMoodCheckin = async (emoji: MoodEmoji)');

  it('trim 后为空传 undefined，而不是空串', () => {
    expect(handler).toContain("const note = moodNote.trim() || undefined;");
    expect(handler).toContain('moodCheckin(emoji, note)');
    expect(handler).not.toMatch(/moodCheckin\(emoji, moodNote\)/);
  });

  it('本地行写用户刚打的那句（服务端响应里没有 note 字段，回读不到）', () => {
    expect(handler).toContain('note: note ?? null,');
    expect(handler).not.toMatch(/note: null,/);
    // 出处：后端响应模型确实不回显 note（所以只能用手上的值），而判空口径两侧一致
    const respBody = /class MoodCheckinResponse\(BaseModel\):([\s\S]*?)\n\nclass /.exec(readRepo(SCHEMAS_PY));
    expect(respBody).not.toBeNull();
    expect(respBody![1]).not.toMatch(/^\s+note:/m);
    expect(readRepo(MOOD_SERVICE_PY)).toMatch(/if note and note\.strip\(\):/);
  });

  it('游客拦截仍在最前面（输入框不改变 requireAuth 口径）', () => {
    expect(handler).toMatch(/if \(!requireAuth\(\)\) return;[\s\S]*const note =/);
  });
});

describe('⑤ 15 语言键齐全', () => {
  const locales = Object.keys(resources);

  it('语言数就是 15，漏一门的话下面那条会假绿', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s：reflect 两个新键都有值', (locale) => {
    const flat: Record<string, string> = {};
    const walk = (obj: Record<string, unknown>, prefix = '') => {
      for (const [k, v] of Object.entries(obj)) {
        if (v && typeof v === 'object') walk(v as Record<string, unknown>, `${prefix}${k}.`);
        else flat[`${prefix}${k}`] = String(v);
      }
    };
    walk((resources as any)[locale].translation as Record<string, unknown>);
    for (const key of KEYS) expect(flat[`reflect.${key}`]).toBeTruthy();
  });
});

describe('⑥ 没有新开请求出口', () => {
  it('ReflectScreen 不发裸 fetch/XMLHttpRequest', () => {
    expect(reflect).not.toMatch(/\bfetch\(/);
    expect(reflect).not.toMatch(/XMLHttpRequest/);
  });
});
