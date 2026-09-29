/**
 * I2 视觉/交互守卫：死目标、危险区分级、对比度（前端 UI 审计）。
 *
 * 为什么用源码守卫而不是渲染测试：这几处都是「点了没反应 / 看起来能点 / 灰到看不见」
 * 一类问题——jest 断言不了人眼可见性，但能断言结构性事实（有没有 onPress、
 * 是 View 还是 TouchableOpacity、颜色取哪个 token）。lineHeight 的单位守卫在
 * lineHeight.test.ts，本文件只覆盖其余四项。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** 取 needle 所在的 JSX 片段（前后各若干行），用于「这一段里有没有 X」的断言。 */
function snippet(rel: string, needle: string, before = 4, after = 8): string {
  const lines = read(rel).split('\n');
  const i = lines.findIndex((l) => l.includes(needle));
  expect(i).toBeGreaterThanOrEqual(0);
  return lines.slice(Math.max(0, i - before), i + after + 1).join('\n');
}

describe('首页「昨天记录」不再是死目标', () => {
  const rel = 'src/screens/HomeScreen.tsx';

  it('yesterdayCard 带 onPress，直达复盘 Tab', () => {
    const block = snippet(rel, 'styles.yesterdayCard', 1, 4);
    expect(block).toMatch(/onPress=\{\(\) => navigation\.navigate\('Tabs', \{ screen: 'Reflect' \}\)\}/);
  });
});

describe('个人页：只读行不做成可点行', () => {
  const rel = 'src/screens/ProfileScreen.tsx';

  it('额度行（usage）是 View 而非 TouchableOpacity，且不显示 › 箭头', () => {
    const lines = read(rel).split('\n');
    const i = lines.findIndex((l) => l.includes("t('profile.usage')"));
    expect(i).toBeGreaterThanOrEqual(0);
    const block = lines.slice(i - 2, i + 4).join('\n');
    expect(block).toContain('<View style={styles.settingItem}>');
    expect(block).not.toMatch(/TouchableOpacity/);
    expect(block).not.toMatch(/styles\.arrow/);
    expect(block).not.toMatch(/onPress/);
  });
});

describe('个人页：登出与注销的权重分级', () => {
  const rel = 'src/screens/ProfileScreen.tsx';

  it('退出登录降为次级灰字（textTertiary），不再用 error 红', () => {
    const style = snippet(rel, 'logoutText:', 0, 1);
    expect(style).toMatch(/logoutText: \{[^}]*Colors\.textTertiary/);
    expect(style).not.toMatch(/logoutText: \{[^}]*Colors\.error/);
  });

  it('注销账号独立成分组（dangerSection），按钮与提示同框', () => {
    const block = snippet(rel, 'dangerSection}', 1, 6);
    expect(block).toContain('styles.deleteBtn');
    expect(block).toContain('onDeleteAccount');
    expect(block).toContain("t('profile.deleteHint')");
  });

  it('dangerSection 用 error 色描边，deleteText 用 error 色', () => {
    const section = snippet(rel, 'dangerSection: {', 0, 4);
    expect(section).toMatch(/borderColor: Colors\.borderError/);
    expect(section).toMatch(/borderWidth: 1/);
    expect(snippet(rel, 'deleteText: {', 0, 1)).toMatch(/Colors\.error/);
  });

  it('borderError token 存在且是 error 色的低透明度版本', () => {
    const colors = read('src/theme/colors.ts');
    expect(colors).toMatch(/borderError: 'rgba\(239, 68, 68, 0\.35\)'/);
    expect(colors).toMatch(/error: '#EF4444'/);
  });
});

describe('对比度与死代码', () => {
  it('复盘输入框 placeholder 用 textTertiary（不再是最暗的 textMuted）', () => {
    const src = read('src/screens/ReflectScreen.tsx');
    expect(src).toContain('placeholderTextColor={Colors.textTertiary}');
    expect(src).not.toContain('placeholderTextColor={Colors.textMuted}');
  });

  it('探索页不留未使用的 requireAuth', () => {
    const src = read('src/screens/ExploreScreen.tsx');
    expect(src).not.toMatch(/requireAuth/);
    // useAuth 仍在用（isLoggedIn），只有 useRequireAuth 该走
    expect(src).toContain('const { isLoggedIn } = useAuth();');
  });
});

describe('注销提示文案：15 语言', () => {
  const langs = Object.keys(resources);

  it('15 个语言都有 profile.deleteHint 且非空', () => {
    expect(langs).toHaveLength(15);
    langs.forEach((lng) => {
      const v = (resources as any)[lng].translation.profile?.deleteHint;
      expect(typeof v).toBe('string');
      expect(v.trim().length).toBeGreaterThan(0);
    });
  });

  it('中文文案讲清「不可逆 + 删全部记录」', () => {
    const v = (resources as any).zh.translation.profile.deleteHint;
    expect(v).toMatch(/不可逆/);
    expect(v).toMatch(/删除/);
  });
});
