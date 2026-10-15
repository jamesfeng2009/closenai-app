import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { arrayBufferToBase64 } from '../utils/base64';

/** 这台设备用不了系统分享面板（或拿不到缓存目录）——界面按 `pdf.unavailable` 提示。 */
export class PdfUnavailableError extends Error {
  constructor() {
    super('pdf sharing unavailable');
    this.name = 'PdfUnavailableError';
  }
}

/**
 * 后端回来的 PDF 字节 → 落盘到 App 缓存目录 → 交给系统分享面板。
 *
 * 顺序是先查可用性再发请求后的写盘：`isAvailableAsync()` 为假时不白写一个没人接的文件。
 * 文件名沿用后端 Content-Disposition 登记值（见 `config/pdfFiles.ts`），
 * 分享面板展示的就是它，用户在其他 App 里找回也认得。
 */
export async function exportPdf(bytes: ArrayBuffer, fileName: string): Promise<void> {
  if (!(await Sharing.isAvailableAsync())) {
    throw new PdfUnavailableError();
  }
  const dir = FileSystem.cacheDirectory;
  if (!dir) {
    throw new PdfUnavailableError();
  }
  const uri = `${dir}${fileName}`;
  await FileSystem.writeAsStringAsync(uri, arrayBufferToBase64(bytes), {
    encoding: FileSystem.EncodingType.Base64,
  });
  await Sharing.shareAsync(uri, { mimeType: 'application/pdf', UTI: 'com.adobe.pdf' });
}
