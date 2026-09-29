/**
 * 提审阻断项（App Store 3.1.2 / 5.1.1）文案与入口守卫 · I1。
 *
 * 为什么需要它：这三处都是"不报错但会被拒"的缺口——
 * ① 自动续订声明只在隐私政策里、购买界面不写 → 3.1.2 拒；
 * ② 注销入口在隐私页只是文字指引、点了没反应 → 5.1.1 拒；
 * ③ consent 拉取失败被渲染成「未同意」→ 把服务端状态说错。
 * ②③ 没有任何功能会变红，只能靠源码断言钉住。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const LANGUAGES = Object.keys(resources);

/** 剥掉注释再扫源码：这些文件头部本身就在解释合规原因。 */
function codeOnly(rel: string): string {
  return fs
    .readFileSync(path.join(__dirname, '..', rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

function readKey(lng: string, dotted: string): unknown {
  return dotted.split('.').reduce<any>((o, p) => o?.[p], (resources as any)[lng].translation);
}

describe('I1：15 语言的计费声明与注销入口文案', () => {
  it('15 个语言全部在册', () => {
    expect(LANGUAGES).toHaveLength(15);
  });

  it.each(LANGUAGES)(
    '%s：付费墙两条计费声明与隐私页两条注销相关键都存在且非空',
    (lng) => {
      for (const key of [
        'paywall.autoRenewNote',
        'paywall.oneTimeNote',
        'privacy.retry',
        'privacy.deletionAction',
      ]) {
        expect(typeof readKey(lng, key)).toBe('string');
        expect((readKey(lng, key) as string).trim().length).toBeGreaterThan(0);
      }
    }
  );

  it('自动续订声明必须写清"会自动续订 + 可取消"（3.1.2 两要素）', () => {
    const zh = readKey('zh', 'paywall.autoRenewNote') as string;
    expect(zh).toContain('自动续订');
    expect(zh).toContain('取消');
    const en = readKey('en', 'paywall.autoRenewNote') as string;
    expect(en.toLowerCase()).toMatch(/renews automatically/);
    expect(en.toLowerCase()).toMatch(/cancel/);
  });

  it('买断声明必须与订阅区分开（不会被读成第三档订阅）', () => {
    const zh = readKey('zh', 'paywall.oneTimeNote') as string;
    expect(zh).toContain('一次性');
    expect(zh).toContain('不会自动续订');
  });

  it('zh / en 注销确认：两行文案含"立即失效"与"24 小时内彻底删除"', () => {
    for (const lng of ['zh', 'en']) {
      const msg = readKey(lng, 'profile.deleteConfirmMessage') as string;
      expect(msg.split('\n')).toHaveLength(2);
      expect(msg).toContain('24');
    }
    expect(readKey('zh', 'profile.deleteConfirmMessage')).toContain('立即失效');
  });
});

describe('I1：入口与状态的源码守卫', () => {
  it('付费墙渲染两条计费声明（不是只躺在 i18n 里）', () => {
    const src = codeOnly('src/screens/PaywallScreen.tsx');
    expect(src).toContain("t('paywall.autoRenewNote')");
    expect(src).toContain("t('paywall.oneTimeNote')");
  });

  it('隐私页注销行可点：navigate 到 Tabs→Profile 并带 openDelete', () => {
    const src = codeOnly('src/screens/PrivacySecurityScreen.tsx');
    expect(src).toContain("navigation.navigate('Tabs'");
    expect(src).toMatch(/screen:\s*'Profile'/);
    expect(src).toMatch(/openDelete/);
    // 行本身必须是可点组件，不能退回 View
    expect(src).toMatch(/<TouchableOpacity[^>]*style=\{styles\.deletionRow\}/);
  });

  it('ProfileScreen 消费 openDelete 后立即清参数（防 Tab 常驻重复弹窗）', () => {
    const src = codeOnly('src/screens/ProfileScreen.tsx');
    expect(src).toMatch(/route\?\.params\?\.openDelete/);
    expect(src).toMatch(/setParams[\s\S]{0,40}openDelete:\s*undefined/);
  });

  it('consent 加载中/失败显示占位符，不再被渲染成「未同意」', () => {
    const src = codeOnly('src/screens/PrivacySecurityScreen.tsx');
    expect(src).toMatch(/type ConsentState = 'loading' \| 'unknown' \| 'agreed' \| 'declined'/);
    expect(src).toMatch(/consentState === 'loading'[\s\S]*—/);
    expect(src).toMatch(/consentState === 'unknown'/);
    // 拉取失败必须落到 unknown（可重试），不能当 declined 展示
    expect(src).toMatch(/catch[\s\S]{0,160}setConsentState\('unknown'\)/);
    // 旧写法：布尔三目直接把"没数据"当 declined
    expect(src).not.toMatch(/profile\?\.ai_consent \? t\('profile\.agreed'\) : t\('profile\.declined'\)/);
  });
});
