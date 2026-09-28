/**
 * 初读卡本地暂存（P1-B01 · 架构方案 §二 M1）。
 *
 * 引导阶段必为游客态：无 token 调 `/onboarding/first-read` 必失败（同 pending_mbti 的既有约定），
 * 所以快问答案先落本地，登录/注册成功后由 AuthContext 补调接口生成卡片。
 * 生成结果另存一份本地缓存：FirstReadCardScreen 首帧直出、HomeScreen 决定入口是否展示。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { FirstReadAnswer, FirstReadCard } from '../types';

export const PENDING_FIRST_READ_KEY = '@closenai/pending_first_read';
export const FIRST_READ_CACHE_KEY = '@closenai/first_read_card';
export const FIRST_READ_SEEN_KEY = '@closenai/first_read_seen';

export interface PendingFirstRead {
  answers: FirstReadAnswer[];
  persona_type: string | null;
}

export async function savePendingFirstRead(
  answers: FirstReadAnswer[],
  personaType: string | null,
): Promise<void> {
  try {
    await AsyncStorage.setItem(
      PENDING_FIRST_READ_KEY,
      JSON.stringify({ answers, persona_type: personaType }),
    );
  } catch {
    // ignore
  }
}

/** 只读取暂存（不删）：POST 成功后由调用方 clear，失败保留待下次登录重试。 */
export async function peekPendingFirstRead(): Promise<PendingFirstRead | null> {
  try {
    const raw = await AsyncStorage.getItem(PENDING_FIRST_READ_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingFirstRead;
    return { answers: parsed.answers ?? [], persona_type: parsed.persona_type ?? null };
  } catch {
    return null;
  }
}

export async function clearPendingFirstRead(): Promise<void> {
  try {
    await AsyncStorage.removeItem(PENDING_FIRST_READ_KEY);
  } catch {
    // ignore
  }
}

export async function saveFirstReadCard(card: FirstReadCard): Promise<void> {
  try {
    await AsyncStorage.setItem(FIRST_READ_CACHE_KEY, JSON.stringify(card));
  } catch {
    // ignore
  }
}

export async function loadFirstReadCard(): Promise<FirstReadCard | null> {
  try {
    const raw = await AsyncStorage.getItem(FIRST_READ_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as FirstReadCard;
    return Array.isArray(parsed.assertions) ? parsed : null;
  } catch {
    return null;
  }
}

/** 用户是否已打开过初读卡（HomeScreen 入口只在未读时展示）。 */
export async function isFirstReadUnseen(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(FIRST_READ_SEEN_KEY)) !== '1';
  } catch {
    return false;
  }
}

export async function markFirstReadSeen(): Promise<void> {
  try {
    await AsyncStorage.setItem(FIRST_READ_SEEN_KEY, '1');
  } catch {
    // ignore
  }
}
