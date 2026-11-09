import fs from 'fs';
import path from 'path';

/**
 * P2 #15：个人页的配额分母来自接口，不再写死免费额度。
 *
 * 判据放在屏源码上：这一格是纯展示，后端那条用例（test_user_service）已经证明
 * 接口会按 tier 下发，这里只防止前端再把分母兜回 5（Pro 显示 8/5）。
 */
const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

describe('个人页配额分母', () => {
  it('分母取接口字段，不再兜底成 5', () => {
    const src = read('src/screens/ProfileScreen.tsx');
    expect(src).toContain('daily_quota_total ?? 0');
    expect(src).not.toMatch(/daily_quota_total\s*\|\|\s*5/);
    // 正向证人：已用量仍在同一行展示，防止整行删掉也算绿。
    expect(src).toContain('daily_quota_used');
  });
});
