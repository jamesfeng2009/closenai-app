/**
 * I4 信任呈现守卫：分享内容清单 + 长文不出 Alert。
 *
 * 这类改动的失效方式是「清单漏了一个真会被分享出去的字段」或「Alert 又长回来」，
 * 所以断言盯两件事：清单条目与拼装分享文本的字段严格对应、屏内卡片取代 Alert。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

function block(rel: string, needle: string, before = 1, after = 10): string {
  const lines = read(rel).split('\n');
  const i = lines.findIndex((l) => l.includes(needle));
  expect(i).toBeGreaterThanOrEqual(0);
  return lines.slice(Math.max(0, i - before), i + after + 1).join('\n');
}

function countNeedle(rel: string, re: RegExp): number {
  return (read(rel).match(re) || []).length;
}

describe('分享内容清单组件', () => {
  const rel = 'src/components/ShareManifest.tsx';

  it('默认折叠，展开前只暴露条数', () => {
    const src = read(rel);
    expect(src).toMatch(/useState\(false\)/);
    expect(src).toMatch(/\{list\.length\}/); // 折叠态也要说清有几项
    expect(src).toContain("t('share.manifestTitle')");
    expect(src).toContain("t('share.manifestTextNote')");
  });

  it('空条目被过滤，全空则整块不渲染（避免出现空清单）', () => {
    const src = read(rel);
    expect(src).toMatch(/items\.filter\(/);
    expect(src).toMatch(/list\.length === 0[\s\S]*return null/);
  });
});

describe('三条分享路径都挂清单，且清单与真实分享字段一致', () => {
  it.each([
    'src/screens/ShareCardScreen.tsx',
    'src/screens/FirstReadCardScreen.tsx',
    'src/screens/InsightsScreen.tsx',
  ])('%s 引入并渲染 ShareManifest', (rel) => {
    const src = read(rel);
    expect(src).toContain("import ShareManifest from '../components/ShareManifest'");
    expect(src).toContain('<ShareManifest');
  });

  it('名片页清单含昵称——后端 share_text 里确实带（share_service.py 名片文案）', () => {
    const items = block('src/screens/ShareCardScreen.tsx', '<ShareManifest', 0, 10);
    expect(items).toContain('card.user_name');
    expect(items).toContain('card.mbti_type');
    expect(items).toContain('card.tagline');
    expect(items).toContain("t('share.manifestLink')");
  });

  it('初读卡清单逐条列出断言句（与 onShare 拼装的 card.assertions 同源）', () => {
    const rel = 'src/screens/FirstReadCardScreen.tsx';
    const items = block(rel, '...card.assertions,', 6, 4);
    expect(items).toContain('card.assertions');
    expect(items).toContain('card.summary');
    expect(items).toContain("t('share.manifestLink')");
  });

  it('报告清单含周期/趋势/正文，且不含昵称（本地分享文本本就不带）', () => {
    const rel = 'src/screens/InsightsScreen.tsx';
    const items = block(rel, '<ShareManifest', 0, 8);
    expect(items).toContain('report.period_label');
    expect(items).toContain('report.mood_trend');
    expect(items).toContain('report.content');
    expect(items).not.toContain('user_name');
  });

  it('清单条数与分享文本构成同步：三个 screens 都以 manifestLink 收尾', () => {
    ['src/screens/ShareCardScreen.tsx', 'src/screens/FirstReadCardScreen.tsx', 'src/screens/InsightsScreen.tsx']
      .forEach((rel) => {
        expect(read(rel)).toContain("t('share.manifestLink')");
      });
  });

  it('清单不能与实际分享文本脱钩：报告页分享正文仍是这四段', () => {
    const body = block('src/screens/InsightsScreen.tsx', 'const text = [', 1, 6);
    expect(body).toContain('report.period_label');
    expect(body).toContain('report.mood_trend');
    expect(body).toContain('report.content');
    expect(body).toContain('report.share_url');
  });

  it('清单不能与实际分享文本脱钩：初读卡分享正文仍是断言 + 小结 + 链接', () => {
    const body = block('src/screens/FirstReadCardScreen.tsx', 'const body = [', 1, 6);
    expect(body).toContain('card.assertions.map');
    expect(body).toContain('card.summary');
    expect(body).toContain('card.share_url');
  });
});

describe('首页签到解读：长文出 Alert 改屏内卡片', () => {
  const rel = 'src/screens/HomeScreen.tsx';
  const src = read(rel);

  it('AI 解读不再走 Alert，改写 checkinNote 状态', () => {
    expect(src).not.toMatch(/Alert\.alert\(\s*`\$\{res\.mood_emoji\}/);
    expect(src).toContain('setCheckinNote({');
    expect(src).toContain('const [checkinNote, setCheckinNote] = useState');
  });

  it('屏内卡片渲染正文，并有关闭入口', () => {
    expect(src).toContain('{checkinNote && (');
    expect(src).toContain('{checkinNote.text}');
    expect(src).toContain('onPress={() => setCheckinNote(null)}');
    expect(src).toContain("t('common.close')");
  });

  it('正文有行高（长文最容易挤成一坨）', () => {
    const style = block(rel, 'checkinNoteBody:', 0, 5);
    expect(style).toContain('lineHeight: LineHeight.small');
  });

  it('其余 Alert 保留（本次口径：只改这一处长文）', () => {
    expect(countNeedle(rel, /Alert\.alert\(/g)).toBe(2); // 配额引导 + 请求失败
  });
});

describe('I4 新增文案：15 语言齐备', () => {
  const langs = Object.keys(resources);
  const keys: [string, string][] = [
    ['common', 'close'],
    ['share', 'manifestTitle'],
    ['share', 'manifestLink'],
    ['share', 'manifestTextNote'],
  ];

  it('15 个语言都在册', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(langs)('%s：四个新键存在且非空', (lng) => {
    keys.forEach(([b, k]) => {
      const v = (resources as any)[lng].translation[b]?.[k];
      expect(typeof v).toBe('string');
      expect((v || '').trim().length).toBeGreaterThan(0);
    });
  });

  it('中文原生：清单标题说「将分享」，备注点明纯文本版', () => {
    const zh = (resources as any).zh.translation;
    expect(zh.share.manifestTitle).toMatch(/将分享/);
    expect(zh.share.manifestTextNote).toMatch(/纯文本/);
    expect(zh.common.close).toBe('关闭');
  });

  it('昵称这一项不能被 i18n 兜底糊掉：链接行明确带推荐码', () => {
    const zh = (resources as any).zh.translation;
    expect(zh.share.manifestLink).toMatch(/推荐码/);
  });
});
