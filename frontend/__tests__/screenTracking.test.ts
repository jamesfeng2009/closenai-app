/**
 * H2 页面埋点的纯函数守卫（node 环境，不渲染组件）。
 * 断言的是「取哪个路由名」与「什么时候算一次页面浏览」两条口径。
 */
import { getActiveRouteName, screenViewProperties, type NavStateLike } from '../src/services/screenTracking';

describe('getActiveRouteName：取叶子路由', () => {
  it('扁平栈取 index 指向的路由', () => {
    const state: NavStateLike = { index: 1, routes: [{ name: 'Tabs' }, { name: 'Paywall' }] };
    expect(getActiveRouteName(state)).toBe('Paywall');
  });

  it('下钻嵌套 Tab，取到具体 Tab 而不是容器名', () => {
    const state: NavStateLike = {
      index: 0,
      routes: [
        {
          name: 'Tabs',
          state: {
            index: 2,
            routes: [
              { name: 'Growth' },
              { name: 'Explore' },
              { name: 'Reflect' },
              { name: 'Profile' },
            ],
          },
        },
        { name: 'Auth' },
      ],
    };
    expect(getActiveRouteName(state)).toBe('Reflect');
  });

  it('index 缺失或越界时退化为最后一条（RN 栈顶语义）', () => {
    expect(getActiveRouteName({ routes: [{ name: 'A' }, { name: 'B' }] })).toBe('B');
    expect(getActiveRouteName({ index: 9, routes: [{ name: 'A' }, { name: 'B' }] })).toBe('B');
    expect(getActiveRouteName({ index: -1, routes: [{ name: 'A' }, { name: 'B' }] })).toBe('B');
  });

  it('空状态返回 null（未就绪时不上报）', () => {
    expect(getActiveRouteName(undefined)).toBeNull();
    expect(getActiveRouteName(null)).toBeNull();
    expect(getActiveRouteName({})).toBeNull();
    expect(getActiveRouteName({ routes: [] })).toBeNull();
  });

  it('嵌套容器自身没有可用叶子时回退容器名', () => {
    expect(getActiveRouteName({ index: 0, routes: [{ name: 'Tabs', state: { routes: [] } }] })).toBe('Tabs');
  });
});

describe('screenViewProperties：浏览事件口径', () => {
  it('首次进入只有 screen', () => {
    expect(screenViewProperties(null, 'Growth')).toEqual({ screen: 'Growth' });
  });

  it('站内跳转带 screen_from', () => {
    expect(screenViewProperties('Growth', 'Paywall')).toEqual({
      screen: 'Paywall',
      screen_from: 'Growth',
    });
  });

  it('同名不重复上报；未知当前页不上报', () => {
    expect(screenViewProperties('Growth', 'Growth')).toBeNull();
    expect(screenViewProperties('Growth', null)).toBeNull();
    expect(screenViewProperties(null, null)).toBeNull();
  });

  it('属性键只允许出现在分析白名单内', () => {
    const props = screenViewProperties('Growth', 'Insights');
    expect(Object.keys(props ?? {})).toEqual(
      expect.arrayContaining(['screen', 'screen_from'])
    );
    expect(Object.keys(props ?? {}).every((k) => ['screen', 'screen_from'].includes(k))).toBe(true);
  });
});
