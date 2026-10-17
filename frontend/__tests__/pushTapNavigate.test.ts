/**
 * 批 2 · D-G7（点开通知不跳转）的行为闸（2026-10-17）。
 *
 * 缺陷原状：`push.ts` 的三个时机（前台收到 / 后台点击 / 冷启动兜底）只 `persistProactivePush`，
 * 一个都不跳转 ⇒ 用户点开主动消息，App 只是打开首页，消息得自己再点进消息中心找。
 * 同文件 `frontend/App.tsx:118` 早有 `useNavigationContainerRef()`、`:303` 早有 `name="Messages"`，
 * 缺的只是回调，所以本批不新建导航通道，只把「点击」这一支接上。
 *
 * 为什么这是行为闸而不是接线闸：本条缺陷的判据全在**哪一支回调、什么顺序、失败时丢不丢消息**，
 * 读源码文本一条都证不了——把 `addNotificationReceivedListener` 也接上跳转，源码里同样是
 * 「两支监听都调 onTap」。手法沿用 sessionReset.test.ts：jest.mock 造依赖，mock 之后再 require。
 */
import fs from 'fs';
import path from 'path';

const state = {
  loggedIn: true,
  received: [] as Array<(n: any) => void>,
  response: [] as Array<(r: any) => void>,
  removed: [] as string[],
  seq: [] as string[],
  upserts: [] as Array<{ id: string; title: string; body: string }>,
  lastResponse: null as any,
};

function makeNotification(data: Record<string, unknown>, title = '今晚想聊点什么', body = '我在') {
  return { request: { content: { data, title, body } } };
}

function responseOf(data: Record<string, unknown>) {
  return { notification: makeNotification(data) };
}

function subscription(kind: 'received' | 'response') {
  return {
    remove: jest.fn(() => {
      state.removed.push(kind);
    }),
  };
}

jest.mock('react-native', () => ({
  Alert: { alert: jest.fn() },
  Linking: { openSettings: jest.fn(async () => undefined) },
  Platform: { OS: 'ios' },
}));

jest.mock('../src/i18n', () => ({ __esModule: true, default: { t: (k: string) => k } }));

jest.mock('../src/services/api', () => ({
  isLoggedIn: () => state.loggedIn,
  registerPushToken: jest.fn(async () => undefined),
}));

jest.mock('../src/services/db/aiMessagesRepository', () => ({
  aiMessagesRepo: {
    upsertFromPush: jest.fn(async (id: string, title: string, body: string) => {
      state.seq.push('persist');
      state.upserts.push({ id, title, body });
    }),
  },
}));

jest.mock('expo-notifications', () => ({
  setNotificationHandler: jest.fn(),
  addNotificationReceivedListener: jest.fn((cb: (n: any) => void) => {
    state.received.push(cb);
    return subscription('received');
  }),
  addNotificationResponseReceivedListener: jest.fn((cb: (r: any) => void) => {
    state.response.push(cb);
    return subscription('response');
  }),
  getLastNotificationResponseAsync: jest.fn(() => Promise.resolve(state.lastResponse)),
}));

// 必须在上面那批 jest.mock 之后才 require（ts-jest 不提升 jest.mock）
import { subscribeProactiveMessageSync } from '../src/services/push';

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  state.loggedIn = true;
  state.received = [];
  state.response = [];
  state.removed = [];
  state.seq = [];
  state.upserts = [];
  state.lastResponse = null;
});

const PROACTIVE = { type: 'proactive', message_id: 'm-1' };
const MILESTONE = { type: 'milestone', message_id: 'm-2' };

describe('① 三支时机里，只有「点击」两支回调（D-G7-2 的正题）', () => {
  it('后台点开通知：落库 + 回调，且回调拿到的是这条推送的 data', () => {
    subscribeProactiveMessageSync(jest.fn());
    expect(state.response).toHaveLength(1);
    state.response[0](responseOf(PROACTIVE));
    expect(state.upserts).toEqual([{ id: 'm-1', title: '今晚想聊点什么', body: '我在' }]);
  });

  it('冷启动由通知拉起：同样落库 + 回调（监听器注册前事件已发生，靠 getLastNotificationResponseAsync 兜底）', async () => {
    const onTap = jest.fn();
    state.lastResponse = responseOf(MILESTONE);
    subscribeProactiveMessageSync(onTap);
    await flush();
    expect(state.upserts.map((u) => u.id)).toEqual(['m-2']);
    expect(onTap).toHaveBeenCalledTimes(1);
    expect(onTap.mock.calls[0][0]).toMatchObject({ type: 'milestone', message_id: 'm-2' });
  });

  it('前台收到（不是用户点击）：落库，但绝不回调跳转', () => {
    const onTap = jest.fn();
    subscribeProactiveMessageSync(onTap);
    expect(state.received).toHaveLength(1);
    state.received[0](makeNotification(PROACTIVE));
    expect(state.upserts).toHaveLength(1);
    expect(onTap).not.toHaveBeenCalled();
  });

  it('跳转排在落库之后：导航失败不许把消息弄丢（D-G7-3）', () => {
    subscribeProactiveMessageSync(() => {
      state.seq.push('navigate');
    });
    state.response[0](responseOf(PROACTIVE));
    expect(state.seq).toEqual(['persist', 'navigate']);
  });
});

describe('② 回调异常与不该跳的那些情况', () => {
  it('onTap 抛错被吞掉：监听器不外泄异常，落库仍在', () => {
    const onTap = jest.fn(() => {
      throw new Error('navRef 未 ready');
    });
    subscribeProactiveMessageSync(onTap);
    expect(() => state.response[0](responseOf(PROACTIVE))).not.toThrow();
    expect(state.upserts).toHaveLength(1);
    expect(onTap).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['缺 message_id', { type: 'proactive' }],
    ['非消息中心类型', { type: 'system', message_id: 'm-3' }],
    ['空 payload', {}],
  ])('%s：既不落库也不跳转（跳过去只会是一条空列表，比不跳更骗人）', (_name, data) => {
    const onTap = jest.fn();
    subscribeProactiveMessageSync(onTap);
    state.response[0](responseOf(data));
    expect(state.upserts).toEqual([]);
    expect(onTap).not.toHaveBeenCalled();
  });

  it('游客态：不落库（原生守卫不变），也不跳转', () => {
    const onTap = jest.fn();
    state.loggedIn = false;
    subscribeProactiveMessageSync(onTap);
    state.response[0](responseOf(PROACTIVE));
    expect(state.upserts).toEqual([]);
    expect(onTap).not.toHaveBeenCalled();
  });

  it('卸载时两支监听都移除', () => {
    const cleanup = subscribeProactiveMessageSync(jest.fn());
    cleanup();
    expect(state.removed).toEqual(['received', 'response']);
  });
});

describe('③ 接线形状：导航真源只有一个（入口），服务层不反向依赖', () => {
  const ROOT = path.join(__dirname, '..');
  const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

  it('push.ts 不 import 任何导航容器', () => {
    const src = read('src/services/push.ts');
    expect(src).not.toMatch(/@react-navigation/);
    expect(src).not.toMatch(/\bnavigate\(/);
  });

  it('App.tsx 把跳转作为回调传进来，且先判 isReady、异常静默', () => {
    const src = read('App.tsx');
    const call = /const proactiveSyncCleanup = subscribeProactiveMessageSync\(\(\) => \{([\s\S]*?)\n {4}\}\);/.exec(src);
    expect(call).not.toBeNull();
    expect(call![1]).toContain("if (!navRef.isReady()) return;");
    expect(call![1]).toContain("navRef.navigate('Messages')");
    expect(call![1]).toMatch(/catch \{/);
  });

  it("跳转目标是已注册的那条栈路由（名字写错=静默失败，所以钉在源码上）", () => {
    const src = read('App.tsx');
    // 目标名从**调用点**取，不在这里写死 'Messages'：写死等于和上面那条重复，
    // 而把 'Messages' 少打一个 s 时 navigate 抛错会被下面的 catch 静默吞掉——
    // 只有「调用点字面量 ∈ 注册表」这一条判据拦得住这种坏法。
    const call = /navRef\.navigate\('([A-Za-z]+)'\)/.exec(src);
    expect(call).not.toBeNull();
    const registered = [...src.matchAll(/<Stack\.Screen\s+name="([A-Za-z]+)"/g)].map((m) => m[1]);
    expect(registered.length).toBeGreaterThan(0);
    expect(registered).toContain(call![1]);
  });
});
