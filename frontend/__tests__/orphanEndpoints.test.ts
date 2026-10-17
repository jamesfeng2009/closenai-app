/**
 * 批 2 · D-G9（三条孤儿端点）的接线/反向闸（2026-10-17）。
 *
 * 「孤儿」在这里有一个可证的定义：`contracts/endpoints.md` 的「前端调用」列带
 * `（封装未接线）`＝api.ts 里有函数但全仓没人调，或该行干脆是 `—`。三条孤儿按工作单分治，
 * 但第三条按实测**改判**，所以本闸同时是三种形状：
 *
 * ① `GET /mood/trend`（`api.ts:415`）→ **接线**：只在 `InsightsScreen` 趋势卡补一行后端 summary，
 *    **不替换** `MoodLineChart` 的数据源（工作单 D-G9 默认值）。前端不重算平均值，
 *    否则同一屏长出第二个「这段情绪算高还是低」的真源。
 * ② `GET /discovery/daily`（原 `api.ts:629`）→ **删封装**：`getDailyDiscovery` + `DailyDiscovery`
 *    类型一并删。删的是前端死代码，后端端点仍在且已 `deprecated=True`，所以契约行不该消失，
 *    只该从「有封装」退回 `—`。
 * ③ `DELETE /user/push-token`（`api.ts:583`）→ **改判：不接登出**。工作单默认「接到登出/注销」，
 *    但实测两处都跟产品口径冲突：
 *    - 登出侧：`AuthContext.tsx:109` 的既有口径是「登出保留推送 token，使重新登录前也能收到主动消息，
 *      APNs 410 由后端自清」⇒ 接上就等于取消那句。
 *    - 注销侧：后端 `user_service.py:425` 的 `soft_delete_account` 自己调了 `clear_push_tokens`，
 *      已有 `backend/tests/test_account_deletion.py:71` 作证；前端再调一次是对已删账号发请求（必 401）。
 *    ⇒ 交付物是「这条端点在当前产品口径下没有合法消费者」的**反向闸**，不是接线。
 *    勘误登记（不改）：`backend/routers/user.py:10` 与 `:112` 的文档串仍写「登出时调用」，
 *    与本改判冲突，归 S 桶台账。
 * 本地 `dailyDiscoveryRepository` 已随 D-G10 删除（见 __tests__/idleRepositoriesWiring.test.ts）；
 * `MoodCheckinResponse` 缺 `note` 是 D-G8 发现的登记项，均不在本闸范围。
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const API = 'src/services/api.ts';
const TYPES = 'src/types/index.ts';
const INSIGHTS = 'src/screens/InsightsScreen.tsx';
const AUTH = 'src/context/AuthContext.tsx';
const SCHEMAS_PY = 'backend/models/schemas.py';
const MOOD_SERVICE_PY = 'backend/services/mood_service.py';
const USER_SERVICE_PY = 'backend/services/user_service.py';
const ACCOUNT_TEST_PY = 'backend/tests/test_account_deletion.py';
const DISCOVERY_PY = 'backend/routers/discovery.py';
const ENDPOINTS_MD = 'contracts/endpoints.md';

const api = read(API);
const types = read(TYPES);
const insights = read(INSIGHTS);
const auth = read(AUTH);

/** 递归列出 frontend/src 下所有源码文件（相对 frontend/ 的路径）。 */
function srcFiles(dir = 'src'): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...srcFiles(rel));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(rel);
  }
  return out;
}

/** endpoints.md 里某条路径那一行（表格行，第 N 列 = 前端调用）。 */
function contractRow(pathFragment: string, method: string): string {
  const line = readRepo(ENDPOINTS_MD)
    .split('\n')
    .find(
      (l) =>
        /^\| \d+ \|/.test(l) &&
        l.includes(pathFragment) &&
        l.split('|')[2].trim() === method
    );
  expect(line).toBeDefined();
  return line!;
}

function contractFrontCell(row: string): string {
  return row.split('|')[6].trim();
}

/** 取两个锚点之间的源码切片（用于「某段注释/某个 handler 里有没有 X」这类判据）。 */
function slice(src: string, from: string, to: string): string {
  const a = src.indexOf(from);
  const b = src.indexOf(to);
  expect(a).toBeGreaterThanOrEqual(0);
  expect(b).toBeGreaterThan(a);
  return src.slice(a, b);
}

/**
 * 去掉注释只留代码。`clearPushToken` 这类改判要在 handler 体里**明文写理由**，
 * 所以「有没有调用」必须只看代码，否则注释里的名字会被当成调用点。
 * 行注释的正则排除了 `://`，免得把 `${API_BASE}://` 之类的字符串切断。
 */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

describe('① GET /mood/trend：孤儿端点接线（只补后端 summary，不换图表数据源）', () => {
  it('api.ts 的封装打的是带 days 的查询串', () => {
    expect(api).toMatch(
      /export async function getMoodTrend\(days: number = 14\): Promise<MoodTrendData> \{\s*\n\s*return request<MoodTrendData>\(`\/mood\/trend\?days=\$\{days\}`\);/
    );
  });

  it('前端类型三键与后端 MoodTrendResponse 一致（summary 不可选）', () => {
    const body = new RegExp(
      String.raw`export interface MoodTrendData \{([\s\S]*?)\n\}`
    ).exec(types);
    expect(body).not.toBeNull();
    expect(body![1]).toMatch(/labels: string\[\];/);
    expect(body![1]).toMatch(/values: number\[\];/);
    expect(body![1]).toMatch(/summary: string;/);
    const py = new RegExp(
      String.raw`class MoodTrendResponse\(BaseModel\):([\s\S]*?)\n\nclass `
    ).exec(readRepo(SCHEMAS_PY));
    expect(py).not.toBeNull();
    expect(py![1]).toMatch(/summary: str/);
  });

  it('summary 是后端按语言出的三档文案，前端不重算', () => {
    const svc = readRepo(MOOD_SERVICE_PY);
    const fn = new RegExp(
      String.raw`def _trend_summary\(self, values: List\[int\], language: str \| None = None\) -> str:([\s\S]*?)\n\n`
    ).exec(svc);
    expect(fn).not.toBeNull();
    for (const key of ['trend.empty', 'trend.high', 'trend.mid', 'trend.low']) {
      expect(fn![1]).toContain(`"${key}"`);
    }
    // 前端只吃 summary 这一句：拿 values/labels 二次计算＝第二个真源
    // （判据只看代码行：注释里必须能写「为什么不换掉折线的数据源」）
    expect(codeOnly(insights)).not.toMatch(/trend\.(values|labels)/);
  });

  it('InsightsScreen 首屏并行取趋势（7 天窗、失败降级为 null）并渲染摘要一行', () => {
    expect(insights).toMatch(/getMoodTrend,/); // import 面
    expect(insights).toMatch(/const TREND_DAYS = 7;/);
    expect(insights).toContain(
      'const [trend, setTrend] = useState<MoodTrendData | null>(null);'
    );
    expect(insights).toMatch(/getMoodTrend\(TREND_DAYS\)\.catch\(\(\) => null\),/);
    expect(insights).toMatch(/const \[w, tl, p, tr\] = await Promise\.all\(\[/);
    expect(insights).toContain('setTrend(tr);');
    expect(insights).toContain('{!!trend?.summary && (');
    expect(insights).toContain(
      '<Text style={styles.trendSummary}>{trend.summary}</Text>'
    );
    const style = /trendSummary: \{([\s\S]*?)\}/.exec(insights);
    expect(style).not.toBeNull();
    expect(style![1]).toMatch(/lineHeight: LineHeight\.\w+/);
  });

  it('折线数据源仍是 /growth/timeline 的 days，没被 trend 替换', () => {
    expect(insights).toMatch(
      /<MoodLineChart days=\{recentDays\(timeline\.days, 7\)\} width=\{340\} height=\{56\} \/>/
    );
  });

  it('趋势摘要在模糊门之内（免费/Pro 分界不因这条而变）', () => {
    const gate = slice(insights, '<InsightBlurGate locked={!isPro}', '<ScrollView horizontal');
    expect(gate).toContain('MoodLineChart');
    expect(gate).toContain('{!!trend?.summary && (');
  });

  it('游客态跳过请求（守卫先于 Promise.all）', () => {
    expect(insights.indexOf('if (!isLoggedIn)')).toBeGreaterThanOrEqual(0);
    expect(insights.indexOf('if (!isLoggedIn)')).toBeLessThan(
      insights.indexOf('getMoodTrend(TREND_DAYS)')
    );
  });

  it('契约：该行的封装已接线（不再有「封装未接线」标记）', () => {
    const row = contractRow('/api/v1/mood/trend', 'GET');
    expect(contractFrontCell(row)).toContain('services/api.ts:');
    expect(row).not.toContain('（封装未接线）');
  });
});

describe('② GET /discovery/daily：删封装（后端保留，端点仍 deprecated）', () => {
  it('api.ts 里 getDailyDiscovery 与 DailyDiscovery 类型引用已消失', () => {
    expect(api).not.toContain('getDailyDiscovery');
    expect(api).not.toContain('DailyDiscovery');
    expect(api).not.toContain('/discovery/daily');
  });

  it('types 里不再导出 DailyDiscovery 接口', () => {
    expect(types).not.toMatch(/export interface DailyDiscovery\b/);
  });

  it('全前端源码没有任何调用点（反向：不许被别处接回去）', () => {
    const hits = srcFiles().filter((f) => {
      const src = read(f);
      return src.includes('getDailyDiscovery') || src.includes('/discovery/daily');
    });
    expect(hits).toEqual([]);
  });

  it('契约：该行的前端列退回「—」，但这条端点本身仍登记在册', () => {
    const row = contractRow('/api/v1/discovery/daily', 'GET');
    expect(contractFrontCell(row)).toBe('—');
  });

  it('后端仍是 deprecated（删的是前端死代码，不是端点）', () => {
    expect(readRepo(DISCOVERY_PY)).toMatch(
      /@router\.get\("\/discovery\/daily", deprecated=True\)/
    );
  });
});

describe('③ DELETE /user/push-token：改判为「有意零消费者」的反向闸', () => {
  it('封装仍保留（不删：注销路径若改口径还有出口）', () => {
    expect(api).toMatch(
      /export async function clearPushToken\(\): Promise<void> \{\s*\n\s*await request<\{ status: string \}>\('\/user\/push-token', \{ method: 'DELETE' \}\);/
    );
  });

  it('除 api.ts 自身外，全前端无人调用 clearPushToken', () => {
    const hits = srcFiles()
      .filter((f) => f !== API)
      .filter((f) => codeOnly(read(f)).includes('clearPushToken'));
    expect(hits).toEqual([]);
  });

  it('logout 块里没有 clearPushToken，且写明了这是有意不接', () => {
    const block = slice(auth, 'const logout = useCallback', 'const deleteMyAccount');
    expect(codeOnly(block)).not.toContain('clearPushToken');
    expect(block).toContain('有意不接');
    expect(block).toContain('orphanEndpoints.test.ts');
    // 既有产品口径不能被这批改掉
    expect(block).toContain('await logoutApi(rt);');
    expect(block).toContain('await resetSession();');
  });

  it('deleteMyAccount 块里没有 clearPushToken，且指向后端自清的取证', () => {
    const block = slice(auth, 'const deleteMyAccount', 'const refreshUser');
    expect(codeOnly(block)).not.toContain('clearPushToken');
    expect(block).toContain('soft_delete_account');
    expect(block).toContain('user_service.py:425');
    expect(block).toContain('test_account_deletion.py:71');
    expect(block).toContain('await deleteAccount();');
  });

  it('后端取证仍然成立（改判依据不是一次性快照）', () => {
    const lines = readRepo(USER_SERVICE_PY).split('\n');
    expect(lines[424]).toContain('await self.clear_push_tokens(db, user_id)');
    expect(readRepo(ACCOUNT_TEST_PY)).toContain(
      'async def test_soft_delete_clears_push_tokens(db)'
    );
  });

  it('契约：该行仍带「封装未接线」——这正是本改判的登记形状', () => {
    const row = contractRow('/api/v1/user/push-token', 'DELETE');
    expect(row).toContain('（封装未接线）');
    expect(row).toContain('api.ts:584');
  });
});
