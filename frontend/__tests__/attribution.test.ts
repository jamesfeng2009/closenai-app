/**
 * KG-20 渠道归因闸。
 *
 * 五组判据，逐条可被打红（`scripts/mutate_kg19_kg20_gates.py`）：
 * 1. 解析：UTM 五键 + App Store 的 `pt/ct/mt` + 品牌域落地路径；非品牌域整条判 null。
 * 2. 清洗：解码、小写、空格归一；非法字符只丢**那一个键**，不连坐整条归因。
 * 3. PII 拒绝：邮箱／连续 7 位以上数字／分段电话／hex 长串／base64 段／嵌套 URL 全部丢，
 *    并配一条**正向证人**（`nov26-sale` 必须放行），否则「全挡」也算假绿。
 * 4. 落盘语义：首触不覆盖、最近一次覆盖、captures 只数本机看到的唤起次数；
 *    读回时重走同一套清洗（本机数据可能被旧版本或手工改写）。
 * 5. 边界证人：这个模块**不上送**——不 import analytics、不调 track、不解析推荐码。
 *    白名单属性键（决策 D6）与服务端安装来源列（决策 D2）都不是本任务能扩的东西。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

// analytics 也被 mock：真实模块会拖进 api/SQLite 依赖链，node 环境跑不动。
// 更要紧的是让 §5 那条「不许 track」的边界证人**自己**红——若这里不 mock，
// 将来谁在归因模块加一行 track，整套用例会因依赖链直接崩，红就不是那条判据的红。
jest.mock('../src/services/analytics', () => ({
  track: jest.fn(),
}));

jest.mock('@react-native-async-storage/async-storage', () => {
  const store = new Map<string, string>();
  return {
    __esModule: true,
    default: {
      getItem: jest.fn(async (key: string) => store.get(key) ?? null),
      setItem: jest.fn(async (key: string, value: string) => {
        store.set(key, value);
      }),
      removeItem: jest.fn(async (key: string) => {
        store.delete(key);
      }),
      clear: jest.fn(async () => {
        store.clear();
      }),
    },
  };
});

import * as fs from 'fs';
import * as path from 'path';

import {
  ATTRIBUTION_STORAGE_KEY,
  attributionForDiagnostics,
  captureAttributionFromUrl,
  clearAttribution,
  parseAttribution,
  readAttribution,
  sanitizeLandingPath,
  sanitizeUtmValue,
  UTM_KEYS,
} from '../src/services/attribution';

const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'services', 'attribution.ts'),
  'utf8',
);

const BRAND = 'https://closenai.app';

beforeEach(async () => {
  await AsyncStorage.clear();
});

// ── 1. 解析 ─────────────────────────────────────────────────────────────────

describe('parseAttribution 的受理面', () => {
  it('品牌域下的五个 UTM 键与落地路径一起收', () => {
    const parsed = parseAttribution(
      `${BRAND}/en/mood-tracker?utm_source=reddit&utm_medium=social&utm_campaign=nov26-sale&utm_term=mood&utm_content=b`,
    );
    expect(parsed).not.toBeNull();
    expect(parsed!.utm).toEqual({
      utm_source: 'reddit',
      utm_medium: 'social',
      utm_campaign: 'nov26-sale',
      utm_term: 'mood',
      utm_content: 'b',
    });
    expect(parsed!.landingPath).toBe('/en/mood-tracker');
    expect(parsed!.campaign).toEqual({});
    expect(Date.parse(parsed!.capturedAt)).not.toBeNaN();
  });

  it('App Store campaign link 只认 pt/ct/mt 三个官方令牌', () => {
    const parsed = parseAttribution(`${BRAND}/?pt=searchterm&ct=brand&mt=3&ignored=yes`);
    expect(parsed!.campaign).toEqual({ pt: 'searchterm', ct: 'brand', mt: '3' });
  });

  it('未知参数与未知 utm_ 变体一律忽略，不「先留着以后看」', () => {
    // kludge 的值故意取一个**完全合法**的形状（`abc`）：一旦有人把它加进 allowlist，
    // 这条就会收下来并变红。用它原本带 11 位数字的话，PII 层会先挡住，判据就没区分度了。
    const parsed = parseAttribution(
      `${BRAND}/?utm_source=partner&utm_kludge_id=abc&fbclid=aB3dE6fG9hI2jK5lM7nP9qR1sT==`,
    );
    expect(Object.keys(parsed!.utm)).toEqual(['utm_source']);
    expect(parsed!.utm.utm_source).toBe('partner');
  });

  it('非品牌域整条判 null：别的域贴的渠道标签不作数', () => {
    expect(parseAttribution('https://evil.example.com/en/mood-tracker?utm_source=reddit')).toBeNull();
    expect(parseAttribution('https://closenai.app.evil.example/?utm_source=reddit')).toBeNull();
    expect(parseAttribution(null)).toBeNull();
    expect(parseAttribution('')).toBeNull();
  });

  it('品牌子域放行（预发与测试环境同一判据）', () => {
    expect(parseAttribution('https://web.closenai.app/?utm_source=reddit')!.utm.utm_source).toBe(
      'reddit',
    );
  });

  it('既无 UTM 也无 campaign、且落地路径不合法时返回 null 而不是空对象', () => {
    expect(parseAttribution('https://closenai.app')).toBeNull();
  });
});

// ── 2. 清洗 ─────────────────────────────────────────────────────────────────

describe('sanitizeUtmValue 的归一与字符集', () => {
  it('大小写、首尾空格、%20、加号都归一到同一个小写下划线值', () => {
    expect(sanitizeUtmValue('  Reddit ')).toBe('reddit');
    expect(sanitizeUtmValue('mo%20od')).toBe('mo_od');
    expect(sanitizeUtmValue('mo+od')).toBe('mo_od');
    expect(sanitizeUtmValue('PODCAST-01')).toBe('podcast-01');
  });

  it('中文、emoji、逗号、冒号、空值都判 null', () => {
    expect(sanitizeUtmValue('小红书')).toBeNull();
    expect(sanitizeUtmValue('ok🚀')).toBeNull();
    expect(sanitizeUtmValue('a,b')).toBeNull();
    expect(sanitizeUtmValue('')).toBeNull();
    expect(sanitizeUtmValue(null)).toBeNull();
  });

  it('超过 48 个字符判 null（长度闸而不是截断）', () => {
    expect(sanitizeUtmValue('x'.repeat(48))).toBe('x'.repeat(48));
    expect(sanitizeUtmValue('x'.repeat(49))).toBeNull();
  });

  it('畸形百分号转义直接判 null，不猜它想表达什么', () => {
    expect(sanitizeUtmValue('%zz')).toBeNull();
    expect(sanitizeUtmValue('a%2')).toBeNull();
  });

  it('单个键脏了不连坐：其余合法键照常收下', () => {
    const parsed = parseAttribution(`${BRAND}/?utm_source=reddit&utm_medium=小红书`);
    expect(parsed!.utm).toEqual({ utm_source: 'reddit' });
  });
});

// ── 3. PII 拒绝（含正向证人）─────────────────────────────────────────────────

describe('PII 形状一律丢弃', () => {
  it.each([
    ['邮箱', 'mei.li@example.com'],
    ['连续 7 位以上数字（含完整日期串）', '20261101_sale'],
    ['分段电话', '138-0013-8000'],
    ['hex 长串（设备指纹／哈希）', 'a1b2c3d4e5f6a7b8c9d0e1f2'],
    ['base64 段（带 padding 的令牌）', 'eyJhbGciOiJIUzI1NiIsInR5cCI2fQ=='],
    ['嵌套 URL', 'https%3A%2F%2Fother.example'],
  ])('%s 判 null', (_label, value) => {
    expect(sanitizeUtmValue(value)).toBeNull();
  });

  it('正向证人：正常渠道命名必须放行，否则「全挡」是假绿', () => {
    expect(sanitizeUtmValue('nov26-sale')).toBe('nov26-sale');
    expect(sanitizeUtmValue('podcast_s02e11')).toBe('podcast_s02e11');
    expect(sanitizeUtmValue('b2')).toBe('b2');
  });

  it('PII 值进不了落盘：整个归因里查不到 @ 与连续数字串', async () => {
    const stored = await captureAttributionFromUrl(
      `${BRAND}/en/journal-prompts?utm_campaign=mei.li@example.com&utm_source=reddit&utm_medium=13800138000`,
    );
    expect(stored).not.toBeNull();
    const text = JSON.stringify(stored);
    expect(text).not.toMatch(/mei\.li|13800138000/);
    expect(stored!.last.utm).toEqual({ utm_source: 'reddit' });
  });

  it('落地路径只留规范化路径，查询串与片段一律不带', () => {
    expect(sanitizeLandingPath(`${BRAND}/en/overthinking/?q=a#top`)).toBe('/en/overthinking');
    expect(sanitizeLandingPath(`${BRAND}/`)).toBe('/');
    expect(sanitizeLandingPath(`${BRAND}/R/../../etc`)).toBeNull();
    // 上一条先被「首字符必须小写」挡住，字符集里放不放点号都红不了它（2026-11-02 变异 A10
    // 就是在这里失手的）。这一条只有**点号规则**能判住：小写开头、含 `..` 的穿越串。
    expect(sanitizeLandingPath(`${BRAND}/en/../../etc/passwd`)).toBeNull();
    expect(sanitizeLandingPath(`${BRAND}/en/小红书`)).toBeNull();
  });
});

// ── 4. 落盘语义 ─────────────────────────────────────────────────────────────

describe('capture / read 的留存语义', () => {
  it('首次触达写入 first 与 last，重复唤起只动 last 与 captures', async () => {
    const first = await captureAttributionFromUrl(`${BRAND}/?utm_source=reddit&utm_campaign=a`);
    expect(first!.captures).toBe(1);
    const second = await captureAttributionFromUrl(`${BRAND}/?utm_source=podcast&utm_campaign=b`);
    expect(second!.captures).toBe(2);
    expect(second!.first.utm).toEqual({ utm_source: 'reddit', utm_campaign: 'a' });
    expect(second!.last.utm).toEqual({ utm_source: 'podcast', utm_campaign: 'b' });
  });

  it('没有归因的唤起不写盘，也不把 captures 加上去', async () => {
    await captureAttributionFromUrl(`${BRAND}/`);
    expect(await AsyncStorage.getItem(ATTRIBUTION_STORAGE_KEY)).toBeNull();
    expect(await readAttribution()).toBeNull();
  });

  it('读回时重走清洗：本机被改写成带邮箱的脏值，读出来必须没有', async () => {
    await AsyncStorage.setItem(
      ATTRIBUTION_STORAGE_KEY,
      JSON.stringify({
        first: {
          utm: { utm_source: 'someone@example.com', utm_medium: 'clean' },
          campaign: {},
          landingPath: '/en/mood-tracker',
          capturedAt: '2026-11-02T00:00:00.000Z',
        },
        last: { utm: { utm_source: 'a'.repeat(40) }, campaign: {}, capturedAt: 'not-a-date' },
        captures: 0,
      }),
    );
    const stored = await readAttribution();
    expect(stored!.first.utm).toEqual({ utm_medium: 'clean' });
    expect(JSON.stringify(stored)).not.toMatch(/example\.com/);
    expect(stored!.captures).toBe(1);
    expect(Date.parse(stored!.last.capturedAt)).not.toBeNaN();
  });

  it('畸形 JSON 与全非法快照按「没有归因」处理，不抛', async () => {
    await AsyncStorage.setItem(ATTRIBUTION_STORAGE_KEY, '{not json');
    expect(await readAttribution()).toBeNull();
    await AsyncStorage.setItem(ATTRIBUTION_STORAGE_KEY, JSON.stringify({ first: {}, last: {} }));
    expect(await readAttribution()).toBeNull();
  });

  it('clearAttribution 之后读回 null', async () => {
    await captureAttributionFromUrl(`${BRAND}/?utm_source=reddit`);
    await clearAttribution();
    expect(await readAttribution()).toBeNull();
  });
});

// ── 5. 诊断输出与边界证人 ────────────────────────────────────────────────────

describe('attributionForDiagnostics 的口径', () => {
  it('输出 first.* 与 captures，last 只在与 first 不同时才重复出现', async () => {
    await captureAttributionFromUrl(`${BRAND}/en/mood-tracker?utm_source=reddit&utm_medium=social`);
    await captureAttributionFromUrl(`${BRAND}/en/journal-prompts?utm_source=reddit`);
    const lines = attributionForDiagnostics(await readAttribution());
    expect(lines).toEqual([
      'first.utm_source=reddit',
      'first.utm_medium=social',
      'first.landing_path=/en/mood-tracker',
      `first.captured_at=${(await readAttribution())!.first.capturedAt}`,
      'last.landing_path=/en/journal-prompts',
      'captures=2',
    ]);
    expect(lines.join('\n')).not.toMatch(/utm_campaign=undefined|undefined|null/);
  });

  it('没有归因时返回空数组，不返回一行占位', () => {
    expect(attributionForDiagnostics(null)).toEqual([]);
  });

  it('UTM 键清单就是那五个，没有第六个', () => {
    expect(UTM_KEYS).toEqual([
      'utm_source',
      'utm_medium',
      'utm_campaign',
      'utm_term',
      'utm_content',
    ]);
  });
});

describe('不上送、不碰推荐码（决策 D6 与 D2 的边界证人）', () => {
  it('这个模块不 import analytics、不调 track、不 enqueue', () => {
    expect(SOURCE).not.toMatch(/from '\.\/analytics'/);
    expect(SOURCE).not.toMatch(/\btrack\s*\(/);
    expect(SOURCE).not.toMatch(/postAnalyticsBatch|analyticsEventsRepo/);
  });

  it('推荐码仍只有 referral.ts 一个解析处', () => {
    expect(SOURCE).not.toMatch(/[?&]ref=/);
    expect(SOURCE).not.toMatch(/\/r\/\(/);
    const referral = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'services', 'referral.ts'),
      'utf8',
    );
    expect(referral).toMatch(/parseReferralFromUrl/);
  });

  it('文件头把「不上送」与「下界」写死，防止后人把它当渠道全量', () => {
    expect(SOURCE).toMatch(/不上送/);
    expect(SOURCE).toMatch(/下界/);
    expect(SOURCE).toMatch(/ALLOWED_PROPERTY_KEYS/);
  });
});
