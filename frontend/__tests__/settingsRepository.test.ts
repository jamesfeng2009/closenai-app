import { createTestDb } from './helpers/testDb';
import { SettingsRepository } from '../src/services/db/settingsRepository';

describe('SettingsRepository (app_settings)', () => {
  it('set / get 基础读写', async () => {
    const db = createTestDb();
    const repo = new SettingsRepository(db);
    await repo.set('theme', 'dark');
    expect(await repo.get('theme')).toBe('dark');
    db.closeSync();
  });

  it('setJson / getJson 存取 JSON 值', async () => {
    const db = createTestDb();
    const repo = new SettingsRepository(db);
    const obj = { streak: 3, emoji: '😄' };
    await repo.setJson('dashboard', obj);
    expect(await repo.getJson<typeof obj>('dashboard')).toEqual(obj);
    db.closeSync();
  });

  it('覆盖已有 key 不产生重复行', async () => {
    const db = createTestDb();
    const repo = new SettingsRepository(db);
    await repo.set('theme', 'dark');
    await repo.set('theme', 'light');
    const rows = db.getAllSync<{ key: string }>(
      "SELECT key FROM app_settings WHERE key = 'theme'"
    );
    expect(rows).toHaveLength(1);
    expect(await repo.get('theme')).toBe('light');
    db.closeSync();
  });

  it('不存在的 key 返回 null，空值 getJson 返回 null', async () => {
    const db = createTestDb();
    const repo = new SettingsRepository(db);
    expect(await repo.get('no_such_key')).toBeNull();
    await repo.set('empty', '');
    expect(await repo.getJson('empty')).toBeNull();
    db.closeSync();
  });
});
