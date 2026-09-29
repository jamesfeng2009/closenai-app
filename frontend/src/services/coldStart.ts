/**
 * 冷启动门闸（App.tsx 的 ready 判定）。
 *
 * 为什么要超时：initI18n 走 AsyncStorage、loadSession 走 SecureStore，原生侧
 * 偶发挂起时这两个 Promise 永不 settle。旧写法 `Promise.all` 等三者全齐才
 * setReady(true)，一旦挂起就是无限转圈白屏——用户没有出路，监控也拿不到信号。
 *
 * 超时放行后的迟到结果只做「向上修正」（false→true）：超时那刻用户可能已经在
 * 引导页点完（onComplete 置 true），把 false 写回去会把他踢回引导页重做一遍。
 */

/** 门闸最长等待：超过即按「未引导」放行，真实结果随后修正。 */
export const COLD_START_TIMEOUT_MS = 4000;

/** 与 App.tsx / session.ts 的 ONBOARDED_KEY 取值口径一致。 */
const ONBOARDED_FLAG = '1';

export interface ColdStartDeps {
  initI18n: () => Promise<unknown>;
  loadSession: () => Promise<unknown>;
  readOnboarded: () => Promise<string | null>;
  /** 到点强制放行（打开 ready 门闸），只触发一次。 */
  onReady: () => void;
  /** 引导标记落定；超时放行后的迟到值只允许 true。 */
  onOnboarded: (onboarded: boolean) => void;
  timeoutMs?: number;
}

export interface ColdStartHandle {
  cancel: () => void;
}

export function runColdStart(deps: ColdStartDeps): ColdStartHandle {
  const timeoutMs = deps.timeoutMs ?? COLD_START_TIMEOUT_MS;
  let settled = false;
  let cancelled = false;

  const timer = setTimeout(() => {
    if (settled || cancelled) return;
    settled = true;
    deps.onReady();
  }, timeoutMs);

  // 三项无依赖、并行；各自吞掉失败（挂了/报错的项不该让整条链路永不 settle）
  const tasks: [Promise<unknown>, Promise<unknown>, Promise<string | null>] = [
    deps.initI18n().catch(() => undefined),
    deps.loadSession().catch(() => undefined),
    deps.readOnboarded().catch(() => null),
  ];

  Promise.all(tasks)
    .then(([, , stored]) => {
      if (cancelled) return;
      const onboarded = stored === ONBOARDED_FLAG;
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        deps.onOnboarded(onboarded);
        deps.onReady();
        return;
      }
      // 已超时放行：只允许把「未引导」修正为「已引导」
      if (onboarded) deps.onOnboarded(true);
    })
    .catch(() => {
      // 回调自身抛错（例如卸载后 setState）不影响门闸，超时兜底已让 UI 可用
    });

  return {
    cancel: () => {
      cancelled = true;
      clearTimeout(timer);
    },
  };
}
