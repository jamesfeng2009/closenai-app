/**
 * 冷启动门闸的超时与修正语义（I3 · App.tsx ready 判定）。
 *
 * 这条链路是纯异步编排，可以脱离 RN 渲染单测；用假计时器把 4s 压成瞬时，
 * 逐条钉死「ready 只开一次」「挂起必须放行」「迟到结果只向上修正」。
 */
import { runColdStart, COLD_START_TIMEOUT_MS } from '../src/services/coldStart';

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (r?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 冲刷微任务队列（假计时器不替 Promise 链推进，setImmediate 也被 fake 掉了）。 */
async function flush(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

beforeEach(() => {
  jest.useFakeTimers();
});
afterEach(() => {
  jest.useRealTimers();
});

function setup(stored: string | null = '1') {
  const ready = jest.fn();
  const onboarded = jest.fn();
  const handle = runColdStart({
    initI18n: () => Promise.resolve(),
    loadSession: () => Promise.resolve(),
    readOnboarded: () => Promise.resolve(stored),
    onReady: ready,
    onOnboarded: onboarded,
  });
  return { ready, onboarded, handle };
}

describe('正常路径（三项都在超时前 settle）', () => {
  it('已引导用户：先写引导标记，再开门闸，且门闸只开一次', async () => {
    const { ready, onboarded } = setup('1');
    await flush();
    expect(onboarded).toHaveBeenCalledTimes(1);
    expect(onboarded).toHaveBeenCalledWith(true);
    expect(ready).toHaveBeenCalledTimes(1);
    // 门闸已开就别留挂起计时器（否则卸载后仍有一次回调排队）
    expect(jest.getTimerCount()).toBe(0);
    jest.advanceTimersByTime(COLD_START_TIMEOUT_MS * 3);
    await flush();
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it('未引导（无标记）：onboarded=false，门闸照样打开', async () => {
    const { ready, onboarded } = setup(null);
    await flush();
    expect(onboarded).toHaveBeenCalledWith(false);
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it('任一项 reject 不阻塞门闸（其余结果仍生效）', async () => {
    const ready = jest.fn();
    const onboarded = jest.fn();
    runColdStart({
      initI18n: () => Promise.reject(new Error('i18n boom')),
      loadSession: () => Promise.reject(new Error('securestore boom')),
      readOnboarded: () => Promise.resolve('1'),
      onReady: ready,
      onOnboarded: onboarded,
    });
    await flush();
    expect(onboarded).toHaveBeenCalledWith(true);
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it('readOnboarded 自身抛错 → 按未引导放行（不把用户永久卡在白屏）', async () => {
    const ready = jest.fn();
    const onboarded = jest.fn();
    runColdStart({
      initI18n: () => Promise.resolve(),
      loadSession: () => Promise.resolve(),
      readOnboarded: () => Promise.reject(new Error('storage boom')),
      onReady: ready,
      onOnboarded: onboarded,
    });
    await flush();
    expect(onboarded).toHaveBeenCalledWith(false);
    expect(ready).toHaveBeenCalledTimes(1);
  });
});

describe('挂起路径（原生侧 Promise 永不 settle）', () => {
  it('到 4s 强制开门闸，且只开一次', async () => {
    const hang = deferred<string | null>();
    const ready = jest.fn();
    const onboarded = jest.fn();
    runColdStart({
      initI18n: () => Promise.resolve(),
      loadSession: () => new Promise(() => {}), // 永不 settle
      readOnboarded: () => hang.promise,
      onReady: ready,
      onOnboarded: onboarded,
    });
    await flush();
    expect(ready).not.toHaveBeenCalled();

    jest.advanceTimersByTime(COLD_START_TIMEOUT_MS);
    expect(ready).toHaveBeenCalledTimes(1);
    expect(onboarded).not.toHaveBeenCalled();

    jest.advanceTimersByTime(COLD_START_TIMEOUT_MS * 3);
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it('超时后真实结果说「已引导」→ 向上修正', async () => {
    const hang = deferred<string | null>();
    const ready = jest.fn();
    const onboarded = jest.fn();
    runColdStart({
      initI18n: () => Promise.resolve(),
      loadSession: () => Promise.resolve(),
      readOnboarded: () => hang.promise,
      onReady: ready,
      onOnboarded: onboarded,
    });
    jest.advanceTimersByTime(COLD_START_TIMEOUT_MS);
    expect(onboarded).not.toHaveBeenCalled();

    hang.resolve('1');
    await flush();
    expect(onboarded).toHaveBeenCalledTimes(1);
    expect(onboarded).toHaveBeenCalledWith(true);
  });

  it('超时后真实结果说「未引导」→ 不回写（否则把刚点完引导的用户踢回去）', async () => {
    const hang = deferred<string | null>();
    const ready = jest.fn();
    const onboarded = jest.fn();
    runColdStart({
      initI18n: () => Promise.resolve(),
      loadSession: () => Promise.resolve(),
      readOnboarded: () => hang.promise,
      onReady: ready,
      onOnboarded: onboarded,
    });
    jest.advanceTimersByTime(COLD_START_TIMEOUT_MS);
    // 这段时间里用户可能已点完引导（onboarded 由 App 侧 onComplete 置 true），
    // 迟到的 null 若回写就会把他踢回引导页
    hang.resolve(null);
    await flush();
    expect(onboarded).not.toHaveBeenCalled();
  });
});

describe('cancel（卸载清理）', () => {
  it('取消后：超时不再开门闸，迟到结果也不回调', async () => {
    const hang = deferred<string | null>();
    const ready = jest.fn();
    const onboarded = jest.fn();
    const handle = runColdStart({
      initI18n: () => Promise.resolve(),
      loadSession: () => Promise.resolve(),
      readOnboarded: () => hang.promise,
      onReady: ready,
      onOnboarded: onboarded,
    });
    handle.cancel();
    jest.advanceTimersByTime(COLD_START_TIMEOUT_MS * 2);
    hang.resolve('1');
    await flush();
    expect(ready).not.toHaveBeenCalled();
    expect(onboarded).not.toHaveBeenCalled();
  });
});
