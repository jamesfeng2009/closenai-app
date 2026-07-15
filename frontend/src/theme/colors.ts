/**
 * ClosenAI 主题颜色定义
 *
 * 设计理念：深色背景为主，金色和紫色为强调色，
 * 营造专注、内省的视觉氛围。
 */

export const colors = {
  /** 主背景色 — 深黑 */
  background: '#050508',

  /** 卡片/表面背景 — 略亮于背景 */
  surface: '#0F0F1A',

  /** 表面高亮 */
  surfaceHighlight: '#1A1A2E',

  /** 主强调色 — 金色 */
  primary: '#FFD700',

  /** 次强调色 — 紫色 */
  secondary: '#C084FC',

  /** 主要文字 */
  text: '#FFFFFF',

  /** 次要文字 */
  textSecondary: '#8888A0',

  /** 辅助文字 */
  textTertiary: '#4A4A5E',

  /** 成功 */
  success: '#4ADE80',

  /** 警告 */
  warning: '#FBBF24',

  /** 错误 */
  error: '#F87171',

  /** 边框 */
  border: '#1E1E2E',

  /** 分割线 */
  divider: '#12121F',
} as const;

export type ColorScheme = typeof colors;
