/**
 * 渠道归因（KG-20 · UTM / App Store campaign link / 落地路径）。
 *
 * 这一份**只做设备侧的解析、清洗与留存**，不上送、不进事件属性：
 * - 决策 D6 写死了埋点属性白名单（`analytics.ts:19-42` 与后端 `ALLOWED_PROPERTY_KEYS` 同源），
 *   加一个 `utm_campaign` 就是两侧同改的契约变更，不属于本任务；
 * - 决策 D2 不新建表，而服务端今天**没有**任何安装来源列（渠道切片只能取
 *   `push_tokens.platform`），上送也没有落点；
 * - 商店侧的渠道读数另有权威口径：App Store Connect 的 campaign link 与 Play Console
 *   的来源类型，见 `docs/metrics-dictionary.md` 的「关键词渠道七格」第 2～3 格。
 *
 * 所以这里留存的唯一用途是：① 延迟深链场景下知道用户从**哪个支柱页**进来
 * （落地路径 + UTM 一起写进月度复盘的本地导出，属 KG-22）；② 本地分群诊断。
 * 推荐码不在这里解析——`referral.ts:42-48` 已经是唯一入口，两处解析必漂移。
 *
 * ⚠️ 归因天花板与 `referral.ts:10-15` 同源：未安装用户经 App Store 装完再打开，
 * URL 参数过不了商店，Apple 不向第三方 App 透传任意参数。因此本模块拿到的是**下界**，
 * 不许当成渠道全量，也不许用来反推某渠道的装机数。
 *
 * 隐私：所有值先解码再按字符集放行，命中 PII 形状（邮箱／长数字串／令牌状长串／
 * 任何嵌套 URL）一律整条丢弃。宁可少一条归因，不把用户邮箱当渠道名存进本机。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export const ATTRIBUTION_STORAGE_KEY = '@closenai/attribution';

/** 放行的 UTM 键；其余 `utm_*` 一律忽略，不做「未知参数也留着以后看」。 */
export const UTM_KEYS = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_term',
  'utm_content',
] as const;

/** App Store campaign link 的三个官方短令牌（pt/ct/mt），除此之外不认任何 campaign 变体。 */
export const CAMPAIGN_TOKEN_KEYS = ['pt', 'ct', 'mt'] as const;

export type UtmKey = (typeof UTM_KEYS)[number];
export type CampaignTokenKey = (typeof CAMPAIGN_TOKEN_KEYS)[number];

export interface Attribution {
  utm: Partial<Record<UtmKey, string>>;
  campaign: Partial<Record<CampaignTokenKey, string>>;
  /** 品牌域下的落地路径，不含查询串与片段；`/en/mood-tracker/` 归一成 `/en/mood-tracker`。 */
  landingPath: string | null;
  capturedAt: string;
}

export interface StoredAttribution {
  /** 首次触达：写入后不再覆盖，first-touch 口径靠它。 */
  first: Attribution;
  /** 最近一次触达：后到的链接代表用户最新来源，与 referral 的覆盖语义一致。 */
  last: Attribution;
  captures: number;
}

/**
 * 落地页承载域。与 `referral.ts:31` 同一条判据、同一个环境变量：
 * 只在品牌域下认归因，否则任何能唤起本 App 的链接都能给自己贴一个「付费渠道」。
 */
const SITE_HOST = (process.env.EXPO_PUBLIC_SITE_HOST || 'closenai.app').toLowerCase().trim();

/** 放行形状：小写字母数字开头，允许 `._+-`，长度 ≤48。中文、空格、`%`、`@`、`:` 全挡在外面。 */
const SAFE_VALUE_RE = /^[a-z0-9][a-z0-9._+-]{0,47}$/;

/** 落地路径形状：`/` 或 `/` 起头的小写路径，只含字母数字与 `/_-`，长度 ≤96，不以 `/` 结尾。
 *  不含点号，所以 `../../etc` 这类穿越串天然进不来。 */
const SAFE_PATH_RE = /^\/([a-z0-9][a-z0-9/_-]{0,95})?$/;

/**
 * PII 形状黑名单（命中即整条丢弃，不做「截断保留」——截断后的邮箱前缀仍然是个人信息）：
 * - 邮箱：`user@host`
 * - 电话/证件类长数字：连续 7 位以上数字，或 `1-2-4` 分段电话形状
 *   （代价写进规范：`20261101_sale` 这种带完整日期串的渠道名也会被挡，
 *   渠道命名请用 `nov26-sale`；宁可少一条归因，不猜那是不是手机号）
 * - 哈希/令牌状长串：连续 20 位以上的十六进制字符
 * - 嵌套 URL：任何地方出现 `://`
 * 姓名式两段大写开头交给字符集闸挡（只放行小写），这里不试图理解自然语言。
 */
const PII_PATTERNS: RegExp[] = [
  /[a-z0-9._+-]+@[a-z0-9.-]+\.[a-z]{2,}/i,
  /\d{7,}/,
  /\d{1,4}[-\s]\d{2,4}[-\s]\d{2,}/,
  /[0-9a-f]{20,}/i,
  /:\/\//,
];

/** 只在**解码后的原文**上判：带 padding 的 base64 段（`eyJhbGciOi...==`）大小写混排才认，
 *  小写归一之后与合法渠道名无法区分，所以这一条不能挪到归一化以后判。 */
const PII_ENCODED_PATTERNS: RegExp[] = [/[A-Za-z0-9+/]{16,}={1,2}/];

function isBrandHost(url: string): boolean {
  const host = (url.match(/^[a-zA-Z][\w+.-]*:\/\/([^/?#]+)/)?.[1] ?? '').toLowerCase();
  return host === SITE_HOST || host.endsWith(`.${SITE_HOST}`);
}

function looksLikePii(value: string): boolean {
  return PII_PATTERNS.some((pattern) => pattern.test(value));
}

/**
 * 单个值的清洗：先解百分号编码（`utm_term=mo%20od` 与 `mo+od` 都得落回同一渠道），
 * 再小写、再字符集闸、最后 PII 闸。任何一步不过关返回 null，调用方直接不收这个键。
 */
export function sanitizeUtmValue(raw: string | null | undefined): string | null {
  if (raw == null) return null;
  let value = String(raw);
  try {
    value = decodeURIComponent(value.replace(/\+/g, ' '));
  } catch {
    // 畸形的 % 转义（`%zz`、`%2`）本来就是脏输入，不猜它想表达什么
    return null;
  }
  if (PII_ENCODED_PATTERNS.some((pattern) => pattern.test(value))) return null;
  const normalized = value.trim().toLowerCase().replace(/\s+/g, '_');
  if (!SAFE_VALUE_RE.test(normalized)) return null;
  if (looksLikePii(normalized)) return null;
  return normalized;
}

/** 落地路径清洗：丢掉查询串与片段，只留品牌域下的规范化路径。 */
export function sanitizeLandingPath(url: string): string | null {
  const withoutQuery = url.replace(/[?#].*$/, '');
  const path = withoutQuery.replace(/^[a-zA-Z][\w+.-]*:\/\/[^/?#]+/, '') || '/';
  const trimmed = path.length > 1 ? path.replace(/\/+$/, '') : path;
  return SAFE_PATH_RE.test(trimmed) ? trimmed : null;
}

/**
 * 从唤起 URL 解析归因。非品牌域、参数全被判非法时返回 null——
 * 返回 null 意味着「这次没有归因」，不是「归因为空字符串」，两种状态在读数上不同。
 *
 * 落地路径只是**附带的来源上下文**：光有路径、没有任何 UTM 或 campaign 令牌，
 * 说明这次唤起什么都没带上，按「没有归因」处理，不写盘、不计数。
 */
export function parseAttribution(url?: string | null): Attribution | null {
  if (!url || !isBrandHost(url)) return null;

  const query = url.match(/[?#].*$/)?.[0] ?? '';
  const utm: Partial<Record<UtmKey, string>> = {};
  for (const key of UTM_KEYS) {
    const match = new RegExp(`[?&]${key}=([^&#]*)`).exec(query);
    const value = sanitizeUtmValue(match?.[1]);
    if (value) utm[key] = value;
  }

  const campaign: Partial<Record<CampaignTokenKey, string>> = {};
  for (const key of CAMPAIGN_TOKEN_KEYS) {
    const match = new RegExp(`[?&]${key}=([^&#]*)`).exec(query);
    const value = sanitizeUtmValue(match?.[1]);
    if (value) campaign[key] = value;
  }

  if (!Object.keys(utm).length && !Object.keys(campaign).length) return null;

  return {
    utm,
    campaign,
    landingPath: sanitizeLandingPath(url),
    capturedAt: new Date().toISOString(),
  };
}

function revalidate(snapshot: unknown): Attribution | null {
  if (!snapshot || typeof snapshot !== 'object') return null;
  const raw = snapshot as Partial<Attribution>;
  const utm: Partial<Record<UtmKey, string>> = {};
  for (const key of UTM_KEYS) {
    const value = sanitizeUtmValue((raw.utm as Record<string, string> | undefined)?.[key]);
    if (value) utm[key] = value;
  }
  const campaign: Partial<Record<CampaignTokenKey, string>> = {};
  for (const key of CAMPAIGN_TOKEN_KEYS) {
    const value = sanitizeUtmValue(
      (raw.campaign as Record<string, string> | undefined)?.[key],
    );
    if (value) campaign[key] = value;
  }
  if (!Object.keys(utm).length && !Object.keys(campaign).length) return null;
  const landingPath =
    typeof raw.landingPath === 'string' && SAFE_PATH_RE.test(raw.landingPath)
      ? raw.landingPath
      : null;
  const capturedAt =
    typeof raw.capturedAt === 'string' && !Number.isNaN(Date.parse(raw.capturedAt))
      ? new Date(raw.capturedAt).toISOString()
      : new Date().toISOString();
  return { utm, campaign, landingPath, capturedAt };
}

/** 读：落盘的值不直接信，再走一遍同一套清洗——本机数据可能被旧版本或手工改写。 */
export async function readAttribution(): Promise<StoredAttribution | null> {
  const text = await AsyncStorage.getItem(ATTRIBUTION_STORAGE_KEY);
  if (!text) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const raw = parsed as Partial<StoredAttribution>;
  const first = revalidate(raw.first);
  const last = revalidate(raw.last);
  if (!first && !last) return null;
  const base = first ?? last!;
  return {
    first: base,
    last: last ?? base,
    captures:
      typeof raw.captures === 'number' && Number.isInteger(raw.captures) && raw.captures >= 1
        ? raw.captures
        : 1,
  };
}

/**
 * 记一次唤起。首触不覆盖、最近一次覆盖；`captures` 只做「本机看到过几次唤起」，
 * 不是安装数、不是渠道量。
 */
export async function captureAttributionFromUrl(
  url?: string | null,
): Promise<StoredAttribution | null> {
  const fresh = parseAttribution(url);
  if (!fresh) return null;
  const existing = await readAttribution();
  const stored: StoredAttribution = existing
    ? {
        first: existing.first,
        last: fresh,
        captures: existing.captures + 1,
      }
    : { first: fresh, last: fresh, captures: 1 };
  await AsyncStorage.setItem(ATTRIBUTION_STORAGE_KEY, JSON.stringify(stored));
  return stored;
}

export async function clearAttribution(): Promise<void> {
  await AsyncStorage.removeItem(ATTRIBUTION_STORAGE_KEY);
}

/**
 * 平铺成 `键=值` 的诊断串，供月度复盘（KG-22）本地导出用。
 *
 * 刻意只输出**清洗过的**值，且不按用户排序、不含时间戳以外的用户信息；
 * 这里出现过的键不许当成事件属性上送（见文件头 D6 段）。
 */
export function attributionForDiagnostics(stored: StoredAttribution | null): string[] {
  if (!stored) return [];
  const lines: string[] = [];
  for (const [key, value] of Object.entries(stored.first.utm)) {
    lines.push(`first.${key}=${value}`);
  }
  for (const [key, value] of Object.entries(stored.first.campaign)) {
    lines.push(`first.campaign_${key}=${value}`);
  }
  if (stored.first.landingPath) lines.push(`first.landing_path=${stored.first.landingPath}`);
  lines.push(`first.captured_at=${stored.first.capturedAt}`);
  for (const key of UTM_KEYS) {
    const value = stored.last.utm[key];
    if (value && value !== stored.first.utm[key]) lines.push(`last.${key}=${value}`);
  }
  if (stored.last.landingPath && stored.last.landingPath !== stored.first.landingPath) {
    lines.push(`last.landing_path=${stored.last.landingPath}`);
  }
  lines.push(`captures=${stored.captures}`);
  return lines;
}
