import type { SharePeriod } from '../types';

/**
 * PDF 导出的落盘文件名（U2-3）。
 *
 * 逐字沿用后端 Content-Disposition 的登记值（`contracts/endpoints.md` §七），
 * 前端不另起一名：分享面板就按文件名展示，改名等于造第二个真源。
 * 这三个值和 §七 的逐字比对由 `__tests__/pdfExportWiring.test.ts` 守。
 */
export const MEMORY_CARD_PDF_FILE = 'closenai_memory_card.pdf';
export const YEAR_IN_REVIEW_PDF_FILE = 'closenai_year_in_review.pdf';

/** 周期报告：后端按路径参数拼名（`closenai_{period}_report.pdf`）。 */
export function reportPdfFile(period: SharePeriod): string {
  return `closenai_${period}_report.pdf`;
}

// 与 backend/core/errors.py 同值的两个码（闸按 errors.py 逐字比对，防两边各改各的）
export const CODE_RESOURCE_NOT_FOUND = 40402; // RESOURCE_NOT_FOUND：该周期/该年零记录
export const CODE_MEMORY_NOT_FOUND = 40403; // MEMORY_NOT_FOUND：记忆不存在或不属于本人

/**
 * PDF 失败码 → 本地化文案键。
 *
 * 只分「用户能自己处理」的两类：没数据、这条记忆没了；其余（网络、5xx、
 * 未登录）一律收进 generic，界面上不弹裸 error.message。
 * 40304（Life Archive 权益门）不进本表——那三条端点本批未接线。
 */
export function pdfFailureKey(code?: number | null): string {
  if (code === CODE_RESOURCE_NOT_FOUND) return 'pdf.failed.noData';
  if (code === CODE_MEMORY_NOT_FOUND) return 'pdf.failed.memoryGone';
  return 'pdf.failed.generic';
}
