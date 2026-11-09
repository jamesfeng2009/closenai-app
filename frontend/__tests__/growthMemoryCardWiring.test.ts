/**
 * 回忆卡（GET /growth/memory-card，P2 #31）的接线闸。
 *
 * 这条端点的失效方式与别的屏不同：它每次调用都会写一次 lite LLM。所以除了
 * 「有没有接」之外，本闸主要钉三件事——
 * ① 只能由按钮触发，不能挂在聚焦刷新里（否则每次进成长记录页都花一次钱）；
 * ② 40303（未同意 AI 共享）与网络失败在屏上是两句话，不能都落成通用错误；
 * ③ 换天数档要清掉旧卡（365 天前的档位下摆着 30 天前那张＝界面在撒谎）。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const readRepo = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const API = 'src/services/api.ts';
const SCREEN = 'src/screens/GrowthRecordsScreen.tsx';

function screen() {
  return read(SCREEN);
}

/** 两个锚点之间的一段。 */
function between(src: string, start: string, end: string): string {
  const i = src.indexOf(start);
  expect(i).toBeGreaterThanOrEqual(0);
  const j = src.indexOf(end, i);
  expect(j).toBeGreaterThan(i);
  return src.slice(i, j);
}

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

describe('① api 封装指向 /growth/memory-card（GET，days_ago 走查询串）', () => {
  it('路径与参数逐字', () => {
    const m = /export async function getGrowthMemoryCard\([\s\S]*?\n}/.exec(read(API));
    expect(m).not.toBeNull();
    const body = m![0]
      .split('\n')
      .filter((line) => !line.trim().startsWith('*') && !line.trim().startsWith('//'))
      .join('\n');
    expect(body).toContain('request<GrowthMemoryCard>(`/growth/memory-card?days_ago=${daysAgo}`)');
    // 不写 method ⇒ GET（request 的默认值）；写成 POST 会打到不存在的路由
    expect(body).not.toContain('method:');
  });

  it('契约台账在册，且 handler 是后端那一条', () => {
    expect(readRepo('contracts/endpoints.md')).toContain(
      '| GET | `/api/v1/growth/memory-card` |',
    );
    expect(readRepo('backend/routers/growth.py')).toContain('@router.get("/growth/memory-card")');
  });
});

describe('② 用户点出来才请求（这条端点会写一次 LLM）', () => {
  it('屏上只有一处调用点，且在 loadMemoryCard 里', () => {
    const src = screen();
    expect(src.match(/getGrowthMemoryCard\(/g)).toHaveLength(1);
    const fn = between(src, 'const loadMemoryCard = async () => {', '\n  };');
    expect(fn).toContain('await getGrowthMemoryCard(cardDays)');
  });

  it('聚焦刷新与加载函数都不带这张卡（进屏不花钱）', () => {
    const src = screen();
    const focus = between(src, 'useFocusEffect(', '}, [load, loadActions])');
    expect(focus).not.toContain('getGrowthMemoryCard');
    const load = between(src, 'const load = useCallback(async () => {', '\n  }, [isLoggedIn]);');
    expect(load).not.toContain('getGrowthMemoryCard');
    const loadActions = between(src, 'const loadActions = useCallback(async () => {', '\n  }, [isLoggedIn]);');
    expect(loadActions).not.toContain('getGrowthMemoryCard');
  });

  it('按钮同时挡游客与重复点击', () => {
    const fn = between(screen(), 'const loadMemoryCard = async () => {', '\n  };');
    expect(fn).toContain('if (!isLoggedIn || cardLoading) return;');
    expect(screen()).toContain('disabled={cardLoading}');
  });
});

describe('③ 40303 与其它失败分开说，且失败不残留上一张卡', () => {
  it('40303 走不同意提示，其余走加载失败', () => {
    const fn = between(screen(), 'const loadMemoryCard = async () => {', '\n  };');
    expect(fn).toContain('e instanceof ApiError && e.code === 40303');
    expect(fn).toContain("else setCardError(t('growthRecords.memoryCard.failed'))");
  });

  it('catch 里先清空卡片（旧卡不能冒充这一次的结果）', () => {
    const fn = between(screen(), 'const loadMemoryCard = async () => {', '\n  };');
    const catchBody = fn.slice(fn.indexOf('} catch (e) {'));
    expect(catchBody.indexOf('setCard(null)')).toBeGreaterThan(-1);
    expect(catchBody.indexOf('setCard(null)')).toBeLessThan(catchBody.indexOf('40303'));
  });

  it('换天数档清掉旧卡与旧错误', () => {
    const fn = between(screen(), 'const selectCardDays = (days: number) => {', '\n  };');
    expect(fn).toContain('setCard(null);');
    expect(fn).toContain('setCardError(null);');
    expect(fn).toContain('setCardConsent(false);');
  });
});

describe('④ 渲染只念服务端给的字，has_data 两档分开', () => {
  it('has_data=false 走空态，has_data=true 才渲染结果', () => {
    const src = screen();
    expect(src).toContain('{!!card && !card.has_data && (');
    expect(src).toContain('{!!card && card.has_data && (');
    expect(src).toContain("t('growthRecords.memoryCard.empty', { days: card.days_ago })");
  });

  it('正文取 card_text，不在前端自造卡片文案', () => {
    const src = screen();
    expect(src).toContain('{!!card.card_text && <Text style={styles.memoryCardText}>{card.card_text}</Text>}');
    const result = between(src, '{!!card && card.has_data && (', '</View>\n        )}');
    expect(result).not.toMatch(/t\('growthRecords\.memoryCard\.[a-zA-Z]+', \{ days \}\)/);
  });

  it('天数档是 30/90/365，全部落在后端 1..365 的校验内', () => {
    const m = /const CARD_DAY_CHOICES = \[([^\]]+)\]/.exec(screen());
    expect(m).not.toBeNull();
    const days = m![1].split(',').map((s) => Number(s.trim()));
    expect(days).toEqual([30, 90, 365]);
    for (const d of days) {
      expect(d).toBeGreaterThanOrEqual(1);
      expect(d).toBeLessThanOrEqual(365);
    }
  });

  it('不发裸 fetch/XMLHttpRequest（请求出口只有 api.ts）', () => {
    expect(screen()).not.toMatch(/\bfetch\(/);
    expect(screen()).not.toMatch(/XMLHttpRequest/);
  });
});

describe('⑤ 15 语言八键齐备且带 {{days}} 占位', () => {
  const locales = Object.keys(resources);
  const keys = [
    'growthRecords.memoryCard.title',
    'growthRecords.memoryCard.hint',
    'growthRecords.memoryCard.days',
    'growthRecords.memoryCard.load',
    'growthRecords.memoryCard.loading',
    'growthRecords.memoryCard.empty',
    'growthRecords.memoryCard.consent',
    'growthRecords.memoryCard.failed',
  ];

  it('语言数就是 15', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s：八键都有值，且 days/empty 带 {{days}}', (locale) => {
    const flat = flatten((resources as any)[locale].translation as Tree);
    for (const key of keys) {
      expect(flat[key]).toBeTruthy();
    }
    expect(flat['growthRecords.memoryCard.days']).toContain('{{days}}');
    expect(flat['growthRecords.memoryCard.empty']).toContain('{{days}}');
  });

  it('en 的键不多不少（多出来的键＝没人核对的第二份文案）', () => {
    const flat = flatten((resources as any).en.translation as Tree);
    const got = Object.keys(flat).filter((k) => k.startsWith('growthRecords.memoryCard.'));
    expect(got.sort()).toEqual([...keys].sort());
  });
});
