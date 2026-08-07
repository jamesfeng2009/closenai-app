import type { SqliteDb } from './types';

export type AssessmentStatus = 'not_started' | 'in_progress' | 'completed';

export interface AssessmentAnswer {
  question_id: string;
  answer: string;
}

export interface PersonalityAssessmentRow {
  id: string;
  status: AssessmentStatus;
  current_index: number;
  total_questions: number;
  answers: string | null; // JSON 数组 [{question_id, answer}]
  updated_at: string;
}

/**
 * 性格评估进度仓储（Tier 1 用户原创：离线断点续答）。
 * 单行表（id='current'），不复用 BaseRepository（主键非 UUID 语义 + answers 需 JSON 转换）。
 */
export class PersonalityAssessmentRepository {
  private db: SqliteDb;

  constructor(db: SqliteDb) {
    this.db = db;
  }

  async getCurrent(): Promise<PersonalityAssessmentRow | null> {
    return this.db.getFirstSync<PersonalityAssessmentRow>(
      "SELECT * FROM personality_assessment WHERE id = 'current'"
    );
  }

  async getAnswers(): Promise<AssessmentAnswer[]> {
    const row = await this.getCurrent();
    if (!row?.answers) return [];
    try {
      return JSON.parse(row.answers) as AssessmentAnswer[];
    } catch {
      return [];
    }
  }

  async save(row: PersonalityAssessmentRow): Promise<void> {
    this.db.runSync(
      `INSERT INTO personality_assessment
         (id, status, current_index, total_questions, answers, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         status = excluded.status,
         current_index = excluded.current_index,
         total_questions = excluded.total_questions,
         answers = excluded.answers,
         updated_at = excluded.updated_at`,
      [
        row.id,
        row.status,
        row.current_index,
        row.total_questions,
        row.answers,
        row.updated_at,
      ]
    );
  }

  /** 记录一题作答（追加到 answers，前移 current_index，标记进行中）。 */
  async recordAnswer(questionId: string, answer: string, totalQuestions: number): Promise<void> {
    const current = await this.getCurrent();
    const answers = await this.getAnswers();
    const existing = answers.findIndex((a) => a.question_id === questionId);
    if (existing >= 0) {
      answers[existing] = { question_id: questionId, answer };
    } else {
      answers.push({ question_id: questionId, answer });
    }
    const currentIndex = Math.min(current?.current_index ?? 0 + 1, totalQuestions);
    await this.save({
      id: 'current',
      status: 'in_progress',
      current_index: currentIndex,
      total_questions: totalQuestions,
      answers: JSON.stringify(answers),
      updated_at: new Date().toISOString(),
    });
  }
}

import { getDatabase } from './database';

export const personalityAssessmentRepo = new PersonalityAssessmentRepository(getDatabase());
