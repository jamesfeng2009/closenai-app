/**
 * T1-01 · 通用数据导出接线的闸门（2026-10-18）。
 *
 * 为什么要有它：Baseline §2.2-6 把「带走」列进 P0，§14.2 又要求界面对用户讲清
 * 数据范围／保存周期／撤回方式三件事。后端那条异步 job 链（申请→cron 生成→下载）
 * 已经交付，剩下的风险全在「前端自己造第二真源」：五态自己列一遍、错误码自己写、
 * 保存周期写死一个「7 天」。这条闸把这三处逐字钉回后端源头，并反向证明下载没有
 * 退回 `download_path` + `Linking.openURL` 那条必然 401 的老路（§14.1 不发签名 URL）。
 *
 * 手法沿用本仓其它接线闸：屏在 node 环境渲染不了，所以读源码文本 + 正则切片；
 * 只有零原生依赖的纯模块（config/exportArchive.ts）才真的 import 进来跑。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';
import { EXPORT_ERROR_KEYS, EXPORT_STATUS_KEYS } from '../src/config/exportArchive';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');
const countIn = (s: string, re: RegExp) => (s.match(re) ?? []).length;

/** `src` 下全部 .ts/.tsx，返回 [相对 src 的路径, 文件内容]。 */
function srcFiles(): [string, string][] {
  const dir = path.join(ROOT, 'src');
  return fs
    .readdirSync(dir, { recursive: true })
    .map(String)
    .filter((rel) => /\.(ts|tsx)$/.test(rel))
    .map((rel) => [rel, fs.readFileSync(path.join(dir, rel), 'utf8')]);
}

const API = 'src/services/api.ts';
const SCREEN = 'src/screens/PrivacySecurityScreen.tsx';
const ARCHIVE = 'src/services/archiveExport.ts';
const ENDPOINTS_MD = 'contracts/endpoints.md';
const ERRORS_PY = 'backend/core/errors.py';
const MODEL_PY = 'backend/models/export.py';
const SERVICE_PY = 'backend/services/export_service.py';

/** 台账 §一 主表里 /user/export 开头的登记行（含方法与前端出处列）。 */
function exportRows(): { method: string; path: string; used: string }[] {
  const lines = readRepo(ENDPOINTS_MD).split('\n');
  const rows: { method: string; path: string; used: string }[] = [];
  for (const line of lines) {
    const m = /^\| \d+ \| (GET|POST|PUT|PATCH|DELETE) \| `(\/api\/v1\/user\/export[^`]*)` \|[^|]*\|[^|]*\| ([^|]*) \|/.exec(line);
    if (m) rows.push({ method: m[1], path: m[2], used: m[3].trim() });
  }
  return rows;
}

function wrapperBody(name: string): string {
  const api = read(API);
  const at = api.indexOf(`export async function ${name}`);
  expect(at).toBeGreaterThan(-1);
  const end = api.indexOf('\n}', at);
  expect(end).toBeGreaterThan(at);
  return api.slice(at, end);
}

describe('① 四条封装各指向台账登记的那一行', () => {
  const rows = exportRows();

  it('台账里 /user/export 面恰好四条（过滤器扫空集的话下面全是假绿）', () => {
    expect(rows).toHaveLength(4);
    expect(rows.map((r) => `${r.method} ${r.path}`).sort()).toEqual([
      'GET /api/v1/user/export',
      'GET /api/v1/user/export/{job_id}',
      'GET /api/v1/user/export/{job_id}/download',
      'POST /api/v1/user/export',
    ]);
  });

  const cases: [string, string, string, string][] = [
    ['postUserExport', 'POST', '/api/v1/user/export', `'/user/export'`],
    ['listUserExports', 'GET', '/api/v1/user/export', `'/user/export'`],
    ['getUserExport', 'GET', '/api/v1/user/export/{job_id}', '`/user/export/${jobId}`'],
    ['getUserExportDownload', 'GET', '/api/v1/user/export/{job_id}/download', '`/user/export/${jobId}/download`'],
  ];

  it.each(cases)('%s：封装在册、台账有对应行、出处列的行号真指着这条路径', (name, method, p, frag) => {
    expect(countIn(read(API), new RegExp(`export async function ${name}\\b`, 'g'))).toBe(1);
    const row = rows.find((r) => r.method === method && r.path === p);
    expect(row).toBeDefined();
    // 出处列必须指回本封装所在行：台账写 `—` 就是「封装没接线」或「扫描器瞎了」
    const ref = /api\.ts:(\d+)/.exec(row!.used);
    expect(ref).not.toBeNull();
    const lineText = read(API).split('\n')[Number(ref![1]) - 1];
    expect(lineText).toContain(frag);
  });

  it('下载那条吃二进制出口，其余三条仍走 JSON 信封解包', () => {
    expect(wrapperBody('getUserExportDownload')).toContain('binary: true');
    expect(wrapperBody('getUserExportDownload')).toContain('/user/export/${jobId}/download');
    for (const name of ['postUserExport', 'listUserExports', 'getUserExport']) {
      expect(wrapperBody(name)).not.toContain('binary: true');
    }
  });
});

describe('② 五态与错误码从后端取，前端不另立一套', () => {
  const model = readRepo(MODEL_PY);
  const py = readRepo(ERRORS_PY);

  function pyStatuses(): string[] {
    const m = /EXPORT_STATUSES = \(([^)]*)\)/.exec(model);
    expect(m).not.toBeNull();
    return [...m![1].matchAll(/["']([^"']+)["']/g)].map((x) => x[1]);
  }

  it('后端 EXPORT_STATUSES 本身解析得出来且是五态（否则上面的相等是两边都空）', () => {
    expect(pyStatuses()).toEqual(['queued', 'running', 'ready', 'failed', 'expired']);
  });

  function pyCode(name: string): number {
    const m = new RegExp(`^\\s+${name} = (\\d+)`, 'm').exec(py);
    expect(m).not.toBeNull();
    return Number(m![1]);
  }

  it('EXPORT_STATUS_KEYS 的键与后端 EXPORT_STATUSES 同集同序', () => {
    expect(Object.keys(EXPORT_STATUS_KEYS)).toEqual(pyStatuses());
  });

  it('五态文案在 config 里逐态登记，且没有漏进屏里的自造状态词', () => {
    for (const key of Object.values(EXPORT_STATUS_KEYS)) {
      expect(key.startsWith('export.status')).toBe(true);
    }
    // 屏只显示后端给的 status，不自己推断（正向证人：上面那条键集非空）
    expect(Object.keys(EXPORT_STATUS_KEYS).length).toBe(5);
    expect(read(SCREEN)).toContain('EXPORT_STATUS_KEYS[latestExport.status]');
  });

  it('三个业务码与 backend/core/errors.py 逐值相等，且后端恰好只有这三个 EXPORT_*', () => {
    expect(EXPORT_ERROR_KEYS).toEqual({
      40905: 'export.notReady',
      42904: 'export.tooFrequent',
      50007: 'export.artifactMissing',
    });
    for (const [code, key] of Object.entries(EXPORT_ERROR_KEYS)) {
      const name = { 'export.notReady': 'EXPORT_NOT_READY', 'export.tooFrequent': 'EXPORT_REQUEST_TOO_FREQUENT', 'export.artifactMissing': 'EXPORT_ARTIFACT_MISSING' }[key]!;
      expect(pyCode(name)).toBe(Number(code));
    }
    expect(countIn(py, /^\s+EXPORT_\w+ = \d+/gm)).toBe(3);
  });

  it('types 的 ExportJob 字段与后端 job_to_dict 同集（响应形状漂移要在这里红）', () => {
    const svc = readRepo(SERVICE_PY);
    const block = svc.slice(svc.indexOf('payload = {'), svc.indexOf('return payload'));
    expect(block.length).toBeGreaterThan(0);
    const pyKeys = new Set([
      ...[...block.matchAll(/"([a-z_]+)":/g)].map((m) => m[1]),
      ...[...block.matchAll(/payload\["([a-z_]+)"\]/g)].map((m) => m[1]),
    ]);
    const iface = /export interface ExportJob \{([\s\S]*?)\n\}/.exec(read('src/types/index.ts'));
    expect(iface).not.toBeNull();
    const tsKeys = [...iface![1].matchAll(/^\s{2}(\w+)\??:/gm)].map((m) => m[1]);
    expect(tsKeys.sort()).toEqual([...pyKeys].sort());
  });
});

describe('③ 保存周期说后端告诉它的话（§14.2）', () => {
  it('列表封装回传 retention_days，屏用它插值而不是自己写天数', () => {
    expect(wrapperBody('listUserExports')).toContain('ExportJobList');
    const src = read(SCREEN);
    expect(src).toContain('setRetentionDays(res.retention_days)');
    expect(src).toContain("t('export.desc', { days: retentionDays })");
    expect(src).not.toMatch(/\{\{days\}\}/);
    expect(src).not.toMatch(/导出[^']*?7\s*天/);
  });

  it('后端那侧的真源在册：settings.export_retention_days 且列表响应带它', () => {
    expect(readRepo('backend/config.py')).toMatch(/export_retention_days: int = \d+/);
    expect(readRepo(SERVICE_PY)).toContain('"retention_days": settings.export_retention_days');
  });

  it('拿不到周期时退到 descNoRetention，不谎报天数', () => {
    const src = read(SCREEN);
    expect(src).toContain('retentionDays === null');
    expect(src).toContain("t('export.descNoRetention')");
  });
});

describe('④ 下载不打开 download_path（§14.1 不发签名 URL）', () => {
  it('download_path 只在 types 里作为契约字段声明，源码无人读它（注释除外）', () => {
    const declared: string[] = [];
    let commentHits = 0;
    for (const [rel, text] of srcFiles()) {
      expect(rel.length).toBeGreaterThan(0);
      for (const line of text.split('\n')) {
        if (!/download_path/.test(line)) continue;
        const trimmed = line.trim();
        if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) {
          commentHits += 1;
        } else {
          declared.push(`${rel}:${trimmed}`);
        }
      }
    }
    expect(declared).toEqual(['types/index.ts:download_path?: string;']);
    expect(commentHits).toBeGreaterThan(0);
    expect(srcFiles().length).toBeGreaterThan(50);
  });

  it('屏里下载 = 重读状态 + 取字节 + 落盘，且只在 download_ready 为真时取字节', () => {
    const src = read(SCREEN);
    const at = src.indexOf('const onDownloadExport');
    expect(at).toBeGreaterThan(-1);
    const body = src.slice(at, src.indexOf('\n  };', at));
    expect(body).toContain('await getUserExport(latestExport.job_id)');
    expect(body).toContain('if (!fresh.download_ready)');
    expect(body).toContain('await getUserExportDownload(fresh.job_id)');
    expect(body).toContain('await saveArchive(bytes, fresh.file_name');
  });

  it('屏里的 Linking 只用于法律条款两个外链，没有拿去开过任何导出线', () => {
    const src = read(SCREEN);
    expect(countIn(src, /Linking\.openURL\(/g)).toBe(1);
    expect(src).toContain('openLink(PRIVACY_POLICY_URL)');
    expect(src).toContain('openLink(TERMS_URL)');
    expect(countIn(src, /const openLink = async/g)).toBe(1);
  });
});

describe('⑤ 落盘两条出口都在，面板不可用时不假装成功', () => {
  it('web 分支走 Blob + a[download]，原生分支先问 Sharing 是否可用', () => {
    const src = read(ARCHIVE);
    expect(src).toContain("Platform.OS === 'web'");
    expect(src).toContain('new globalAny.Blob([bytes], { type: ZIP_MIME })');
    expect(src).toContain('anchor.download = fileName');
    expect(src).toContain('revokeObjectURL(url)');
    expect(src).toContain('await Sharing.isAvailableAsync()');
    expect(src).toContain('FileSystem.EncodingType.Base64');
    expect(src).toContain("UTI: 'public.zip-archive'");
  });

  it('三条不可用路径都抛 ArchiveUnavailableError，屏里翻成 export.unavailable', () => {
    const src = read(ARCHIVE);
    expect(countIn(src, /throw new ArchiveUnavailableError\(\)/g)).toBe(3);
    const screen = read(SCREEN);
    expect(screen).toContain("if (e instanceof ArchiveUnavailableError) return t('export.unavailable')");
  });

  it('未知错误退回服务端文案再退通用失败提示，绝不静默成功', () => {
    const screen = read(SCREEN);
    const at = screen.indexOf('function exportFailureMessage');
    expect(at).toBeGreaterThan(-1);
    const body = screen.slice(at, screen.indexOf('\n  }', at));
    expect(body).toContain('EXPORT_ERROR_KEYS[e.code]');
    expect(body).toContain('if (e.message) return e.message');
    expect(body).toContain("return t('export.failedGeneric')");
  });
});

describe('⑥ 入口唯一且在隐私与安全页（§730 口径：Profile/隐私入口）', () => {
  it('只有这一屏接线，四个封装各有一个调用点', () => {
    const screen = read(SCREEN);
    const sites: [RegExp, number][] = [
      [/\blistUserExports\(\)/g, 1],
      [/\bpostUserExport\('all'\)/g, 1],
      [/\bgetUserExport\(/g, 2],
      [/\bgetUserExportDownload\(/g, 1],
      [/\bsaveArchive\(/g, 1],
    ];
    for (const [re, n] of sites) expect(countIn(screen, re)).toBe(n);
    const importers = srcFiles()
      .filter(([rel]) => rel !== 'services/api.ts' && rel !== 'screens/PrivacySecurityScreen.tsx')
      .filter(([, text]) => /postUserExport|listUserExports|getUserExportDownload/.test(text))
      .map(([rel]) => rel);
    expect(importers).toEqual([]);
  });

  it('排队/生成中才轮询，且离开页面清掉定时器', () => {
    const src = read(SCREEN);
    expect(src).toContain("BLOCKING_EXPORT_STATUSES = ['queued', 'running']");
    expect(src).toContain('clearInterval(pollRef.current)');
    expect(src).toMatch(/}, \[pollJobId\]\);/);
  });
});

/** 取某语言的文案表（i18n 的实际形状是 resources[lang].translation.<ns>）。 */
function langTable(lang: string): any {
  return (resources as any)[lang].translation;
}

describe('⑦ 文案面：export.* 十六键 × 15 语言齐，插值位不漏', () => {
  const langs = Object.keys(resources);
  const KEYS = [
    'export.title', 'export.desc', 'export.descNoRetention', 'export.request',
    'export.inProgress', 'export.download',
    'export.statusQueued', 'export.statusRunning', 'export.statusReady',
    'export.statusFailed', 'export.statusExpired',
    'export.unavailable', 'export.notReady', 'export.tooFrequent',
    'export.artifactMissing', 'export.failedGeneric',
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

  it('export.desc 每支语言都带 {{days}} 插值位（漏一支就等于那一支永远不说保存周期）', () => {
    for (const lang of langs) {
      expect(langTable(lang).export.desc).toContain('{{days}}');
      expect(langTable(lang).export.descNoRetention).not.toContain('{{days}}');
    }
  });

  it('屏与 config 里用到的 export.* 字面量键都能在 en 表查到', () => {
    const en = langTable('en');
    const used = new Set<string>();
    for (const rel of [SCREEN]) {
      for (const m of read(rel).matchAll(/t\(['"](export\.[a-zA-Z.]+)['"]/g)) used.add(m[1]);
    }
    for (const key of Object.values(EXPORT_STATUS_KEYS)) used.add(key);
    for (const key of Object.values(EXPORT_ERROR_KEYS)) used.add(key);
    expect(used.size).toBeGreaterThanOrEqual(9);
    for (const key of used) {
      expect(key.split('.').reduce<any>((acc, k) => (acc == null ? acc : acc[k]), en)).toBeTruthy();
    }
  });
});
