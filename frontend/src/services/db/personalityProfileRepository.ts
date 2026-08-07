import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

export interface PersonalityProfileRow {
  id: string;
  mbti: string | null;
  zodiac_sign: string | null;
  enneagram: string | null;
  summary: string | null;
  refreshed_at: string | null;
  created_at: string;
  updated_at: string;
}

/** 性格画像结果缓存（Tier 2 AI 计算：服务端为准，本地只读展示缓存）。 */
export class PersonalityProfileRepository extends BaseRepository<PersonalityProfileRow> {
  constructor(db: SqliteDb) {
    super(db, 'personality_profile');
  }

  /** 取最新一份画像（单行，按用户 id）。 */
  async getLatest(): Promise<PersonalityProfileRow | null> {
    return this.db.getFirstSync<PersonalityProfileRow>(
      'SELECT * FROM personality_profile ORDER BY updated_at DESC LIMIT 1'
    );
  }

  /** 服务端结果覆盖本地（只读缓存：远端总是胜出）。 */
  async cache(row: PersonalityProfileRow): Promise<void> {
    this.upsert(row);
  }
}

import { getDatabase } from './database';

export const personalityProfileRepo = new PersonalityProfileRepository(getDatabase());
