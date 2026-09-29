/**
 * lineHeight 单位守卫（I2 · 前端 UI 审计）。
 *
 * 为什么需要它：RN 的 `lineHeight` 单位是 **pt**，不是 CSS 那样的无单位倍数。
 * 代码里曾把 `lineHeight: 1.6` 当倍数写，实际把行高压到 1.6pt，多行文字重叠
 * （2026-09-29 实测 30 处 / 14 文件）。功能测试全绿，只有肉眼能发现，所以立守卫。
 *
 * 口径：只禁「小于 10 的行高数字」——pt 行高不可能低于 10（最小字号 FontSize.xs=9）。
 * 倍数写法（1.x）与手写的过小 pt 值都会被同一条规则抓到，不必枚举具体数字。
 */
import fs from 'fs';
import path from 'path';
import { LineHeight, FontSize } from '../src/theme/colors';

const SRC_DIR = path.join(__dirname, '../src');

/** 只扫代码不扫注释：守卫自身与修复说明里会出现被禁的写法。 */
function codeOnly(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    // 行内注释（ StyleSheet 块里常见 // fontSize …）
    .replace(/([^:])\/\/.*$/gm, '$1');
}

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === '__snapshots__' ? [] : walk(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

const FILES = walk(SRC_DIR);
// 每条命中记录 [相对路径, 行号, 该行内容]
const LINE_HEIGHT_LINES: [string, number, string][] = [];
FILES.forEach((f) => {
  codeOnly(fs.readFileSync(f, 'utf8')).split('\n').forEach((line, i) => {
    if (/lineHeight:/.test(line)) LINE_HEIGHT_LINES.push([path.relative(SRC_DIR, f), i + 1, line]);
  });
});

/** 取 lineHeight 的值字面量：`LineHeight.body` / `22` / `1.6`；解析不出返回空串（由下方判据拦下）。 */
function valueOf(line: string): string {
  return line.match(/lineHeight:\s*([\w.]+|\d+(?:\.\d+)?)/)?.[1] ?? '';
}

describe('lineHeight 单位：源码层守卫', () => {
  it('src 下确有 lineHeight 声明（守卫没有扫空目录）', () => {
    expect(LINE_HEIGHT_LINES.length).toBeGreaterThan(0);
  });

  it('不再出现把倍数当 pt 写的 lineHeight: 1.x', () => {
    const offenders = LINE_HEIGHT_LINES
      .filter(([, , line]) => /^1(\.\d+)?$/.test(valueOf(line)))
      .map(([f, n, line]) => `${f}:${n} ${line.trim()}`);
    expect(offenders).toEqual([]);
  });

  it('每一处 lineHeight 要么是 LineHeight.<token>，要么是 ≥10 的 pt 值', () => {
    const offenders = LINE_HEIGHT_LINES.filter(([, , line]) => {
      const v = valueOf(line);
      if (v.startsWith('LineHeight.')) return false;
      const num = Number(v);
      return !(Number.isFinite(num) && num >= 10);
    }).map(([f, n, line]) => `${f}:${n} ${line.trim()}`);
    expect(offenders).toEqual([]);
  });

  it('LineHeight.<token> 引用的键在主题里真实存在', () => {
    const keys = [...new Set(
      LINE_HEIGHT_LINES.map(([, , line]) => valueOf(line))
        .filter((v) => v.startsWith('LineHeight.'))
        .map((v) => v.split('.')[1])
    )];
    expect(keys.length).toBeGreaterThan(0);
    keys.forEach((k) => {
      expect(Object.keys(LineHeight)).toContain(k);
    });
  });
});

describe('lineHeight 主题 token', () => {
  it('四个档位齐备且单调递增', () => {
    const values = [LineHeight.caption, LineHeight.small, LineHeight.body, LineHeight.heading];
    values.forEach((v, i) => {
      if (i > 0) expect(v).toBeGreaterThan(values[i - 1]);
    });
  });

  it('行高对字号构成 ≥1.3 倍的可读间距（覆盖全部 FontSize 档位）', () => {
    // 映射口径：≤12→caption、13→small、14–15→body、≥16→heading
    const match = (fontSize: number) =>
      fontSize <= 12 ? LineHeight.caption
        : fontSize <= 13 ? LineHeight.small
          : fontSize <= 15 ? LineHeight.body
            : LineHeight.heading;
    Object.values(FontSize).forEach((size) => {
      expect(match(size) / size).toBeGreaterThanOrEqual(1.3);
    });
  });
});
