/**
 * U2 · PDF 导出接线的闸门（2026-10-15）。
 *
 * 为什么要有它：服务端五个 PDF 端点早就写好（reportlab + 中文 CID 字体在册），
 * App 里却没有任何动作能拿到那份 PDF——半边功能悬空。接线本身只用现成端点，
 * 风险全在「前端自己造的第二真源」上：文件名、错误码、鉴权方式。这条闸逐字钉住
 * 三处（文件名对齐 contracts/endpoints.md §七、错误码对齐 backend/core/errors.py、
 * 请求只走统一的 request），并反向证明没退回 Linking.openURL 那条必然 401 的老路。
 *
 * 手法沿用本仓其它接线闸：屏在 node 环境渲染不了，所以读源码文本 + 正则切片；
 * 只有零原生依赖的纯模块（config/pdfFiles.ts、utils/base64.ts）才真的 import 进来跑。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';
import {
  MEMORY_CARD_PDF_FILE,
  YEAR_IN_REVIEW_PDF_FILE,
  reportPdfFile,
  lifeArchivePdfFile,
  pdfFailureKey,
  CODE_RESOURCE_NOT_FOUND,
  CODE_MEMORY_NOT_FOUND,
  CODE_ENTITLEMENT_REQUIRED,
} from '../src/config/pdfFiles';
import { arrayBufferToBase64 } from '../src/utils/base64';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const API = 'src/services/api.ts';
const EXPORT_SVC = 'src/services/pdfExport.ts';
const INSIGHTS = 'src/screens/InsightsScreen.tsx';
const SHARE = 'src/screens/ShareCardScreen.tsx';
const PAYWALL = 'src/screens/PaywallScreen.tsx';
const ENDPOINTS_MD = 'contracts/endpoints.md';
const ERRORS_PY = 'backend/core/errors.py';

/** §七 内按小节切行：7.1 是 PDF 表（第二列＝文件名），7.2 是 ZIP 表（第二列＝Content-Type）。 */
function sectionRows(head: string, stop: RegExp): { path: string; cols: string[] }[] {
  const lines = readRepo(ENDPOINTS_MD).split('\n');
  const start = lines.findIndex((l) => l.startsWith(head));
  expect(start).toBeGreaterThan(-1);
  const end = lines.findIndex((l, i) => i > start && stop.test(l));
  expect(end).toBeGreaterThan(start);
  const rows: { path: string; cols: string[] }[] = [];
  for (const line of lines.slice(start, end)) {
    const m = /^\| (?:POST|GET) `\/api\/v1\/([^`]+)` \|(.*)$/.exec(line);
    if (m) rows.push({ path: `/${m[1]}`, cols: m[2].split('|').map((c) => c.trim()) });
  }
  return rows;
}

/** endpoints.md §7.1 的表格行：`/api/v1/<path>` → 登记的落盘文件名（可能是拼接模板）。 */
function pdfNameRows(): { path: string; name: string }[] {
  return sectionRows('### 7.1', /^### 7\.2|^## 八、/).map((r) => ({
    path: r.path,
    // 对拼接名 §七 会带一句「（按路径/查询参数拼接）」注尾，比对前摘掉
    name: (r.cols[0] ?? '').replace(/^`|`$/g, '').replace(/（[^）]*）$/, '').trim(),
  }));
}

describe('① 落盘文件名与 contracts/endpoints.md §七 逐字（U2-3）', () => {
  const rows = pdfNameRows();

  it('§七 确实解析出了五条登记行（解析器空转的话下面全是假绿）', () => {
    expect(rows).toHaveLength(5);
  });

  // 按小节切分的证人：T1-01 在 §七 加了 7.2 ZIP 小节，它必须被读到、又不混进 PDF 名单，
  // 否则「只取 7.1」这句话本身就可能是扫空集。
  it('7.2 的 ZIP 行确实在册、且没有混进 PDF 名单（T1-01 加的第二小节）', () => {
    const zip = sectionRows('### 7.2', /^## 八、/);
    expect(zip.map((r) => r.path)).toEqual(['/user/export/{job_id}/download']);
    expect(zip[0].cols[0]).toBe('`application/zip`');
    expect(rows.map((r) => r.path)).not.toContain('/user/export/{job_id}/download');
  });

  it('回忆卡、年报两个固定名逐字相等', () => {
    const byPath = new Map(rows.map((r) => [r.path, r.name]));
    expect(byPath.get('/share/memory-card')).toBe(MEMORY_CARD_PDF_FILE);
    expect(byPath.get('/share/year-in-review/pdf')).toBe(YEAR_IN_REVIEW_PDF_FILE);
  });

  it('周期报告是拼接名：四个周期逐个按 §七 模板代入再比', () => {
    const tpl = new Map(rows.map((r) => [r.path, r.name])).get('/share/report/{period}/pdf');
    expect(tpl).toBeDefined();
    const periods = /export type SharePeriod = ([^;]+);/.exec(read('src/types/index.ts'));
    expect(periods).not.toBeNull();
    const values = [...(periods![1].matchAll(/'([^']+)'/g) as Iterable<RegExpMatchArray>)].map((m) => m[1]);
    expect(values).toHaveLength(4);
    for (const p of values) {
      expect(reportPdfFile(p as any)).toBe(tpl!.replace('{period}', p));
    }
  });

  it('Life Archive 是拼接名：三个档位逐个按 §七 模板代入再比（批1 G2 新增的第四出口）', () => {
    const tpl = new Map(rows.map((r) => [r.path, r.name])).get('/share/life-archive/pdf');
    expect(tpl).toBeDefined();
    const scopes = /export type LifeArchiveScope = ([^;]+);/.exec(read('src/types/index.ts'));
    expect(scopes).not.toBeNull();
    const values = [...(scopes![1].matchAll(/'([^']+)'/g) as Iterable<RegExpMatchArray>)].map((m) => m[1]);
    expect(values).toHaveLength(3);
    for (const s of values) {
      expect(lifeArchivePdfFile(s as any)).toBe(tpl!.replace('{scope}', s));
    }
  });

  it('后端 share.py 里这四个文件名字面量确实在册（§七 是生成物，源头要能回溯）', () => {
    const router = readRepo('backend/routers/share.py');
    expect(router).toContain('filename="closenai_memory_card.pdf"');
    expect(router).toContain('filename="closenai_year_in_review.pdf"');
    expect(router).toContain('closenai_{period}_report.pdf');
    expect(router).toContain('closenai_life_archive_{scope}.pdf');
  });
});

describe('② 四条封装在册、都真被屏调用，且都吃二进制出口', () => {
  const api = read(API);

  const wrappers: [string, string][] = [
    ['getReportPdf', `/share/report/`],
    ['getYearInReviewPdf', '/share/year-in-review/pdf'],
    ['postMemoryCardPdf', '/share/memory-card'],
    ['getLifeArchivePdf', '/share/life-archive/pdf'],
  ];

  it.each(wrappers)('%s：路径写在 request<ArrayBuffer> 的位子上（契约扫描器只认这个形态）', (name, frag) => {
    const at = api.indexOf(`export async function ${name}`);
    expect(at).toBeGreaterThan(-1);
    const body = api.slice(at, api.indexOf('\n}', at));
    expect(body).toContain('request<ArrayBuffer>(');
    expect(body).toContain(frag);
    expect(body).toContain('binary: true');
  });

  it('四个封装各有且只有一个调用点，且都在屏里（防「封装未接线」）', () => {
    const sites: [string, string][] = [
      ['getReportPdf', INSIGHTS],
      ['getYearInReviewPdf', INSIGHTS],
      ['postMemoryCardPdf', SHARE],
      ['getLifeArchivePdf', PAYWALL],
    ];
    for (const [name, rel] of sites) {
      expect(countIn(read(rel), new RegExp(`await ${name}\\(`, 'g'))).toBe(1);
      expect(countIn(api, new RegExp(`export async function ${name}\\b`, 'g'))).toBe(1);
    }
  });

  it('api.ts 里二进制出口只有这五处：四个 PDF ＋ T1-01 的 ZIP，其余调用仍走信封解包', () => {
    // 第五条不是漂移而是 T1-01 的 /user/export/{job_id}/download——它由
    // __tests__/dataExportWiring.test.ts 逐条钉；这里计数若再涨就是有人新开了
    // 一条没登记的字节流出口。
    expect(countIn(api, /binary: true/g)).toBe(5);
    const zip = api.slice(api.indexOf('export async function getUserExportDownload'));
    expect(zip.slice(0, zip.indexOf('\n}'))).toContain('/user/export/${jobId}/download');
    expect(api).toContain('binary?: boolean');
  });

  it('PDF 封装带 language 透传，回忆卡带 memory_id（POST body 不能丢）', () => {
    const memory = api.slice(api.indexOf('export async function postMemoryCardPdf'));
    expect(memory.slice(0, 600)).toContain("method: 'POST'");
    expect(memory.slice(0, 600)).toContain('JSON.stringify({ memory_id: memoryId })');
    const report = api.slice(api.indexOf('export async function getReportPdf'));
    expect(report.slice(0, 400)).toContain('language=${langParam()}');
  });

  it('档案封装带 scope 与 regenerate 查询参（权益与缓存口径都在后端，参数丢了就退化成默认档）', () => {
    const at = api.indexOf('export async function getLifeArchivePdf');
    const body = api.slice(at, api.indexOf('\n}', at));
    expect(body).toContain('scope=${scope}');
    expect(body).toContain('language=${langParam()}');
    expect(body).toContain('regenerate=${regenerate}');
  });
});

describe('③ 不许退回 Linking.openURL（带不上 Bearer 必然 401）', () => {
  // 按「调用形态」判，不按词判：api.ts 的注释里正写着这条路为什么不通
  it.each([API, EXPORT_SVC, INSIGHTS, SHARE])('%s 没有 Linking.openURL 调用', (rel) => {
    expect(read(rel)).not.toMatch(/Linking\.openURL\(/);
  });

  it('字节 → 落盘 → 系统分享面板三步都在 pdfExport.ts，且走 Bearer 的只有 request', () => {
    const svc = read(EXPORT_SVC);
    expect(svc).toContain('FileSystem.cacheDirectory');
    expect(svc).toContain('FileSystem.writeAsStringAsync');
    expect(svc).toContain('FileSystem.EncodingType.Base64');
    expect(svc).toContain('Sharing.isAvailableAsync()');
    expect(svc).toContain('Sharing.shareAsync(');
    expect(svc).toContain("mimeType: 'application/pdf'");
    // 这个 service 自己不发请求：网络出口只有 api.ts 那一处
    expect(svc).not.toContain('fetch(');
  });

  it('「全前端只有 3 处发请求」没退化（frontend-compat.md §一）', () => {
    const api = read(API);
    expect(countIn(api, /fetch\(/g)).toBe(2); // refresh + 统一 request（PDF 也走后者）
    expect(countIn(api, /new XMLHttpRequest\(/g)).toBe(1); // SSE
  });
});

describe('④ 失败码 → 文案键，与 backend/core/errors.py 同值（U2-4）', () => {
  const py = readRepo(ERRORS_PY);

  function pyCode(name: string): number {
    const m = new RegExp(`^\\s+${name} = (\\d+)`, 'm').exec(py);
    expect(m).not.toBeNull();
    return Number(m![1]);
  }

  it('40402 / 40403 / 40304 三个常量与 Python 侧逐值相等', () => {
    expect(CODE_RESOURCE_NOT_FOUND).toBe(pyCode('RESOURCE_NOT_FOUND'));
    expect(CODE_MEMORY_NOT_FOUND).toBe(pyCode('MEMORY_NOT_FOUND'));
    expect(CODE_ENTITLEMENT_REQUIRED).toBe(pyCode('ENTITLEMENT_REQUIRED'));
  });

  it('三个码各映射到自己的文案键，其余一律 generic', () => {
    expect(pdfFailureKey(40402)).toBe('pdf.failed.noData');
    expect(pdfFailureKey(40403)).toBe('pdf.failed.memoryGone');
    expect(pdfFailureKey(40304)).toBe('pdf.failed.entitlement');
    expect(pdfFailureKey(500)).toBe('pdf.failed.generic');
    expect(pdfFailureKey(undefined)).toBe('pdf.failed.generic');
    expect(pdfFailureKey(null)).toBe('pdf.failed.generic');
  });

  it('首读卡仍不接线：只登记，不在本批顺手补（发现缺陷不顺手修）', () => {
    expect(read(API)).not.toContain('/share/first-read-card');
  });

  it('三个屏的失败出口不弹裸 error.message（PDF 那条一律走 pdfFailureKey）', () => {
    expect(read(INSIGHTS)).toContain('t(pdfFailureKey(');
    expect(read(SHARE)).toContain('t(pdfFailureKey(');
    expect(read(PAYWALL)).toContain('t(pdfFailureKey(');
    expect(read(INSIGHTS)).toContain("t('pdf.unavailable')");
    expect(read(SHARE)).toContain("t('pdf.unavailable')");
    expect(read(PAYWALL)).toContain("t('pdf.unavailable')");
  });
});

describe('⑤ base64：手写的编码器逐长度对上 node Buffer（PDF 落盘靠它）', () => {
  it('0..12 字节每个长度都与 Buffer 一致（含 padding 的三种形态）', () => {
    for (let len = 0; len <= 12; len += 1) {
      const bytes = Uint8Array.from({ length: len }, (_, i) => (i * 37 + 11) % 256);
      expect(arrayBufferToBase64(bytes)).toBe(Buffer.from(bytes).toString('base64'));
    }
  });

  it('255/256/257 字节边界与 ArrayBuffer 入参同结果', () => {
    for (const len of [255, 256, 257, 4099]) {
      const bytes = Uint8Array.from({ length: len }, (_, i) => i % 256);
      const expectB64 = Buffer.from(bytes).toString('base64');
      expect(arrayBufferToBase64(bytes)).toBe(expectB64);
      const buf = new ArrayBuffer(len);
      new Uint8Array(buf).set(bytes);
      expect(arrayBufferToBase64(buf)).toBe(expectB64);
    }
  });

  it('输出里没有换行（expo 的 Base64 写盘不接受折行）', () => {
    const bytes = Uint8Array.from({ length: 9000 }, (_, i) => i % 256);
    expect(arrayBufferToBase64(bytes)).not.toMatch(/[\r\n]/);
  });
});

describe('⑥ jest 的 node 环境约束：纯模块不许 import 原生包', () => {
  it.each(['src/config/pdfFiles.ts', 'src/utils/base64.ts'])('%s 无 expo / react-native 依赖', (rel) => {
    const src = read(rel);
    expect(src).not.toContain('expo-');
    expect(src).not.toContain("from 'react-native'");
  });

  it('package.json 声明了这两个 Expo 托管原生包（重建 dev client 的凭据）', () => {
    const pkg = JSON.parse(read('package.json'));
    expect(pkg.dependencies['expo-file-system']).toMatch(/^~17/);
    expect(pkg.dependencies['expo-sharing']).toMatch(/^~12/);
  });
});

/** 取某语言的文案表（i18n 的实际形状是 resources[lang].translation.<ns>）。 */
function langTable(lang: string): any {
  return (resources as any)[lang].translation;
}

describe('⑦ 文案面：pdf.* 七键 × 15 语言齐，且屏里没有自造键', () => {
  const langs = Object.keys(resources);
  const KEYS = [
    'pdf.export', 'pdf.exporting', 'pdf.unavailable',
    'pdf.failed.noData', 'pdf.failed.memoryGone', 'pdf.failed.entitlement',
    'pdf.failed.generic',
  ];

  it('15 个语言都在册', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(KEYS)('%s：15 语言都非空', (key) => {
    for (const lang of langs) {
      const value = key.split('.').reduce<any>((acc, k) => (acc == null ? acc : acc[k]), langTable(lang));
      expect(typeof value).toBe('string');
      expect((value as string).trim().length).toBeGreaterThan(0);
    }
  });

  it('屏里用到的 pdf.* 字面量键都能在 en 表查到（动态键走 pdfFailureKey）', () => {
    const en = langTable('en');
    for (const rel of [INSIGHTS, SHARE, PAYWALL]) {
      for (const m of read(rel).matchAll(/t\(['"](pdf\.[a-zA-Z.]+)['"]/g)) {
        const parts = m[1].split('.');
        expect(parts.reduce<any>((acc, k) => (acc == null ? acc : acc[k]), en)).toBeTruthy();
      }
    }
    for (const key of KEYS) {
      const parts = key.split('.');
      expect(parts.reduce<any>((acc, k) => (acc == null ? acc : acc[k]), en)).toBeTruthy();
    }
  });

  it('三个入口都各有📄按钮，忙态文案与空闲文案分开', () => {
    const insights = read(INSIGHTS);
    expect(countIn(insights, /t\('pdf\.export'\)/g)).toBe(2);
    expect(countIn(insights, /t\('pdf\.exporting'\)/g)).toBe(2);
    const share = read(SHARE);
    expect(share).toContain("t('pdf.export')");
    expect(share).toContain("t('pdf.exporting')");
    const paywall = read(PAYWALL);
    expect(paywall).toContain("t('pdf.export')");
    // 忙态用 ActivityIndicator 而非 exporting 文案：三档共用一个按钮位，逐档文案放不下
    expect(paywall).toContain('{exporting ? (');
  });

  it('档案导出只长在已购那一支（未购位仍是购买按钮：付款通、货也要能领）', () => {
    const paywall = read(PAYWALL);
    const owned = paywall.indexOf('{owned ? (');
    const buy = paywall.indexOf('onBuyArchive(scope, sku)');
    const exportAt = paywall.indexOf('onExportArchivePdf(scope)');
    expect(owned).toBeGreaterThan(-1);
    expect(exportAt).toBeGreaterThan(owned);
    expect(exportAt).toBeLessThan(buy);
    expect(paywall).toContain('lifeArchivePdfFile(scope)');
    expect(paywall).toContain('await exportPdf(bytes, lifeArchivePdfFile(scope));');
    // 权益判定不留第二真源：屏里不许自己算 owned 之外再判一次 40304
    expect(paywall).not.toMatch(/code === 40304/);
  });

  it('防连点：忙位覆盖三个入口，PDF 与文本分享互斥', () => {
    const insights = read(INSIGHTS);
    expect(insights).toContain("const [pdfBusy, setPdfBusy] = useState<'report' | 'year' | null>(null);");
    expect(insights).toContain('if (!reportPeriod || pdfBusy) return;');
    expect(countIn(insights, /setPdfBusy\(null\);/g)).toBe(2);
    const share = read(SHARE);
    expect(share).toContain('if (!memory || pdfBusy || sharing) return;');
    expect(share).toContain('disabled={pdfBusy || sharing}');
    // 导出与购买共用一个 anyBusy：付费流程进行中不许并发导档案
    const paywall = read(PAYWALL);
    expect(paywall).toContain(
      'const anyBusy = busy || archiveBusyScope !== null || archivePdfScope !== null;',
    );
    expect(paywall).toContain('if (anyBusy || !isArchiveOwned(scope)) return;');
    expect(paywall).toContain('setArchivePdfScope(null);');
  });
});

function countIn(text: string, re: RegExp): number {
  return (text.match(re) || []).length;
}
