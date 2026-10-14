/**
 * 首页每日卡与睡前复盘的接线闸（C4 主位 / C2 次位 / C1 全屏 + 兜底卡）。
 *
 * 这批改动的失效方式全是「静默不生效」：路径写错只让卡不出现、i18n 漏键只让界面回退成
 * 英文、危机分支写错会把转介当第 1 步成功推进。RN 组件在 node 环境下渲染不了
 * （jest.config.js 的 testEnvironment），所以这里钉三件可核验的事：
 * ① 前端出网形状与 contracts/openapi.json 逐字对上；② 屏与屏的接线点存在；
 * ③ 源码里每一个 t('键') 在英文表真的存在。取数判定本身在 dailyCards.test.ts 里断言。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
const readBackend = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', 'backend', rel), 'utf8');

const openapi = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', '..', 'contracts', 'openapi.json'), 'utf8')
);

const HOME = 'src/screens/HomeScreen.tsx';
const BEDTIME = 'src/screens/BedtimeReviewScreen.tsx';
const APP = 'App.tsx';

/** en 表的扁平取值：a.b.c → 值；缺失即 undefined。 */
function lookup(lang: string, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (acc: unknown, part) =>
      acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined,
    (resources as Record<string, { translation: Record<string, unknown> }>)[lang].translation
  );
}

/** 抓源码里所有字面量 t('a.b') 的键（动态键另案断言）。 */
function usedKeys(src: string): string[] {
  return [...new Set([...src.matchAll(/\bt\('([a-zA-Z0-9_.]+)'/g)].map((m) => m[1]))].sort();
}

describe('每日卡三条端点：出网形状与冻结契约逐字对上', () => {
  it('四条路径都在 openapi 契约里且方法一致', () => {
    const paths: Record<string, string[]> = {
      '/api/v1/share/on-this-day': ['get'],
      '/api/v1/curriculum/week': ['get'],
      '/api/v1/curriculum/{event_id}/read': ['post'],
      '/api/v1/bedtime/today': ['get'],
      '/api/v1/bedtime/step': ['post'],
    };
    Object.entries(paths).forEach(([p, methods]) => {
      expect(Object.keys(openapi.paths[p] ?? {})).toEqual(methods);
    });
  });

  it('api.ts 里五个函数各打这几条路径（路径写错就是卡静默不出）', () => {
    const api = read('src/services/api.ts');
    expect(api).toContain("request<OnThisDayResponse>('/share/on-this-day')");
    expect(api).toContain("request<CurriculumWeekResponse>('/curriculum/week')");
    expect(api).toContain('request<{ id: string; read: boolean }>(`/curriculum/${eventId}/read`, { method: \'POST\' })');
    expect(api).toContain("request<BedtimeTodayResponse>('/bedtime/today')");
    expect(api).toContain("request<BedtimeStepResponse>('/bedtime/step'");
  });

  it('step 入参只送 step + text，且空文本显式 null（缺省即跳过，不能靠字段缺失表达）', () => {
    const body = read('src/services/api.ts');
    const fn = body.slice(body.indexOf('export async function postBedtimeStep'));
    expect(fn.slice(0, 700)).toContain('body: JSON.stringify({ step, text: text ?? null })');
  });

  it('两条 AI 端点挂同意门、考古卡不挂：未同意时前端只收卡不报错', () => {
    // step2/step3 与主题卡正文是 AI 生成内容 → require_ai_consent（合规 5.1.2(i)）；
    // 考古卡是用户自己的历史原话、服务端零 LLM（share_service.on_this_day），不该被同意门挡住。
    expect(readBackend('routers/bedtime.py')).toContain('require_ai_consent');
    expect(readBackend('routers/curriculum.py')).toContain('require_ai_consent');
    expect(readBackend('services/share_service.py')).toMatch(/零 LLM；按年份升序/);
    const onThisDay = readBackend('routers/share.py');
    expect(onThisDay.slice(onThisDay.indexOf('async def on_this_day'))).not.toContain('require_ai_consent');
    // 复盘屏自己处理 403/40303：弹同意窗而不是报错
    expect(read(BEDTIME)).toContain('const CODE_AI_CONSENT_REQUIRED = 40303;');
  });
});

describe('C4 · 首页每日主位「N 年前的今天」接线', () => {
  const src = read(HOME);

  it('取数→判定→渲染三点都在，且空候选不渲染空卡', () => {
    expect(src).toContain('getOnThisDay()');
    expect(src).toContain('const visibleCards = archiveCards(archive?.candidates ?? []);');
    expect(src).toContain('{visibleCards.length > 0 && (');
  });

  it('周年标题用 count 交给 i18next 选 CLDR 档，前端不自算日期', () => {
    expect(src).toContain('t(ARCHIVE_TITLE_KEY, {');
    expect(src).toContain('count: visibleCards[0].years_ago');
    expect(src).not.toMatch(/new Date\(\)\.getFullYear\(\) - /);
  });

  it('created_at 缺失时不显日期行（不拿「今天」反推一个像样的落库日）', () => {
    expect(src).toContain('{!!c.created_at && (');
  });

  it('折叠态只端首条，展开后才列满并给分享出口', () => {
    expect(src).toContain('(archiveOpen ? visibleCards : visibleCards.slice(0, 1))');
    expect(src).toContain('{archiveOpen && (');
  });

  it('分享出口只有深链一条路：真要点分享才取推荐码，且没有第二条 PDF 出口', () => {
    expect(src).toContain('await getShareLink()');
    // 深链惰性生成推荐码：预取会白烧一次码，所以整屏只这一处调用
    expect((src.match(/getShareLink\(\)/g) ?? []).length).toBe(1);
    expect(src).not.toContain('/share/memory-card');
  });

  it('分享两拍埋点齐全：点开与真正分享出去分开计', () => {
    expect(src).toContain("trackFunnelShare('share_tapped', 'memory')");
    expect(src).toContain("trackFunnelShare('share_completed', 'memory')");
    expect(src).toContain('if (res.action === Share.sharedAction)');
  });
});

describe('C2 · 首页次位本周主题卡接线', () => {
  const src = read(HOME);

  it('取数→取卡→渲染三点都在，全读即隐藏', () => {
    expect(src).toContain('getCurriculumWeek()');
    expect(src).toContain('const themeCard = week ? pickThemeCard(week.days) : null;');
    expect(src).toContain('{!!themeCard && (');
  });

  it('周内序用后端同一份 day_no 编码，槽位从 1 起显', () => {
    expect(src).toContain('weekSlot(themeCard.day_no) + 1');
    expect(src).toContain('weekCells(week?.days ?? [])');
  });

  it('点击即记已读并乐观收敛未读数；标记失败不拦跳转（下次仍能看到这张卡）', () => {
    expect(src).toContain('await markCurriculumRead(eventId)');
    expect(src).toContain('read: true');
    expect(src).toContain('unread_count: Math.max(0, prev.unread_count - 1)');
    expect(src).toContain("navigation.navigate('Tabs', { screen: 'Reflect' })");
  });

  it('主题卡点击埋点带 feature/action 白名单键', () => {
    expect(src).toContain("track('curriculum_card_opened', { feature: 'curriculum', action: 'open' })");
  });
});

describe('C1 · 睡前复盘：首页晚间兜底卡 + 全屏三步', () => {
  const home = read(HOME);
  const bed = read(BEDTIME);

  it('兜底卡由本地时刻与完成态共同决定，点卡进全屏屏并埋点', () => {
    expect(home).toContain('const bedtimeCardVisible = showBedtimeCard(minutesOfDay, bedtimeDone);');
    expect(home).toContain('{bedtimeCardVisible && (');
    expect(home).toContain("track('bedtime_card_opened', { feature: 'bedtime', action: 'open' })");
    expect(home).toContain("navigation.navigate('BedtimeReview')");
  });

  it('时刻每次聚焦重取：跨午夜后窗口自然结束，复盘归为昨日', () => {
    expect(home).toContain('setMinutesOfDay(now.getHours() * 60 + now.getMinutes())');
  });

  it('三条请求各自 catch，一条失败不牵连另两条出卡；游客态清空', () => {
    expect(home).toContain('.catch(() => setArchive(null))');
    expect(home).toContain('.catch(() => setWeek(null))');
    expect(home).toContain('.catch(() => setBedtimeDone(false))');
    expect(home).toContain('if (!isLoggedIn) {');
  });

  it('三步同屏常驻：第 1 步过了也不消失（原型 Screen 26 的锚点）', () => {
    expect(bed).toContain("{phase === 'step1' ? (");
    // 整块条件渲染会让第 1 步在推进后消失，用户失去已提交那句话的回看位置
    expect(bed).not.toContain("{phase === 'step1' && (");
    expect(bed).toContain("t('bedtime.step1Label')");
    expect(bed).toContain("t('bedtime.step2Label')");
    expect(bed).toContain("t('bedtime.step3Label')");
  });

  it('半程恢复按已落库的最深一步续接，重进不重跑第 1 步', () => {
    expect(bed).toContain('setPhase(resumePhase(data.record))');
    expect(bed).toContain('hydrate(data.record)');
  });

  it('第 2/3 步自动生成且去重：同一步不并发二次 LLM 请求', () => {
    expect(bed).toContain('if (generating.current.has(step)) return;');
    expect(bed).toContain("if (phase === 'step2' && !step2) runStep(2);");
    expect(bed).toContain("if (phase === 'step3' && !step3) runStep(3);");
  });

  it('危机拦截：停在原地不落流程，正文逐字渲染服务端出稿文案，前端不另写危机话术', () => {
    const branch = bed.slice(bed.indexOf('const submitStep1'));
    expect(branch).toContain('if (isCrisisResponse(res)) {');
    expect(branch).toContain('setCrisis(res.crisis);');
    expect(branch.slice(branch.indexOf('setCrisis(res.crisis);')).startsWith('setCrisis(res.crisis);\n        return;'));
    expect(bed).toContain('{crisis.reply}');
    expect(bed).toContain('{t(\'bedtime.crisisHotline\', { hotline: crisis.hotline })}');
    // 系统弹窗读不完关怀文案，也不该由前端另起一套危机话术
    expect(bed).not.toContain('Alert.alert');
  });

  it('未同意 AI 共享走同意窗而不是报错；拒绝即退出本屏', () => {
    expect((bed.match(/e\.code === CODE_AI_CONSENT_REQUIRED/g) ?? []).length).toBe(3);
    expect(bed).toContain('<AIConsentModal');
    expect(bed).toContain('await setAiConsent(true)');
    expect(bed).toContain('navigation.goBack()');
  });

  it('输入上限与后端 schema 同值：前端放行、后端 422 的分叉不许存在', () => {
    const n = Number(
      (readBackend('models/schemas.py').match(/text: Optional\[str\] = Field\(None, max_length=(\d+)/) ?? [])[1]
    );
    expect(n).toBeGreaterThan(0);
    expect(bed).toContain(`maxLength={${n}}`);
  });

  it('失败有出口：错误条 + 重试 + 生成位显式失败文案，不静默留白', () => {
    expect(bed).toContain('onPress={load}');
    expect(bed).toContain("t('bedtime.step2Failed')");
    expect(bed).toContain("t('bedtime.step3Failed')");
    expect(bed).toContain("t('bedtime.loadFailed')");
  });

  it('屏在导航注册（参数表 + Stack.Screen + import 三处齐，缺一处就是白屏或未登记路由）', () => {
    const app = read(APP);
    expect(app).toContain("import BedtimeReviewScreen from './src/screens/BedtimeReviewScreen'");
    expect(app).toContain('BedtimeReview: undefined;');
    expect(app).toContain('name="BedtimeReview"');
    expect(app).toContain('component={BedtimeReviewScreen}');
  });
});

describe('文案不漏键：源码用到的每个 t() 键都在英文表里', () => {
  // i18next 的 fallback 让漏键只在真机上显英文，编译与运行都不报错——
  // 上一轮 13 个语言的约 270 个逐字英文值就是这么躲过所有检查的。
  [HOME, BEDTIME, 'src/components/ShareManifest.tsx'].forEach((rel) => {
    it(`${rel} 的字面量键在 en 表全部命中`, () => {
      // 复数键在表里只有 _one/_other 变体，基础键名由 i18next 按 count 解析，
      // 所以命中任一变体即算在册（home.milestoneStreak / home.archiveTitle 都是这种写法）。
      const missing = usedKeys(read(rel)).filter(
        (k) => lookup('en', k) === undefined && lookup('en', `${k}_other`) === undefined
      );
      expect(missing).toEqual([]);
    });
  });

  it('动态键 ARCHIVE_TITLE_KEY 展开后是 home.archiveTitle，且 15 语言都有 other 档', () => {
    expect(Object.keys(resources).length).toBe(15);
    Object.keys(resources).forEach((lng) => {
      expect(typeof lookup(lng, 'home.archiveTitle_other')).toBe('string');
    });
    expect(lookup('en', 'home.archiveTitle_one')).toBe('A year ago today');
  });

  it('睡前复盘 26 键在 en/zh 齐备（新增文案的锚点表）', () => {
    const bedKeys = usedKeys(read(BEDTIME)).filter((k) => k.startsWith('bedtime.'));
    expect(bedKeys.length).toBeGreaterThanOrEqual(24);
    bedKeys.forEach((k) => {
      expect(typeof lookup('en', k)).toBe('string');
      expect(typeof lookup('zh', k)).toBe('string');
    });
  });
});
