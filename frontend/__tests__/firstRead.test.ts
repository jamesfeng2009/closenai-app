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
