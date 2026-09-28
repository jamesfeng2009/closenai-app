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

  async getCurrent(id: string = 'current'): Promise<PersonalityAssessmentRow | null> {
    return this.db.getFirstSync<PersonalityAssessmentRow>(
      'SELECT * FROM personality_assessment WHERE id = ?',
      [id]
    );
  }

  async getAnswers(id: string = 'current'): Promise<AssessmentAnswer[]> {
    const row = await this.getCurrent(id);
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

  /**
   * 更新评估进度（状态/题号/总题数），保留本地已记录的 answers。
   * 远端状态同步专用：answers 是离线作答凭证，仅「远端保存成功（评估完成）」才可清除，
   * 进度同步不允许用 answers: null 覆盖抹除。
   * id 区分评估种类：'current' = 8 题 16 型评估，'enneagram' = 36 题九型评估。
   */
  async saveProgress(
    progress: {
      status: AssessmentStatus;
      current_index: number;
      total_questions: number;
    },
    id: string = 'current'
  ): Promise<void> {
    const current = await this.getCurrent(id);
    await this.save({
      id,
      status: progress.status,
      current_index: progress.current_index,
      total_questions: progress.total_questions,
      answers: current?.answers ?? null,
      updated_at: new Date().toISOString(),
    });
  }

  /** 记录一题作答（追加到 answers，前移 current_index，标记进行中）。 */
  async recordAnswer(
    questionId: string,
    answer: string,
    totalQuestions: number,
    id: string = 'current'
  ): Promise<void> {
    const current = await this.getCurrent(id);
    const answers = await this.getAnswers(id);
    const existing = answers.findIndex((a) => a.question_id === questionId);
    if (existing >= 0) {
      answers[existing] = { question_id: questionId, answer };
    } else {
      answers.push({ question_id: questionId, answer });
    }
    const currentIndex = Math.min((current?.current_index ?? 0) + 1, totalQuestions);
    await this.save({
      id,
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
