import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

// closenai.app 落地站：纯静态 SSG（CF Pages build output = site/dist）
// i18n：/ = en（默认，无前缀），/zh/ = 中文——Astro 原生 i18n 路由，
// 两个语言页面都是构建期静态 HTML，hreflang 由 sitemap 集成自动带出。
export default defineConfig({
  site: 'https://closenai.app',
  // 不改成 build.format='file'：站点现在是目录式（/zh/ ＝ zh/index.html），
  // 改格式会让已被分享过的 /zh/ 变成 404。法务页要的无扩展名形态由
  // public/_redirects 的 200 重写给出，不动整站 URL 结构。
  integrations: [
    sitemap({
      i18n: {
        defaultLocale: 'en',
        locales: { en: 'en', zh: 'zh' },
      },
    }),
  ],
  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh'],
    routing: {
      prefixDefaultLocale: false,
      redirectToDefaultLocale: false,
    },
  },
});
