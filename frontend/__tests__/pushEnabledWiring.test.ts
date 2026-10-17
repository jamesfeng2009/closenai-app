/**
 * 批 2 · D-G6（推送总开关）的接线闸（2026-10-17）。
 *
 * 为什么要有它：这条缺陷的正题不是「后端没实现」，而是**写得进、读不出**——
 * `users.push_enabled` 早就是主动消息的分流入口（cron_service.py 六处 + journey_service.py:339），
 * PATCH /user/settings 也早已落库（user_service.py:250 → user_repository.py:174-175），
 * 唯独 UserProfileResponse 没这个字段 ⇒ 界面无从显示开关的真实位置。
 * 所以本闸的第一判据是**读路径**，手法沿用 U3-a 的 pushFrequencyWiring.test.ts：
 * 屏在 node 环境渲染不了 ⇒ 读源码文本 + 正则切片；跨端一致直接解析 backend/models/schemas.py。
 *
 * 行为级判据（默认值 / 写后回读 / 改频率不顶掉开关）在
 * backend/tests/test_push_enabled_setting.py，这里只管接线形状。
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
const KEYS = ['pushEnabled', 'pushEnabledOn', 'pushEnabledOff'];

/** 取出 schemas.py 里某个类体的正文（到下一个 class 为止）。 */
function modelBody(className: string): string {
  const src = readRepo(SCHEMAS_PY);
  const body = new RegExp(`class ${className}\\(BaseModel\\):([\\s\\S]*?)\\n\\nclass `).exec(src);
  expect(body).not.toBeNull();
  return body![1];
}

/** 某字段在类体内的声明行（去注释）。 */
function fieldLine(className: string, field: string): string {
  const lines = modelBody(className)
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith(`${field}:`));
  expect(lines).toHaveLength(1);
  return lines[0];
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

describe('① 后端声明了开关，且读写两侧各是自己那一态', () => {
  it('读侧 UserProfileResponse.push_enabled 是非 Optional 的 bool', () => {
    const line = fieldLine('UserProfileResponse', 'push_enabled');
    expect(line).toMatch(/^push_enabled: bool = False$/);
    expect(line).not.toContain('Optional');
  });

  it('写侧 UserSettingsUpdate.push_enabled 是三态 Optional[bool]', () => {
    expect(fieldLine('UserSettingsUpdate', 'push_enabled')).toMatch(
      /^push_enabled: Optional\[bool\] = None/,
    );
  });

  it('新字段追加在末尾，不打动上面已有字段的声明顺序', () => {
    // 切流面两侧逐字比的是字节序：中途插字段会改已有端点的响应字节
    const freq = modelBody('UserProfileResponse').indexOf('push_frequency:');
    const enabled = modelBody('UserProfileResponse').indexOf('push_enabled:');
    expect(freq).toBeGreaterThan(-1);
    expect(enabled).toBeGreaterThan(freq);
    expect(modelBody('UserProfileResponse').slice(enabled + 1)).not.toMatch(/^\s{4}\w+.*:/m);
  });
});

describe('② 后端真的回显（行为级见 backend/tests/test_push_enabled_setting.py）', () => {
  it('get_profile 构造里显式传了 push_enabled', () => {
    const src = readRepo(USER_SERVICE_PY);
    const call = /return UserProfileResponse\(([\s\S]*?)\n        \)/.exec(src);
    expect(call).not.toBeNull();
    expect(call![1]).toContain('push_enabled=');
  });
});

describe('③ 前端类型读写同源', () => {
  const src = read(TYPES);

  it('UserProfile（读）与 UserSettings（写）都带 push_enabled', () => {
    for (const iface of ['UserProfile', 'UserSettings']) {
      const body = new RegExp(`export interface ${iface} \\{([\\s\\S]*?)\\n\\}`).exec(src);
      expect(body).not.toBeNull();
      expect(body![1]).toContain('push_enabled');
    }
  });

  it('读侧可以是 null（老后端/未登录），写侧只收布尔', () => {
    expect(read(TYPES)).toMatch(/push_enabled\?: boolean \| null;/);
    const settings = /export interface UserSettings \{([\s\S]*?)\n\}/.exec(src);
    expect(settings![1]).toMatch(/push_enabled\?: boolean;/);
  });
});

describe('④ 界面上开关与频率各管一件事（这条缺陷的正题）', () => {
  const src = read(PROFILE);

  it('标签读的是 profile.push_enabled，不是本地 state', () => {
    expect(src).toContain('{profile?.push_enabled ? t(\'profile.pushEnabledOn\') : t(\'profile.pushEnabledOff\')}');
    expect(src).toContain('accessibilityState={{ checked: !!profile?.push_enabled }}');
    expect(src).not.toMatch(/const \[pushEnabled, setPushEnabled\]/);
  });

  it('点开关走 PATCH /user/settings，且用响应刷新（不许只改本地 state 装作存上了）', () => {
    const handler = /const onTogglePushEnabled = async \(next: boolean\) => \{([\s\S]*?)\n  \};/.exec(src);
    expect(handler).not.toBeNull();
    expect(handler![1]).toContain('updateUserSettings({ push_enabled: next })');
    expect(handler![1]).toContain('setProfile(updated)');
    expect(handler![1]).not.toMatch(/setProfile\(\{ \.\.\.profile/);
  });

  it('未登录不许翻开关，且连点不会并发发两次 PATCH', () => {
    const handler = /const onTogglePushEnabled = async \(next: boolean\) => \{([\s\S]*?)\n  \};/.exec(src);
    expect(handler![1]).toMatch(/if \(!requireAuth\(\)\) return;/);
    expect(handler![1]).toMatch(/if \(pushBusy\) return;/);
    expect(handler![1]).toMatch(/setPushBusy\(true\);/);
    expect(handler![1]).toMatch(/setPushBusy\(false\);/);
    expect(src).toMatch(/disabled=\{pushBusy\}/);
  });

  it('开关条目在频率条目上方，且频率行不看开关脸色（关掉开关仍能改档位）', () => {
    const switchRow = /accessibilityRole="switch"[\s\S]*?<\/TouchableOpacity>/.exec(src);
    expect(switchRow).not.toBeNull();
    const freqRow = /accessibilityRole="button"\s+onPress=\{\(\) => \{ if \(requireAuth\(\)\) setFreqVisible\(true\); \}\}/
      .exec(src);
    expect(freqRow).not.toBeNull();
    expect(switchRow!.index).toBeLessThan(freqRow!.index);
    // 频率那一行里不许出现 push_enabled：出现就等于「关了总开关就藏掉档位」，
    // 而 D-G6-2 的口径是开关＝要不要、频率＝要什么，两者独立可编辑。
    const freqBlock = /推送频率（U3-a）[\s\S]*?<\/TouchableOpacity>/.exec(src);
    expect(freqBlock).not.toBeNull();
    expect(freqBlock![0]).not.toContain('push_enabled');
    expect(src).not.toMatch(/profile\?\.push_enabled && \(/);
  });

  it('文案只宣称主动消息，不宣称「所有通知」（proactive_gate.go:319 那侧 push_enabled=false 单独不拦）', () => {
    const en = flatten((resources as any).en.translation as Tree);
    const lower = KEYS.map((k) => en[`profile.${k}`].toLowerCase()).join(' | ');
    expect(lower).toContain('proactive');
    expect(lower).not.toMatch(/all (push )?notifications|every notification|notifications off/);
  });
});

describe('⑤ 15 语言键齐全', () => {
  const locales = Object.keys(resources);

  it('语言数就是 15，漏一门的话下面那条会假绿', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s：profile.pushEnabled* 三个键都有值', (locale) => {
    const flat = flatten((resources as any)[locale].translation as Tree);
    for (const key of KEYS) expect(flat[`profile.${key}`]).toBeTruthy();
  });

  it('开与关两档的文案不同一门语言内不重合（重合＝界面上看不出位置）', () => {
    for (const locale of locales) {
      const flat = flatten((resources as any)[locale].translation as Tree);
      expect(flat['profile.pushEnabledOn']).not.toEqual(flat['profile.pushEnabledOff']);
    }
  });

  it('开关行用到的每个键在 en 里都存在', () => {
    const flat = flatten((resources as any).en.translation as Tree);
    for (const key of [...srcUsedKeys()]) {
      expect(flat[key]).toBeTruthy();
    }
  });
});

function srcUsedKeys(): string[] {
  const m = read(PROFILE).match(/t\('profile\.(pushEnabled[A-Za-z]*)'\)/g) || [];
  return Array.from(new Set(m.map((s) => /profile\.[A-Za-z]+/.exec(s)![0])));
}

describe('⑥ 没有新开请求出口', () => {
  it('ProfileScreen 不发裸 fetch/XMLHttpRequest', () => {
    const src = read(PROFILE);
    expect(src).not.toMatch(/\bfetch\(/);
    expect(src).not.toMatch(/XMLHttpRequest/);
  });
});
