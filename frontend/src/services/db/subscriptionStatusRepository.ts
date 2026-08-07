import type { SqliteDb } from './types';

export interface SubscriptionStatusRow {
  id: string;
  plan: string | null; // 'free' | 'pro' | 'pro_plus'
  expires_at: string | null;
  is_active: number; // 0/1
  cached_at: string | null;
  updated_at: string;
}

/**
 * 订阅状态展示副本仓储（Tier 3 服务端权威）。
 *
 * ⚠️ 红线：本地副本仅用于「上次看到的界面」展示，**任何 paywall / 权益 /
 * 配额 gate 判断必须实时回服务端验证**，绝不能用本副本做 gate。
 */
export class SubscriptionStatusRepository {
  private db: SqliteDb;

  constructor(db: SqliteDb) {
    this.db = db;
  }

  /** 读取展示副本（单行 id='current'）。 */
  async getCurrent(): Promise<SubscriptionStatusRow | null> {
    return this.db.getFirstSync<SubscriptionStatusRow>(
      "SELECT * FROM subscription_status WHERE id = 'current'"
    );
  }

  /** 服务端返回后更新展示副本。 */
  async cache(row: SubscriptionStatusRow): Promise<void> {
    this.db.runSync(
      `INSERT INTO subscription_status
         (id, plan, expires_at, is_active, cached_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         plan = excluded.plan,
         expires_at = excluded.expires_at,
         is_active = excluded.is_active,
         cached_at = excluded.cached_at,
         updated_at = excluded.updated_at`,
      [
        row.id,
        row.plan,
        row.expires_at,
        row.is_active,
        row.cached_at,
        row.updated_at,
      ]
    );
  }
}

import { getDatabase } from './database';

export const subscriptionStatusRepo = new SubscriptionStatusRepository(getDatabase());
