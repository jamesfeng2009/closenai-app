/**
 * 站点外链常量（G3）：全站唯一的「点了会去哪」定义处。
 *
 * 为什么要有这个文件：断链的两端各写各的常量，是这次隐私政策/条款打不开的成因——
 * App 侧 `frontend/src/config/links.ts` 早就指向 `closenai.app/privacy`，
 * 站点侧却没有那个页面，而页脚两条按钮写的是 `href="#"`。
 *
 * APP_STORE_URL 刻意**没有默认值**：上架前手上没有真的数字 ID，写死占位
 * （`id6740000000`）的表现不是「链接不好看」，而是分享出去的那条链接 404，
 * 且断在「已经发给别人」这一步，事后修不回来。所以未注入时消费方必须渲染成
 * 非链接状态，见 components/Landing.astro 的下载区。与 backend/config.py 的
 * `app_store_url`（同样 env-only、未配置回落站点）同一条口径。
 *
 * 读 process.env 而不写 `import.meta.env`：站点没装 `astro check`，构建期取值
 * 只需要 node 侧的环境变量，这里用一次显式取值把类型收窄，避免依赖 node 类型定义。
 */

function readEnv(name: string): string {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return (proc?.env?.[name] ?? '').trim();
}

/** App Store 真链；上架前由 Pages 构建期注入 `PUBLIC_APP_STORE_URL`，未注入即空串。 */
export const APP_STORE_URL = readEnv('PUBLIC_APP_STORE_URL');

/** 支持邮箱——与 App 内 `frontend/src/config/links.ts` 的 SUPPORT_EMAIL 同一个值（有闸盯着）。 */
export const SUPPORT_EMAIL = 'support@closenai.app';

/** 法务页路径：en 无前缀、zh 带 `/zh/` 段，与落地页的路由约定一致。 */
export function privacyPath(lang: 'en' | 'zh'): string {
  return lang === 'zh' ? '/zh/privacy' : '/privacy';
}

export function termsPath(lang: 'en' | 'zh'): string {
  return lang === 'zh' ? '/zh/terms' : '/terms';
}

export function homePath(lang: 'en' | 'zh'): string {
  return lang === 'zh' ? '/zh/' : '/';
}

export const MAILTO = `mailto:${SUPPORT_EMAIL}`;
