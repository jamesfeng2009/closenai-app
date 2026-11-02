/**
 * 金句卡（KG-30）的接线闸：入口、预览/导出的分界、失败文案与「不许有金句库」。
 *
 * 屏在 jest 的 node 环境渲染不了（同 dailyCardWiring / memoryCardWiring 的先例），所以这里
 * 钉的是「改了就不安全」的结构事实。这一批的失效方式全是静默的，而且两种会伤人：
 *
 * 1. 预览顺手发请求 → 用户还没同意分享，服务端已经为他的卡片惰性生成了推荐码；
 * 2. 导出时把本地那句一起 POST → 端点退化成「任意文本 + 品牌水印 + 本人短链」的生成器，
 *    而它对外的承诺是「这是你自己写的话」。
 *
 * 正侧（服务端）判据在 backend/tests/test_quote_card.py：body 里塞不进正文、AI 抽取的记忆
 * 走 40405、归属与类型同进 WHERE。这里钉的是反侧：前端不许自作聪明把句子发出去、不许
 * 长出「别人的金句」这条路，以及 40405 要说人话（不套「记忆已经不在了」那句假话）。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(ROOT, '..', rel), 'utf8');

const SHARE = 'src/screens/ShareCardScreen.tsx';
const MEMORIES = 'src/screens/MemoriesScreen.tsx';
const EXPLORE = 'src/screens/ExploreScreen.tsx';
const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const FUNNEL = 'src/services/funnel.ts';
const PDF_CFG = 'src/config/pdfFiles.ts';

const count = (rel: string, re: RegExp) => (read(rel).match(re) || []).length;

/** 两个锚点之间的一段（固定字符窗在中间加注释时会把尾部挤出窗口，锚点不会）。 */
function between(rel: string, start: string, end: string): string {
  const src = read(rel);
  const i = src.indexOf(start);
  expect(i).toBeGreaterThanOrEqual(0);
  const j = src.indexOf(end, i);
  expect(j).toBeGreaterThan(i);
  return src.slice(i, j);
}

describe('变体在册：类型、变体表、来源类型三处齐', () => {
  it("ShareCardVariant 含 'quote'（类型缺一项，屏里的分支就是类型错误现场）", () => {
    expect(read(TYPES)).toContain(
      "export type ShareCardVariant = 'classic' | 'minimal' | 'combo' | 'memory' | 'quote';",
    );
  });

  it("QuoteSourceType 只有 memory / reflection（与后端 QuoteCardRequest 的 Literal 同集合）", () => {
    expect(read(TYPES)).toContain("export type QuoteSourceType = 'memory' | 'reflection';");
    const py = readRepo('backend/models/schemas.py');
    expect(py).toContain('source_type: Literal["memory", "reflection"]');
  });

  it('变体表五个都在（金句卡自己点不进去，但至少不能从选单里消失）', () => {
    expect(read(SHARE)).toContain(
      "const VARIANTS: ShareCardVariant[] = ['classic', 'minimal', 'combo', 'memory', 'quote'];",
    );
  });

  it('route 参数表带金句四参且仍是可选（旧调用点不传参也得能进名片）', () => {
    const block = between('App.tsx', "ShareCard: {", "} | undefined;");
    ['variant?', 'memory_id?', 'source_type?', 'source_id?', 'quote_text?', 'quote_date?'].forEach(
      (k) => expect(block).toContain(k),
    );
  });
});

describe('入口两处：各带满五个参数，且各只有一处', () => {
  it('记忆详情→金句卡：source_type=memory、id 与正文与日期一起带走', () => {
    const at = between(MEMORIES, "navigation.navigate('ShareCard', {", '\n    });');
    expect(at).toContain("variant: 'quote'");
    expect(at).toContain("source_type: 'memory'");
    expect(at).toContain('source_id: item.id');
    expect(at).toContain('quote_text: item.content');
    expect(at).toContain('quote_date: item.created_at');
    expect(count(MEMORIES, /variant: 'quote'/g)).toBe(1);
  });

  it('反思行→金句卡：source_type=reflection、正文取 summary、日期取 r.date', () => {
    const at = between(EXPLORE, "navigation.navigate('ShareCard', {", '})}');
    expect(at).toContain("variant: 'quote'");
    expect(at).toContain("source_type: 'reflection'");
    expect(at).toContain('source_id: r.id');
    expect(at).toContain('quote_text: r.summary');
    expect(at).toContain('quote_date: r.date');
    expect(count(EXPLORE, /variant: 'quote'/g)).toBe(1);
  });

  it('空正文那条不给按钮（点下去只会拿到一句「这句是空的」）', () => {
    expect(read(EXPLORE)).toContain('{!!r.summary?.trim() && (');
    expect(read(MEMORIES)).toContain('{!!detail && (');
  });

  it('两个入口都是按钮语义（读屏要知道按下去会发生什么）', () => {
    expect(read(MEMORIES)).toContain('accessibilityRole="button"');
    expect(read(EXPLORE)).toContain('accessibilityRole="button"');
    expect(read(MEMORIES)).toContain("{t('share.quoteEntry')}");
    expect(read(EXPLORE)).toContain("{t('share.quoteEntry')}");
  });
});

describe('预览不发请求：导出是唯一授权点', () => {
  it('取数 effect 里金句分支在任何 await 之前 return', () => {
    const src = read(SHARE);
    const at = src.indexOf("if (variant === 'quote') return;");
    expect(at).toBeGreaterThanOrEqual(0);
    expect(src.indexOf('await getOnThisDay()')).toBeGreaterThan(at);
    expect(src.indexOf('await getPersonalityCard(')).toBeGreaterThan(at);
  });

  it('金句分支的整段渲染里没有一次网络调用（预览不许白烧推荐码）', () => {
    const block = between(SHARE, ": variant === 'quote' ?", ': !card ? (');
    expect(block).not.toContain('postQuoteCardPdf');
    expect(block).not.toContain('getShareLink');
    expect(block).not.toContain('await ');
  });

  it('postQuoteCardPdf 只在导出这一条路上，且导出后才算分享完成', () => {
    expect(count(SHARE, /await postQuoteCardPdf\(/g)).toBe(1);
    const fn = between(SHARE, 'const onExportQuotePdf = async () => {', '\n  };\n');
    expect(fn).toContain("trackFunnelShare('share_tapped', 'quote')");
    expect(fn).toContain('await exportPdf(bytes, QUOTE_CARD_PDF_FILE);');
    expect(fn).toContain("trackFunnelShare('share_completed', 'quote')");
    expect(fn).toContain('setPdfBusy(true);');
    expect(fn).toContain('} finally {');
    expect(fn).toContain('setPdfBusy(false);');
  });

  it('闸门：来源缺任一或不传正文就 return，忙态互斥', () => {
    const fn = between(SHARE, 'const onExportQuotePdf = async () => {', 'try {');
    expect(fn).toContain("if (!params?.source_type || !params.source_id || pdfBusy || sharing) return;");
    expect(read(SHARE)).toContain('disabled={!canExportQuote || pdfBusy || sharing}');
    expect(read(SHARE)).toContain('const canExportQuote = !!route?.params?.source_type && !!route?.params?.source_id && !!quoteText;');
  });

  it('埋点来源用 quote，且 quote 在册（口径与首页考古卡同一张表）', () => {
    expect(read(FUNNEL)).toContain("'quote'");
    expect(count(SHARE, /trackFunnelShare\('share_(tapped|completed)', 'quote'\)/g)).toBe(2);
  });
});

describe('正文两头都不猜：预览只读 route 参数，导出只交 id', () => {
  it('预览正文来自 quote_text，并按本地 280 上限裁（与后端同数字同省略号）', () => {
    expect(read(SHARE)).toContain("const quoteText = (route?.params?.quote_text || '').trim();");
    expect(read(SHARE)).toContain('{quoteCardExcerpt(quoteText)}');
    expect(read(PDF_CFG)).toContain('export function quoteCardExcerpt(');
  });

  it('api.ts 的 body 只有 source_type / source_id 两个键', () => {
    const api = read(API);
    const i = api.indexOf('export async function postQuoteCardPdf');
    const body = api.slice(i, api.indexOf('\n}', i));
    expect(body).toContain('JSON.stringify({ source_type: sourceType, source_id: sourceId })');
    expect(body).not.toMatch(/source_text|quote_text|content:/);
  });

  it('失败出口照旧走 pdfFailureKey，不弹裸 error.message', () => {
    const fn = between(SHARE, 'const onExportQuotePdf = async () => {', '\n  };\n');
    expect(fn).toContain("t('pdf.unavailable')");
    expect(fn).toContain('t(pdfFailureKey(e instanceof ApiError ? e.code : undefined))');
    expect(fn).not.toContain('e.message');
  });

  it('金句空态与名片空态分开：空句子说「这张卡上没有句子」，不是转圈也不是报错', () => {
    expect(read(SHARE)).toContain('{t(\'share.quoteEmpty\')}');
    expect(read(SHARE)).toContain("{t('share.memoryEmpty')}"); // 回忆卡空态没被挤掉
    expect(read(SHARE)).toContain("{t('share.empty')}");
  });
});

describe('清单诚实：卡上有什么，清单就写什么', () => {
  it('清单两项＝那句原话 + 深链，且卡面要素齐备（正文 + 日期 + 水印）', () => {
    const block = between(SHARE, ": variant === 'quote' ?", ': !card ? (');
    expect(block).toContain("<ShareManifest items={[t('share.quoteManifestQuote'), t('share.manifestLink')]} />");
    expect(block).toContain('{t(\'share.quoteIntro\')}');
    expect(block).toContain('quoteCardExcerpt(quoteText)');
    expect(block).toContain('<Text style={styles.cardWatermark}>ClosenAI</Text>');
    expect(block).toContain('{quoteDate}');
  });

  it('导出前那一句「什么都不会被分享」的披露长在卡外、清单之后', () => {
    const block = between(SHARE, "<ShareManifest items={[t('share.quoteManifestQuote')", 'onPress={onExportQuotePdf}');
    expect(block).toContain("{t('share.quoteNote')}");
  });

  it('后端卡面确实带深链（清单说「深链」就得真带：否则虚报分享面）', () => {
    const svc = readRepo('backend/services/share_service.py');
    const fn = svc.slice(svc.indexOf('async def export_quote_card_pdf'), svc.indexOf('async def export_year_in_review'));
    expect(fn).toContain('brand = await self._brand_for(');
    expect(fn).toContain('return self._render_pdf(');
    expect(svc.slice(svc.indexOf('async def _brand_for'), svc.indexOf('def _escape_xml'))).toContain(
      'referral_link(settings.site_url, code)',
    );
  });
});

describe('不许长出金句库：只出用户自己的原话', () => {
  it('前端没有第二个金句出口（api.ts 里金句路径只出现在一处 request 里）', () => {
    // 只数带引号的路径字面量：注释里那句「POST /share/quote-card」用反引号，不算一条出口
    expect(count(API, /'\/share\/quote-card'/g)).toBe(1);
    expect(read(API)).not.toMatch(/getQuotes|randomQuote|quoteLibrary|getQuoteCard\b/);
  });

  it('后端只注册了 POST 一条金句端点，没有 GET 列表/随机那条路', () => {
    const router = readRepo('backend/routers/share.py');
    expect((router.match(/@router\.post\("\/share\/quote-card"\)/g) || []).length).toBe(1);
    expect(router).not.toContain('@router.get("/share/quote');
  });

  it('金句卡正文上限两侧同值（闸在 pdfExportWiring.test.ts，这里只证明常量在册）', () => {
    expect(read(PDF_CFG)).toContain('export const QUOTE_CARD_MAX_CHARS = 280;');
    expect(readRepo('backend/services/share_service.py')).toContain('QUOTE_MAX_CHARS = 280');
  });
});

describe('文案面：share.quote* 六键 + pdf.failed.notOriginal × 15 语言齐', () => {
  const langs = Object.keys(resources);
  const SHARE_KEYS = ['quote', 'quoteEntry', 'quoteEmpty', 'quoteIntro', 'quoteManifestQuote', 'quoteNote'];

  it('15 个语言都在册', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(SHARE_KEYS)('share.%s：15 语言都有且非空', (key) => {
    for (const lng of langs) {
      const v = (resources as any)[lng].translation.share[key];
      expect(typeof v).toBe('string');
      expect((v || '').trim().length).toBeGreaterThan(0);
    }
  });

  it('40405 的文案 × 15：说清「这句是 AI 整理的」，不复用记忆已删那句', () => {
    for (const lng of langs) {
      const v = (resources as any)[lng].translation.pdf.failed.notOriginal;
      expect(typeof v).toBe('string');
      expect(v.trim().length).toBeGreaterThan(0);
      const gone = (resources as any)[lng].translation.pdf.failed.memoryGone;
      expect(v).not.toBe(gone); // 同文案＝这条码白分了
    }
  });

  it('中文原生：变体叫「金句」、披露提到推荐链接、notOriginal 提到 AI', () => {
    const zh = (resources as any).zh.translation;
    expect(zh.share.quote).toBe('金句');
    expect(zh.share.quoteNote).toMatch(/推荐/);
    expect(zh.pdf.failed.notOriginal).toMatch(/AI/);
    expect(zh.share.quoteEntry).toMatch(/分享/);
  });

  it('金句卡用到的每个字面量 t() 键都在 en 表里（漏键只真机显英文）', () => {
    const en = (resources as any).en.translation;
    const lookup = (key: string) =>
      key.split('.').reduce<unknown>(
        (acc, p) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[p] : undefined),
        en,
      );
    for (const rel of [SHARE, MEMORIES, EXPLORE]) {
      const missing = [...new Set([...read(rel).matchAll(/\bt\('([a-zA-Z0-9_.]+)'/g)].map((m) => m[1]))]
        .filter((k) => lookup(k) === undefined && lookup(`${k}_other`) === undefined);
      expect(missing).toEqual([]);
    }
  });
});

describe('样式口径：金句卡的两行长文案各有行高（卡面挤成一坨是最常见的静默回归）', () => {
  it('quoteIntro 与 quoteNote 各自在册并取 LineHeight token', () => {
    const src = read(SHARE);
    const styles = src.slice(src.indexOf('const styles = StyleSheet.create'));
    for (const key of ['quoteIntro', 'quoteNote']) {
      expect(new RegExp(`^\\s+${key}: \\{`, 'm').test(styles)).toBe(true);
    }
    expect(styles).toContain('lineHeight: LineHeight.caption');
  });
});
