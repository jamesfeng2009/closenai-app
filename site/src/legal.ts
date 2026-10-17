/**
 * 法务页正文（G3 · 隐私政策 + 服务条款，中英双份）。
 *
 * 写作红线（这批唯一真正要紧的约束）：**只写代码里指得回的采集事实**。
 * 一句没实现的承诺（「我们绝不保留任何日志」「数据端到端加密」）在这里比在
 * 落地页更贵：提审时 Apple 与用户都会把它当成可核对的陈述。所以每一条都能落到
 * 下面这些出处，改动正文时请同步核对：
 *
 *   账号标识与口令        backend/models/user.py:78-96（email / username / apple_id /
 *                        argon2 password_hash，明文不存）、backend/routers/auth.py:50-174
 *   你写下的内容          backend/models/{chat,mood,memory,personality,growth,journey,share}.py、
 *                        PURGE_TARGETS（backend/services/user_service.py）
 *   记忆带向量            backend/models/memory.py:36（HALFVEC(512)）
 *   设置与偏好            backend/models/user.py:26-41（语言/时区/push_time/push_frequency/
 *                        push_enabled 默认关/conversation_style/mbti_type/enneagram_type）
 *   推送令牌              backend/models/push.py:14-27（platform + device_token + timezone）
 *   行为元数据白名单      backend/services/analytics_service.py:33-56、
 *                        frontend/src/services/analytics.ts:17-38
 *   AI 调用只记元数据      backend/models/generation_run.py:29-56（provider/model/字符数/token/延迟）
 *   危机转介摘录          backend/models/journey.py:129-160（kind/matched_terms/hotline/
 *                        excerpt ≤280 字符/24h 人工抽检）
 *   崩溃上报脱敏          frontend/src/services/monitoring.ts:22-60
 *   服务商                backend/config.py:33-34（Supabase）、:42-44（Resend）、
 *                        :49-74（LLM provider）、:103-113（Upstash Redis + QStash）、
 *                        :117-120（APNs）、:186-191（PostHog Cloud EU）、
 *                        frontend/src/services/monitoring.ts（Sentry）、site/public/_headers（Cloudflare）
 *   删除覆盖与举证        backend/services/user_service.py purge_user / purge_deleted_users、
 *                        backend/routers/user.py:117-131、backend/routers/cron.py:281-300
 *   令牌与会话            backend/config.py:37-40（HS256、access 30 分钟、refresh 30 天）、
 *                        backend/models/auth.py:14-36（refresh 只存哈希、轮换 revoked_at）、
 *                        frontend/src/services/api.ts:49-114（SecureStore ＝ iOS 钥匙串）
 *   权益与配额            backend/core/errors.py:55（40304 买断或订阅月度免费额度）、
 *                        backend/config.py:165-175（免费档每日额度与画像刷新上限）
 *
 * 中英两份按**同序同数**组织（闸门 backend/tests/test_legal_pages.py 逐节比长度），
 * 只改一边会当场打红——这类页面最坏的情况是两种语言说的不是同一件事。
 */
import type { Lang } from './i18n';

export const LEGAL_KINDS = ['privacy', 'terms'] as const;
export type LegalKind = (typeof LEGAL_KINDS)[number];

export interface LegalSection {
  h: string;
  ps?: string[];
  ul?: string[];
}

export interface LegalDoc {
  title: string;
  description: string;
  intro: string;
  sections: LegalSection[];
}

/** 本页生效日期：改动正文时一并更新，App 内引用的是同一 URL。 */
export const LEGAL_UPDATED = '2026-10-17';

const EN: Record<LegalKind, LegalDoc> = {
  privacy: {
    title: 'Privacy Policy — ClosenAI',
    description:
      'What ClosenAI collects about you, who processes it, how you turn the optional parts off, and what happens to your data when you delete your account.',
    intro:
      'ClosenAI keeps what you tell it — conversations, check-ins, memories about you. That is the whole point of the product, and it is also the reason this page exists. Below is exactly what we store, what we send to other services, and which switches are yours.',
    sections: [
      {
        h: 'Who we are, and how to reach us',
        ps: [
          'ClosenAI is the iOS app at closenai.app. Questions about your data — including a request to confirm what we hold or to delete it — go to support@closenai.app.',
        ],
      },
      {
        h: 'Account identifiers you give us',
        ps: [
          'You sign in with an email address, a username plus password, or Sign in with Apple. Depending on the method we store the email, the username, or the Apple account identifier, plus the display name you choose.',
          'Passwords are stored only as an argon2 hash. We never keep the plain text. Sign-in and reset codes are stored hashed, expire quickly, and are invalidated after repeated wrong attempts.',
        ],
      },
      {
        h: 'What you write',
        ul: [
          'Conversation messages with the companion.',
          'Mood check-ins (the mood you pick, and the note if you add one).',
          'Memories: the text itself, its category, confidence and version history, and a numeric vector used to find related memories.',
          'Reflections, bedtime reviews, personality profiles and assessments, growth records, milestones, and the first-week plan events.',
          'The exports you generate: share cards and archive/report PDFs.',
        ],
        ps: [
          'This is the core of the product. Everything here is content you typed, and it is stored so the app can remember and reflect it back to you.',
        ],
      },
      {
        h: 'Settings and preferences',
        ps: [
          'Language, timezone, the daily hour you prefer to be reached, notification frequency, conversation style, and any MBTI or Enneagram type you enter yourself.',
          'Proactive notifications are off by default; nothing is pushed to you until you turn them on.',
        ],
      },
      {
        h: 'Device and delivery tokens',
        ps: [
          'If you enable notifications, we store the push token your platform issues (Apple APNs, or Google FCM where applicable) together with the device platform and timezone. Tokens are per device, and signing out clears them so a logged-out phone stops receiving messages.',
          'For rate limiting we read the client IP as reported by the proxy in front of the service. It is used to blunt abuse, not to profile you.',
        ],
      },
      {
        h: 'Behaviour metadata — only if you say yes',
        ps: [
          'A separate consent switch governs product analytics. Without it, the events are dropped.',
          'With it, we keep behaviour metadata only: screen names, section, feature, action, trigger, outcome, durations, counts, your plan tier, language, platform, app version, streak and relationship level. Both the app and the server re-check this whitelist, so a text field — a reflection, a memory, a message, a mood value — cannot ride along even if a bug puts it there.',
          'These events are tied to your account id so that deleting your account can also delete them; they are not tied to your name or email, and they do not contain your content.',
        ],
      },
      {
        h: 'Metadata about AI generation',
        ps: [
          'For cost and reliability we record each model call: which provider and model, purpose, language, status, fallback reason, error type, input and output character counts, token counts and latency. The prompt and the response text are not written into that record.',
        ],
      },
      {
        h: 'Crisis and safety records',
        ps: [
          'When your own words match a crisis pattern (self-harm, or relationship violence), the app shows a helpline for your region and language, and we keep a review record: the category, where the match came from, the matched trigger words, the helpline shown — and a truncated excerpt of up to 280 characters so a human can spot-check false positives within 24 hours.',
          'That excerpt exists to catch mistakes in the detector. It is not exported into anything you see, and it is deleted along with your account. Matching a keyword also pauses promotional and upgrade-type messages to you.',
        ],
      },
      {
        h: 'Purchases',
        ps: [
          'Payments are handled by the Apple App Store. We receive the transaction receipt and its environment, product identifier and dates so we can grant entitlements; card numbers and billing addresses never reach us.',
        ],
      },
      {
        h: 'Crash reports',
        ps: [
          'If a crash monitor is configured for the build, we deliberately bind it to nothing: no user identity is attached, console breadcrumbs are dropped entirely, request and response bodies and URL query strings are stripped from network breadcrumbs, and extra context is cleared. A crash report should say which screen broke, not what you wrote on it.',
          'The monitor is disabled unless a project key is configured for that build.',
        ],
      },
      {
        h: 'Who else processes your data',
        ul: [
          'Model providers — your conversation and content text is sent to the large-language-model service configured for the deployment (Qwen through Aliyun DashScope by default; Doubao, Gemini or any OpenAI-compatible endpoint can be configured instead). This is how replies, memories, profiles and reports get generated.',
          'Supabase — the Postgres database where your content lives.',
          'Upstash — Redis (verification codes, rate limits) and QStash (delayed jobs such as account deletion).',
          'Resend — transactional email for sign-in and password-reset codes.',
          'Apple APNs and Google FCM — delivery of notifications you enabled.',
          'PostHog Cloud (EU region) — product analytics, behind the consent switch above.',
          'Sentry — crash monitoring, when configured for the build.',
          'Apple App Store — payment processing and purchase history.',
          'Cloudflare — hosting and delivery of the closenai.app website.',
        ],
        ps: [
          'We use these services to run the product. We do not sell your data, and we do not put it in advertising audiences.',
        ],
      },
      {
        h: 'Where it is processed',
        ps: [
          'Our providers operate data centres in different countries, so your data may be processed outside the country you live in — analytics is hosted in the EU, while the database and model services may sit elsewhere. The exact set is a deployment configuration, and we keep this page aligned with what production actually uses.',
        ],
      },
      {
        h: 'How long we keep it, and what deletion does',
        ps: [
          'You keep data as long as your account exists. Deleting the account from inside the app works in two steps: the account is marked deleted immediately — it can no longer sign in, session tokens are revoked, push tokens are cleared — and the physical delete then runs asynchronously, with retries and a periodic sweep so a failed first attempt still finishes.',
          'The physical delete removes your conversations, check-ins, memories, profiles and assessments, growth records, model-call records, milestones, push tokens, share exports, receipts, subscriptions and entitlements, plan events, onboarding answers, safety records, derived insights and their evidence, feedback, and the pending message queue — then asks the analytics platform to erase what it holds under your id.',
          'Deletion is not a deactivation, and we do not keep a list of who deleted: the proof we rely on is an internal log line recording the deletion time and the number of rows removed per table. A single memory can also be deleted on its own, without deleting the account.',
          'Analytics events, once consented and sent, are erased with the account. If the erase call fails, the failure is logged and retried by the sweep; it never blocks the deletion you asked for.',
        ],
      },
      {
        h: 'Your controls',
        ul: [
          'Turn AI features on or off: the AI data-sharing consent is explicit, and features that need it refuse to run without it.',
          'Turn analytics on or off, independently.',
          'Turn notifications off entirely, or choose the frequency.',
          'Delete one memory, export reports and cards as PDF, or delete the whole account.',
          'Write to support@closenai.app for anything the app does not cover.',
        ],
      },
      {
        h: 'Security',
        ps: [
          'Traffic between the app and our servers uses HTTPS through the same proxy as the website. Sessions use short-lived access tokens (30 minutes) and rotating refresh tokens (30 days) that are stored only as hashes server-side and revoked on sign-out. On your phone, tokens live in the iOS Keychain, not in a web cookie jar or a plain file.',
          'What we are not claiming: your content is stored as readable rows in the database, protected by access controls, not end-to-end encrypted. We do not have a mechanism that would let us read only your memory titles without also being able to read their text, and we would rather say that plainly than imply otherwise.',
        ],
      },
      {
        h: 'The website itself',
        ps: [
          'The pages at closenai.app — including the landing page a shared card opens — set no cookies and load no third-party scripts. The app does the same on its side: no analytics SDK in the app, and the app never talks to the analytics provider directly; events go through our backend.',
        ],
      },
      {
        h: 'Children',
        ps: [
          'ClosenAI is built for adults doing self-reflection, and the app is not intended for children. We do not knowingly collect data from children; if that happens, write to us and we will delete the account.',
        ],
      },
      {
        h: 'Changes to this policy',
        ps: [
          'This page states the current date at the bottom. When the app starts collecting something new, this page is updated before that collection ships — the links in the app point here, so there is only one version to read.',
        ],
      },
    ],
  },
  terms: {
    title: 'Terms of Service — ClosenAI',
    description:
      'The agreement between you and ClosenAI: what the app is for, who may use it, what you keep ownership of, how plans and purchases work, and what we disclaim.',
    intro:
      'These terms are the agreement between you and ClosenAI for using the app at closenai.app. Please read the section on what the app is not — it is the part that matters most for a product that talks back about your own life.',
    sections: [
      {
        h: 'Accepting these terms',
        ps: [
          'By creating an account or using the app, you agree to these terms and to the privacy policy, which explains what we collect. If you do not agree, please do not use the app.',
        ],
      },
      {
        h: 'Who may use it',
        ps: [
          'You must be old enough to consent under the law where you live. The app is not designed for children.',
          'You must provide accurate account information, and you are responsible for keeping your password and device secure. If you think someone else can reach your sign-in, tell us.',
        ],
      },
      {
        h: 'What ClosenAI is — and what it is not',
        ps: [
          'ClosenAI is a companion and a journal that talks back: it stores what you tell it, retrieves it later, and shows you patterns over time.',
          'It is not therapy, counselling, or medical, psychological, legal or financial advice, and it is not a crisis service. If your own words indicate you may be in danger, the app surfaces a helpline for your region instead of carrying on as usual — but that is a signpost, not treatment, and it cannot replace emergency services. If you are in immediate danger, contact your local emergency number.',
          'Generated text is produced by a model. It can be wrong, it can misremember emphasis, and it can be confidently off. Treat it as a reflection of your entries, not as a record of fact.',
        ],
      },
      {
        h: 'Your account',
        ps: [
          'You can register with an email address, a username and password, or Sign in with Apple. One email or one Apple account corresponds to one account.',
          'Your account is personal to you — do not sell, transfer or share access to it.',
        ],
      },
      {
        h: 'Your content stays yours',
        ps: [
          'You own what you write. Nothing in these terms transfers that to us.',
          'You give us a limited licence to store, process and display your content for the purpose of running the features you asked for — memory retrieval, reports, your own profile — and to comply with the law when we are required to. We do not licence your content to advertisers.',
          'You are responsible for the content you record, including that it does not violate anyone else\'s rights.',
        ],
      },
      {
        h: 'Things we ask you not to do',
        ul: [
          'Use the app unlawfully, or to plan or incite harm to yourself or others.',
          'Probe, disable or overload the service, or bypass its quotas, payment checks or safety limits.',
          'Resell, sub-license or run the service for others without our agreement.',
          'Impersonate another person, or register accounts that are not yours.',
        ],
      },
      {
        h: 'Shared cards are public to whoever has the link',
        ps: [
          'A share card you export, and the landing page at closenai.app/r/ behind it, are readable by anyone you send them to. Sharing is your choice; treat a card as a document you handed to someone. Anyone tapping the link with the app installed is taken straight into it.',
        ],
      },
      {
        h: 'Free tier, Pro and one-time purchases',
        ps: [
          'The free tier includes daily conversation and check-in quotas and a limited number of personality-profile refreshes; exact limits are shown in the app and may change.',
          'Pro and Pro+ raise those limits. The Life Archive is a one-time purchase, and exporting its PDF requires either that purchase or the monthly allowance that comes with an active subscription — otherwise the app tells you so instead of charging you silently.',
          'Changing a quota or a price is a product change; we do not charge you for something you did not buy, and existing entitlements you already paid for are not taken away.',
        ],
      },
      {
        h: 'Payment, renewal and refund',
        ps: [
          'Purchases are made through the Apple App Store and billed by Apple. Subscriptions renew automatically until cancelled, and cancellation is done in your Apple account settings — that also stops the next charge.',
          'Refunds and charge disputes are handled by Apple under their policies. Manage your purchase history in the App Store.',
        ],
      },
      {
        h: 'Availability, changes and the notifications you opt into',
        ps: [
          'The app requires a supported iOS version and an internet connection. Features are added, changed and sometimes removed; some parts are offered only to a subset of users or regions.',
          'Proactive messages are off until you turn them on, and you can change the frequency or turn them off again at any time. We keep volume limited by design: milestone and care-type messages are capped per month.',
          'We do not promise uninterrupted service. If the app is down, nothing about your data is lost — and if it stays down, we will say so rather than keep charging.',
        ],
      },
      {
        h: 'Third-party services',
        ps: [
          'The app depends on services run by other companies — the model provider that generates text, the database and job infrastructure behind it, notification delivery, and the App Store. Their own terms and outages are part of using ClosenAI, and the privacy policy lists who does what.',
        ],
      },
      {
        h: 'Disclaimers',
        ps: [
          'To the fullest extent the law allows, the app is provided "as is", without warranties that it is uninterrupted, error-free, or that anything it says about you is accurate.',
          'Some jurisdictions do not allow certain disclaimers, so those may not apply to you.',
        ],
      },
      {
        h: 'Liability',
        ps: [
          'To the extent permitted by law, our liability for the app is limited, and we are not liable for indirect or consequential losses, including losses arising from text generated by a model.',
          'This does not exclude or limit liability that cannot be excluded, including for death or personal injury caused by negligence or for wilful misconduct, and it does not affect mandatory consumer rights where you live.',
        ],
      },
      {
        h: 'Ending the agreement',
        ps: [
          'You can stop at any time and delete your account from inside the app; the privacy policy describes exactly what deletion removes.',
          'We may suspend or end access to an account that is being used against these terms, or where we are required to do so by law. Before we do that, we will normally give you a chance to fix it or to export what is yours.',
        ],
      },
      {
        h: 'Questions, and changes to these terms',
        ps: [
          'Write to support@closenai.app. We update these terms when the product changes; the current date appears at the bottom of this page, and continuing to use the app after an update means the updated terms apply to you.',
          'These terms are not legal advice, and this page is a plain-language agreement rather than a jurisdiction-specific contract.',
        ],
      },
    ],
  },
};

const ZH: Record<LegalKind, LegalDoc> = {
  privacy: {
    title: '隐私政策 — ClosenAI',
    description:
      'ClosenAI 采集你的哪些数据、由谁代为处理、可选的部分怎么关掉，以及注销账号之后你的数据会怎样。',
    intro:
      'ClosenAI 存下你告诉它的话——对话、签到、关于你的记忆。这既是这个产品的全部意义，也是这一页存在的理由。下面写清楚：我们存什么、我们会把什么交给别的服务处理、哪些开关在你手里。',
    sections: [
      {
        h: '我们是谁，怎么联系我们',
        ps: [
          'ClosenAI 是 closenai.app 上的 iOS 应用。关于你的数据的任何问题——包括「确认你们到底存了什么」或「删除」——写信到 support@closenai.app。',
        ],
      },
      {
        h: '你提供的账号标识',
        ps: [
          '登录方式有三种：邮箱、用户名加密码、或 Apple 登录。按方式不同，我们存的是邮箱、用户名、或 Apple 账号标识符，另加你自己填写的显示名。',
          '密码只以 argon2 哈希保存，不存明文。登录与重置验证码同样以哈希保存、很快过期，并且连续输错后立即作废。',
        ],
      },
      {
        h: '你写下的内容',
        ul: [
          '与伙伴的对话消息。',
          '情绪签到（你选的情绪，以及你愿意附上的一句话）。',
          '记忆：正文、分类、置信度与版本历史，以及用于找出相关记忆的数值向量。',
          '反思、睡前复盘、性格画像与评估、成长记录、里程碑，以及第一周剧本的事件。',
          '你自己生成的导出物：分享卡，以及档案与报告的 PDF。',
        ],
        ps: [
          '这些是产品的核心，全部是你输入的内容。存下来是为了之后能记得，并把它递还给你。',
        ],
      },
      {
        h: '设置与偏好',
        ps: [
          '语言、时区、你希望被打扰的时间点、通知频率、对话风格，以及你主动填写的 MBTI 或九型人格编号。',
          '主动消息默认关闭：在你打开它之前，我们不会向你推送任何东西。',
        ],
      },
      {
        h: '设备与投递令牌',
        ps: [
          '如果你打开通知，我们会保存平台签发的推送令牌（Apple APNs，适用时也含 Google FCM），并附带设备平台与时区。令牌按设备保存，登出即清除，已登出的设备不会再收到消息。',
          '为了限流，我们读取服务前置代理报告的客户端 IP。它用来压制滥用，不用来给你画像。',
        ],
      },
      {
        h: '行为元数据——只有你同意之后',
        ps: [
          '产品分析由一个独立的同意开关决定。没有它，事件会被直接丢弃。',
          '有了它，我们只保留行为元数据：屏幕名、区块、功能、动作、触发来源、结果、耗时、计数、你的订阅档位、语言、平台、应用版本、连续天数与关系等级。这个白名单在客户端和服务端各校验一遍，所以即使某个缺陷把文本字段塞了进来，反思、记忆、消息或情绪的具体值也带不出去。',
          '这些事件与你的账号 id 关联——这样注销时才能一并删除；它们不与你的姓名或邮箱关联，也不包含你的内容。',
        ],
      },
      {
        h: 'AI 生成的元数据',
        ps: [
          '出于成本与可用性考虑，我们记录每一次模型调用：由哪个服务商的哪个模型、用途、语言、状态、降级原因、错误类型、输入与输出的字符数、token 数、延迟。这条记录里不写提示词，也不写返回的正文。',
        ],
      },
      {
        h: '危机与安全记录',
        ps: [
          '当你自己的表述命中危机模式（自我伤害，或关系暴力），应用会按你的地区和语言给出援助热线，并留下一条复核记录：类别、命中来源、命中的触发词、展示的热线，以及一段截断到 280 字符以内的摘录，供人工在 24 小时内抽检误报。',
          '这段摘录存在的唯一目的是抓住检测器的错判。它不会出现在你看到的任何导出物里，并且随账号一起删除。命中之后，营销与升级类的消息也会对该用户暂停。',
        ],
      },
      {
        h: '购买记录',
        ps: [
          '付款由 Apple App Store 处理。我们收到交易收据及其环境、商品标识与日期，用来发放权益；卡号与账单地址不会经过我们。',
        ],
      },
      {
        h: '崩溃报告',
        ps: [
          '如果这个构建配置了崩溃监控，我们是刻意把它绑不到人身上的：不附加任何用户身份，console 类型的面包屑整条丢弃，网络面包屑里的请求体、响应体与 URL 查询串剥掉，extra 一律清空。一份崩溃报告应该说清楚哪个界面坏了，而不是你在上面写了什么。',
          '没有为该构建配置项目标识时，这个监控不启用。',
        ],
      },
      {
        h: '还有谁在处理你的数据',
        ul: [
          '大模型服务商——你的对话与内容文本会发送给当前部署所配置的大模型服务（默认通过阿里云 DashScope 的通义千问；也可改为豆包、Gemini 或任一 OpenAI 兼容端点）。回复、记忆、画像与报告就是这样生成的。',
          'Supabase——存放你内容的 Postgres 数据库。',
          'Upstash——Redis（验证码、限流）与 QStash（延迟任务，例如注销）。',
          'Resend——登录与重置密码的验证邮件。',
          'Apple APNs 与 Google FCM——投递你开启的通知。',
          'PostHog Cloud（EU 区域）——产品分析，受上面的同意开关控制。',
          'Sentry——崩溃监控，配置了才启用。',
          'Apple App Store——支付与购买历史。',
          'Cloudflare——closenai.app 网站的托管与分发。',
        ],
        ps: [
          '我们用这些服务来把产品跑起来。我们不出售你的数据，也不会把它放进任何广告受众。',
        ],
      },
      {
        h: '数据在哪里被处理',
        ps: [
          '我们的服务商分布在不同国家，所以你的数据可能在你居住国之外被处理——分析平台在欧盟，而数据库与模型服务可能位于别处。具体清单取决于部署配置，我们会让这一页与生产环境实际使用的清单保持一致。',
        ],
      },
      {
        h: '保留多久，删除到底做了什么',
        ps: [
          '账号存在多久，数据就存多久。从应用内注销分两步：账号立即被标记为已删除——无法再登录、会话令牌吊销、推送令牌清除——随后物理删除异步执行，带重试和定时兜底扫描，第一次失败也会跑完。',
          '物理删除会清掉：对话、签到、记忆、画像与评估、成长记录、模型调用记录、里程碑、推送令牌、分享导出、收据、订阅与权益、剧本事件、引导答案、安全记录、派生洞察及其证据、用户反馈，以及待投递的消息队列；然后请求分析平台抹除它以你的 id 存留的数据。',
          '删除不是「停用」，我们也不留一份「谁注销了」的名单：我们依赖的举证是一行内部日志，记录删除时刻与每张表删除的行数。你也可以只删一条记忆，不必注销账号。',
          '已同意并上送的分析事件随账号一起抹除。如果那次抹除调用失败，失败会被记录并由兜底扫描重试；它不会卡住你要求的注销。',
        ],
      },
      {
        h: '你手里的开关',
        ul: [
          'AI 功能开关：AI 数据共享同意是显式的，需要它的功能在没有同意时拒绝运行。',
          '分析开关：独立的一项，可单独开合。',
          '通知开关：可以整个关掉，也可以只改频率。',
          '删除一条记忆、把报告与卡片导出成 PDF，或者注销整个账号。',
          '应用没覆盖到的事，写信到 support@closenai.app。',
        ],
      },
      {
        h: '安全',
        ps: [
          '应用与我们服务器之间的流量走 HTTPS，与网站同一套前置代理。会话用短期访问令牌（30 分钟）与轮换的刷新令牌（30 天），刷新令牌在服务端只存哈希、登出即吊销。在你手机上，令牌存放在 iOS 钥匙串里，不是浏览器的 cookie，也不是明文文件。',
          '我们不自以为是的这部分：你的内容在数据库里是可读的行列，受访问控制保护，但不是端到端加密。我们没有那种「只能看到你的记忆标题、却看不到正文」的机制——宁可直说，也不暗示自己做到了。',
        ],
      },
      {
        h: '网站本身',
        ps: [
          'closenai.app 上的页面——包括分享卡打开的那个落地页——不种 cookie，也不加载任何第三方脚本。应用侧同理：应用里没有分析 SDK，也不会直连分析服务商，事件都经过我们的后端。',
        ],
      },
      {
        h: '未成年人',
        ps: [
          'ClosenAI 是为成年人做自我觉察而设计的，不面向儿童。我们不会有意收集儿童的数据；如果发生了，写信告诉我们，我们会删除该账号。',
        ],
      },
      {
        h: '本页的更新',
        ps: [
          '这一页的当前日期标在底部。应用开始采集任何新东西时，这一页先于那次采集上线——应用里的链接指向这里，所以只有一个版本要读。',
        ],
      },
    ],
  },
  terms: {
    title: '服务条款 — ClosenAI',
    description:
      '你与 ClosenAI 之间的约定：这个应用是做什么的、谁可以使用、哪些东西的所有权不变、订阅与买断怎么算，以及我们声明不承担的部分。',
    intro:
      '这些条款是你与 closenai.app 上的 ClosenAI 之间的约定。请重点读「这个应用不是什么」那一节——对一个会回过头来谈论你人生细节的产品来说，那部分最要紧。',
    sections: [
      {
        h: '接受条款',
        ps: [
          '注册或使用应用即表示你同意这些条款，并同意隐私政策（那一节说明我们采集什么）。如果你不同意，请不要使用应用。',
        ],
      },
      {
        h: '谁可以使用',
        ps: [
          '你必须达到你所在司法辖区要求你能够自行同意的年龄。这个应用不为儿童设计。',
          '你需要提供准确的账号信息，并负责保管自己的密码与设备。如果怀疑别人能碰到你的登录方式，告诉我们。',
        ],
      },
      {
        h: 'ClosenAI 是——以及不是——什么',
        ps: [
          'ClosenAI 是一个伙伴，也是一本会回应你的日记：它存下你告诉它的话，之后取回，并把其中的模式呈现给你。',
          '它不是心理咨询、治疗，不是医疗、法律或财务建议，也不是危机干预服务。当你自己的表述显示你可能处于危险中，应用会先给出你所在地区的援助热线，而不是照常聊下去——但那是一块路标，不是治疗，替代不了急救服务。如果你正面临直接危险，请立即联系当地紧急号码。',
          '生成的文字来自模型。它可能出错，可能记错你的重点，也可能自信地说错。请把它当作对你记录的映照，而不是一份事实档案。',
        ],
      },
      {
        h: '你的账号',
        ps: [
          '你可以用邮箱、用户名加密码，或 Apple 登录注册。一个邮箱或一个 Apple 账号对应一个账号。',
          '账号只属于你个人——不要出售、转让或与他人共用访问权限。',
        ],
      },
      {
        h: '内容的所有权仍然是你的',
        ps: [
          '你写下的东西归你。这些条款里没有任何一条把它转给我们。',
          '你授予我们的是一项有限许可：为了运行你所请求的功能——记忆检索、报告、你自己的画像——而对内容进行存储、处理和展示，以及在法律要求时用于合规。我们不会把你的内容许可给广告商。',
          '你对自己记录的内容负责，包括它不侵犯他人的权利。',
        ],
      },
      {
        h: '我们请你不要做的事',
        ul: [
          '违法使用，或用于策划、煽动对自己或他人的伤害。',
          '探测、破坏或过载本服务，绕过配额、付费校验或安全限制。',
          '未经我们同意转售、再许可，或把服务为他人运营。',
          '冒充他人，或注册并不属于你的账号。',
        ],
      },
      {
        h: '分享出去的卡片，拿到链接的人都能看',
        ps: [
          '你导出的分享卡，以及它背后的 closenai.app/r/ 落地页，任何收到的人都可读。分享是你的选择；请把它当作你亲手递给对方的那份文件。装了应用的人点这条链接，会直接进入应用。',
        ],
      },
      {
        h: '免费档、Pro 与单次买断',
        ps: [
          '免费档包含每日的对话与签到额度，以及有限次数的性格画像刷新；确切上限以应用内展示为准，并可能调整。',
          'Pro 与 Pro+ 提高这些上限。Life Archive 是一次性买断，导出它的 PDF 需要这项权益，或者用掉有效订阅带来的当月免费次数——否则应用会明确告诉你，而不是悄悄扣费。',
          '调整配额或价格是产品变更；我们不会为你没买的东西收费，也不会收回你已经付费取得的权益。',
        ],
      },
      {
        h: '付款、续订与退款',
        ps: [
          '购买通过 Apple App Store 完成，由 Apple 收款。订阅会自动续订，取消在你的 Apple 账户设置里操作——取消同时也会停止下一次扣费。',
          '退款与扣款争议由 Apple 按其政策处理，请在 App Store 管理你的购买记录。',
        ],
      },
      {
        h: '可用性、变更，以及你选择加入的推送',
        ps: [
          '应用需要受支持的 iOS 版本和联网。功能会增加、改动，偶尔也会移除；某些部分只提供给部分用户或地区。',
          '主动消息在你打开之前是关闭的，你随时可以改频率或整个关掉。消息量在设计上就有限制：里程碑与关心类的推送每月有上限。',
          '我们不承诺服务不中断。应用不可用时你的数据不会丢——如果长时间不可用，我们会说明，而不是继续收费。',
        ],
      },
      {
        h: '第三方服务',
        ps: [
          '应用依赖由其他公司运营的服务——生成文本的模型服务、背后的数据库与任务基础设施、通知投递，以及 App Store。它们各自的条款与故障都是用 ClosenAI 的一部分，谁做什么由隐私政策列明。',
        ],
      },
      {
        h: '免责',
        ps: [
          '在法律允许的最大范围内，应用按「现状」提供，不担保其不中断、无错误，也不担保它关于你说的任何内容是准确的。',
          '某些司法辖区不允许某些免责声明，那些条款对你可能不适用。',
        ],
      },
      {
        h: '责任限制',
        ps: [
          '在法律允许的范围内，我们对应用承担的责任有限，并且对间接或后果性损失不承担责任，包括由模型生成的文字引发的损失。',
          '这不排除或限制依法不得排除的责任，包括因过失造成的死亡或人身伤害、因故意不当行为造成的责任，也不影响你所在地区强制性的消费者权利。',
        ],
      },
      {
        h: '协议终止',
        ps: [
          '你可以随时停止使用，并在应用内注销账号；删除具体清掉了什么由隐私政策说明。',
          '对于违反这些条款的使用，或法律要求的情形，我们可以暂停或终止对该账号的访问。在那之前，我们通常会给你一次改正或导出自己数据的机会。',
        ],
      },
      {
        h: '问题，以及条款的变更',
        ps: [
          '写信到 support@closenai.app。产品变更时我们会更新这些条款，当前日期标在这一页底部；更新之后继续使用应用，即表示变更后的条款对你适用。',
          '这些条款不是法律建议；这一页是平语化的约定，而不是针对某一司法辖区的合同文本。',
        ],
      },
    ],
  },
};

export const LEGAL: Record<Lang, Record<LegalKind, LegalDoc>> = { en: EN, zh: ZH };
