/**
 * 反思提示库的前端纯逻辑（KG-26）。
 *
 * 单独成 `.ts`（而不是写进 `PromptLibrarySheet.tsx`）有两个理由，都是这个项目已经付过学费的：
 * 1. `.tsx` 在本机 jest 的 node 环境里渲染不了，逻辑留在组件里就等于没有测试；
 * 2. 上送后端的三个溯源字段名必须与 `backend/models/schemas.py` 的 `ReflectionCreate` 一致，
 *    名字在这里集中写一次，测试就能拿它跟 schema 对（两处各写各的＝漂移的起点）。
 */

export type PromptFunction = 'reflect' | 'mood' | 'goal';

export interface ReflectionPrompt {
  id: string;
  cluster: string;
  function: PromptFunction;
  title: string;
  question: string;
  why: string;
  follow_up: string;
}

export interface PromptClusterMeta {
  key: string;
  title: string;
  hint: string;
  count: number;
}

export interface PromptLibraryPayload {
  version: string;
  language_used: string;
  fallback: boolean;
  total: number;
  clusters: PromptClusterMeta[];
  prompts: ReflectionPrompt[];
}

/** 保存反思时附带的溯源；字段名即后端 `ReflectionCreate` 的字段名。 */
export interface PromptTraceability {
  prompt_id: string;
  prompt_version: string;
  source: 'prompt';
}

/** 库里的提示能不能直接填进输入框（正文被裁空的条目不值得让用户点）。 */
export function isPromptWritable(prompt: ReflectionPrompt | null | undefined): boolean {
  return !!prompt && prompt.question.trim().length > 0;
}

/**
 * 「存为反思」前对正文的判定：只含空白＝返回 null（调用方据此拦住，不发请求）。
 * 返回的是**裁过空白**的正文——后端把「只有空白」也当空内容拒（40001），
 * 这里先拦住，用户看到的是本地提示，而不是一个转圈的请求之后弹错。
 */
export function saveableText(raw: string): string | null {
  const text = raw.trim();
  return text.length > 0 ? text : null;
}

/**
 * 换一条：返回当前条目的下一条（到尾回顶）。
 *
 * 跳过正文为空的条目，且**不许返回自己**（除非整批只有一条能写）——
 * 「换一条」按下去还是同一段文字，用户会认为按钮坏了。
 */
export function nextPrompt(
  prompts: ReflectionPrompt[],
  currentId: string | null,
): ReflectionPrompt | null {
  const writable = prompts.filter(isPromptWritable);
  if (writable.length === 0) return null;
  if (writable.length === 1) return writable[0];
  const index = writable.findIndex((p) => p.id === currentId);
  const next = writable[(index + 1) % writable.length];
  return next.id === currentId ? writable[(index + 2) % writable.length] : next;
}

/**
 * 按簇分组，顺序跟随服务端给的 `clusters`（不是 prompts 的出现顺序）。
 *
 * 服务端切簇 tab 时 `clusters[].count` 始终按全库给，所以这里的 `count` 用它做徽章数字、
 * 用 `items.length` 做实际行数——两者不等（簇外残留 id）时把那一组单列出来，
 * 而不是把行丢掉：丢行会让「30 条」的徽章和屏幕上数到的条数对不上。
 */
export function groupByCluster(
  payload: Pick<PromptLibraryPayload, 'clusters' | 'prompts'>,
): { meta: PromptClusterMeta; items: ReflectionPrompt[] }[] {
  const buckets = new Map<string, ReflectionPrompt[]>();
  for (const prompt of payload.prompts) {
    const list = buckets.get(prompt.cluster);
    if (list) list.push(prompt);
    else buckets.set(prompt.cluster, [prompt]);
  }
  const known = new Set(payload.clusters.map((c) => c.key));
  const groups = payload.clusters.map((meta) => ({ meta, items: buckets.get(meta.key) ?? [] }));
  for (const key of [...buckets.keys()]) {
    if (!known.has(key)) {
      groups.push({ meta: { key, title: key, hint: '', count: buckets.get(key)!.length }, items: buckets.get(key)! });
    }
  }
  return groups;
}

/**
 * 语言回退提示（`fallback=true` ＝这批正文是英文，而设备语言不是 en/zh）。
 *
 * 只有 `fr`／`th` 这类设备语言才提示；`language_used` 恰好等于设备语言时不提示——
 * 服务端在 `language=en` 的请求上会给 `fallback=false`，但客户端不该赌它一定对。
 */
export function showsFallbackNotice(
  fallback: boolean,
  languageUsed: string,
  deviceLanguage: string,
): boolean {
  const device = (deviceLanguage || 'en').split('-')[0].toLowerCase();
  const used = (languageUsed || 'en').split('-')[0].toLowerCase();
  return fallback && used !== device;
}

/** 上送体里的三个溯源字段（成对出现；后端缺一个就 40001）。 */
export function traceabilityFor(promptId: string, version: string): PromptTraceability {
  return { prompt_id: promptId, prompt_version: version, source: 'prompt' };
}

/** `PromptTraceability` 的键名清单——测试拿它跟后端 schema 逐字对。 */
// 值用占位符而不是真 id：这条闸不许任何消费者硬编码 `rp-0xx`（见 test_prompt_library.py）。
export const TRACEABILITY_KEYS = Object.keys(traceabilityFor('probe', '0')) as (keyof PromptTraceability)[];
