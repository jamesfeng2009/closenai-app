/**
 * 占星已移除的回归守卫（PRD v2.1「占星已移除」· A1 · tasks.md G 节）。
 *
 * 为什么需要它：A1 移除过一次占星，但 zodiac 推导、12 键星座文案、个人页
 * 「主要框架 = 占星」选项在之后仍留在代码里（2026-09-29 实测）。这类残留不会
 * 让任何功能变红，只会让产品在商店截图与合规声明里重新长出星座。
 *
 * 措辞黑名单（safety_service 的「星座配对 / 星座运势」）是**闸门不是功能**，
 * 它必须继续存在，所以这里只扫 i18n 与前端类型，不扫后端。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const LOCALE_DIR = path.join(__dirname, '../src/i18n/locales');
const LANGUAGES = Object.keys(resources);

/** 星座标识符。natal（本命星盘）与 swisseph（星历库）一并纳入，防换名复活。 */
const ASTRO = /zodiac|astrolog|horoscope|natal|sun[\s_]?sign|星座|占星/i;

function flatten(obj: any, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    return typeof v === 'string' ? [key] : flatten(v, key);
  });
}

describe('占星移除：15 语言文案', () => {
  it('15 个语言全部在册', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)('%s：不存在任何星座文案键', (lng) => {
    const keys = flatten((resources as any)[lng].translation);
    expect(keys.filter((k) => ASTRO.test(k))).toEqual([]);
  });

  it.each(LANGUAGES)('%s：不存在任何星座译文值', (lng) => {
    const values = flatten((resources as any)[lng].translation)
      .map((k) => k.split('.').reduce<any>((o, p) => o?.[p], (resources as any)[lng].translation))
      .filter((v) => typeof v === 'string');
    expect(values.filter((v) => ASTRO.test(v))).toEqual([]);
  });
});

describe('占星移除：前端代码', () => {
  const files: [string, string][] = [
    ['src/types/index.ts', '星座类型定义'],
    ['src/screens/ProfileScreen.tsx', '主要框架选择器'],
    ['src/screens/PersonalityScreen.tsx', '画像页星座维度块'],
    ['src/screens/ExploreScreen.tsx', '探索页画像映射'],
    ['src/services/db/migrations.ts', 'SQLite 表定义'],
    ['src/services/db/personalityProfileRepository.ts', '画像缓存行'],
  ];

  /** 只扫代码不扫注释：本文件自身就在解释"为什么移除了星座"。 */
  const codeOnly = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

  it.each(files)('%s：标识符里不出现星座', (rel) => {
    const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', rel), 'utf8'));
    expect(src.match(ASTRO)).toBeNull();
  });

  it('PrimaryFramework 只剩两枚举', () => {
    const src = fs.readFileSync(path.join(__dirname, '../src/types/index.ts'), 'utf8');
    const decl = src.match(/export type PrimaryFramework =([^;]+);/);
    expect(decl).not.toBeNull();
    expect(decl![1].split('|').map((s) => s.trim().replace(/'/g, '')).sort())
      .toEqual(['enneagram', 'mbti']);
  });

  it('SQLite personality_profile 表无 zodiac_sign 列', () => {
    const src = fs.readFileSync(path.join(__dirname, '../src/services/db/migrations.ts'), 'utf8');
    const table = src.match(/CREATE TABLE IF NOT EXISTS personality_profile \(([\s\S]*?)\)/);
    expect(table).not.toBeNull();
    expect(table![1]).not.toMatch(/zodiac/);
  });
});
