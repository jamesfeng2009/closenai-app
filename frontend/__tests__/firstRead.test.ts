/**
 * B1 性格初读卡前端契约测试。
 *
 * 1. 本地暂存helpers（游客态 → 登录补写链路依赖 AsyncStorage）
 * 2. 15 语言 i18n 键齐全（OnboardingScreen Step 3 与 FirstReadCardScreen 全靠这些键）
 */
import resources from '../src/i18n/locales';

jest.mock('@react-native-async-storage/async-storage', () => {
  const store = new Map<string, string>();
  return {
    __esModule: true,
    default: {
      getItem: jest.fn(async (key: string) => store.get(key) ?? null),
      setItem: jest.fn(async (key: string, value: string) => { store.set(key, value); }),
      removeItem: jest.fn(async (key: string) => { store.delete(key); }),
    },
  };
});

import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  clearPendingFirstRead, FIRST_READ_CACHE_KEY, isFirstReadUnseen, loadFirstReadCard,
  markFirstReadSeen, PENDING_FIRST_READ_KEY, peekPendingFirstRead, saveFirstReadCard,
  savePendingFirstRead,
} from '../src/services/firstRead';
import type { FirstReadCard } from '../src/types';

const CARD: FirstReadCard = {
  assertions: ['你倾向于先憋着。', '深夜是你思路最清楚的时间。'],
  summary: '你把自己归为 INTJ：先把事情想透再开口。',
  closer: '30 天后，你会收到第一份《每月故事》。',
  persona_type: 'INTJ',
  language: 'zh',
  share_url: 'https://apps.apple.com/app/id0000000000',
};

describe('firstRead 本地暂存（游客态 → 登录补写）', () => {
  it('savePendingFirstRead 写入答案与 persona_type', async () => {
    await savePendingFirstRead([{ q: 'conflict', option: 'hold_in' }], 'INTJ');
    const peeked = await peekPendingFirstRead();

    expect(peeked).toEqual({
      answers: [{ q: 'conflict', option: 'hold_in' }],
      persona_type: 'INTJ',
    });
  });

  it('peek 不删，clear 才删（POST 失败时暂存必须保留）', async () => {
    await savePendingFirstRead([{ q: 'night', option: 'replay' }], null);
    await peekPendingFirstRead();
    expect(AsyncStorage.removeItem).not.toHaveBeenCalledWith(PENDING_FIRST_READ_KEY);

    await clearPendingFirstRead();
    expect(await peekPendingFirstRead()).toBeNull();
  });

  it('无暂存时返回 null（未走引导的老用户不触发 POST）', async () => {
    await clearPendingFirstRead();
    expect(await peekPendingFirstRead()).toBeNull();
  });

  it('卡缓存写入后可读出，断言缺失的脏数据按 null 处理', async () => {
    await saveFirstReadCard(CARD);
    expect(await loadFirstReadCard()).toEqual(CARD);

    await AsyncStorage.setItem(FIRST_READ_CACHE_KEY, '{"summary":"x"}');
    expect(await loadFirstReadCard()).toBeNull();
  });

  it('已读标记决定首页入口是否展示', async () => {
    expect(await isFirstReadUnseen()).toBe(true);
    await markFirstReadSeen();
    expect(await isFirstReadUnseen()).toBe(false);
  });
});

describe('初读卡 i18n（15 语言键齐全）', () => {
  const ONBOARDING_KEYS = [
    'step3Title', 'step3Subtitle', 'step3Confirm',
    'q1Title', 'q1Opt1', 'q1Opt2', 'q1Opt3',
    'q2Title', 'q2Opt1', 'q2Opt2', 'q2Opt3',
    'q3Title', 'q3Opt1', 'q3Opt2', 'q3Opt3',
  ];
  const FIRST_READ_KEYS = [
    'title', 'subtitle', 'cardTitle', 'cta', 'share',
    'homeEntryTitle', 'homeEntry', 'empty', 'loadFailed', 'shareFailed',
  ];
  const locales = Object.keys(resources);
  type Blocks = Record<string, Record<string, string>>;
  const blocksOf = (locale: string): Blocks =>
    (resources as unknown as Record<string, { translation: Blocks }>)[locale].translation;

  it('locales 共 15 种', () => {
    expect(locales).toHaveLength(15);
  });

  it.each(locales)('%s：onboarding 快问键齐全且非空', (locale) => {
    const block = blocksOf(locale).onboarding;
    for (const key of ONBOARDING_KEYS) {
      expect(typeof block[key]).toBe('string');
      expect(block[key].trim().length).toBeGreaterThan(0);
    }
  });

  it.each(locales)('%s：firstRead 段键齐全且非空', (locale) => {
    const block = blocksOf(locale).firstRead;
    expect(Object.keys(block).sort()).toEqual([...FIRST_READ_KEYS].sort());
    for (const key of FIRST_READ_KEYS) {
      expect(block[key].trim().length).toBeGreaterThan(0);
    }
  });
});

/**
 * 登录补调链路（tasks.md:96 登记的游客态约束，2026-10-14 收口）。
 *
 * .tsx/Context 在 node 环境跑不起来（react-native 顶层 import），按本仓既有口径做源码闸：
 * 顺序、fire-and-forget、失败保留这三件事一旦被改坏都必须变红。
 */
describe('登录补调链路（AuthContext 源码闸）', () => {
  const fs = require('fs');
  const path = require('path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'context', 'AuthContext.tsx'),
    'utf8'
  ) as string;

  const body = src.slice(
    src.indexOf('const flushPendingOnboarding'),
    src.indexOf('const login = useCallback')
  );

  it('补调链在位且顺序是 取暂存 → POST 生成 → 落缓存 → 清暂存', () => {
    expect(body.trim().length).toBeGreaterThan(200); // 防切片空跑
    const order = [
      'await peekPendingFirstRead()',
      'await postFirstRead(',
      'await saveFirstReadCard(card)',
      'await clearPendingFirstRead()',
    ];
    const at = order.map((s) => body.indexOf(s));
    for (const idx of at) {
      expect(idx).toBeGreaterThan(-1);
    }
    expect(at).toEqual([...at].sort((a, b) => a - b));
  });

  it('清暂存排在 POST 之后：POST 抛错时暂存保留，下次登录还能重试', () => {
    const post = body.indexOf('await postFirstRead(');
    const clear = body.indexOf('await clearPendingFirstRead()');
    expect(clear).toBeGreaterThan(post);
    // catch 块里不许出现 clear/POST 之外的写操作（失败路径必须什么都不清）
    const catchBlock = body.slice(body.indexOf('} catch {', post), body.indexOf('}, []);'));
    expect(catchBlock).not.toContain('clearPendingFirstRead');
  });

  it('无暂存直接返回：未走引导的老用户不会被打一次空 POST', () => {
    expect(body).toContain('if (!pending) return;');
  });

  it('登录与冷启动都挂补调，且是 fire-and-forget（不阻断登录主流程）', () => {
    // 逐点定位：只写 toContain('void …') 会被冷启动那一处顶包（F27 变异就是这么溜过去的）
    const loginBody = src.slice(
      src.indexOf('const login = useCallback'),
      src.indexOf('}, [flushPendingOnboarding]);')
    );
    expect(loginBody.trim().length).toBeGreaterThan(200); // 防切片空跑
    expect(loginBody).toContain('void flushPendingOnboarding();');
    expect(loginBody).not.toMatch(/^\s*flushPendingOnboarding\(\);/m);
    expect(src).toContain('if (user) void flushPendingOnboarding();');
    expect(src).not.toContain('await flushPendingOnboarding()');
  });
});
