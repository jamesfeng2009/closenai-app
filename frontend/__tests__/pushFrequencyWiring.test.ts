/**
 * U3-a · 推送频率设置接线的闸门（2026-10-16）。
 *
 * 为什么要有它：这条缺陷的原状不是「后端没实现」，而是**写进得、读不出**——
 * PATCH /user/settings 早就能落库（user_service.py:241），cron_service.py:445-447
 * 也早已按三档分流，唯独 UserProfileResponse 没有这个字段，于是界面无从显示当前档。
 * 所以本闸的第一判据是**读路径**，不是「有没有一个按钮」。
 *
 * 手法沿用本仓其它接线闸：屏在 node 环境渲染不了，读源码文本 + 正则切片；
 * 跨端一致（档位集合）直接解析 backend/models/schemas.py 的 Literal，
 * 这样「前端悄悄多一档/少一档」会在 CI 上红，而不是等真机上发现选了存不进。
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
const USER_SERVICE_PY = 'backend/services/user_service.py';

const TIERS = ['daily', 'important', 'off'];

/** 解析 schemas.py 里某个类体内某字段的 Literal 成员（按声明顺序，逐字比）。 */
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

describe('① 档位集合三处逐字同集（后端写侧/读侧 + 前端常量）', () => {
  it('后端读侧与写侧的 Literal 就是 daily|important|off', () => {
    expect(literalMembers('UserProfileResponse', 'push_frequency')).toEqual(TIERS);
    expect(literalMembers('UserSettingsUpdate', 'push_frequency')).toEqual(TIERS);
  });

  it('前端 ProfileScreen 的三档常量与后端同集同序', () => {
    const src = read(PROFILE);
    const m = /const PUSH_FREQUENCIES: PushFrequency\[\] = \[([^\]]+)\]/.exec(src);
    expect(m).not.toBeNull();
    expect(m![1].split(',').map((s) => s.trim().replace(/'/g, ''))).toEqual(TIERS);
  });

  it('types 里 UserProfile（读）与 UserSettings（写）都带 push_frequency', () => {
    const src = read(TYPES);
    for (const iface of ['UserProfile', 'UserSettings']) {
      const body = new RegExp(`export interface ${iface} \\{([\\s\\S]*?)\\n\\}`).exec(src);
      expect(body).not.toBeNull();
      expect(body![1]).toContain('push_frequency');
    }
  });
});

describe('② 读路径与写路径都在界面上（这条缺陷的正题）', () => {
  const src = read(PROFILE);

  it('行与弹层都从 profile.push_frequency 取当前档，不是本地 state', () => {
    // 读侧两处：设置行的标签 + 弹层里勾中的那一档
    expect(src).toContain("t(`profile.pushFreqOption.${profile?.push_frequency || 'daily'}`)");
    expect(src).toContain("f === (profile?.push_frequency || 'daily')");
  });

  it('点档位走 PATCH /user/settings，且用响应刷新（不许只改本地 state 装作存上了）', () => {
    const handler = /const onSelectPushFrequency = async \(frequency: PushFrequency\) => \{([\s\S]*?)\n  \};/.exec(src);
    expect(handler).not.toBeNull();
    expect(handler![1]).toContain('updateUserSettings({ push_frequency: frequency })');
    expect(handler![1]).toContain('setProfile(updated)');
    expect(handler![1]).not.toMatch(/setProfile\(\{ \.\.\.profile/);
  });

  it('未登录不许打开弹层（与主动消息时间同一道 requireAuth）', () => {
    expect(src).toMatch(
      /onPress=\{\(\) => \{ if \(requireAuth\(\)\) setFreqVisible\(true\); \}\}/
    );
  });

  it('三档各渲一行，且每行都挂上 onSelectPushFrequency', () => {
    const modal = /推送频率选择[\s\S]*?<\/Modal>/.exec(src);
    expect(modal).not.toBeNull();
    expect(modal![0]).toContain('PUSH_FREQUENCIES.map');
    expect(modal![0]).toContain('onSelectPushFrequency(f)');
    expect(modal![0]).toContain('t(`profile.pushFreqHint.${f}`)');
  });
});

describe('③ 后端真的回显（源码级，行为级见 backend/tests/test_push_frequency_setting.py）', () => {
  it('get_profile 构造里显式传了 push_frequency', () => {
    const src = readRepo(USER_SERVICE_PY);
    const call = /return UserProfileResponse\(([\s\S]*?)\n        \)/.exec(src);
    expect(call).not.toBeNull();
    expect(call![1]).toContain('push_frequency=');
  });
});

describe('④ 15 语言键齐全，且选项子键跟着档位集合走', () => {
  const locales = Object.keys(resources);
  const keys = [
    'profile.pushFreq',
    'profile.pushFreqTitle',
    ...TIERS.map((t) => `profile.pushFreqOption.${t}`),
    ...TIERS.map((t) => `profile.pushFreqHint.${t}`),
  ];

  it('语言数就是 15，漏一门的话下面那条会假绿', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s：8 个键都有值', (locale) => {
    const flat = flatten((resources as any)[locale].translation as Tree);
    for (const key of keys) {
      expect(flat[key]).toBeTruthy();
    }
  });

  it('pushFreqOption 的子键集合与档位集合一字不差', () => {
    const flat = flatten((resources as any).en.translation as Tree);
    const got = Object.keys(flat)
      .filter((k) => k.startsWith('profile.pushFreqOption.'))
      .map((k) => k.split('.').pop()!);
    expect(got.sort()).toEqual([...TIERS].sort());
  });
});

describe('⑤ 没有新开请求出口', () => {
  it('ProfileScreen 不发裸 fetch/XMLHttpRequest', () => {
    const src = read(PROFILE);
    expect(src).not.toMatch(/\bfetch\(/);
    expect(src).not.toMatch(/XMLHttpRequest/);
  });
});
