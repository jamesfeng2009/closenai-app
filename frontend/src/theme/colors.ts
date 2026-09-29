/**
 * ClosenAI Design Tokens。
 *
 * 从 UI 原型 (closenai-p0-prototype.html) 的 CSS 变量映射而来。
 * 暗色主题，金色/紫色高亮。
 */
export const Colors = {
  // Gold
  gold400: '#FFD700',
  gold500: '#D4AF37',
  gold600: '#B8960C',

  // Accent (Purple)
  accent500: '#6366F1',
  accent600: '#4F46E5',
  accent800: '#1E1B48',

  // Neon
  neonPink: '#E879F9',
  neonPurple: '#C084FC',
  neonDeep: '#7C3AED',

  // Backgrounds
  bgPrimary: '#050508',
  bgSecondary: '#0D0D12',
  bgTertiary: '#16161D',
  bgCard: '#1E1B48',
  bgElevated: '#1F0F22',

  // Text
  textPrimary: '#FFFFFF',
  textSecondary: '#A1A1AA',
  textTertiary: '#71717A',
  textGold: '#FFD700',
  textMuted: '#4A4A5A',

  // Borders
  borderGold: 'rgba(255, 215, 0, 0.1)',
  borderGoldStrong: 'rgba(255, 215, 0, 0.3)',
  borderLight: 'rgba(255, 255, 255, 0.1)',
  // 危险区（注销账号）描边：error 色的低透明度版本，与 borderGold 同一口径
  borderError: 'rgba(239, 68, 68, 0.35)',

  // Semantic
  success: '#10B981',
  warning: '#F59E0B',
  error: '#EF4444',

  // Gradients
  gradGold: ['#FFE566', '#FFD700', '#B8860B', '#FFE566'] as const,
  gradNeon: ['#E879F9', '#C084FC', '#7C3AED'] as const,
  gradCta: ['#8B05CF', '#8B5CF6', '#EC4899'] as const,
  gradCosmic: ['#050508', '#1E1B48', '#050508'] as const,
  gradCard: ['rgba(255,215,0,0.05)', 'rgba(255,215,0,0.02)'] as const,
} as const;

export const Spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const;

export const FontSize = {
  xs: 9,
  sm: 10,
  md: 11,
  lg: 12,
  xl: 13,
  xxl: 14,
  title: 15,
  h3: 18,
} as const;

// RN 的 lineHeight 单位是 pt，不是倍数（写成 1.6 会把行高压到 1.6pt，文字重叠）。
// 按 FontSize 就近取整：≥1.4 倍行距，小字号留相对更大的呼吸。
export const LineHeight = {
  caption: 18, // fontSize 9–12
  small: 20,   // fontSize 13
  body: 22,    // fontSize 14–15
  heading: 26, // fontSize ≥16
} as const;

export const BorderRadius = {
  sm: 8,
  md: 10,
  lg: 12,
  xl: 14,
  pill: 20,
  phone: 47,
} as const;

export const Shadow = {
  gold: {
    shadowColor: '#FFD700',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.3,
    shadowRadius: 20,
    elevation: 10,
  },
  card: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 16,
    elevation: 8,
  },
} as const;