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

/** 与英文拼写完全撞车的同形词：不是漏翻，是该语言本来就这么写。 */
const HOMOGRAPHS: Record<string, string[]> = {
  es: ['home.social', 'reflect.aiError'],
  pt: ['home.social'],
  fr: ['home.social', 'home.stress'],
  de: ['home.details', 'home.stress'],
  it: ['home.stress'],
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

  it.each(LANGUAGES)('%s：键集合与 en 完全一致（漏键会静默回退成英文）', (lng) => {
    const table = tables[lng];
    expect(Object.keys(table).sort()).toEqual(Object.keys(en).sort());
  });

  it.each(LANGUAGES)('%s：每个值都是非空字符串', (lng) => {
    for (const [key, value] of Object.entries(tables[lng])) {
      expect(typeof value).toBe('string');
      expect(value.trim()).not.toBe('');
    }
  });

  it.each(LANGUAGES)('%s：{{占位符}}名称与个数逐键相等（少一个就是运行时渲染出裸花括号）', (lng) => {
    const bad = Object.keys(en).filter(
      (k) => k in tables[lng] && placeholders(tables[lng][k]).join(',') !== placeholders(en[k]).join(',')
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

  it.each(Object.entries(SCRIPTS))('%s：每个值都含目标文字（同字白名单除外）', (lng, re) => {
    const missing = Object.keys(en).filter(
      (k) => k in tables[lng] && !isSharedByDesign(k) && !re.test(tables[lng][k])
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
