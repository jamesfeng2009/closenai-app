const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

/**
 * ArrayBuffer / Uint8Array → base64（无换行）。
 *
 * 自己写而不走 btoa / Buffer：渲染进程里 Hermes 不保证有 btoa，jest 跑在 node
 * 环境又有 Buffer——两条都不能作为产品代码的依赖，所以按 3 字节一组手算，
 * 由闸用例逐长度比对 node 的 Buffer.toString('base64') 自证正确。
 */
export function arrayBufferToBase64(input: ArrayBuffer | Uint8Array): string {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  const len = bytes.length;
  let out = '';
  let chunk = '';
  for (let i = 0; i < len; i += 3) {
    const b0 = bytes[i];
    const b1 = i + 1 < len ? bytes[i + 1] : 0;
    const b2 = i + 2 < len ? bytes[i + 2] : 0;
    const n = (b0 << 16) | (b1 << 8) | b2;
    chunk +=
      ALPHABET[(n >> 18) & 63] +
      ALPHABET[(n >> 12) & 63] +
      (i + 1 < len ? ALPHABET[(n >> 6) & 63] : '=') +
      (i + 2 < len ? ALPHABET[n & 63] : '=');
    // 分段落字符串：PDF 是几十 KB，逐段拼接比一次 return 长串更稳
    if (chunk.length >= 8192) {
      out += chunk;
      chunk = '';
    }
  }
  return out + chunk;
}
