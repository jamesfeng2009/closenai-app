/**
 * 组合名片（16 型 × 九型）契约测试。
 *
 * 背景：dimensions.enneagram 曾写死 None，卡的名头写着「双维度组合」，内容只有一个框架；
 * 前端类型里有 dimensions 字段但从未渲染，所以就算后端填了真值用户也看不见。
 * 这里同时守后端取值、前端消费、以及「分享纯文本没被偷偷加长」（清单必须与文本对齐）。
 */
import * as fs from 'fs';
import * as path from 'path';

import resources from '../src/i18n/locales';

const langs = Object.keys(resources);

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
const readBackend = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', 'backend', rel), 'utf8');

describe('后端：九型维度接真值', () => {
  const src = readBackend('services/share_service.py');
  // 只截 card["dimensions"] 那一段，避免全文匹配把别处的 enneagram 字样算成通过
  const block = src.slice(
    src.indexOf('card["dimensions"] = {'),
    src.indexOf('card["combo_insight"]')
  );

  it('share_service 的 dimensions 段落存在且非空', () => {
    expect(block.length).toBeGreaterThan(20);
  });

  it('dimensions.enneagram 取 users.enneagram_type，不再写死 None', () => {
    expect(block).toContain('"enneagram": user.enneagram_type');
    expect(block).not.toContain('"enneagram": None');
  });

  it('dimensions 只有 mbti 与 enneagram 两个键（占星不得回流）', () => {
    const keys = Array.from(block.matchAll(/"([a-z_]+)":/g)).map((m) => m[1]);
    expect(keys.sort()).toEqual(['enneagram', 'mbti']);
  });
});

describe('前端：组合卡真的渲染两维度', () => {
  const src = read('src/screens/ShareCardScreen.tsx');
  const comboAt = src.indexOf("card.layout === 'combo' && !!card.combo_insight");

  it('名片卡面上消费 dimensions.mbti 与 dimensions.enneagram', () => {
    expect(src).toContain('card.dimensions?.mbti');
    expect(src).toContain('card.dimensions?.enneagram');
    expect(src).toContain('{card.dimensions.mbti}');
    expect(src).toContain('{card.dimensions.enneagram}');
  });

  it('维度块在 combo 布局门控里（classic/minimal 不出双维度）', () => {
    const at = src.indexOf("card.layout === 'combo' && !!(card.dimensions");
    expect(at).toBeGreaterThan(-1);
    // 维度块必须排在洞察文本之前，且整段都在同一个 combo 条件里
    expect(at).toBeLessThan(comboAt);
    expect(src.slice(at, comboAt)).not.toContain('{card.layout ===');
  });

  it('九型标签走 i18n，不把「九型人格」硬编码进 JSX', () => {
    expect(src).toContain("t('personality.enneagramLabel')");
    expect(src).not.toMatch(/<Text[^>]*>\s*九型/);
  });

  it('没测九型时不渲染空 chip（断言门控条件本身，不是 JSX 存在）', () => {
    expect(src).toContain('!!card.dimensions?.mbti &&');
    expect(src).toContain('!!card.dimensions?.enneagram &&');
  });
});

describe('跨仓契约：九型只在图片预览，不在纯文本', () => {
  const screen = read('src/screens/ShareCardScreen.tsx');
  const at = screen.indexOf('<ShareManifest');
  const manifest = screen.slice(at, at + 400);

  it('内容清单没有列九型（后端 share_text 不带它，列了就是虚报）', () => {
    expect(manifest).toContain('card.user_name');
    expect(manifest).toContain('card.tagline');
    expect(manifest).not.toContain('dimensions');
  });
});

describe('文案：组合名片不再宣称三维度', () => {
  it.each(langs)('%s：share.proGateMessage 不提三维度', (lng) => {
    const v = (resources as any)[lng].translation.share.proGateMessage;
    expect(v).toBeTruthy();
    expect(v).not.toMatch(/三维度|三个维度|three dimensions|3 dimensions/i);
  });

  it('zh 写明两个框架各自是谁', () => {
    const zh = (resources as any).zh.translation;
    expect(zh.share.proGateMessage).toMatch(/双维度/);
    expect(zh.share.proGateMessage).toMatch(/16 型/);
    expect(zh.share.proGateMessage).toMatch(/九型/);
  });

  it('非 zh 语言点名两个框架（16-type + Enneagram）', () => {
    for (const lng of langs.filter((l) => l !== 'zh')) {
      const v = (resources as any)[lng].translation.share.proGateMessage;
      expect(v).toMatch(/16-type/);
      expect(v).toMatch(/Enneagram/);
    }
  });

  it.each(langs)('%s：personality.enneagramLabel 在位（卡面 chip 用它）', (lng) => {
    expect((resources as any)[lng].translation.personality.enneagramLabel).toBeTruthy();
  });
});
