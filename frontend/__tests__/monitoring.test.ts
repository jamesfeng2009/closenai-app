/**
 * H1 崩溃监控的纯逻辑守卫（node 环境，不加载真实 SDK / react-native）。
 *
 * 断言的是三条隐私与门控不变量，而不是 Sentry 的行为：
 * 1. 没配 DSN 时整个模块必须是 no-op（不 init、不上报）。
 * 2. 脱敏函数必须剥掉可能带用户原文的字段。
 * 3. 源码里不得出现 setUser / sendDefaultPii=true：崩溃报告与用户身份不得可关联。
 */
import * as fs from 'fs';
import * as path from 'path';

jest.mock('@sentry/react-native', () => ({
  init: jest.fn(),
  captureException: jest.fn(),
  flush: jest.fn(() => Promise.resolve(true)),
}));

import * as Sentry from '@sentry/react-native';
import {
  SENTRY_DSN,
  isMonitoringConfigured,
  stripUrlQuery,
  scrubBreadcrumb,
  scrubEvent,
  startMonitoring,
  reportError,
} from '../src/services/monitoring';

const mockInit = Sentry.init as jest.Mock;
const mockCapture = Sentry.captureException as jest.Mock;

/** 剥掉块注释与行注释，避免源码守卫被自己的文档注释骗过。 */
function codeOnly(file: string): string {
  return fs
    .readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('monitoring：DSN 门控', () => {
  beforeEach(() => jest.clearAllMocks());

  it('默认未配置 DSN', () => {
    expect(SENTRY_DSN).toBe('');
    expect(isMonitoringConfigured()).toBe(false);
  });

  it('有 DSN 才算已配置（空白串不算）', () => {
    expect(isMonitoringConfigured('https://abc@o1.ingest.sentry.io/1')).toBe(true);
    expect(isMonitoringConfigured('   ')).toBe(false);
  });

  it('未配置时 startMonitoring / reportError 全静默', () => {
    startMonitoring();
    reportError(new Error('boom'), { stage: 'chat' });
    expect(mockInit).not.toHaveBeenCalled();
    expect(mockCapture).not.toHaveBeenCalled();
  });
});

describe('monitoring：URL 与 breadcrumb 脱敏', () => {
  it('stripUrlQuery 去掉 query 与 fragment，保留路径', () => {
    expect(stripUrlQuery('https://api/x/messages?limit=20&q=我的焦虑')).toBe('https://api/x/messages');
    expect(stripUrlQuery('https://api/x/messages#frag')).toBe('https://api/x/messages');
    expect(stripUrlQuery('https://api/x/messages')).toBe('https://api/x/messages');
  });

  it('console breadcrumb 整条丢弃（console 可能打印对话/反思原文）', () => {
    expect(scrubBreadcrumb({ category: 'console', message: '用户输入：……' })).toBeNull();
  });

  it('http breadcrumb 去 query、删请求体，保留 method/status/url 路径', () => {
    const out = scrubBreadcrumb({
      category: 'http',
      data: {
        url: 'https://api/y/chat/conv-1?text=私密内容',
        method: 'POST',
        status_code: 500,
        request_body: '{"content":"私密内容"}',
        json_data: { content: '私密内容' },
      },
    });
    expect(out).toEqual({
      category: 'http',
      data: { url: 'https://api/y/chat/conv-1', method: 'POST', status_code: 500 },
    });
  });

  it('navigation breadcrumb 原样放过（否则报告没有诊断价值）', () => {
    const bc = { category: 'navigation', data: { from: '/tabs', to: '/paywall' } };
    expect(scrubBreadcrumb(bc)).toBe(bc);
  });
});

describe('monitoring：事件级脱敏', () => {
  it('清空 extra 与 user，并过滤 breadcrumbs 里的 console', () => {
    const out = scrubEvent({
      message: 'boom',
      extra: { reflection_text: '今天我很焦虑' },
      user: { id: 'u-1', email: 'a@b.c' },
      breadcrumbs: [
        { category: 'console', message: '原文泄露' },
        { category: 'ui.click' },
      ],
    });
    expect(out.message).toBe('boom');
    expect(out.extra).toBeUndefined();
    expect(out.user).toBeUndefined();
    expect(out.breadcrumbs).toEqual([{ category: 'ui.click' }]);
  });

  it('全部 breadcrumb 被丢弃时不留下空数组', () => {
    const out = scrubEvent({ breadcrumbs: [{ category: 'console' }] });
    expect(out.breadcrumbs).toBeUndefined();
  });
});

describe('monitoring：隐私不变量（源码守卫）', () => {
  const src = codeOnly(path.join(__dirname, '..', 'src', 'services', 'monitoring.ts'));

  it('不调用 setUser / 不开 sendDefaultPii', () => {
    expect(src).not.toMatch(/\bsetUser\s*\(/);
    expect(src).not.toMatch(/sendDefaultPii\s*:\s*true/);
  });

  it('init 选项全部为 SDK 真实字段（防止拼错字段变成静默 no-op）', () => {
    for (const key of ['dsn', 'maxBreadcrumbs', 'beforeSend', 'beforeBreadcrumb', 'enableNativeNagger']) {
      expect(src).toContain(`${key}:`);
    }
  });
});
