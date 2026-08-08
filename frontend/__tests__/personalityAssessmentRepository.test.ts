import { createTestDb } from './helpers/testDb';
import { PersonalityAssessmentRepository } from '../src/services/db/personalityAssessmentRepository';

describe('PersonalityAssessmentRepository (Tier 1 评估进度)', () => {
  it('默认未开始：无记录，answers 为空', async () => {
    const db = createTestDb();
    const repo = new PersonalityAssessmentRepository(db);
    expect(await repo.getCurrent()).toBeNull();
    expect(await repo.getAnswers()).toEqual([]);
    db.closeSync();
  });

  it('recordAnswer 追加答案并前移进度，单行持久化', async () => {
    const db = createTestDb();
    const repo = new PersonalityAssessmentRepository(db);
    await repo.recordAnswer('q1', 'a', 8);
    await repo.recordAnswer('q2', 'b', 8);

    const current = await repo.getCurrent();
    expect(current?.status).toBe('in_progress');
    expect(current?.total_questions).toBe(8);
    // 进度必须逐题递增（回归：?? 与 + 的运算符优先级 bug 曾导致 current_index 永远卡在 1）
    expect(current?.current_index).toBe(2);
    expect(await repo.getAnswers()).toEqual([
      { question_id: 'q1', answer: 'a' },
      { question_id: 'q2', answer: 'b' },
    ]);
    db.closeSync();
  });

  it('同一题重复作答覆盖旧答案，不重复追加', async () => {
    const db = createTestDb();
    const repo = new PersonalityAssessmentRepository(db);
    await repo.recordAnswer('q1', 'a', 8);
    await repo.recordAnswer('q1', 'neutral', 8);
    expect(await repo.getAnswers()).toEqual([{ question_id: 'q1', answer: 'neutral' }]);
    db.closeSync();
  });

  it('saveProgress 更新进度但保留本地已记录答案（远端同步不得抹除离线答案）', async () => {
    const db = createTestDb();
    const repo = new PersonalityAssessmentRepository(db);
    await repo.recordAnswer('q1', 'a', 8);
    await repo.recordAnswer('q2', 'b', 8);

    // 远端状态同步（如 getAssessment 回包）只更新进度字段
    await repo.saveProgress({ status: 'in_progress', current_index: 2, total_questions: 8 });

    const current = await repo.getCurrent();
    expect(current?.status).toBe('in_progress');
    expect(current?.current_index).toBe(2);
    // 回归：历史实现用 answers:null 覆盖保存，会抹掉本地答案
    expect(await repo.getAnswers()).toEqual([
      { question_id: 'q1', answer: 'a' },
      { question_id: 'q2', answer: 'b' },
    ]);
    db.closeSync();
  });

  it('saveProgress 无历史记录时 answers 为空', async () => {
    const db = createTestDb();
    const repo = new PersonalityAssessmentRepository(db);
    await repo.saveProgress({ status: 'in_progress', current_index: 1, total_questions: 8 });
    const current = await repo.getCurrent();
    expect(current?.current_index).toBe(1);
    expect(await repo.getAnswers()).toEqual([]);
    db.closeSync();
  });
});
