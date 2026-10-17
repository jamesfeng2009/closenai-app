/**
 * 批 2 · D-G10（四个空转本地模块）的接线/删除闸（2026-10-17）。
 *
 * 缺陷原状：`services/db/` 下四个模块的**生产引用＝0**，只有测试在保绿——
 * 「有测试、无运行时」正是工作单要避免的第三种状态。分治按 D-G10 默认值：
 * 能接读路径的接（有网即写、无网即读），接不上的整块删，不留第三种状态。
 *
 * - `comboReportRepository` → 接 `PersonalityScreen`：生成成功后 `cache()`、进屏先 `getLatest()`。
 *   这条尤其值：组合报告要 **POST** 且是 Pro 算力，首屏不能自动打，
 *   所以离线时缓存就是唯一的正文来源（不是可有可无的加速）。
 * - `subscriptionStatusRepository` → 接 `PaywallScreen.loadStatus`：成功写副本、失败读副本，
 *   只为「上次看到的界面」。**红线**（仓储头注释 `:11-15`）：副本不参与任何 gate 判断，
 *   所以 `isPro / currentTier / isCurrentPlan / canPurchase` 四条一律只看服务端 `status`，
 *   本闸把这四条逐行钉住——接线时最容易顺手把 `?? staleStatus` 写进权益判断里。
 * - `dailyDiscoveryRepository` → 删（它唯一的数据源是 D-G9 已删封装的 deprecated 端点）。
 * - `syncService.resolveConflict` → 删（含 `__tests__/sync.test.ts`）：两条新接线都是单行缓存，
 *   没有合并语义，用不上这个 LWW helper；将来真做向上同步时连调用方一起写回来更诚实。
 * - 表不动：`migrations.ts` 的 `combo_report / daily_discovery / subscription_status` 保留——
 *   迁移历史只追加，删表是一次不可回滚的数据动作，不在本批。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
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

const PERSONALITY = 'src/screens/PersonalityScreen.tsx';
const PAYWALL = 'src/screens/PaywallScreen.tsx';
const COMBO_REPO = 'src/services/db/comboReportRepository.ts';
const SUB_REPO = 'src/services/db/subscriptionStatusRepository.ts';
const MIGRATIONS = 'src/services/db/migrations.ts';
const CACHE_TEST = '__tests__/cacheRepositories.test.ts';

const personality = read(PERSONALITY);
const paywall = read(PAYWALL);
const comboRepo = read(COMBO_REPO);
const subRepo = read(SUB_REPO);

function srcFiles(dir = 'src'): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...srcFiles(rel));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(rel);
  }
  return out;
}

function slice(src: string, from: string, to: string): string {
  const a = src.indexOf(from);
  expect(a).toBeGreaterThanOrEqual(0);
  const b = src.indexOf(to, a + from.length);
  expect(b).toBeGreaterThan(a);
  return src.slice(a, b);
}

describe('① comboReportRepository：进屏读缓存、生成成功写缓存（Tier 2 只读，服务端为准）', () => {
  it('屏里只用到这两个仓储方法（没有第三条旁路）', () => {
    const calls = personality.match(/comboReportRepo\.\w+/g) ?? [];
    expect([...new Set(calls)].sort()).toEqual(['comboReportRepo.cache', 'comboReportRepo.getLatest']);
    expect(personality).toContain("import { comboReportRepo } from '../services/db/comboReportRepository';");
  });

  it('首屏读缓存在登录守卫之前（游客/离线也有正文可读）', () => {
    const load = slice(personality, 'const loadInitial = useCallback', 'const onComboReport');
    expect(load).toContain('comboReportRepo.getLatest().catch(() => null),');
    expect(load).toContain('if (cachedCombo) setCombo(comboRowToState(cachedCombo));');
    expect(load.indexOf('comboReportRepo.getLatest()')).toBeLessThan(
      load.indexOf('if (!isLoggedIn) return;')
    );
    expect(load.indexOf('if (cachedCombo)')).toBeLessThan(load.indexOf('setLoading(false);'));
  });

  it('生成成功后才写缓存，且写失败不顶掉已渲染的报告', () => {
    const handler = slice(personality, 'const onComboReport = async', 'if (loading) {');
    expect(handler).toContain('const report = await generateComboReport();');
    expect(handler).toContain('setCombo(report);');
    expect(handler).toContain('comboReportRepo.cache(comboStateToRow(report)).catch(() => {});');
    expect(handler.indexOf('setCombo(report);')).toBeLessThan(
      handler.indexOf('comboReportRepo.cache(')
    );
    // 服务端为准：catch 分支里不许写缓存
    const catchBlock = slice(handler, '} catch (e) {', '} finally {');
    expect(catchBlock).not.toContain('comboReportRepo');
  });

  it('映射：整份报告进 report 列，period 取后端「每月 1 份」的生成月', () => {
    const toRow = slice(personality, 'function comboStateToRow', 'function comboRowToState');
    expect(toRow).toContain('report: JSON.stringify(c)');
    expect(toRow).toContain('period: c.generated_at.slice(0, 7)');
    expect(toRow).toContain("id: 'current'");
    const fromRow = slice(personality, 'function comboRowToState', 'function resolveStageIndex');
    expect(fromRow).toMatch(/JSON\.parse\(row\.report\)/);
    expect(fromRow).toMatch(/catch \{\s*\n\s*return null;/);
  });

  it('仓储语义没被接线改掉：cache 仍只留最新 1 份', () => {
    expect(comboRepo).toContain('ORDER BY updated_at DESC LIMIT 1');
    expect(comboRepo).toContain('DELETE FROM combo_report WHERE id NOT IN');
  });
});

describe('② subscriptionStatusRepository：只作展示副本，红线是它不进任何 gate', () => {
  it('屏里只用到 cache 与 getCurrent 两个方法', () => {
    const calls = paywall.match(/subscriptionStatusRepo\.\w+/g) ?? [];
    expect([...new Set(calls)].sort()).toEqual([
      'subscriptionStatusRepo.cache',
      'subscriptionStatusRepo.getCurrent',
    ]);
  });

  it('成功写副本、失败读副本（读的那条在 catch 里）', () => {
    const load = slice(paywall, 'const loadStatus = async', 'const loadProducts');
    expect(load).toContain('setStatus(s);');
    expect(load).toContain('subscriptionStatusRepo.cache(statusToRow(s)).catch(() => {});');
    const catchBlock = slice(load, '} catch (e) {', '} finally {');
    expect(catchBlock).toContain(
      'setStaleStatus(await subscriptionStatusRepo.getCurrent().catch(() => null));'
    );
    expect(slice(load, 'try {', '} catch')).not.toContain('getCurrent');
  });

  it('写的那一行就是仓储读的那一行（id 两侧都是 current）', () => {
    expect(paywall).toContain("const STATUS_ROW_ID = 'current';");
    expect(paywall).toContain('id: STATUS_ROW_ID,');
    expect(subRepo).toContain("WHERE id = 'current'");
  });

  it('四条权益判断逐行只看服务端 status，没有 stale 旁路', () => {
    const lines = [
      /^\s*const isPro = .*$/m,
      /^\s*const currentTier: UserTier = .*$/m,
      /^\s*const isCurrentPlan = .*$/m,
      /^\s*const canPurchase = .*$/m,
    ].map((re) => {
      const hit = re.exec(paywall);
      expect(hit).not.toBeNull();
      return hit![0];
    });
    for (const line of lines) {
      expect(line).not.toMatch(/stale/i);
      expect(line).not.toContain('subscriptionStatusRepo');
    }
    // 正向的那一半：直接读权益的两条确实只看服务端 status
    expect(lines[0]).toContain('status?.is_active');
    expect(lines[2]).toContain('status?.is_active');
  });

  it('展示卡只在服务端没数据时出现，且自带「非实时」标注', () => {
    const flag = slice(paywall, 'const showStaleStatus =', 'const badgeFor');
    expect(flag).toContain('!status &&');
    expect(flag).toContain('staleStatus.is_active === 1');
    expect(flag).toContain("staleStatus.plan !== 'free'");
    expect(paywall).toContain('{showStaleStatus && staleStatus && (');
    expect(paywall).toContain("{t('paywall.staleStatusNote')}");
    expect(slice(paywall, 'staleNote: {', '},')).toContain('fontSize: FontSize.sm');
  });

  it('副本行不假装带得出 is_trial/product_id（只回档位与到期日）', () => {
    const toRow = slice(paywall, 'function statusToRow', 'const SKU_MAP');
    expect(toRow).toContain('plan: s.tier,');
    expect(toRow).toContain('is_active: s.is_active ? 1 : 0,');
    expect(toRow).not.toMatch(/product_id|is_trial|auto_renew/);
  });
});

describe('③ 接不上的两块整块删除（表与迁移历史保留）', () => {
  it('dailyDiscoveryRepository.ts 与 syncService.ts 已不存在', () => {
    expect(fs.existsSync(path.join(ROOT, 'src/services/db/dailyDiscoveryRepository.ts'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/services/db/syncService.ts'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, '__tests__/sync.test.ts'))).toBe(false);
  });

  it('全 src 无人引用这两个模块的名字', () => {
    const offenders = srcFiles().filter((f) => {
      const src = read(f);
      return (
        /\bresolveConflict\b/.test(src) ||
        /\bDailyDiscoveryRepository\b/.test(src) ||
        /\bdailyDiscoveryRepo\b/.test(src)
      );
    });
    expect(offenders).toEqual([]);
  });

  it('cacheRepositories.test.ts 不再引用已删仓储，但保留两条在用的 case', () => {
    const cacheTest = read(CACHE_TEST);
    expect(cacheTest).not.toContain('DailyDiscovery');
    expect(cacheTest).toContain("new ComboReportRepository(db)");
    expect(cacheTest).toContain("new SubscriptionStatusRepository(db)");
  });

  it('三张表仍在迁移里（删表是数据动作，不在本批）', () => {
    const migrations = read(MIGRATIONS);
    for (const table of ['combo_report', 'daily_discovery', 'subscription_status']) {
      expect(migrations).toContain(`CREATE TABLE IF NOT EXISTS ${table}`);
    }
  });
});

describe('④ 文案面：15 语言都要有「非实时」这句标注', () => {
  const locales = Object.keys(resources);

  it('语言数就是 15，漏一门的话下面两条会假绿', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s 有 paywall.staleStatusNote 且非空', (code) => {
    const flat = flatten((resources as any)[code].translation as Tree);
    expect(flat['paywall.staleStatusNote']).toBeTruthy();
  });

  it('15 份文案两两不同（复制粘贴同一句等于没翻）', () => {
    const values = locales.map(
      (code) => flatten((resources as any)[code].translation as Tree)['paywall.staleStatusNote']
    );
    expect(new Set(values).size).toBe(15);
  });
});
