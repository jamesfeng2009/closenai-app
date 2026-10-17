/**
 * trace id 生成（批 T-D，工作单 T-7 拍板）。
 *
 * 形状＝8-4-4-4-12 小写 hex（36 字符），逐字符取自 `Math.random`：
 * React Native 的 JS 运行时**没有** WebCrypto（`crypto.randomUUID` 在 Hermes 上不存在），
 * 而这里的诉求只是「一次排障会话的唯一键」，不是安全令牌——
 * 安全令牌面在 auth（JWT），与本文件无关，别把这里当随机数源的安全依赖。
 *
 * 采用值必须能过服务端两语的 T-8 消毒闸（`^[A-Za-z0-9._-]{1,128}$`），
 * hex+dash 的形状是它的真子集，所以「发出去必被原样采用」是构造保证，不靠运气。
 */
function hex(n: number): string {
  let s = '';
  for (let i = 0; i < n; i++) {
    s += Math.floor(Math.random() * 0x10000).toString(16).padStart(4, '0');
  }
  return s;
}

/** 每请求一个新值：不复用、不加前缀修饰（两侧日志与回执都以它为逐字判据）。 */
export function newTraceId(): string {
  return `${hex(2)}-${hex(1)}-${hex(1)}-${hex(1)}-${hex(3)}`;
}
