/**
 * G-D1（工作单批 T-D）：前端每个请求带 X-Trace-Id，值可被服务端原样采用。
 *
 * 手法是本仓两种形状的合体：纯模块（utils/traceId）真 import 真跑；
 * api.ts 用 mock 剥掉原生依赖后**真的走一遍 request()/streamChat()**——
 * 「fetch 收到的 headers」「XHR setRequestHeader」这类判据在源码扫描里是判不出
 * 「函数被调了但头没加上」的，所以这里必须是行为证人。
 * 变异 M-D1（删 getHeaders 里那行）红的就是下面的行为断言，不是文本匹配。
 */

jest.mock('react-native', () => ({
  Platform: { select: (opts: Record<string, string>) => opts.default },
}));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(async () => null),
    setItem: jest.fn(async () => undefined),
    clear: jest.fn(async () => undefined),
  },
}));
jest.mock('expo-localization', () => ({ getLocales: () => [{ languageCode: 'en', regionCode: 'US' }] }));
jest.mock('../src/i18n', () => ({
  __esModule: true,
  default: { language: 'en', t: (key: string) => key },
}));
jest.mock('@sentry/react-native', () => ({
  init: jest.fn(),
  captureException: jest.fn(),
  flush: jest.fn(() => Promise.resolve(true)),
}));

import { newTraceId } from '../src/utils/traceId';
import { getLastTrace, noteLastTrace } from '../src/services/monitoring';
import { ApiError, postAnalyticsBatch, streamChat } from '../src/services/api';

const SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

describe('newTraceId（纯模块真跑）', () => {
  it('形状是严格的 8-4-4-4-12 小写 hex（T-7 拍板形状）', () => {
    for (let i = 0; i < 50; i++) {
      const id = newTraceId();
      expect(id).toMatch(SHAPE);
      expect(id).toHaveLength(36);
    }
  });

  it('两次调用值不同（每请求新生成，不是会话级复用）', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 200; i++) seen.add(newTraceId());
    expect(seen.size).toBe(200);
  });
});

type FakeResponse = {
  ok: boolean;
  status: number;
  headers: { get: (k: string) => string | null };
  json: () => Promise<unknown>;
  arrayBuffer?: () => Promise<ArrayBuffer>;
};

function fakeResponse(init: { status?: number; echo?: string | null; body?: unknown }): FakeResponse {
  const status = init.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => init.echo ?? null },
    json: async () => init.body ?? { code: 200, message: 'ok', data: { relayed: 1, dropped: 0 } },
  };
}

function lastFetchHeaders(fetchMock: jest.Mock): Record<string, string> {
  const calls = fetchMock.mock.calls;
  const init = calls[calls.length - 1][1] as { headers: Record<string, string> };
  return init.headers;
}

describe('G-D1：request() 发出去的 headers 真带合规 trace', () => {
  it('两次调用各带一个合规且互不相同的 X-Trace-Id', async () => {
    const fetchMock = jest.fn(async () => fakeResponse({ echo: 'T-SERVER-ADOPT-1' }));
    (globalThis as Record<string, unknown>).fetch = fetchMock;

    await postAnalyticsBatch([]);
    const first = lastFetchHeaders(fetchMock)['X-Trace-Id'];
    await postAnalyticsBatch([]);
    const second = lastFetchHeaders(fetchMock)['X-Trace-Id'];

    expect(first).toMatch(SHAPE);
    expect(second).toMatch(SHAPE);
    expect(first).not.toBe(second);
    // 「最近一次」被服务端回执值覆盖：报障话术要指服务端认的那个
    expect(getLastTrace()).toBe('T-SERVER-ADOPT-1');
  });

  it('失败路径 ApiError 附带服务端采用的 traceId（用户报障可直接给出）', async () => {
    (globalThis as Record<string, unknown>).fetch = jest.fn(async () =>
      fakeResponse({
        status: 500,
        echo: 'T-TRACE-500',
        body: { code: 50001, message: '服务器内部错误', data: null },
      }),
    );
    expect.assertions(2);
    try {
      await postAnalyticsBatch([]);
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).traceId).toBe('T-TRACE-500');
    }
  });

  it('回执没有头时不造假：traceId 缺席，而不是回落到本地发送值', async () => {
    (globalThis as Record<string, unknown>).fetch = jest.fn(async () =>
      fakeResponse({ status: 502, echo: null, body: 'not json' as unknown as never }),
    );
    try {
      await postAnalyticsBatch([]);
      throw new Error('不该成功');
    } catch (err) {
      if (err instanceof ApiError) expect(err.traceId).toBeUndefined();
      else throw err;
    }
  });
});

describe('G-D1：SSE 的 XHR 面同样带头', () => {
  class FakeXHR {
    headers: Record<string, string> = {};
    responseText = '';
    status = 200;
    timeout = 0;
    onprogress: (() => void) | null = null;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    open() {}
    setRequestHeader(k: string, v: string) {
      this.headers[k] = v;
    }
    send() {}
    abort() {}
  }

  it('streamChat 的 setRequestHeader 收到合规 X-Trace-Id', () => {
    const instances: FakeXHR[] = [];
    (globalThis as Record<string, unknown>).XMLHttpRequest = class extends FakeXHR {
      constructor() {
        super();
        instances.push(this);
      }
    };
    const cancel = streamChat({ messages: [] } as never, () => {}, () => {}, () => {});
    expect(instances).toHaveLength(1);
    const sent = instances[0].headers['X-Trace-Id'];
    expect(sent).toMatch(SHAPE);
    cancel();
  });
});

describe('monitoring 的最近 trace 内存格（T-D 的崩溃关联面）', () => {
  it('只收合形值：含 CR/LF 或超长的候选连内存都不进', () => {
    noteLastTrace('T-OK');
    expect(getLastTrace()).toBe('T-OK');
    noteLastTrace('evil\r\nINJECT');
    expect(getLastTrace()).toBe('T-OK');
    noteLastTrace('x'.repeat(129));
    expect(getLastTrace()).toBe('T-OK');
    noteLastTrace(null);
    expect(getLastTrace()).toBe('T-OK');
  });
});
