/**
 * 推送注册链路（P1-F01）。
 *
 * Token 格式核实结论（以服务端为准）：
 *   后端推送服务（backend/services/push/service.py）是自建 APNs/FCM 直发——
 *   send_apns 直接向 APNs provider API（api.push.apple.com/3/device/{device_token}）
 *   按原生 device token 寻址设备，FCM 同理（HTTP v1 message.token）。
 *   因此这里必须用 getDevicePushTokenAsync 的原生 APNs/FCM token，
 *   而非 getExpoPushTokenAsync 的 ExponentPushToken（后者只能发给 Expo 推送网关，
 *   后端不使用 Expo 网关，发了也送达不到）。后端 user_service 将 ios→apns / android→fcm 落库。
 *
 * 静默跳过（不抛错、不阻塞主流程）：模拟器 / Expo Go / 权限被拒 / 游客态。
 * 登出不注销 token：设备卸载后 APNs 返回 410，后端自清（见 send_apns）。
 */
import { Alert, Linking, Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import i18n from '../i18n';
import { isLoggedIn, registerPushToken } from './api';
import { aiMessagesRepo } from './db/aiMessagesRepository';

// App 前台不弹通知横幅（用户已在 App 内，主动消息不打断当前体验）
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: false,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

/** 用户当场拒绝权限后，按项目权限引导惯例弹窗引导去系统设置（iOS: Linking.openSettings）。 */
function showPermissionGuide(): void {
  Alert.alert(
    i18n.t('push.permissionTitle'),
    i18n.t('push.permissionMessage'),
    [
      { text: i18n.t('common.cancel'), style: 'cancel' },
      { text: i18n.t('push.openSettings'), onPress: () => void Linking.openSettings() },
    ],
  );
}

async function uploadToken(token: string): Promise<void> {
  await registerPushToken(token, Platform.OS === 'ios' ? 'ios' : 'android');
}

/**
 * 请求通知权限并把原生 device token 上报后端（fire-and-forget，绝不抛错）。
 *
 * 时机：App 冷启动首帧后（仅已登录）+ AuthContext.login 登录成功后补报；
 * token 轮换由 subscribePushTokenChanges 重新上报。
 */
export async function registerForPushNotifications(): Promise<void> {
  try {
    // 游客守卫：未登录不上报（与时区上报同一模式）
    if (!isLoggedIn()) return;

    const existing = await Notifications.getPermissionsAsync();
    if (!existing.granted) {
      if (!existing.canAskAgain) {
        // 曾被拒绝且系统不再弹窗：静默跳过，不每次启动打扰
        return;
      }
      const req = await Notifications.requestPermissionsAsync();
      if (!req.granted) {
        showPermissionGuide();
        return;
      }
    }

    // 原生 APNs/FCM device token（见文件头核实说明）；
    // 模拟器 / Expo Go / 无 Google Play 服务时该调用会抛错 → 外层 catch 静默跳过
    const token = await Notifications.getDevicePushTokenAsync();
    if (!token?.data) return;
    await uploadToken(token.data);
  } catch {
    // 静默降级：推送注册失败不影响任何主流程
  }
}

/**
 * 监听系统推送 token 轮换（APNs 会不定期更换 device token），变化时重新上报后端。
 * 返回 cleanup（App.tsx 卸载时移除监听）。
 */
export function subscribePushTokenChanges(): () => void {
  try {
    const sub = Notifications.addPushTokenListener((token) => {
      if (!isLoggedIn() || !token?.data) return;
      uploadToken(token.data).catch(() => {});
    });
    return () => sub.remove();
  } catch {
    return () => {};
  }
}

/** 主动消息推送 payload 契约（后端 cron_service 下发 data={"type", "message_id"}）。 */
interface ProactivePushData {
  type?: string;
  message_id?: string;
  title?: string;
  body?: string;
}

/** 通知对象里取自定义 data + alert 文案（iOS 顶层 "data" 键 / Android remote message data 归一）。 */
function extractPushData(notification: Notifications.Notification): ProactivePushData {
  const raw = notification?.request?.content?.data;
  const base: ProactivePushData =
    raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as ProactivePushData) : {};
  const alert = notification?.request?.content;
  return { ...base, title: alert?.title ?? '', body: alert?.body ?? '' };
}

/**
 * 这条推送属不属于消息中心（带 message_id 的主动消息 / 里程碑）。
 * 落库与「点开后跳哪」共用同一判据：跳过去必须有东西可看——只有真落进 ai_messages 的那些
 * 才把用户带到消息中心，别的推送（未登录、系统/营销类、缺 id）点了不跳转，比跳进空列表诚实。
 */
function isMessageCenterPush(data: ProactivePushData): boolean {
  const { type, message_id: messageId } = data;
  return !!messageId && (type === 'proactive' || type === 'milestone');
}

/** 推送落库：带 message_id 的主动消息/里程碑推送写入本地 ai_messages（消息中心离线可见）。 */
function persistProactivePush(data: ProactivePushData, receivedAt?: string): void {
  if (!isLoggedIn()) return;
  if (!isMessageCenterPush(data)) return;
  const { message_id: messageId, title, body } = data;
  try {
    // 推送 body 是包装文案，非消息全文；后续 /chat/history 同步按同 id 覆盖为全文
    aiMessagesRepo.upsertFromPush(messageId!, title ?? '', body ?? '', receivedAt).catch(() => {});
  } catch {
    // 落库失败不影响推送主流程
  }
}

/**
 * 主动消息推送落库监听（消息中心数据源之一）：
 * - 前台收到 / 后台点击通知：监听器实时落库
 * - 冷启动由通知拉起：getLastNotificationResponseAsync 兜底（监听器注册前已错过事件）
 * 全量历史仍以 /chat/history 同步为准（ReflectScreen 聚焦时拉取覆盖）。
 *
 * `onTap`（D-G7）：用户**点开**通知时的回调，由入口（frontend/App.tsx）传导航动作。
 * 服务层不 import 导航容器——反向依赖入口文件会长出第二个导航真源。
 * 只有两支「点击」会回调：后台 response 与冷启动兜底；
 * `addNotificationReceivedListener` 是 App 在前台收到，不是用户点击，正在用时被弹走界面是反的。
 * 顺序固定为**先落库、后回调**，且回调异常一律吞掉：导航失败不能把消息弄丢。
 * 返回 cleanup（App.tsx 卸载时移除）。
 */
export function subscribeProactiveMessageSync(
  onTap?: (data: ProactivePushData) => void,
): () => void {
  const onOpen = (notification: Notifications.Notification): void => {
    const data = extractPushData(notification);
    persistProactivePush(data);
    if (!onTap || !isLoggedIn() || !isMessageCenterPush(data)) return;
    try {
      onTap(data);
    } catch {
      // 静默：navRef 未 ready / 路由名不存在都不该影响已经落库的消息
    }
  };

  try {
    const subs = [
      Notifications.addNotificationReceivedListener((n) =>
        persistProactivePush(extractPushData(n)),
      ),
      Notifications.addNotificationResponseReceivedListener((resp) => onOpen(resp.notification)),
    ];
    // 冷启动兜底：App 未运行时点开通知，上面两个监听器注册前事件已发生
    Notifications.getLastNotificationResponseAsync()
      .then((resp) => {
        if (resp?.notification) onOpen(resp.notification);
      })
      .catch(() => {});
    return () => subs.forEach((s) => s.remove());
  } catch {
    return () => {};
  }
}
