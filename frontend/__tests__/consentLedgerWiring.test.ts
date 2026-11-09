/**
 * P2 #29 接线闸：同意台账真的能看、能撤回。
 *
 * 缺陷原状：`GET/POST /user/consents` 早已存在，前端零调用——用户只能看到一个
 * `ai_consent` 布尔位，看不到「哪一版、何时授予、何时撤回」，也没有逐项撤回入口。
 *
 * 判据：
 * 1. api 层两条函数打在 /user/consents 上，POST 带 purpose+granted；
 * 2. 屏幕从服务端返回的 items 渲染（不自建六用途枚举），只展示当前文案版本；
 * 3. 撤回/授予后回读台账（不本地拼状态，否则时刻是假的）；同刻只发一个请求；
 * 4. core_service 不给撤回按钮（撤回语义等同注销）；
 * 5. 隐私与安全页有入口、App 有路由。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

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

describe('api 层', () => {
  const src = read('src/services/api.ts');

  it('GET /user/consents', () => {
    const fn = block(src, 'export async function getConsentLedger');
    expect(fn).toContain("request<ConsentLedger>('/user/consents')");
  });

  it('POST /user/consents 带 purpose 与 granted', () => {
    const fn = block(src, 'export async function setConsentPurpose');
    expect(fn).toContain("'/user/consents'");
    expect(fn).toContain("method: 'POST'");
    expect(fn).toContain('JSON.stringify({ purpose, granted })');
  });
});

describe('ConsentLedgerScreen', () => {
  const src = read('src/screens/ConsentLedgerScreen.tsx');

  it('聚焦时拉台账（游客不请求）', () => {
    const load = block(src, 'const load = useCallback');
    expect(load).toContain('getConsentLedger()');
    expect(load).toContain('if (!isLoggedIn)');
    expect(src).toContain('useFocusEffect(load)');
  });

  it('只展示服务端下发的用途与当前版本，不在前端写死六项', () => {
    expect(src).toContain('ledger.items.filter((item) => item.version === ledger.version)');
    // 屏幕里不许出现六个 purpose 字面量组成的清单（core_service 是策略特例，允许出现）
    const purposeLiterals = [
      'ai_processing',
      'proactive_push',
      'analytics',
      'data_export',
      'model_training',
    ];
    purposeLiterals.forEach((p) => expect(src).not.toContain(`'${p}'`));
  });

  it('授予/撤回打服务端并回读台账', () => {
    const toggle = block(src, 'const onToggle = async');
    expect(toggle).toContain('await setConsentPurpose(item.purpose, !item.granted)');
    // 回读（第二次 GET）——本地改 granted 会写出假的 granted_at/revoked_at
    expect(toggle).toContain('getConsentLedger()');
    expect(toggle).not.toContain('granted: !item.granted');
  });

  it('同刻只允许一个表态在飞', () => {
    const toggle = block(src, 'const onToggle = async');
    expect(toggle).toContain('if (busy) return;');
    expect(toggle.indexOf('setBusy(item.purpose)')).toBeLessThan(
      toggle.indexOf('await setConsentPurpose')
    );
  });

  it('core_service 不提供撤回按钮', () => {
    expect(src).toContain("item.purpose !== 'core_service'");
    expect(src).toContain('consentLedger.coreHint');
  });
});

describe('入口与路由', () => {
  it('隐私与安全页有入口', () => {
    const src = read('src/screens/PrivacySecurityScreen.tsx');
    expect(src).toContain("navigation.navigate('ConsentLedger')");
  });

  it('App 注册了 ConsentLedger 路由', () => {
    const src = read('App.tsx');
    expect(src).toContain('name="ConsentLedger"');
    expect(src).toContain('ConsentLedger: undefined');
  });
});

describe('15 语言文案', () => {
  const langs = Object.keys(resources);
  const keys = [
    'title', 'intro', 'version', 'granted', 'notGiven', 'withdrawn',
    'grant', 'withdraw', 'grantedAt', 'withdrawnAt', 'coreHint', 'updateFailed',
  ];
  const purposes = [
    'core_service', 'ai_processing', 'proactive_push',
    'analytics', 'data_export', 'model_training',
  ];

  it('15 个语言都带齐台账文案（含六个用途名）', () => {
    expect(langs).toHaveLength(15);
    for (const lng of langs) {
      const table = (resources as any)[lng].translation.consentLedger;
      expect(table).toBeDefined();
      keys.forEach((k) => expect(typeof table[k]).toBe('string'));
      purposes.forEach((p) => expect(typeof table.purposes[p]).toBe('string'));
    }
  });

  it('英文源表与服务端用途清单逐值一致（六个 key 不多不少）', () => {
    const en = (resources as any).en.translation.consentLedger.purposes;
    expect(Object.keys(en).sort()).toEqual([...purposes].sort());
  });
});
