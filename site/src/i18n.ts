/**
 * 落地站双语字典（构建期静态注入——非 JS 运行时切换，
 * 两个语言都是完整静态 HTML，SEO/GEO 抓什么语言由路径决定）。
 * 文案红线：只说「它记得你说过什么」的既有事实，禁因果断言式卖点。
 */
export type Lang = 'en' | 'zh';

export const T = {
  en: {
    nav: { features: 'Features', memory: 'Memory', privacy: 'Privacy', faq: 'FAQ', get: 'Get the App' },
    hero: {
      kicker: 'iOS · 15 languages · AI companion',
      h1Html: 'It remembers <em>everything</em> you tell it.',
      sub: 'ClosenAI is an AI companion built on long-term memory. What you said last Tuesday, how you felt after that call, the small things you mentioned once — it keeps them, and it grows with you.',
      cta1: 'Download on the App Store',
      cta2: 'See how memory works',
      note: 'Free to start · No ads · Your data stays yours',
    },
    trust: [
      'Remembers across days, weeks and years',
      'Export or delete everything, anytime',
      'No feed, no ads, no infinite scroll',
    ],
    feat: {
      kicker: 'What it does',
      h2: 'A companion that keeps the whole picture',
      sub: 'Not a chat window that forgets. ClosenAI builds a living record of your days and reflects it back when it matters.',
      cards: [
        { h: 'Long-term memory', p: 'Everything you share becomes memory with confidence scores. Important things stick; noise fades on its own.', icon: 'brain', v: false },
        { h: 'Mood check-ins', p: 'A daily one-tap check-in turns into trends and heatmaps — see the week you actually had, not the one you remember.', icon: 'activity', v: false },
        { h: 'Personality profile', p: 'MBTI and Enneagram, grounded in what you actually said — refreshed as you grow, not a one-off quiz.', icon: 'user', v: false },
        { h: 'AI weekly report', p: 'Every week, a quiet summary of your patterns: what kept showing up, and what changed.', icon: 'calendar', v: false },
        { h: 'Bedtime review', p: "A gentle nightly wrap-up before sleep — the day, kept. Tomorrow's you will thank tonight's you.", icon: 'moon', v: false },
        { h: 'Share cards', p: 'Turn your personality or milestones into beautiful cards — and send a piece of your journey to a friend.', icon: 'share', v: true },
      ],
    },
    mem: {
      kicker: 'Memory',
      h2: 'Things you mentioned once, kept for when they matter',
      p: 'Memory in ClosenAI is versioned and scored. When you repeat yourself, it strengthens what it knows instead of storing duplicates. Anniversaries resurface on their own — the interview, the move, the person you were becoming.',
      p2: 'And when a year passes, it remembers that too.',
      items: [
        { w: 'Today', html: '"I finally told my boss about the <b>transfer</b>."' },
        { w: '3 weeks ago', html: 'Mood recovered after <b>morning runs</b> — pattern noted.' },
        { w: '2 months ago', html: '"Mom\'s surgery is on the <b>12th</b>."' },
        { w: '1 year ago', html: '<b>One year ago today</b>: first conversation. It was a rough Tuesday.' },
      ],
    },
    per: {
      kicker: 'Personality',
      h2: 'Two lenses, one picture of you',
      p: 'Your profile is generated from real conversations and re-examined as you grow. Combine the two frameworks into a single shareable card — a portrait, not a label.',
      p1: 'The Advocate — drawn from your conversations, not a 20-question quiz.',
      p2: 'The Individualist — cross-checked against the same memories, from another angle.',
    },
    pri: {
      kicker: 'Privacy',
      h2: 'Intimate by design, private by default',
      sub: 'A companion that remembers must first be trusted to remember. The controls are yours, in plain language.',
      policyLink: 'Read the full privacy policy',
      items: [
        { h: 'Export anything', p: 'Memories, reports, share cards — export as files whenever you want. No lock-in.', icon: 'export' },
        { h: 'Delete for real', p: 'Delete a single memory or the whole account — deletion means deletion, not "deactivated".', icon: 'trash' },
        { h: 'Keys stay on device', p: 'Sign-in credentials live in the iOS Keychain — never in a cookie jar or a spreadsheet.', icon: 'lock' },
        { h: 'No ads, no resale', p: 'ClosenAI is funded by subscriptions. Your conversations are not the product.', icon: 'shield' },
      ],
    },
    faq: {
      h2: 'Common questions',
      items: [
        { q: 'Is my data used to train AI models?', a: 'No. Your conversations are used to serve you — memory retrieval, reports, your own profile. They are not sold, and not used to train third-party models. AI consent is explicit and revocable in settings.' },
        { q: 'What happens if I stop using it for a while?', a: 'Your memories stay. Come back after two weeks or two months and it picks up where you left off — no guilt-tripping, just a "welcome back" and maybe a gentle nudge about what you left open.' },
        { q: 'Which languages does it speak?', a: '15: English, Chinese, Spanish, Korean, Japanese, Russian, Indonesian, Malay, Thai, German, French, Italian, Portuguese, Vietnamese and Khmer. It replies in whatever language you use.' },
        { q: 'Is it a replacement for therapy?', a: 'No. ClosenAI is a companion and a journal that talks back. If you are in crisis, it points you to real help lines — that behavior is built in, not bolted on.' },
        { q: 'How much does it cost?', a: 'Free to start, with daily conversations and the core memory features. Pro unlocks deeper reports, longer windows and the full archive export — cancel anytime.' },
      ],
    },
    fin: { kicker: 'Start tonight', h2: 'Say something worth remembering.', cta: 'Download on the App Store', note: 'Requires iOS 15+ · Free to start', unreleased: 'The iOS app is not on the App Store yet — this becomes a real download button the day it is.' },
    foot: { tag: 'It remembers everything you tell it', privacy: 'Privacy Policy', terms: 'Terms of Service', contact: 'Contact' },
    mock: {
      ai1: 'How did the interview go? You were nervous about it last week.',
      me1: 'Just finished! They asked about the project I mentioned to you before.',
      ai2: 'The one you stayed up two nights for? Told you it would pay off.',
      chip1: 'Saved to memory',
      mem1: 'Interview prep · two all-nighters',
      mem2: "Felt calm after Sunday's walk",
      mem3: "Sister's birthday is May 3",
    },
    meta: {
      title: 'ClosenAI — It remembers everything you tell it',
      description: 'ClosenAI is an AI companion with long-term memory. It remembers what you said, how you felt, and who you are becoming. iOS, 15 languages.',
    },
  },
  zh: {
    nav: { features: '功能', memory: '记忆', privacy: '隐私', faq: '常见问题', get: '获取 App' },
    hero: {
      kicker: 'iOS · 15 种语言 · AI 陪伴',
      h1Html: '它记得你告诉过它的<em>每一句话</em>。',
      sub: 'ClosenAI 是一个建立在长期记忆上的 AI 伙伴。上周二你说的话、那通电话之后的心情、你只随口提过一次的小事——它都记得，并且陪你一起成长。',
      cta1: '在 App Store 下载',
      cta2: '看看记忆如何运作',
      note: '免费开始 · 无广告 · 数据属于你',
    },
    trust: [
      '记忆横跨数天、数周与数年',
      '随时导出或删除全部数据',
      '没有信息流、没有广告、没有无限下滑',
    ],
    feat: {
      kicker: '它能做什么',
      h2: '一个记得完整图景的伙伴',
      sub: '不是聊完就忘的对话框。ClosenAI 为你的每一天留下活的记录，在重要的时刻轻轻递还给你。',
      cards: [
        { h: '长期记忆', p: '你分享的一切都会成为带置信度的记忆。重要的留下，噪音自己淡去。', icon: 'brain', v: false },
        { h: '情绪签到', p: '每天一次一键签到，沉淀成趋势与热力图——看见你真实度过的一周，而不是记忆里那一周。', icon: 'activity', v: false },
        { h: '性格画像', p: 'MBTI 与九型人格，来自你真实说过的话——随着成长持续更新，不是一次性的测验。', icon: 'user', v: false },
        { h: 'AI 周报', p: '每周一份安静的总结：什么在反复出现，什么悄悄变了。', icon: 'calendar', v: false },
        { h: '睡前复盘', p: '睡前一段温柔的收尾——这一天，被好好收藏。明天的你会感谢今晚的你。', icon: 'moon', v: false },
        { h: '分享卡', p: '把你的性格或里程碑做成好看的卡片——把一段旅程寄给朋友。', icon: 'share', v: true },
      ],
    },
    mem: {
      kicker: '记忆',
      h2: '你只提过一次的事，在重要的时刻被想起',
      p: 'ClosenAI 的记忆带版本与置信度。你重复表达时，它会加固已知的部分而不是存下重复副本。周年会自己浮上来——那场面试、那次搬家、那个正在变化的你。',
      p2: '一年过去时，它也记得。',
      items: [
        { w: '今天', html: '"我终于跟老板提了<b>转岗</b>的事。"' },
        { w: '3 周前', html: '心情在<b>晨跑</b>之后回稳——已记下这个规律。' },
        { w: '2 个月前', html: '"妈妈的手术定在 <b>12 号</b>。"' },
        { w: '1 年前', html: '<b>一年前的今天</b>：第一次对话。那是个不太好过的周二。' },
      ],
    },
    per: {
      kicker: '性格',
      h2: '两个视角，拼出完整的你',
      p: '你的画像从真实对话中生成，并随成长持续复核。把两个框架合成一张可分享的卡片——是肖像，不是标签。',
      p1: '提倡者——来自你的对话，而不是 20 道选择题。',
      p2: '自我型——用同一批记忆、从另一个角度交叉验证。',
    },
    pri: {
      kicker: '隐私',
      h2: '因亲密而生，以隐私为本',
      sub: '一个会记住你的伙伴，首先要配得上「被信任地记住」。控制权都在你手里，说人话的那种。',
      policyLink: '阅读完整隐私政策',
      items: [
        { h: '随时导出', p: '记忆、报告、分享卡——想导出就导出成文件。不锁定。', icon: 'export' },
        { h: '真的删除', p: '删除一条记忆，或注销整个账号——删除就是删除，不是「停用」。', icon: 'trash' },
        { h: '密钥留在设备', p: '登录凭证存在 iOS 钥匙串里——不进浏览器的糖果罐，也不进任何表格。', icon: 'lock' },
        { h: '无广告、不倒卖', p: 'ClosenAI 靠订阅运转。你的对话不是商品。', icon: 'shield' },
      ],
    },
    faq: {
      h2: '常见问题',
      items: [
        { q: '我的数据会被用来训练模型吗？', a: '不会。你的对话只用于为你服务——记忆检索、报告、你自己的画像。不被出售，也不用于训练第三方模型。AI 数据同意是显式开关，随时可撤回。' },
        { q: '如果我一段时间不用，会怎样？', a: '记忆都在。两周或两个月后回来，它从你离开的地方接着聊——不搞愧疚绑架，只有一句「欢迎回来」，也许还有一条温柔提醒，关于你没聊完的那件事。' },
        { q: '它会说哪些语言？', a: '15 种：中文、英语、西班牙语、韩语、日语、俄语、印尼语、马来语、泰语、德语、法语、意大利语、葡萄牙语、越南语和高棉语。你用什么语言，它就用什么语言回复。' },
        { q: '它能替代心理咨询吗？', a: '不能。ClosenAI 是伙伴，是一本会回应你的日记。如果你正处于危机中，它会引导你去找真实的援助热线——这是内建行为，不是事后补丁。' },
        { q: '要花多少钱？', a: '免费开始，含每日对话与核心记忆功能。Pro 解锁更深的报告、更长的时间窗与完整档案导出——随时可取消。' },
      ],
    },
    fin: { kicker: '从今晚开始', h2: '说一句值得被记住的话。', cta: '在 App Store 下载', note: '需要 iOS 15 或更高版本 · 免费开始', unreleased: 'iOS 应用还没有上架 App Store——上架那天，这里会变成一个真的下载按钮。' },
    foot: { tag: '它记得你告诉过它的每一句话', privacy: '隐私政策', terms: '服务条款', contact: '联系我们' },
    mock: {
      ai1: '面试怎么样？你上周还为它紧张来着。',
      me1: '刚面完！他们问到了我之前跟你提过的那个项目。',
      ai2: '你为它熬了两个通宵的那个？就说会值的。',
      chip1: '已存入记忆',
      mem1: '面试准备 · 熬了两个通宵',
      mem2: '周日散步后心情平静',
      mem3: '妹妹生日是 5 月 3 日',
    },
    meta: {
      title: 'ClosenAI — 它记得你告诉过它的每一句话',
      description: 'ClosenAI 是一个拥有长期记忆的 AI 伙伴。它记得你说过的话、你的心情，以及你正在成为的人。iOS，15 种语言。',
    },
  },
} as const;
