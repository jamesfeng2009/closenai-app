---
name: "closenai-frontend"
description: "ClosenAI React Native/Expo iOS app: dark theme UI, Growth Dashboard, mood check-in, personality profile, SSE chat, push notifications, IAP purchase flow. Invoke when working on mobile UI, screens, components, or Expo configuration."
---

# ClosenAI Frontend Development

React Native + Expo iOS App 前端开发规范。目标平台：iOS（App Store 上架）。

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Framework | React Native + Expo (managed workflow) |
| Language | TypeScript |
| Target OS | iOS (iPhone, App Store) |
| Navigation | State-based tab switching (4 tabs, no router library) |
| State | React useState + useEffect |
| Push | expo-notifications |
| IAP | react-native-iap |
| Styling | StyleSheet.create() (inline at bottom of each component file) |
| Splash | expo-splash-screen |

## 🔴 文件位置约束（必须遵守）

**所有前端代码必须放在 `frontend/` 目录下，严禁放在项目根目录或其他位置。**

**禁止行为：**
- ❌ 在项目根目录创建 `.tsx` / `.ts` 文件
- ❌ 在 `backend/` 里写前端代码
- ❌ 在 `frontend/` 里写后端代码

## Project Structure（实际目录）

```
frontend/
├── App.tsx                       # 入口：4 Tab 切换 + SafeAreaView
├── app.json                      # Expo 配置（iOS 专用）
├── package.json                  # 依赖
├── tsconfig.json                 # TypeScript 配置
├── src/
│   ├── screens/                  # 全屏页面组件
│   │   ├── HomeScreen.tsx        # 成长首页（Growth Dashboard）
│   │   ├── ExploreScreen.tsx     # 探索页（性格画像 + 冷启动）
│   │   ├── ReflectScreen.tsx     # Reflect 对话页（SSE + 情绪签到）
│   │   └── ProfileScreen.tsx     # 我的/设置页
│   ├── components/               # 可复用 UI 组件
│   │   ├── StreakBadge.tsx       # Streak 徽章
│   │   ├── MoodPicker.tsx        # 情绪选择器
│   │   ├── GrowthMetrics.tsx     # 成长指标卡片
│   │   ├── TimelineHeatmap.tsx   # 热力图
│   │   ├── PersonalityCard.tsx   # 性格画像卡片
│   │   ├── ChatBubble.tsx        # 聊天气泡
│   │   ├── QuotaModal.tsx        # 配额用完弹窗
│   │   └── ColdStartProgress.tsx # 冷启动进度条
│   ├── services/                 # 业务逻辑
│   │   ├── api.ts                # 后端 API 调用
│   │   ├── iap.ts                # IAP 封装
│   │   └── push.ts               # 推送通知封装
│   ├── theme/                    # 设计系统
│   │   └── colors.ts             # 26 个 Design Tokens（TypeScript 常量）
│   └── types/                    # 类型定义
│       └── index.ts              # 16 个 TypeScript 接口/类型
```

### 新增文件时的位置规则

| 文件类型 | 必须放在 |
|---------|---------|
| 页面级组件 | `frontend/src/screens/` |
| 可复用 UI 组件（Button/Card/Modal） | `frontend/src/components/` |
| API 调用、推送、IAP 逻辑 | `frontend/src/services/` |
| 主题/颜色/字体常量 | `frontend/src/theme/` |
| TypeScript 类型/接口定义 | `frontend/src/types/` |
| 测试文件 | `frontend/__tests__/` |
| 静态资源（图片/字体） | `frontend/assets/` |

## iOS 特有约束（必须遵守）

### SafeAreaView

所有页面必须包裹在 `SafeAreaView` 中，适配刘海屏和 Home Indicator：

```typescript
import { SafeAreaView } from 'react-native';

// 页面根组件
<SafeAreaView style={{ flex: 1, backgroundColor: Colors.bgPrimary }}>
  {/* 页面内容 */}
</SafeAreaView>
```

### 状态栏

- 状态栏高度约 50px（含刘海区域）
- 页面顶部 padding 需预留：`paddingTop: 50`（有刘海）/ `paddingTop: 20`（无刘海）
- 使用 `expo-status-bar` 设置白色文字

### 底部 Tab Bar

- 高度 80px（含 Home Indicator 安全区域）
- 背景 `rgba(13, 13, 18, 0.95)` 毛玻璃效果
- 4 个 Tab：🌱 成长 | 🧭 探索 | 📝 Reflect | 👤 我的
- 默认 Tab = 成长（第一位）

### 键盘避让

聊天页面需使用 `KeyboardAvoidingView`：

```typescript
import { KeyboardAvoidingView, Platform } from 'react-native';

<KeyboardAvoidingView
  style={{ flex: 1 }}
  behavior={Platform.OS === 'ios' ? 'padding' : undefined}
  keyboardVerticalOffset={90}
>
```

## Design System

### Color Tokens（TypeScript 常量）

```typescript
// frontend/src/theme/colors.ts
export const Colors = {
  // Gold
  gold400: '#FFD700',
  gold500: '#D4AF37',
  gold600: '#B8960C',

  // Accent (Purple)
  accent500: '#6366F1',
  accent600: '#4F46E5',
  accent800: '#1E1B48',

  // Neon
  neonPink: '#E879F9',
  neonPurple: '#C084FC',
  neonDeep: '#7C3AED',

  // Backgrounds
  bgPrimary: '#050508',
  bgSecondary: '#0D0D12',
  bgTertiary: '#16161D',
  bgCard: '#1E1B48',
  bgElevated: '#1F0F22',

  // Text
  textPrimary: '#FFFFFF',
  textSecondary: '#A1A1AA',
  textTertiary: '#71717A',
  textGold: '#FFD700',
  textMuted: '#4A4A5A',

  // Semantic
  success: '#10B981',
  warning: '#F59E0B',
  error: '#EF4444',
};
```

**注意**：React Native 不支持 CSS `linear-gradient`。渐变效果需使用 `expo-linear-gradient`：

```typescript
import { LinearGradient } from 'expo-linear-gradient';

<LinearGradient
  colors={['#8B05CF', '#8B5CF6', '#EC4899']}
  start={{ x: 0, y: 0 }}
  end={{ x: 1, y: 1 }}
  style={styles.gradient}
/>
```

### 样式规范

- 所有样式用 `StyleSheet.create()` 写在组件文件底部
- 不允许内联样式对象（除非动态计算的值）
- 不允许使用任何 CSS 文件或 CSS-in-JS 库

```typescript
// ✅ 正确
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  title: { fontSize: 18, fontWeight: '700', color: Colors.gold400 },
});

// ❌ 错误
<div style={{ color: 'var(--gold-400)' }}>  // CSS 变量在 RN 中无效
```

## 4 个 Tab 页面

### 1. 🌱 成长首页（HomeScreen）

按 UI 原型 `screen-home` 实现。**审核安全首页**：审核员第一眼看到的是成长仪表盘，不是 AI 聊天。

组件层级：
- 日期 + 金色问候语
- Streak 条（🔥 天数 + 等级 + 聊天次数）
- Today's Reflection（5 个情绪 emoji 选择）
- Growth This Week（6 维度指标卡片 + 14 天迷你热力图）
- 本周 Insight 卡片
- 昨天记录延续卡片
- 性格画像入口

新用户（Day 1）显示 `screen-home-new` 空状态引导。

### 2. 🧭 探索页（ExploreScreen）

按 UI 原型 `screen-explore` 实现。

- 性格画像主体卡片（70% 视觉占比，金色渐变）
- 冷启动进度条（新用户 Day 1-6，5 阶段：破冰→观察→探索→揭示→运用）
- 更多探索（本命星盘、性格名片）

### 3. 📝 Reflect 对话页（ReflectScreen）

按 UI 原型 `screen-chat` 实现。

- Streak badge + 等级指示器
- 情绪签到提醒条（今日未签到时显示）
- 快捷签到面板（6 个 emoji 选择）
- AI 记忆延续消息（引用昨日对话）
- AI 预填 Reflection 草稿（可编辑）
- SSE 流式对话消息列表
- 输入框 + 发送按钮
- 所有 AI 消息底部标注"AI 生成"（合规）

### 4. 👤 我的/设置页（ProfileScreen）

按 UI 原型 `screen-profile` 实现。

- 头像 + 姓名 + 性格类型
- AI 与记忆（记忆管理、对话风格、主动消息时间）
- 订阅（升级 Pro、使用情况）
- 其他设置（语言、隐私、帮助）
- 关于 AI（合规：免责声明、模型来源、数据共享）
- 退出登录（红色）

## Tab 导航实现

不使用第三方路由库，用 `useState` 管理当前 Tab：

```typescript
// App.tsx
import { SafeAreaView, View, TouchableOpacity, Text, StyleSheet } from 'react-native';

type TabName = 'home' | 'explore' | 'reflect' | 'profile';

const TABS: { name: TabName; icon: string; label: string }[] = [
  { name: 'home', icon: '🌱', label: '成长' },
  { name: 'explore', icon: '🧭', label: '探索' },
  { name: 'reflect', icon: '📝', label: 'Reflect' },
  { name: 'profile', icon: '👤', label: '我的' },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<TabName>('home');

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: Colors.bgPrimary }}>
      {activeTab === 'home' && <HomeScreen />}
      {activeTab === 'explore' && <ExploreScreen />}
      {activeTab === 'reflect' && <ReflectScreen />}
      {activeTab === 'profile' && <ProfileScreen />}

      <View style={styles.tabBar}>
        {TABS.map((tab) => (
          <TouchableOpacity
            key={tab.name}
            style={styles.tabItem}
            onPress={() => setActiveTab(tab.name)}
          >
            <Text style={[styles.tabIcon, activeTab === tab.name && styles.tabActive]}>
              {tab.icon}
            </Text>
            <Text style={[styles.tabLabel, activeTab === tab.name && styles.tabLabelActive]}>
              {tab.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
    </SafeAreaView>
  );
}
```

## Push Notification Handling

iOS 推送需要 `expo-notifications` + APNs 证书：

```typescript
import * as Notifications from 'expo-notifications';

// App 前台不显示通知横幅
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: false,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

// 注册推送 Token
const { status } = await Notifications.requestPermissionsAsync();
if (status !== 'granted') {
  // 引导用户去设置开启通知
}
const token = (await Notifications.getExpoPushTokenAsync()).data;

// 上传到后端
await api.updateUserSettings({ push_token: token });

// 处理通知点击（App 后台/被杀时）
Notifications.addNotificationResponseReceivedListener(response => {
  // 跳转到 Reflect Tab
  setActiveTab('reflect');
});
```

## SSE Chat Implementation

```typescript
// frontend/src/services/api.ts
export async function streamChat(
  request: ChatStreamRequest,
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (error: string) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });

  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const event = JSON.parse(line.slice(6));
        if (event.type === 'token') onToken(event.content);
        if (event.type === 'done') onDone();
        if (event.type === 'error') onError(event.message);
      }
    }
  }
}
```

## IAP Purchase Flow

```typescript
import { purchaseErrorListener, purchaseUpdatedListener } from 'react-native-iap';

// 监听购买结果
purchaseUpdatedListener(async (purchase) => {
  const receipt = purchase.transactionReceipt;
  // 收据发回后端校验
  await api.verifyReceipt({ receipt, productId: purchase.productId });
  // 更新本地订阅状态
});

// 处理购买错误
purchaseErrorListener((error) => {
  if (error.code !== 'E_USER_CANCELLED') {
    alert('购买失败，请重试');
  }
});
```

## UX Principles

1. **审核安全首页** — 首屏是 Growth Dashboard，不是 AI 聊天界面。审核员前 3 秒看到的是成长数据
2. **成长优先语言** — "完成今天的 Reflection" 而非 "和 AI 聊天"
3. **弱化配额感知** — "剩余次数" → "明天再来"（自然表达）
4. **纯暗色主题** — P0 不做亮色模式，Cosmic/mystical 美学
5. **3 秒签到** — 情绪 emoji 一点即完成，每日快速 hook
6. **AI 消息标注** — 所有 AI 生成内容底部标注"AI 生成"（合规）
7. **iOS 原生体验** — SafeAreaView、键盘避让、毛玻璃 Tab Bar、触觉反馈