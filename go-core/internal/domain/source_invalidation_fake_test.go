// T2-06 的传播面桩：登记行的读、单向翻转、失败恢复，以及它的分母。
//
// 这四个方法刻意**照 SQL 的谓词形状**记账（`status <> 'deleted'` 才翻、
// `status = 'deleted'` 才恢复），而不是无条件改写——否则 `Marked=false` 那一格
// 在单测里永远是绿的，真库的单向闸写坏了也没人知道（单向闸的真值只有
// `canonical_integration_test.go` 那组真库用例给得出，两边各判一层）。
package domain

import "context"

func (f *fakeStore) SourceRecordByPointer(ctx context.Context, userID, sourceTable,
	sourceID string) (SourceRecordState, error) {
	if f.sourceErr != nil {
		return SourceRecordState{}, f.sourceErr
	}
	f.sourceLookups = append(f.sourceLookups, sourceTable+":"+sourceID)
	return f.sourceRecord, nil
}

func (f *fakeStore) MarkSourceRecordDeletedOnce(ctx context.Context, id, userID string) (bool, error) {
	if f.sourceErr != nil {
		return false, f.sourceErr
	}
	row := f.sourceRecord
	if !row.Found || row.Status == "deleted" {
		return false, nil
	}
	row.Status = "deleted"
	f.sourceRecord = row
	f.deletedMarks++
	return true, nil
}

func (f *fakeStore) RestoreSourceRecordStatus(ctx context.Context, id, userID,
	status string) (int64, error) {
	if f.restoreErr != nil {
		return 0, f.restoreErr
	}
	if f.sourceErr != nil {
		return 0, f.sourceErr
	}
	row := f.sourceRecord
	if !row.Found || row.Status != "deleted" {
		return 0, nil
	}
	row.Status = status
	f.sourceRecord = row
	f.restores++
	return 1, nil
}

func (f *fakeStore) ListEvidenceIDsForSource(ctx context.Context, userID,
	sourceRecordID string) ([]string, error) {
	if f.sourceErr != nil {
		return nil, f.sourceErr
	}
	return f.evidenceForSource, nil
}
