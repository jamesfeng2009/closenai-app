/**
 * I3 反馈与状态完备性守卫：pending / 超时 / 键盘 / 三态 / 引导。
 *
 * 口径：能被结构性断言钉死的用源码守卫（这些改动的失效方式都是「删掉一行
 * disabled / 把 error 分支合回空态」，源码守卫足够）；真正的异步编排语义
 * （冷启动超时与修正）在 coldStart.test.ts 里用假计时器单测。
 */
import fs from 'fs';
import path from 'path';
import resources from '../src/i18n/locales';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** 取 needle 前后若干行拼成片段（断言「这一段里有没有 X」）。 */
function snippet(rel: string, needle: string, before = 2, after = 8): string {
  const lines = read(rel).split('\n');
  const i = lines.findIndex((l) => l.includes(needle));
  expect(i).toBeGreaterThanOrEqual(0);
  return lines.slice(Math.max(0, i - before), i + after + 1).join('\n');
}

/**
 * 从 needle 那行切到函数体收尾（缩进两格的 `};`）。
 *
 * 为什么要有它：固定行窗的 `snippet` 断不了长函数的尾部——317b390 往 `onSaveDraft`
 * 的 try 里插了 13 行本地落库，`finally { setSavingId(null) }` 就掉出窗外，守卫报了
 * 一条「结束后没复位」的**假红**。函数体的收尾标记不会随中间长代码漂移。
 * 收尾找不到就红（而不是退回整份文件）：整份文件里没有那条复位也一样算得过，
 * 那条守卫就空转了。
 */
function fnBlock(rel: string, needle: string): string {
  const lines = read(rel).split('\n');
  const i = lines.findIndex((l) => l.includes(needle));
  expect(i).toBeGreaterThanOrEqual(0);
  const end = lines.findIndex((l, idx) => idx > i && /^ {2}};?\s*$/.test(l));
  expect(end).toBeGreaterThan(i);
  return lines.slice(i, end + 1).join('\n');
}

describe('复盘页：草稿保存有 pending 反馈且防连点', () => {
  const rel = 'src/screens/ReflectScreen.tsx';

  it('onSaveDraft 记录 savingId、结束后复位', () => {
    const block = fnBlock(rel, 'const onSaveDraft');
    expect(block).toContain('setSavingId(msg.id)');
    expect(block).toContain('setSavingId(null)');
    expect(block).toMatch(/finally\s*\{/);
    // 连点守卫在设置 savingId 之前
    expect(block).toMatch(/if \(savingId\) return;[\s\S]*setSavingId\(msg\.id\)/);
  });

  it('按钮在保存中 disabled 且文案切到「保存中…」', () => {
    const block = snippet(rel, 'styles.saveDraftBtn,', 2, 10);
    expect(block).toContain('disabled={savingId === msg.id}');
    expect(block).toContain("t('common.saving')");
  });

  it('既有键盘避让参数不动（keyboardVerticalOffset=90 是被验证过的）', () => {
    expect(read(rel)).toContain('keyboardVerticalOffset={90}');
  });
});

describe('冷启动门闸：App 用带超时的 runColdStart，不再裸等 Promise.all', () => {
  const rel = 'App.tsx';

  it('App.tsx 调用 runColdStart 并接上 ready 门闸', () => {
    const src = read(rel);
    expect(src).toContain("from './src/services/coldStart'");
    expect(src).toContain('runColdStart({');
    expect(src).toContain('setReady(true)');
    expect(src).toContain('coldStart.cancel()');
  });

  it('旧的「三者全齐才放行」写法不复现', () => {
    expect(read(rel)).not.toContain('const [, , stored] = await Promise.all([');
  });
});

describe('认证页：iOS 键盘避让', () => {
  it.each([
    ['src/screens/AuthScreen.tsx', '注册态 4 个输入框'],
    ['src/screens/ForgotPasswordScreen.tsx', '验证码 + 两次新密码'],
  ])('%s（%s）包 KeyboardAvoidingView，iOS 用 padding', (rel) => {
    const src = read(rel);
    expect(src).toContain('KeyboardAvoidingView');
    expect(src).toMatch(/behavior=\{Platform\.OS === 'ios' \? 'padding' : undefined\}/);
  });
});

describe('名片页：加载 / 失败 / 空 三态可分辨且失败能重试', () => {
  const rel = 'src/screens/ShareCardScreen.tsx';

  it('loading、error、空数据三条分支各自独立', () => {
    const block = snippet(rel, '{loading ?', 0, 12);
    expect(block).toMatch(/\{loading \? \(/);
    expect(block).toMatch(/\) : error \? \(/);
    expect(block).toMatch(/\) : !card \? \(/);
    expect(block).toContain("t('common.retry')");
    expect(block).toContain("t('share.empty')");
    // 旧写法：失败被并进 loading 分支（永远转圈）
    expect(read(rel)).not.toMatch(/loading \|\| !card \?/);
  });

  it('失败写进 error 而不是只弹 Alert', () => {
    const block = snippet(rel, 'setError(e instanceof ApiError', 6, 2);
    expect(block).toContain('setError(');
  });

  it('变体按钮在 loading 期间 disabled', () => {
    const block = snippet(rel, 'onPress={() => setVariant(v)}', 5, 1);
    expect(block).toContain('disabled={loading}');
  });
});

describe('六屏：聚焦刷新 + 失败态/空态分离', () => {
  it.each([
    'src/screens/MemoriesScreen.tsx',
    'src/screens/RelationshipTimelineScreen.tsx',
    'src/screens/PersonalityScreen.tsx',
    'src/screens/MessagesScreen.tsx',
  ])('%s 用 useFocusEffect 而非只 mount 加载', (rel) => {
    const src = read(rel);
    expect(src).toContain('useFocusEffect(');
    // mount-only 的 useEffect 加载器不应残留（Tab/栈页重复进入会拿到过期数据）
    expect(src).not.toContain('useEffect(');
  });

  it.each([
    'src/screens/MemoriesScreen.tsx',
    'src/screens/RelationshipTimelineScreen.tsx',
    'src/screens/PersonalityScreen.tsx',
  ])('%s：async 加载器外包一层 useCallback（useFocusEffect 要的是同步返回 cleanup 的回调）', (rel) => {
    expect(read(rel)).toMatch(/useFocusEffect\(\s*useCallback\(/);
  });

  it.each([
    ['src/screens/MemoriesScreen.tsx', 'memories.empty'],
    ['src/screens/RelationshipTimelineScreen.tsx', 'relationship.emptyTimeline'],
    ['src/screens/PersonalityScreen.tsx', 'common.loadFailed'],
    ['src/screens/ExploreScreen.tsx', 'common.loadFailed'],
    ['src/screens/InsightsScreen.tsx', 'insights.weeklyEmpty'],
    ['src/screens/MessagesScreen.tsx', 'messages.empty'],
  ])('%s 同时具备空态文案与失败重试入口（%s 在位）', (rel, emptyKey) => {
    const src = read(rel);
    expect(src).toContain(`t('${emptyKey}')`);
    expect(src).toContain("t('common.retry')");
    expect(src).toMatch(/common\.loadFailed|error|Failed|failed/);
  });

  it('没有把失败态写成统一组件（本次口径：各屏就地处理）', () => {
    expect(fs.existsSync(path.join(ROOT, 'src/components/ErrorState.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/components/LoadState.tsx'))).toBe(false);
  });

  /**
   * 失败分支必须「由失败标志门控」，而不是只保证重试 JSX 存在于文件里——
   * 后者在「把条件改成 false / 删掉条件」时仍然全绿（变异验证踩过三次）。
   * 这里从 common.retry 行向上回溯，要求那段渲染条件本身引用失败标志。
   */
  function retryGate(rel: string, window = 6): string {
    const lines = read(rel).split('\n');
    const i = lines.findIndex((l) => l.includes("t('common.retry')"));
    expect(i).toBeGreaterThanOrEqual(0);
    return lines.slice(Math.max(0, i - window), i + 1).join('\n');
  }

  const failureGates: [string, string][] = [
    ['src/screens/InsightsScreen.tsx', 'weeklyFailed'],
    ['src/screens/MessagesScreen.tsx', 'failed'],
    ['src/screens/RelationshipTimelineScreen.tsx', 'error'],
    ['src/screens/MemoriesScreen.tsx', 'error'],
    ['src/screens/PersonalityScreen.tsx', 'profileFailed'],
    ['src/screens/ExploreScreen.tsx', 'failed'],
  ];

  it.each(failureGates)('%s：重试入口挂在 %s 的门控条件里（不是无条件渲染）', (rel, flag) => {
    const gate = retryGate(rel);
    expect(gate).toContain(flag);
    expect(gate).toMatch(/[?&]{2}|\?\s*\(/);
  });

  it.each(failureGates.map(([rel]) => [rel]))('%s：失败门控没有被写成常量 false', (rel) => {
    const src = read(rel);
    expect(src).not.toMatch(/\{\s*false\s*[?&]/);
    expect(src).not.toMatch(/\)\s*:\s*false\s*\?/);
  });

  it.each(failureGates)('%s：%s 是可变状态（useState），不是常量门控', (rel, flag) => {
    const src = read(rel);
    const setter = `set${flag[0].toUpperCase()}${flag.slice(1)}`;
    expect(src).toMatch(new RegExp(`const \\[${flag}, ${setter}\\] = useState`));
    expect(src).toMatch(new RegExp(`${setter}\\(`));
  });
});

describe('引导页：进度点、可后退、提交有兜底', () => {
  const rel = 'src/screens/OnboardingScreen.tsx';
  const src = read(rel);

  it('三个 StepDots 各自标当前步', () => {
    ['step={1}', 'step={2}', 'step={3}'].forEach((needle) => {
      expect(src).toContain(needle);
    });
    expect(src).toMatch(/function StepDots/);
    expect(src).toContain('{ step: 1 | 2 | 3 }');
  });

  it('Step2/Step3 有返回入口，Step1 没有（它是第一屏）', () => {
    expect(src).toContain('setStep(1)} label={t(\'common.back\')}');
    expect(src).toContain('setStep(2)} label={t(\'common.back\')}');
    const backCount = (src.match(/<StepBack/g) || []).length;
    expect(backCount).toBe(2);
  });

  it('finish() 有 try/catch，失败弹提示且按钮恢复可点', () => {
    const block = snippet(rel, 'const finish = async', 1, 40);
    expect(block).toMatch(/\} catch \(e\) \{/);
    expect(block).toContain("t('onboarding.finishFailed')");
    expect(block).toContain('setFinishing(true)');
    expect(block).toContain('setFinishing(false)');
    expect(src).toContain('disabled={finishing}');
    expect(src).toContain("finishing ? t('common.saving')");
  });
});

describe('I3 新增文案：15 语言齐备', () => {
  const langs = Object.keys(resources);
  const keys: [string, string][] = [
    ['common', 'saving'],
    ['common', 'retry'],
    ['common', 'loadFailed'],
    ['common', 'back'],
    ['share', 'empty'],
    ['onboarding', 'finishFailed'],
  ];

  it('15 个语言都在册', () => {
    expect(langs).toHaveLength(15);
  });

  it.each(langs)('%s：六个新键全部存在且非空', (lng) => {
    keys.forEach(([block, key]) => {
      const v = (resources as any)[lng].translation[block]?.[key];
      expect(typeof v).toBe('string');
      expect((v || '').trim().length).toBeGreaterThan(0);
    });
  });

  it('中文 loadFailed 明确是「加载失败」而非空态话术', () => {
    expect((resources as any).zh.translation.common.loadFailed).toMatch(/加载失败/);
    expect((resources as any).zh.translation.onboarding.finishFailed).toMatch(/重试/);
  });
});
