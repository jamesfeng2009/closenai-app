/**
 * 15 语言文案完整性守卫 · #39。
 *
 * 为什么需要它：这轮之前有 13 个语言里约 270 个键的值**逐字等于英文**（i18next 的
 * fallbackLng 让它们在真机上直接显英文，所以没有任何一处会报错），还有一个键
 * （relationship.rewardTitle）在 13 个文件里**根本不存在**——漏键比漏翻译更难发现，
 * 因为它只会静默回退。这类缺口不修，提审时「支持 15 种语言」就是一句假话。
 *
 * 断言按语言逐个做，不按「所有语言都一样」做：只要有一个语言漏了就得红，
 * 早期用「13 个语言同时同字」做筛子恰好漏掉了这类单语言残留。
 */
import resources from '../src/i18n/locales';

const SOURCE = 'en';
const LANGUAGES = Object.keys(resources).filter((l) => l !== SOURCE);

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

const en = flatten((resources as any)[SOURCE].translation as Tree);
const tables: Record<string, Record<string, string>> = Object.fromEntries(
  LANGUAGES.map((l) => [l, flatten((resources as any)[l].translation as Tree)])
);

/**
 * 复数形态键（P0-2）：count 驱动 i18next 的 CLDR 类别选择。
 * 这 8 个键拆成 `_one`/`_few`/`_many`/`_other` 变体后，扁平键名因语言而异
 * （zh 只有 `_other`，ru 有四档），键集断言必须按逻辑键归一，否则全红。
 */
const PLURAL_KEYS = new Set([
  'relationship.toNextLevel',
  'relationship.dayChats',
  'home.milestoneStreak',
  'personality.quotaLeft',
  'personality.signalsCount',
  'personality.stageEta',
  'share.streak',
  'insights.chatCount',
]);

const PLURAL_SUFFIXES = ['one', 'two', 'few', 'many', 'other'];

function pluralLogicalKey(key: string): string {
  const idx = key.lastIndexOf('_');
  if (idx < 0) return key;
  const suffix = key.slice(idx + 1);
  if (!PLURAL_SUFFIXES.includes(suffix)) return key;
  const base = key.slice(0, idx);
  return PLURAL_KEYS.has(base) ? base : key;
}

/**
 * 允许与英文同字的键：品牌与套餐名、语言自称呼（enonyms 一律不译）、
 * 纯格式串、厂商名。逐条给理由，防止这张表变成懒人兜底区。
 */
const SHARED_BY_DESIGN = [
  /^language\.[a-z]{2}$/, // 各语言的自称：English / 日本語 / 한국어，译了反而错
  /^paywall\.planPro(Plus)?$/, // 套餐名
  /^profile\.hourOption$/, // "{{hour}}:00"，只有占位符
  /^profile\.version$/, // 版本号
  /^profile\.modelSourceValue$/, // "Gemini API (Google)"，厂商名
  /^demo\.personaName$/, // 演示角色名（罗马字）
];

/**
 * 与英文拼写完全撞车的同形词：不是漏翻，是该语言本来就这么写。
 *
 * 这张表在 2026-09-30 的母语审校轮（L2）里补全过：13 个语言逐键复核后，
 * 审校认定「该用英文原词」的都进了这里，理由逐条写死——
 * 主要是 Enneagram / Insight / Combo / FAQ / Password 这类 app 界面通用借词，
 * 以及 16personalities 官方译名本身就是拉丁词（id 的 Virtuoso）。
 * 下面的「不许留死条目」用例保证：此刻仍逐字同英文才算数。
 */
const HOMOGRAPHS: Record<string, string[]> = {
  es: ['home.social', 'reflect.aiError', 'relationship.dayInsight'], // 界面同款词 Insight
  pt: ['home.social', 'share.combo'], // Combo 是葡语正字，不是漏翻
  fr: [
    'home.social',
    'home.stress',
    'relationship.badgesTitle',
    'relationship.dayBadges',
    'personality.enneagramResult',
    'insights.chatCount_one',
    'insights.chatCount_other',
  ], // Badges / Type / conversations 均为法语 app 常规写法
  de: ['home.details', 'home.stress', 'share.combo', 'help.sectionFaq'],
  it: ['home.stress', 'relationship.dayInsight', 'auth.password'], // Password / Insight 意大利语直接借用
  id: ['personality.enneagramTitle', 'profile.frameworkOption.enneagram', 'mbti.ISTP'], // Virtuoso 是 16P 印尼语官方译名
  ms: [
    'personality.enneagramLabel',
    'personality.enneagramTitle',
    'profile.frameworkOption.enneagram',
  ],
  vi: [
    'personality.enneagramLabel',
    'personality.enneagramTitle',
    'profile.frameworkOption.enneagram',
  ],
  km: [
    'personality.enneagramLabel',
    'personality.enneagramTitle',
    'profile.frameworkOption.enneagram',
  ],
};

function isSharedByDesign(key: string): boolean {
  return SHARED_BY_DESIGN.some((re) => re.test(key));
}

function placeholders(value: string): string[] {
  return (value.match(/\{\{[A-Za-z0-9_]+\}\}/g) ?? []).sort();
}

describe('15 语言文案表与英文源表同构', () => {
  it('除英文外有 14 个语言', () => {
    expect(LANGUAGES).toHaveLength(14);
  });

  it.each(LANGUAGES)('%s：键集合与 en 完全一致（漏键会静默回退成英文；复数键按逻辑键归一去重）', (lng) => {
    const table = tables[lng];
    const logical = (tbl: Record<string, string>): string[] =>
      [...new Set(Object.keys(tbl).map(pluralLogicalKey))].sort();
    expect(logical(table)).toEqual(logical(en));
  });

  it.each(LANGUAGES)('%s：每个值都是非空字符串', (lng) => {
    for (const [key, value] of Object.entries(tables[lng])) {
      expect(typeof value).toBe('string');
      expect(value.trim()).not.toBe('');
    }
  });

  it.each(LANGUAGES)('%s：{{占位符}}名称与个数逐键相等（复数键按逻辑键聚合；少一个就是运行时渲染出裸花括号）', (lng) => {
    const table = tables[lng];
    const unionFor = (tbl: Record<string, string>, logicalKey: string): string[] => [
      ...new Set(
        Object.keys(tbl)
          .filter((k) => pluralLogicalKey(k) === logicalKey)
          .flatMap((k) => placeholders(tbl[k]))
      ),
    ].sort();
    const bad = [...new Set(Object.keys(en).map(pluralLogicalKey))].filter(
      (logicalKey) => unionFor(en, logicalKey).join(',') !== unionFor(table, logicalKey).join(',')
    );
    expect(bad).toEqual([]);
  });

  it.each(LANGUAGES)('%s：换行转义个数逐键相等', (lng) => {
    const bad = Object.keys(en).filter(
      (k) => k in tables[lng] && (tables[lng][k].match(/\n/g) ?? []).length !== (en[k].match(/\n/g) ?? []).length
    );
    expect(bad).toEqual([]);
  });
});

describe('复数键的 CLDR 类别齐备（P0-2）', () => {
  // i18next v23 按 Intl.PluralRules 选类别，解析链只有 [基础键, 命中类别键] 两级——
  // 命中类别缺键时不会回退到 _other，而是裸键名直出。所以每个语言必须备齐
  // 自己的全部类别：en/de/es/fr/it/pt = one+other；ru = one/few/many/other；
  // zh/ja/ko/th/vi/km/id/ms（CLDR 单形态语言）= 仅 other。
  const CATEGORIES: Record<string, string[]> = {
    en: ['one', 'other'],
    de: ['one', 'other'],
    es: ['one', 'other'],
    fr: ['one', 'other'],
    it: ['one', 'other'],
    pt: ['one', 'other'],
    ru: ['one', 'few', 'many', 'other'],
  };
  const allTables: Record<string, Record<string, string>> = { en, ...tables };

  it.each(Object.keys(allTables))('%s：8 个复数键的变体键恰好等于该语言的类别集（缺档=裸键名，多档=死键）', (lng) => {
    const table = allTables[lng];
    for (const base of PLURAL_KEYS) {
      const present = Object.keys(table).filter(
        (k) => k.startsWith(`${base}_`) && PLURAL_SUFFIXES.includes(k.slice(base.length + 1))
      );
      const expected = (CATEGORIES[lng] ?? ['other']).map((c) => `${base}_${c}`);
      expect(present.sort()).toEqual(expected.sort());
    }
  });
});

describe('13 个语言不留英文兜底（#39）', () => {
  it.each(LANGUAGES)('%s：没有任何一个值逐字等于英文，除非落在设计同字白名单', (lng) => {
    const allowed = new Set(HOMOGRAPHS[lng] ?? []);
    const untranslated = Object.keys(en).filter(
      (k) => k in tables[lng] && tables[lng][k] === en[k] && !isSharedByDesign(k) && !allowed.has(k)
    );
    expect(untranslated).toEqual([]);
  });

  it('同形词豁免表不许留死条目', () => {
    // 允许与英文同字的"同形词"是会过期的：源文案一改就不再同字。
    // 留着死条目等于给以后的漏翻预留合法位置，所以逐条要求它此刻真的同字。
    for (const [lng, keys] of Object.entries(HOMOGRAPHS)) {
      for (const key of keys) {
        expect(tables[lng][key]).toBe(en[key]);
      }
    }
  });
});

describe('非拉丁文字语言必须真的落成该文字', () => {
  // 兜住"改了个别词但整句仍是英文"这类漏翻：字节相等那道闸放得过它。
  // 实测抓到的就是 km 的 reflect.quotaMessage —— 整句英文，只差几个词。
  const SCRIPTS: Record<string, RegExp> = {
    zh: /[\u4e00-\u9fff]/,
    ja: /[\u3040-\u30ff\u4e00-\u9fff]/, // 片假名或汉字都算落成日文
    ko: /[\uac00-\ud7af]/,
    th: /[\u0e00-\u0e7f]/,
    km: /[\u1780-\u17ff]/,
    ru: /[\u0400-\u04ff]/,
  };

  it.each(Object.entries(SCRIPTS))('%s：每个值都含该语言文字（同形词豁免除外）', (lng, re) => {
    // Enneagram / Insight 这类借词按审校认定就该写拉丁文，所以两道闸共用同一张豁免表，
    // 免得「不许与英文同字」和「必须含本文字」互相把借词逼成生造词。
    const allowed = new Set(HOMOGRAPHS[lng] ?? []);
    const missing = Object.keys(en).filter(
      (k) => k in tables[lng] && !isSharedByDesign(k) && !allowed.has(k) && !re.test(tables[lng][k])
    );
    expect(missing).toEqual([]);
  });
});

describe('任何语言都不许留整句英文', () => {
  /** 连续英文虚词的最大长度——拉丁语系的语言不会这样连用 these/of/your。 */
  function englishRun(value: string): number {
    const stop = new Set([
      'the', 'of', 'to', 'and', 'you', 'your', 'with', 'for', 'please', 'our', 'is', 'are',
      'this', 'that', 'it', 'we', 'all', 'not', 'be', 'a', 'an', 'in', 'on', 'my', 'me',
      'us', 'their', 'them', 'from', 'will', 'have', 'has', 'new', 'can', 'get', 'set',
    ]);
    const words = value.replace(/\{\{[A-Za-z0-9_]+\}\}/g, ' ').match(/[A-Za-z']+/g) ?? [];
    let run = 0;
    let best = 0;
    for (const w of words) {
      if (stop.has(w.toLowerCase())) {
        run += 1;
        best = Math.max(best, run);
      } else {
        run = 0;
      }
    }
    return best;
  }

  it.each(LANGUAGES)('%s：没有整句英文的值（连续 ≥3 个英文虚词）', (lng) => {
    const hits = Object.keys(en).filter(
      (k) => k in tables[lng] && !isSharedByDesign(k) && englishRun(tables[lng][k]) >= 3
    );
    expect(hits).toEqual([]);
  });
});
