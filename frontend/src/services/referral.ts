/**
 * 分享推荐码归因（P1-B04 · M4 分享卡体系 / 架构方案 §二 决策点 3）。
 *
 * 链路：别人扫分享卡上的二维码 → `https://closenai.app/r/{code}` →
 * 已装 App 时由 Universal Links 直接唤起（AASA 的 `/r/*` 规则），URL 落到这里。
 *
 * 存 AsyncStorage 而不是内存：唤起 → 冷启动 → 走到注册页之间可能跨进程，
 * 内存态必丢（与 firstRead 的 pending 暂存同一套理由）。
 *
 * ⚠️ 归因天花板（v1 已知断点，不靠小聪明绕）：**未安装**的用户从落地页跳 App Store
 * 装完再打开，链接参数过不了商店（Apple 不向第三方 App 透传任意 URL 参数），归因必断。
 * 想无损只能读剪贴板 handoff，而 App Store 对剪贴板读取的审核风险高、且需额外隐私说明，
 * 因此 v1 不做剪贴板：只有「已安装 → Universal Link 唤起」这条路径归因完整，
 * 「新装」路径的 ref 到不了注册请求（v1 也没有手工填码入口）。
 * 结论：referral 统计口径是**下界**，不能当分享总数看。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export const PENDING_REFERRAL_KEY = '@closenai/pending_referral';

/**
 * 与后端 `backend/core/referral.py` 同一字符集：数字去掉 0/1/8/9、字母去掉 I/L/O。
 * 两侧改动必须同步——这里放行而后端判非法，结果是静默丢掉归因，没人会报错。
 */
const REFERRAL_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ234567]{8}$/;

/** 分享深链的承载域（后端 settings.site_url 的同一值；API 域不承载分享路径）。 */
const SITE_HOST = 'closenai.app';

/**
 * 从唤起 URL 里取推荐码，两种形态都支持：
 * - Universal Links 路径段：`https://closenai.app/r/ABCD2345`
 * - 普通分享链接的查询参数：`https://closenai.app/?ref=ABCD2345`
 *
 * 只认品牌域：`Linking` 的 url 事件对任何能唤起本 App 的链接都会触发，
 * 若在任意域名下都蹭一次 `/r/xxxx`，别人就能强行把自己的码绑到这次安装上。
 * 返回大写码；域名不符、含易混字符或长度不对一律 null。
 */
export function parseReferralFromUrl(url?: string | null): string | null {
  if (!url || !isBrandUrl(url)) return null;
  const path = url.match(/\/r\/([A-Za-z0-9]+)/);
  const query = url.match(/[?&]ref=([A-Za-z0-9]+)/);
  const raw = (path?.[1] ?? query?.[1] ?? '').toUpperCase();
  return REFERRAL_CODE_RE.test(raw) ? raw : null;
}

/** 品牌域判定（决策点 3：分享链接统一走 closenai.app，API 域不承载分享路径）。 */
function isBrandUrl(url: string): boolean {
  const host = (url.match(/^[a-zA-Z][\w+.-]*:\/\/([^/?#]+)/)?.[1] ?? '').toLowerCase();
  return host === SITE_HOST || host.endsWith(`.${SITE_HOST}`);
}

/** 记下推荐码（幂等覆盖：后到的链接代表用户最新一次的来源）。 */
export async function savePendingReferral(code: string): Promise<void> {
  await AsyncStorage.setItem(PENDING_REFERRAL_KEY, code);
}

/** 读，不删——注册可能失败，失败时来源必须还在。 */
export async function peekPendingReferral(): Promise<string | null> {
  const code = await AsyncStorage.getItem(PENDING_REFERRAL_KEY);
  return code && REFERRAL_CODE_RE.test(code) ? code : null;
}

/** 注册请求发出后清掉（无论归因是否被后端接受，同一次安装只该归因一次）。 */
export async function clearPendingReferral(): Promise<void> {
  await AsyncStorage.removeItem(PENDING_REFERRAL_KEY);
}
