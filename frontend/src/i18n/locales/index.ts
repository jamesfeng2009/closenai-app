/**
 * 语言资源文件入口。
 *
 * 15 种语言，键结构与后端/前端文案保持一致。
 */
import en from './en';
import zh from './zh';
import es from './es';
import ko from './ko';
import ja from './ja';
import ru from './ru';
import id from './id';
import ms from './ms';
import th from './th';
import de from './de';
import fr from './fr';
import it from './it';
import pt from './pt';
import vi from './vi';
import km from './km';

const resources = {
  en: { translation: en },
  zh: { translation: zh },
  es: { translation: es },
  ko: { translation: ko },
  ja: { translation: ja },
  ru: { translation: ru },
  id: { translation: id },
  ms: { translation: ms },
  th: { translation: th },
  de: { translation: de },
  fr: { translation: fr },
  it: { translation: it },
  pt: { translation: pt },
  vi: { translation: vi },
  km: { translation: km },
};

export default resources;
