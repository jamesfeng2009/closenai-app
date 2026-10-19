/**
 * 数据导出的文案映射（T1-01）。
 *
 * 错误码与状态集合的真源在后端：`backend/core/errors.py` 的三个 EXPORT_* 码、
 * `backend/models/export.py` 的 `EXPORT_STATUSES`。这里只是把它们翻成 i18n 键，
 * 逐字对账由 `__tests__/dataExportWiring.test.ts` 钉住——对不上就是在给用户念一个
 * 后端已经改名的状态。
 */
import type { ExportJobStatus } from '../types';

/** 后端业务码 → i18n 键。未登记码由界面退回服务端文案，不假装成功。 */
export const EXPORT_ERROR_KEYS: Record<number, string> = {
  40905: 'export.notReady',
  42904: 'export.tooFrequent',
  50007: 'export.artifactMissing',
};

/** 五态 → 一句用户看得懂的话（顺序即生命周期，与后端 EXPORT_STATUSES 同集）。 */
export const EXPORT_STATUS_KEYS: Record<ExportJobStatus, string> = {
  queued: 'export.statusQueued',
  running: 'export.statusRunning',
  ready: 'export.statusReady',
  failed: 'export.statusFailed',
  expired: 'export.statusExpired',
};
