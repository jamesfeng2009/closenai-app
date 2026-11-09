/**
 * P2 #25：快捷签到连点会重复提交。
 *
 * 两个入口（HomeScreen 情绪行、ReflectScreen 快捷面板）过去都只看 useState：
 * setState 要等下一次渲染才可见，同一帧里的第二次点击读到的仍是旧值，
 * 于是同一天签到会被提交两遍。判据要求 await 之前用 ref 同步占位、finally 释放。
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const REFLECT = 'src/screens/ReflectScreen.tsx';
const HOME = 'src/screens/HomeScreen.tsx';

/** 切出某个函数/块的源码（按花括号配平，从签名处开始）。 */
function block(src: string, signature: string): string {
  const start = src.indexOf(signature);
  expect(start).toBeGreaterThanOrEqual(0);
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error('花括号没配平');
}

describe('ReflectScreen 快捷签到', () => {
  const src = read(REFLECT);
  const handler = block(src, 'const quickMoodCheckin = async (emoji: MoodEmoji)');

  it('await 之前用 ref 同步占位，而不是只靠 state', () => {
    const guardAt = handler.indexOf('if (moodCheckinRef.current) return;');
    const setAt = handler.indexOf('moodCheckinRef.current = true;');
    const awaitAt = handler.indexOf('await moodCheckin(');
    expect(guardAt).toBeGreaterThanOrEqual(0);
    expect(setAt).toBeGreaterThan(guardAt);
    expect(awaitAt).toBeGreaterThan(setAt);
  });

  it('finally 释放占位（成功、配额失败、网络失败都要放）', () => {
    const finallyAt = handler.indexOf('} finally {');
    expect(finallyAt).toBeGreaterThan(handler.indexOf('await moodCheckin('));
    expect(handler.slice(finallyAt)).toContain('moodCheckinRef.current = false;');
  });

  it('按钮在请求中 disabled，连点连事件都进不来', () => {
    expect(src).toContain('disabled={moodCheckingIn}');
  });
});

describe('HomeScreen 情绪行', () => {
  const src = read(HOME);
  const handler = block(src, 'const onMoodPress = async (emoji: MoodEmoji)');

  it('await 之前用 ref 同步占位', () => {
    const guardAt = handler.indexOf('if (checkingInRef.current || dashboard?.today_mood) return;');
    const setAt = handler.indexOf('checkingInRef.current = true;');
    const awaitAt = handler.indexOf('await moodCheckin(');
    expect(guardAt).toBeGreaterThanOrEqual(0);
    expect(setAt).toBeGreaterThan(guardAt);
    expect(awaitAt).toBeGreaterThan(setAt);
  });

  it('finally 释放占位', () => {
    const finallyAt = handler.indexOf('} finally {');
    expect(finallyAt).toBeGreaterThan(handler.indexOf('await moodCheckin('));
    expect(handler.slice(finallyAt)).toContain('checkingInRef.current = false;');
  });

  it('已签到短路仍在（今天签过就不再发请求）', () => {
    expect(handler).toContain('dashboard?.today_mood');
  });
});
