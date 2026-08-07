import { resolveConflict } from '../src/services/db/syncService';

interface Rec {
  id: string;
  updated_at?: string;
  created_at?: string;
  value: number;
}

describe('resolveConflict (last-write-wins)', () => {
  it('本地无记录 -> 采用远端', () => {
    const r: Rec = { id: 'r', updated_at: '2026-08-02T00:00:00Z', value: 2 };
    expect(resolveConflict<Rec>(null, r)).toEqual(r);
  });

  it('远端无记录 -> 保留本地', () => {
    const l: Rec = { id: 'l', updated_at: '2026-08-01T00:00:00Z', value: 1 };
    expect(resolveConflict<Rec>(l, null)).toEqual(l);
  });

  it('远端更新 -> 远端覆盖本地', () => {
    const l: Rec = { id: 'x', updated_at: '2026-08-01T00:00:00Z', value: 1 };
    const r: Rec = { id: 'x', updated_at: '2026-08-02T00:00:00Z', value: 2 };
    expect(resolveConflict<Rec>(l, r)).toEqual(r);
  });

  it('本地更新 -> 保留本地', () => {
    const l: Rec = { id: 'x', updated_at: '2026-08-03T00:00:00Z', value: 3 };
    const r: Rec = { id: 'x', updated_at: '2026-08-02T00:00:00Z', value: 2 };
    expect(resolveConflict<Rec>(l, r)).toEqual(l);
  });

  it('无 updated_at 时退回 created_at 比较', () => {
    const l: Rec = { id: 'x', created_at: '2026-08-01T00:00:00Z', value: 1 };
    const r: Rec = { id: 'x', created_at: '2026-08-02T00:00:00Z', value: 2 };
    expect(resolveConflict<Rec>(l, r)).toEqual(r);
  });
});
