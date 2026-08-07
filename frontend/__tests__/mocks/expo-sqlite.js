// expo-sqlite 的 Node 测试替身。
// 生产代码经 getDatabase() 打开真实数据库；测试一律注入 __tests__/helpers/testDb.ts
// 创建的 better-sqlite3 库。此处返回无害 stub，仅避免 Repository 模块级单例在 import 时触发真实原生模块。
const noop = () => {};
const emptyRun = () => ({ lastInsertRowId: 0, changes: 0 });

module.exports = {
  openDatabaseSync: () => ({
    execSync: noop,
    runSync: emptyRun,
    getAllSync: () => [],
    getFirstSync: () => null,
    closeSync: noop,
  }),
};
