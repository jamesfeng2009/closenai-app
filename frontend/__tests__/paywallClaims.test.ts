/**
 * KG-24 的回归守卫：付费墙只许卖**代码里真存在的权益**。
 *
 * 为什么要有这条：2026-11-01 之前的四条 Pro 权益写的是 `Unlimited AI conversations`／
 * `Priority model responses`，Pro+ 写的是 `100 AI conversations per day`／
 * `Unlimited personality refreshes`／`Multiple proactive messages per day`。
 * 实现里的真值是：对话轮数 pro=100／pro_plus=1000（`backend/config.py`）、
 * 画像刷新 pro=5/月／pro_plus=999/月（`personality_service.py:563`）、
 * AI 主动开场**所有档位都一天一条**（`cron_service.py` 的 `proactive:<uid>:<本地日>:0` 幂等槽）。
 * 也就是说旧文案里有三条在卖不存在的东西，退款与投诉都算在交付失败上。
 *
 * 两条一起钉（归零闸会因改名或删键而假绿）：
 *   ① 禁语在 15 个语言的 `paywall` 段内 0 命中；
 *   ② 正向证人——真实数字（5／999／1000）必须出现在对应那一条里，且三个改过名的键在册。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const LOCALE_DIR = path.join(__dirname, '../src/i18n/locales');
const LANGS = Object.keys(resources);

/** 只取 `paywall` 段：`subtitle`／「无限导出」这类词在别的段里是合法表述。 */
function paywallBlock(lng: string): string {
  const src = fs.readFileSync(path.join(LOCALE_DIR, `${lng}.ts`), 'utf8');
  const start = src.indexOf('"paywall": {');
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf('\n  },', start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

/** 不许再出现的「未实现能力」表述（跨 15 语）。 */
const BANNED = [
  { label: 'unlimited', re: /\bunlimited\b/i },
  { label: 'zh 无限/不限', re: /无限|不限/ },
  { label: 'priority model', re: /priority|优先|優先/i },
  { label: '多条主动消息', re: /multiple proactive|多条主动|複数の主動/i },
];

/** KG-24 改过名的三个键：旧名留着＝下一个人还会照着键名把「unlimited」写回去。 */
const RENAMED_FROM = ['featureUnlimitedChat', 'featurePriorityLLM', 'plusFeatureProactive'];
const RENAMED_TO = ['featureContinuity', 'featureReports', 'plusFeaturePool'];

describe('KG-24：付费墙文案不承诺未实现能力', () => {
  it('15 个语言全部在册', () => {
    expect(LANGS).toHaveLength(15);
  });

  it.each(LANGS)('%s：paywall 段内没有禁语', (lng) => {
    const block = paywallBlock(lng);
    const hits = BANNED.map((b) => [b.label, block.match(b.re)?.[0]]).filter(([, v]) => v);
    expect(hits).toEqual([]);
  });

  it.each(LANGS)('%s：旧键名已更名，新键名在册', (lng) => {
    const block = paywallBlock(lng);
    for (const old of RENAMED_FROM) {
      expect(block).not.toContain(`"${old}"`);
    }
    for (const key of RENAMED_TO) {
      expect(block).toContain(`"${key}"`);
    }
  });

  it.each(LANGS)('%s：权益数字与实现一致（5 次/月、999 次/月、1000 条/日）', (lng) => {
    const t = (resources as any)[lng].translation.paywall;
    expect(t.featurePersonalityRefresh).toContain('5');
    expect(t.plusFeatureRefresh).toContain('999');
    expect(t.plusFeatureChat).toContain('1000');
    expect(t.featureTimeline).toMatch(/30/);
    expect(t.featureTimeline).toMatch(/365/);
  });

  it('英文逐字钉住四条权益的落点（台账要求「四条都能指回一个真实功能」）', () => {
    const t = (resources as any).en.translation.paywall;
    expect(t.featureReports).toContain('Monthly, quarterly and yearly reports');
    expect(t.featureContinuity).toContain('build on each other');
    expect(t.subtitle).not.toMatch(/unlimited/i);
    expect(t.compareSample).toMatch(/Example/);
    // 旧那句是对「模式」的断言（insight.pattern 尚未生产化），只能作为示例出现
    expect(t.compareSample).not.toMatch(/you write more honestly/i);
  });
});

describe('KG-24：付费墙展示的权益键与代码一致', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '../src/screens/PaywallScreen.tsx'),
    'utf8',
  );

  it('PRO_FEATURES 是四条，且就是更名后的那四条', () => {
    const list = src.match(/const PRO_FEATURES = \[([\s\S]*?)\] as const/);
    expect(list).not.toBeNull();
    const keys = list![1].split(',').map((s) => s.trim().replace(/'/g, '')).filter(Boolean);
    expect(keys).toEqual([
      'paywall.featureContinuity',
      'paywall.featureReports',
      'paywall.featurePersonalityRefresh',
      'paywall.featureTimeline',
    ]);
  });

  it('PRO_PLUS_FEATURES 里不许再挂「多条主动消息」那一条', () => {
    const list = src.match(/const PRO_PLUS_FEATURES = \[([\s\S]*?)\] as const/);
    expect(list).not.toBeNull();
    expect(list![1]).not.toMatch(/Proactive/);
    expect(list![1]).toMatch(/plusFeaturePool/);
  });

  it('四个展示键在每一语言都有非空译文', () => {
    const shown = [
      'featureContinuity',
      'featureReports',
      'featurePersonalityRefresh',
      'featureTimeline',
      'plusFeatureAllPro',
      'plusFeatureChat',
      'plusFeatureRefresh',
      'plusFeaturePool',
    ];
    for (const lng of LANGS) {
      const t = (resources as any)[lng].translation.paywall;
      for (const key of shown) {
        expect(typeof t[key]).toBe('string');
        expect(t[key].trim().length).toBeGreaterThan(0);
      }
    }
  });
});
