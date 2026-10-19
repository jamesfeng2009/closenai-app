/**
 * 通用数据导出的落盘出口（T1-01 · Baseline §2.2-6「带走」／§3 P0）。
 *
 * 为什么不让浏览器/系统直接打开 `download_path`：那条 URL 靠 Authorization 头鉴权，
 * `Linking.openURL` 带不上 Bearer，必然 401（与 PDF 四条同一理由，见 api.ts 的注释）。
 * 所以字节一律先经 request(binary:true) 拿回来，再由这里落地。
 *
 * 两条出口分开写：
 * - 原生（iOS／Android）：写 App 缓存目录 → 系统分享面板，用户自己选「存到文件」
 *   或发到别的 App。面板不可用时报 `ArchiveUnavailableError`，界面按
 *   `export.unavailable` 说人话，不假装成功。
 * - Web（expo start --web）：Blob + 临时 URL + a[download] 点击。这条分支存在是因为
 *   P0 说的是「入口真实可用」；只接原生就等于把 Web 构建的按钮做成摆设。
 */
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { Platform } from 'react-native';
import { arrayBufferToBase64 } from '../utils/base64';

/** 这台设备既给不了分享面板也给不了浏览器下载——界面按 `export.unavailable` 提示。 */
export class ArchiveUnavailableError extends Error {
  constructor() {
    super('archive save unavailable');
    this.name = 'ArchiveUnavailableError';
  }
}

const ZIP_MIME = 'application/zip';

/**
 * 后端回来的 zip 字节 → 交给系统。
 *
 * 文件名取自响应的 `file_name`（后端的 `closenai-export-<job_id>.zip`），这里不自己拼：
 * 包名与库里 `file_path` 的对应关系是用户跨设备找回产物的唯一线索。
 */
export async function saveArchive(bytes: ArrayBuffer, fileName: string): Promise<void> {
  if (Platform.OS === 'web') {
    // 只在 web 分支触碰 DOM：原生环境没有 Blob/URL.createObjectURL，写在一起会当场崩。
    const globalAny = globalThis as unknown as {
      Blob?: typeof Blob;
      URL?: typeof URL;
      document?: Document;
    };
    if (!globalAny.Blob || !globalAny.URL || !globalAny.document) {
      throw new ArchiveUnavailableError();
    }
    const blob = new globalAny.Blob([bytes], { type: ZIP_MIME });
    const url = globalAny.URL.createObjectURL(blob);
    const anchor = globalAny.document.createElement('a');
    anchor.href = url;
    anchor.download = fileName;
    anchor.click();
    // 同一轮就 revoke，部分浏览器会取消还没真正开始的下载，所以推迟到下一个任务。
    setTimeout(() => globalAny.URL?.revokeObjectURL(url), 0);
    return;
  }

  if (!(await Sharing.isAvailableAsync())) {
    throw new ArchiveUnavailableError();
  }
  const dir = FileSystem.cacheDirectory;
  if (!dir) {
    throw new ArchiveUnavailableError();
  }
  const uri = `${dir}${fileName}`;
  await FileSystem.writeAsStringAsync(uri, arrayBufferToBase64(bytes), {
    encoding: FileSystem.EncodingType.Base64,
  });
  await Sharing.shareAsync(uri, { mimeType: ZIP_MIME, UTI: 'public.zip-archive' });
}
