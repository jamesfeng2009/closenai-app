/**
 * 数据共享四态与匿名分析同意守卫 · 三节「存在但占位/降级」第 3 项。
 *
 * 为什么需要它：users 表一直有独立的 analytics_consent 列，后端也按它整批丢弃上报，
 * 但接口不返回、没有修改入口 —— 界面把两件事（AI 处理 / 匿名分析）显示成一个布尔，
 * 用户既看不见自己同意了几件事，也没有地方撤回分析那一项。这类缺口不报错，只能钉住。
 *
 * 覆盖：四态真值表、i18n 键 15 语言齐备、track() 门闸行为、撤回路径（停入队 + 清本地队列
 * + 两种如实文案）、前后端端点契约、api↔analytics 的循环依赖约束。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';
import {
  SHARING_STATE_KEYS,
  sharingHasConsent,
  sharingState,
  type SharingState,
} from '../src/config/consent';

jest.mock('../src/services/api', () => ({
  postAnalyticsBatch: jest.fn(),
}));

const LANGUAGES = Object.keys(resources);

/** 分析行自身要用的键；四态值键另从 SHARING_STATE_KEYS 取，避免两处各写一份。 */
const CONSENT_KEYS = [
  'profile.analyticsRow',
  'profile.analyticsHint',
  'profile.analyticsErased',
  'profile.analyticsErasePending',
];

function codeOnly(rel: string): string {
  return fs
    .readFileSync(path.join(__dirname, '..', rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

function readKey(lng: string, dotted: string): unknown {
  return dotted.split('.').reduce<any>((o, p) => o?.[p], (resources as any)[lng].translation);
}

describe('四态真值表（2×2，不由任一边单独决定）', () => {
  it.each([
    [true, true, 'both'],
    [true, false, 'ai_only'],
    [false, true, 'analytics_only'],
    [false, false, 'none'],
  ] as const)('sharingState(ai=%s, analytics=%s) = %s', (ai, analytics, expected) => {
    expect(sharingState(ai, analytics)).toBe(expected);
  });

  it('四项各有独立键，且都挂在 profile 命名空间下', () => {
    const keys = Object.values(SHARING_STATE_KEYS);
    expect(keys).toHaveLength(4);
    expect(new Set(keys).size).toBe(4);
    for (const k of keys) expect(k.startsWith('profile.')).toBe(true);
  });

  it('只有全拒绝算"无同意"：三种部分同意不能显示成未同意', () => {
    const states: SharingState[] = ['both', 'ai_only', 'analytics_only', 'none'];
    expect(states.filter(sharingHasConsent)).toEqual(['both', 'ai_only', 'analytics_only']);
  });

  it.each(LANGUAGES)('%s：四态文案 + 分析行 8 个键都存在且非空', (lng) => {
    for (const key of [...Object.values(SHARING_STATE_KEYS), ...CONSENT_KEYS]) {
      expect(typeof readKey(lng, key)).toBe('string');
      expect((readKey(lng, key) as string).trim().length).toBeGreaterThan(0);
    }
  });
});

describe('文案语义（不能把两件事说成一件事）', () => {
  it('zh：both 同时点名两项；none 明确"两项都未同意"', () => {
    expect(readKey('zh', 'profile.sharingBoth')).toContain('AI');
    expect(readKey('zh', 'profile.sharingBoth')).toContain('分析');
    expect(readKey('zh', 'profile.sharingAiOnly')).toContain('仅');
    expect(readKey('zh', 'profile.sharingAnalyticsOnly')).toContain('仅');
    expect(readKey('zh', 'profile.sharingNone')).toContain('两项');
  });

  it('zh：分析行说清"不含书写内容"（分析只收行为元数据）', () => {
    const hint = readKey('zh', 'profile.analyticsHint') as string;
    expect(hint).toContain('不含');
    expect(hint).toContain('内容');
  });

  it('撤回后两种结果是两句话：已删除 ≠ 待删除', () => {
    const erased = readKey('zh', 'profile.analyticsErased') as string;
    const pending = readKey('zh', 'profile.analyticsErasePending') as string;
    expect(erased).not.toBe(pending);
    expect(erased).toContain('删除');
    // 待删除那句不能承诺已删除
    expect(pending).toContain('待');
  });
});

describe('track() 的本地门闸（撤回即停止入队）', () => {
  // 每个用例都从干净状态开始：门闸是模块级状态，残留会让后续用例假绿
  beforeEach(() => {
    jest.resetModules();
  });

  function loadAnalytics() {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require('../src/services/analytics');
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const repo = require('../src/services/db/analyticsEventsRepository');
    return { mod, repo };
  }

  it('默认允许入队：本地只有匿名元数据，出网由后端按 consent 整批丢弃', () => {
    const { mod, repo } = loadAnalytics();
    const spy = jest.spyOn(repo.analyticsEventsRepo, 'enqueue').mockImplementation(() => {});
    mod.track('screen_view', { screen: 'Home' });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(mod.analyticsConsentAllowedValue()).toBe(true);
    spy.mockRestore();
  });

  it('灌入 false 后 track() 不再入队；改回 true 恢复', () => {
    const { mod, repo } = loadAnalytics();
    const spy = jest.spyOn(repo.analyticsEventsRepo, 'enqueue').mockImplementation(() => {});

    mod.setAnalyticsConsentAllowed(false);
    mod.track('screen_view', { screen: 'Home' });
    expect(spy).not.toHaveBeenCalled();
    expect(mod.analyticsConsentAllowedValue()).toBe(false);

    mod.setAnalyticsConsentAllowed(true);
    mod.track('screen_view', { screen: 'Home' });
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });

  it('clearAnalyticsQueue 走仓储 clearAll（不是 cleanupSynced：那只会漏掉未上送的）', () => {
    const { mod, repo } = loadAnalytics();
    const allSpy = jest.spyOn(repo.analyticsEventsRepo, 'clearAll').mockImplementation(() => {});
    const cleanSpy = jest
      .spyOn(repo.analyticsEventsRepo, 'cleanupSynced')
      .mockImplementation(() => {});
    mod.clearAnalyticsQueue();
    expect(allSpy).toHaveBeenCalledTimes(1);
    expect(cleanSpy).not.toHaveBeenCalled();
    allSpy.mockRestore();
    cleanSpy.mockRestore();
  });
});

describe('ProfileScreen 接线守卫', () => {
  const src = codeOnly('src/screens/ProfileScreen.tsx');

  it('共享行取值走四态，且两个 flag 都参与（退回单布尔就会被这条抓到）', () => {
    const at = src.indexOf('t(SHARING_STATE_KEYS[sharingState(');
    expect(at).toBeGreaterThan(-1);
    const call = src.slice(at, at + 160);
    expect(call).toContain('profile?.ai_consent');
    expect(call).toContain('profile?.analytics_consent');
  });

  it('值文案颜色按四态的"有无同意项"决定，不按单个布尔', () => {
    expect(src).toContain('sharingHasConsent(sharingState(');
  });

  it('拿到 profile 就灌门闸（api 不能反向 import analytics，只能在调用点灌）', () => {
    const start = src.indexOf('const loadProfile');
    expect(start).toBeGreaterThan(-1);
    const block = src.slice(start, start + 500);
    expect(block).toContain('setAnalyticsConsentAllowed(data.analytics_consent)');
  });

  it('撤回路径：停入队 + 清本地队列两件一起做，且顺序是先设标志再清', () => {
    const start = src.indexOf('const onAnalyticsConsentChange');
    expect(start).toBeGreaterThan(-1);
    const end = src.indexOf('const onSelectPushTime', start);
    expect(end).toBeGreaterThan(start);
    const handler = src.slice(start, end);

    expect(handler).toContain('setAnalyticsConsentAllowed(res.analytics_consent)');
    const gate = handler.indexOf('if (!consent)');
    expect(gate).toBeGreaterThan(-1);
    const revokeBlock = handler.slice(gate, gate + 500);
    expect(revokeBlock).toContain('clearAnalyticsQueue()');
    // 两种结果分别对应两条文案，不能一律宣称已删除
    expect(revokeBlock).toContain('res.posthog_erased ?');
    expect(revokeBlock).toContain("t('profile.analyticsErased')");
    expect(revokeBlock).toContain("t('profile.analyticsErasePending')");
  });

  it('分析行是独立一行，点击切换的是 analytics_consent 而非 ai_consent', () => {
    const start = src.indexOf("onPress={() => onAnalyticsConsentChange(");
    expect(start).toBeGreaterThan(-1);
    const end = src.indexOf('</TouchableOpacity>', start);
    expect(end).toBeGreaterThan(start);
    const block = src.slice(start, end);
    expect(block).toContain('!profile?.analytics_consent');
    expect(block).toContain("t('profile.analyticsRow')");
    // 不是把原来的数据共享行改了个名：那一行仍然独立存在
    expect(block).not.toContain("t('profile.dataSharing')");
    expect(src).toContain("onPress={() => setConsentVisible(true)}");
  });
});

describe('前后端契约与依赖方向', () => {
  const api = codeOnly('src/services/api.ts');

  it('api.ts 端点路径与 body 字段和后端 router 逐字对齐', () => {
    const backend = codeOnly('../backend/routers/user.py');
    const front = api.match(/'\/user\/analytics-consent'/);
    expect(front).not.toBeNull();
    expect(backend).toContain('@router.post("/user/analytics-consent")');
    // 请求体只有一个布尔，字段名两边必须一致
    expect(api).toContain('JSON.stringify({ consent })');
    expect(backend).toContain('request: AIConsentUpdate');
  });

  it('响应键两端一致（前端类型跟着后端返回走）', () => {
    const service = codeOnly('../backend/services/user_service.py');
    expect(api).toContain('analytics_consent: boolean; posthog_erased: boolean');
    expect(service).toContain('"analytics_consent"');
    expect(service).toContain('"posthog_erased"');
  });

  it('api.ts 不得 import analytics（analytics 已依赖 api，反向会成环）', () => {
    expect(api).not.toMatch(/from '\.\/analytics'/);
    expect(api).not.toMatch(/services\/analytics/);
  });

  it('profile 响应模型下发 analytics_consent（否则前端永远读到 undefined）', () => {
    const schemas = codeOnly('../backend/models/schemas.py');
    const service = codeOnly('../backend/services/user_service.py');
    expect(schemas).toMatch(/analytics_consent:\s*bool/);
    expect(service).toContain('analytics_consent=user.analytics_consent');
  });
});
