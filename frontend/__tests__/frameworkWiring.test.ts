/**
 * P2 #24 · 主要性格框架的跨端接线闸。
 *
 * 缺陷原状：界面确实调了 `updateUserSettings({ primary_framework })`，后端
 * `UserSettingsUpdate` 里却没这个字段（pydantic 静默丢弃），界面再拿本地 state
 * 顶上——看起来能选，刷新/换设备就退回默认。所以判据必须跨端成对：
 *
 * ① 后端读写两侧都声明了字段，且合法集合只有 mbti/enneagram；
 * ② 前端 types 读写两侧都声明，且 `PrimaryFramework` 与后端 Literal 逐字相同；
 * ③ 展示以服务端回显为准（`setProfile(updated)`），不许再拼本地覆盖值——
 *    这一格就是「刷新即丢」的根，撤回后端持久化时它会连同 ② 一起红；
 * ④ 两个框架的文案 15 语言齐全（漏一个键就是一条假承诺）。
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

const declares = (body: string, field: string) =>
  new RegExp(`^\\s*${field}\\??\\s*:`, 'm').test(body);

function backendFrameworkValues(): string[] {
  const src = readRepo(SCHEMAS_PY);
  const m = /primary_framework:\s*Optional\[Literal\[([^\]]+)\]\]/.exec(src);
  if (!m) return [];
  return [...m[1].matchAll(/"([a-z]+)"/g)].map((x) => x[1]).sort();
}

describe('① 后端读写两侧都声明 primary_framework，且只收两个值', () => {
  const src = readRepo(SCHEMAS_PY);

  it('读侧 UserProfileResponse 回显该字段', () => {
    const body = /class UserProfileResponse\(BaseModel\):([\s\S]*?)\n\n/.exec(src);
    expect(body).not.toBeNull();
    expect(declares(body![1], 'primary_framework')).toBe(true);
  });

  it('写侧 UserSettingsUpdate 接受该字段（否则 PATCH 被静默丢弃）', () => {
    const start = src.indexOf('class UserSettingsUpdate(BaseModel):');
    expect(start).toBeGreaterThan(-1);
    expect(declares(src.slice(start, start + 2600), 'primary_framework')).toBe(true);
  });

  it('两个值恰好是 mbti/enneagram', () => {
    expect(backendFrameworkValues()).toEqual(['enneagram', 'mbti']);
  });
});

describe('② 前端 types 读写两侧都在，且枚举与后端逐字一致', () => {
  const src = read(TYPES);

  it('UserProfile 读侧 / UserSettings 写侧都带该字段', () => {
    const readSide = /export interface UserProfile \{([\s\S]*?)\n\}/.exec(src);
    const writeSide = /export interface UserSettings \{([\s\S]*?)\n\}/.exec(src);
    expect(readSide).not.toBeNull();
    expect(writeSide).not.toBeNull();
    expect(declares(readSide![1], 'primary_framework')).toBe(true);
    expect(declares(writeSide![1], 'primary_framework')).toBe(true);
  });

  it('PrimaryFramework 联合类型与后端 Literal 同一集合', () => {
    const m = /export type PrimaryFramework =([^;]+);/.exec(src);
    expect(m).not.toBeNull();
    const front = [...m![1].matchAll(/'([a-z]+)'/g)].map((x) => x[1]).sort();
    expect(front).toEqual(backendFrameworkValues());
  });
});

describe('③ 展示以服务端回显为准（写路径与刷新后的读路径同一份数据）', () => {
  const src = read(PROFILE);

  it('onSelectFramework 发出 primary_framework', () => {
    const handler = /const onSelectFramework[\s\S]*?\n {2}\};/.exec(src);
    expect(handler).not.toBeNull();
    expect(handler![0]).toMatch(/primary_framework:\s*framework/);
  });

  it('返回后直接 setProfile(updated)，不再拼本地覆盖（刷新即丢的根在这里）', () => {
    const handler = /const onSelectFramework[\s\S]*?\n {2}\};/.exec(src);
    expect(handler).not.toBeNull();
    expect(handler![0]).toContain('setProfile(updated)');
    expect(handler![0]).not.toMatch(/setProfile\(\{[^)]*primary_framework/);
  });

  it('界面展示值只从 profile 取', () => {
    expect(src).toContain("t(`profile.frameworkOption.${profile?.primary_framework || 'mbti'}`)");
  });
});

describe('④ 两个框架的文案 15 语言齐全', () => {
  const frameworks = ['mbti', 'enneagram'];

  it.each([SOURCE, ...LANGUAGES])('%s：frameworkOption 两个键都在', (lang) => {
    const table =
      lang === SOURCE
        ? flatten((resources as any)[SOURCE].translation as Tree)
        : tables[lang];
    for (const f of frameworks) {
      expect(table[`profile.frameworkOption.${f}`]).toBeDefined();
      expect(table[`profile.frameworkOption.${f}`].length).toBeGreaterThan(0);
    }
  });
});
