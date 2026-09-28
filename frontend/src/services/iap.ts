/**
 * IAP 购买服务 (P1-F02) + Life Archive 一次性买断 (P0-2 / M6)。
 *
 * 封装 react-native-iap（StoreKit 2）：
 * - 订阅购买：requestSubscription → purchaseUpdatedListener → 服务端收据校验 → finishTransaction
 * - 一次性买断：requestPurchase（Life Archive 三档，非消耗品）
 * - 恢复购买：getAvailablePurchases → 逐笔服务端校验（App Store 合规必需入口）
 *
 * 安全约束（SKILL closenai-iap）：
 * - 收据必须由后端 /iap/verify 校验，禁止信任客户端结果
 * - 校验通过并 finishTransaction 后才算完成
 *
 * 环境说明：react-native-iap 为原生模块，需要开发构建（expo prebuild / dev client），
 * Expo Go 中不可用时 isIapAvailable() 返回 false，UI 显示提示而非崩溃。
 */
import * as RNIap from 'react-native-iap';
import { verifyReceipt } from './api';
import type { ReceiptVerifyResult } from '../types';

/** 订阅产品 SKU（与 App Store Connect 配置一致，同一订阅组支持升降级）。 */
export const PRODUCT_SKUS = [
  'closenai_pro_monthly',
  'closenai_pro_yearly',
  'closenai_pro_plus_monthly',
  'closenai_pro_plus_yearly',
] as const;

/** 默认购买产品（Paywall 主按钮）。 */
export const DEFAULT_SKU = 'closenai_pro_monthly';

/**
 * Life Archive 一次性买断 SKU（App Store Connect 非消耗品，M6）。
 *
 * 与订阅组完全独立：买断不产生 tier，后端落 user_entitlements，
 * 因此买断的收据校验同样走 /iap/verify（响应 kind='entitlement'）。
 */
export const LIFE_ARCHIVE_SKUS = [
  'life_archive_1y',
  'life_archive_all',
  'life_archive_legacy',
] as const;

export type LifeArchiveSku = (typeof LIFE_ARCHIVE_SKUS)[number];

/**
 * 购买超时但交易可能仍在处理中（StoreKit 事件延迟送达）。
 *
 * 超时后流程内 listener 已移除、purchaseFlowActive 复位，全局启动期 listener
 * 会接管后续送达的交易（verify + finish）。调用方应展示"处理中"提示，
 * 让用户稍后重启 App 查看订阅状态，而非视为失败或成功。
 */
export interface PurchaseTimeoutPending {
  status: 'timeout_pending';
}

/** 购买/恢复流程超时兜底（避免 StoreKit 事件丢失导致 Promise 永不 settle、busy 卡死）。 */
const IAP_TIMEOUT_MS = 30_000;

let connected = false;
/** 购买流程进行中标记：流程内事件由其自有 listener 处理，全局 listener 跳过避免重复校验。 */
let purchaseFlowActive = false;

/** 初始化 IAP 连接。返回当前环境是否可用（Expo Go 无原生模块时返回 false）。 */
export async function initIAP(): Promise<boolean> {
  if (connected) return true;
  try {
    const result = await RNIap.initConnection();
    connected = !!result;
  } catch {
    connected = false;
  }
  return connected;
}

// ── 启动期全局监听 ──
// react-native-iap 规范要求尽早注册 purchaseUpdatedListener / purchaseErrorListener：
// App 冷启动时 StoreKit 可能立即投递挂起交易（上次购买被中断），
// 若等到购买流程内才注册 listener 会丢失事件导致掉单。
// Expo Go 无原生模块时 initIAP 返回 false，静默跳过。
let startupListenersRegistered = false;

export function registerStartupListeners(): void {
  if (startupListenersRegistered) return;
  startupListenersRegistered = true;
  void (async () => {
    if (!(await initIAP())) return; // Expo Go 等环境：静默跳过
    try {
      RNIap.purchaseUpdatedListener(async (purchase) => {
        if (purchaseFlowActive) return; // 购买流程内的事件由流程 listener 处理
        // 挂起/中断恢复的交易：尽力服务端校验并完成，失败静默（StoreKit 下轮会重新投递）
        try {
          if (!purchase.transactionReceipt) return;
          await verifyReceipt(purchase.transactionReceipt, purchase.productId);
          await RNIap.finishTransaction({ purchase, isConsumable: false });
        } catch {
          // 静默：网络或校验失败时下轮启动重试
        }
      });
      RNIap.purchaseErrorListener(() => {
        // 全局错误事件无需处理（购买流程内有自己的 error listener）
      });
    } catch {
      // 原生模块异常：静默跳过
    }
  })();
}

// 模块初始化即注册（符合 react-native-iap 规范）
registerStartupListeners();

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('iap_timeout')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/** 拉取订阅商品（用于展示本地化价格）。失败返回空数组。 */
export async function getProducts(): Promise<RNIap.Subscription[]> {
  if (!(await initIAP())) return [];
  try {
    return await RNIap.getSubscriptions({ skus: [...PRODUCT_SKUS] });
  } catch {
    return [];
  }
}

/** 拉取一次性买断商品（Life Archive 三档，展示本地化价格）。失败返回空数组。 */
export async function getLifeArchiveProducts(): Promise<RNIap.Product[]> {
  if (!(await initIAP())) return [];
  try {
    return await RNIap.getProducts({ skus: [...LIFE_ARCHIVE_SKUS] });
  } catch {
    return [];
  }
}

/**
 * 通用购买流程：发起 → purchaseUpdatedListener 收到收据 → 后端校验 → finishTransaction。
 *
 * 订阅（requestSubscription）与一次性买断（requestPurchase）只有"发起方式"不同，
 * 监听、超时兜底、服务端校验与交易完成处理完全一致，因此共用本流程。
 *
 * 用户取消时 reject 携带 code 'E_USER_CANCELLED'。
 */
async function purchaseAndVerify(
  sku: string,
  request: () => Promise<unknown>,
): Promise<ReceiptVerifyResult | PurchaseTimeoutPending> {
  if (!(await initIAP())) {
    throw new Error('iap_unavailable');
  }
  return new Promise<ReceiptVerifyResult | PurchaseTimeoutPending>((resolve, reject) => {
    let purchaseSub: { remove: () => void } | null = null;
    let errorSub: { remove: () => void } | null = null;
    purchaseFlowActive = true;
    // 超时兜底：StoreKit 事件可能延迟送达，此时不视为失败。cleanup 复位
    // purchaseFlowActive，让全局启动期 listener 接管后续送达的交易（verify +
    // finish）。resolve timeout_pending 让调用方展示"处理中"提示，避免用户看到
    // 错误但实际已被扣费。
    const timer = setTimeout(() => {
      cleanup();
      resolve({ status: 'timeout_pending' });
    }, IAP_TIMEOUT_MS);
    const cleanup = () => {
      clearTimeout(timer);
      purchaseFlowActive = false;
      purchaseSub?.remove();
      errorSub?.remove();
    };

    purchaseSub = RNIap.purchaseUpdatedListener(async (purchase) => {
      if (purchase.productId !== sku) return;
      cleanup();
      try {
        const receipt = purchase.transactionReceipt;
        if (!receipt) throw new Error('missing_receipt');
        // 服务端校验通过后才 finishTransaction（防伪造/防掉单）
        const result = await verifyReceipt(receipt, purchase.productId);
        await RNIap.finishTransaction({ purchase, isConsumable: false });
        resolve(result);
      } catch (e) {
        reject(e);
      }
    });

    errorSub = RNIap.purchaseErrorListener((error) => {
      cleanup();
      reject(error);
    });

    request().catch((e) => {
      cleanup();
      reject(e);
    });
  });
}

/**
 * 发起订阅购买（requestSubscription → 校验 → finishTransaction）。
 */
export function purchaseSubscription(
  sku: string = DEFAULT_SKU,
): Promise<ReceiptVerifyResult | PurchaseTimeoutPending> {
  return purchaseAndVerify(sku, () => RNIap.requestSubscription({ sku }));
}

/**
 * 发起 Life Archive 一次性买断（requestPurchase → 校验 → finishTransaction）。
 *
 * 非消耗品无续订周期，校验响应 kind='entitlement'，tier 保持用户当前订阅档位。
 */
export function purchaseLifeArchive(
  sku: LifeArchiveSku,
): Promise<ReceiptVerifyResult | PurchaseTimeoutPending> {
  return purchaseAndVerify(sku, () => RNIap.requestPurchase({ sku }));
}

/**
 * 恢复购买（App Store 合规必需）。
 *
 * 拉取全部可恢复交易，逐笔回传后端校验恢复权益——同一函数同时覆盖订阅与
 * 一次性买断（后端 /iap/verify 按收据内交易类型分流）。
 */
export async function restorePurchases(): Promise<{
  restored: boolean;
  entitlementRestored: boolean;
}> {
  if (!(await initIAP())) {
    throw new Error('iap_unavailable');
  }
  // 超时兜底：原生侧无响应时 reject，避免调用方 busy 状态永久卡死
  const purchases = await withTimeout(RNIap.getAvailablePurchases(), IAP_TIMEOUT_MS);
  let restored = false;
  let entitlementRestored = false;
  for (const purchase of purchases) {
    if (!purchase.transactionReceipt) continue;
    try {
      const result = await verifyReceipt(purchase.transactionReceipt, purchase.productId);
      if (result.valid) {
        restored = true;
        if (result.kind === 'entitlement') entitlementRestored = true;
      }
      await RNIap.finishTransaction({ purchase, isConsumable: false });
    } catch {
      // 单笔失败不阻塞其他交易恢复
    }
  }
  return { restored, entitlementRestored };
}

/** 判断错误是否为用户主动取消（取消不视为失败，不弹错误提示）。 */
export function isUserCancelled(e: unknown): boolean {
  const code = (e as { code?: string })?.code ?? '';
  return code === 'E_USER_CANCELLED' || code === 'E_USER_CANCELED';
}
