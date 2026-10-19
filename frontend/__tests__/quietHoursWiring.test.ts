/**
 * T5-02 · 免打扰时段接线的闸门（2026-10-18）。
 *
 * 为什么要有它：后端这一批把「静默窗口」做成了真逻辑（cron 与 push 两道投递门），
 * 但**界面上没有入口**就等于这条功能不存在——本仓同类缺陷的原状一直是
 * 「写得进、读不出」（U3-a 推送频率就是同一形状）。所以判据顺序照旧：
 * 先拍读路径回显，再拍写路径成对，最后才拍交互态。
 *
 * 手法沿用 pushFrequencyWiring：屏在 node 环境渲染不了，读源码文本 + 正则切片；
 * 跨端一致（字段名、HH:MM 形态）直接解析 backend/models/schemas.py，
 * 这样「前端悄悄改字段名」会在 CI 上红，而不是等真机上选了存不进。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const PROFILE = 'src/screens/ProfileScreen.tsx';
const TYPES = 'src/types/index.ts';
const SCHEMAS_PY = 'backend/models/schemas.py';
const TIME_UTILS_PY = 'backend/core/time_utils.py';
const CRON_PY = 'backend/services/cron_service.py';
const PUSH_PY = 'backend/services/push/service.py';

const QUIET_KEYS = [
  'profile.quietHours',
  'profile.quietHoursOn',
  'profile.quietHoursOff',
  'profile.quietWindow',
  'profile.quietWindowTitle',
  'profile.quietStart',
  'profile.quietEnd',
  'profile.quietWindowHint',
];

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

const SOURCE = 'en';
const LANGUAGES = Object.keys(resources).filter((l) => l !== SOURCE);
const tables: Record<string, Record<string, string>> = Object.fromEntries(
  LANGUAGES.map((l) => [l, flatten((resources as any)[l].translation as Tree)])
);

describe('① 字段名单源：后端 schema ↔ 前端 types ↔ 界面', () => {
  // 判「字段声明行」而不是「正文出现过这个名字」：两个文件都在注释里写过 quiet_start，
  // 用 toContain 时把声明行整行删掉也不会红（变异自证 F2/B10 打的就是这一口）。
  // `^\s*名字\s*?:` 同时命中 TS 的 `quiet_start?: string` 与 Python 的 `quiet_start: Optional[str]`。
  const declares = (body: string, field: string) =>
    new RegExp(`^\\s*${field}\\??\\s*:`, 'm').test(body);

  it('后端读侧 UserProfileResponse 回显 quiet_start/quiet_end', () => {
    const src = readRepo(SCHEMAS_PY);
    const body = new RegExp(`class UserProfileResponse\\(BaseModel\\):([\\s\\S]*?)\\n\\n`).exec(src);
    expect(body).not.toBeNull();
    expect(declares(body![1], 'quiet_start')).toBe(true);
    expect(declares(body![1], 'quiet_end')).toBe(true);
  });

  it('后端写侧 UserSettingsUpdate 有 quiet_start/quiet_end/quiet_hours_enabled', () => {
    const src = readRepo(SCHEMAS_PY);
    const start = src.indexOf('class UserSettingsUpdate(BaseModel):');
    expect(start).toBeGreaterThan(-1);
    const body = src.slice(start, start + 2200);
    for (const field of ['quiet_start', 'quiet_end', 'quiet_hours_enabled']) {
      expect(declares(body, field)).toBe(true);
    }
  });

  it('前端 types 读写两侧都带这三个字段', () => {
    const src = read(TYPES);
    const read1 = /export interface UserProfile \{([\s\S]*?)\n\}/.exec(src);
    const write = /export interface UserSettings \{([\s\S]*?)\n\}/.exec(src);
    expect(read1).not.toBeNull();
    expect(write).not.toBeNull();
    expect(declares(read1![1], 'quiet_start')).toBe(true);
    expect(declares(read1![1], 'quiet_end')).toBe(true);
    expect(declares(write![1], 'quiet_start')).toBe(true);
    expect(declares(write![1], 'quiet_hours_enabled')).toBe(true);
  });
});

describe('② 读路径：开关态与窗口都从 profile 取，不是本地 state', () => {
  const src = read(PROFILE);

  it('quietOn 由两列是否成对决定（NULL＝关闭，不是拿 00:00 当哨兵）', () => {
    const m = /const quietOn = ([^;]+);/.exec(src);
    expect(m).not.toBeNull();
    expect(m![1].replace(/\s+/g, '')).toContain('!!profile?.quiet_start&&!!profile?.quiet_end');
    expect(src).not.toContain("quiet_start === '00:00'");
  });

  it('开关与窗口两行的展示值都来自 profile', () => {
    expect(src).toContain('accessibilityState={{ checked: quietOn }}');
    expect(src).toContain('`${profile?.quiet_start} – ${profile?.quiet_end}`');
  });
});

describe('③ 写路径：PATCH 的载荷形状必须过得了后端校验', () => {
  const src = read(PROFILE);

  it('开启时 enabled 与起止一起发（后端 schemas.py 拒「开启但缺起止」）', () => {
    expect(src).toMatch(/quiet_hours_enabled:\s*true,\s*\.\.\.quietWindow\(\)/);
    const quietWindow = /const quietWindow = [\s\S]*?\n {2}\}\);/.exec(src);
    expect(quietWindow).not.toBeNull();
    expect(quietWindow![0]).toContain('quiet_start');
    expect(quietWindow![0]).toContain('quiet_end');
  });

  it('关闭只发 enabled=false（两列由 service 一起写 NULL）', () => {
    expect(src).toMatch(/quiet_hours_enabled:\s*false\s*\}/);
  });

  it('改起止永远成对发送，绝不只发一列', () => {
    // 只发一列在后端是「半开窗口」400 的语义，不是「改一端」
    const handler = /const onSelectQuietMoment[\s\S]*?\n {2}\};/.exec(src);
    expect(handler).not.toBeNull();
    expect(handler![0]).toContain('quietWindow(which');
    expect(handler![0]).not.toMatch(/quiet_start:\s*time\s*\}/);
  });

  it('busy 期间禁点，避免两次 PATCH 谁后到谁赢', () => {
    const src2 = read(PROFILE);
    expect(src2).toContain('if (quietBusy) return;');
    expect(src2).toMatch(/disabled=\{!quietOn \|\| quietBusy\}/);
  });

  it('关闭态下起止行不可编辑（整行只读）', () => {
    const row = /testID="quietWindowRow"[\s\S]{0,200}/.exec(read(PROFILE));
    expect(row).not.toBeNull();
    expect(read(PROFILE)).toMatch(/style=\{\[styles\.settingItem, !quietOn && styles\.settingItemDisabled\]\}/);
  });
});

describe('④ 后端投递门真的在（前端只是入口，门在库里）', () => {
  it('in_quiet_hours 支持跨零点、NULL＝关闭、start==end 退化窗口不静默', () => {
    const src = readRepo(TIME_UTILS_PY);
    const fn = /def in_quiet_hours[\s\S]*?\n\ndef /.exec(src);
    expect(fn).not.toBeNull();
    expect(fn![0]).toContain('start is None or end is None or start == end');
    expect(fn![0]).toContain('now_local >= start or now_local < end');
  });

  it('cron 的静默门在配额与写库之前，返回 skipped_quiet', () => {
    const src = readRepo(CRON_PY);
    const gate = src.indexOf('return "skipped_quiet"');
    expect(gate).toBeGreaterThan(-1);
    // 配额判定与消息落库都必须排在门之后，否则「静默」照样吃当日配额、照样留痕
    expect(src.indexOf('is_proactive_quota_exceeded')).toBeGreaterThan(gate);
    expect(src.indexOf('create_idempotent')).toBeGreaterThan(gate);
  });

  it('push 投递点也过同一道门（剧本/里程碑/关心都走 send_to_user）', () => {
    const src = readRepo(PUSH_PY);
    expect(src).toContain('user_in_quiet_hours');
  });
});

describe('⑤ 15 语言文案齐全（漏一个键就是一条假承诺）', () => {
  it.each(LANGUAGES)('%s：8 个 quiet* 键都在', (lang) => {
    for (const key of QUIET_KEYS) {
      expect(tables[lang][key]).toBeDefined();
      expect(tables[lang][key].length).toBeGreaterThan(0);
    }
  });

  it('英文侧同样有这 8 个键（正向证人：不是只加了 15 个语种）', () => {
    const en = flatten((resources as any)[SOURCE].translation as Tree);
    for (const key of QUIET_KEYS) expect(en[key]).toBeDefined();
    expect(Object.keys(en).filter((k) => k.startsWith('profile.quiet')).length).toBe(8);
  });

  it('界面上用到的每个键都在文案表里（防手打错键名）', () => {
    const en = flatten((resources as any)[SOURCE].translation as Tree);
    const used = new Set(
      [...read(PROFILE).matchAll(/t\('profile\.(quiet[A-Za-z]*)'\)/g)].map((m) => `profile.${m[1]}`)
    );
    expect(used.size).toBeGreaterThanOrEqual(5);
    for (const key of used) expect(en[key]).toBeDefined();
  });
});
