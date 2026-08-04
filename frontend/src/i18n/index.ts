/**
 * i18n 初始化。
 *
 * - 支持 15 种语言（见 constants 与 locales/）
 * - 初始语言跟随系统语言（expo-localization），可手动切换并持久化
 * - 语言码与后端 users.language 字段保持一致
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import * as Localization from 'expo-localization';
import AsyncStorage from '@react-native-async-storage/async-storage';

import resources from './locales';

// 15 种支持语言（与后端 supported_languages 一致）
export const SUPPORTED_LANGUAGES = [
  'en', 'zh', 'es', 'ko', 'ja', 'ru',
  'id', 'ms', 'th', 'de', 'fr', 'it', 'pt',
  'vi', 'km',
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number];

const STORAGE_KEY = '@closenai/language';

// 系统语言映射到支持列表（取最接近的）
function detectLanguage(): string {
  const locales = Localization.getLocales?.() ?? [];
  const systemLang = locales[0]?.languageCode ?? 'en';
  // 中文：zh / zh-CN / zh-Hans → zh
  if (systemLang.startsWith('zh')) return 'zh';
  if (SUPPORTED_LANGUAGES.includes(systemLang as LanguageCode)) {
    return systemLang;
  }
  return 'en';
}

// 初始化（异步加载持久化语言）
export async function initI18n(): Promise<void> {
  let saved: string | null = null;
  try {
    saved = await AsyncStorage.getItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  const lng = saved || detectLanguage();

  await i18n.use(initReactI18next).init({
    resources,
    lng,
    fallbackLng: 'en',
    supportedLngs: [...SUPPORTED_LANGUAGES],
    interpolation: { escapeValue: false },
    compatibilityJSON: 'v4',
  });
}

// 切换语言并持久化
export async function setAppLanguage(code: string): Promise<void> {
  await i18n.changeLanguage(code);
  try {
    await AsyncStorage.setItem(STORAGE_KEY, code);
  } catch {
    // ignore
  }
}

export default i18n;