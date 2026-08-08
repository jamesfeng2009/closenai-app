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
