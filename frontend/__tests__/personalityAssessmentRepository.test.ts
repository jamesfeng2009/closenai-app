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
});
