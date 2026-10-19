// 来源行归属回查（T2-03）：`domain.AdmissibleSourceTables` 说「这张表认」，
// 这里回答「那一行的主人是谁」。
//
// 为什么 SQL 手写在这里而不是 db/queries/canonical.sql：memories 是 legacy 表，
// 不在 sqlc 的 schema 快照里，先例是 canonical_repo.go:524-541（LastUserActivityAt
// 读 chat_messages）与 users.go:21-23（legacy 表 1:1 翻译）。这条判据只要「一行一列」，
// 不值得为它把 legacy 表拖进 sqlc 的生成面。
//
// 口径边界（T2-03b 的入口，2026-10-18 登记）：只回查 `user_id`，**不加**
// `status='current'` 与 `deleted=false`。理由不是省事：那两格问的是
// 「来源行已经不在了，已入账的证据怎么办」，那需要事件面的 source.deleted 传播
// 才有意义；在准入面顺手加上，会把「用户改写了一行导致旧版本行 superseded」读成
// 「这条候选是毒消息」，而它其实是时序。GDPR purge 是硬删，天然落进 found=false。
//
// 2026-10-19 B-08 裁定后这一格的口径（T2-06 §怎么做.4①）：来源行的「不在了」分两支，
// 判据不同且**不许互换**——① 用户删除（软删）走事件面：`source.deleted` → 翻
// `source_records.status='deleted'` → `live_support` 归零 → 仅依赖者退场，`memories`
// 那一行本身仍在，所以这里的回查**照样 found=true**（准入面因此不会在删除后误判时序）；
// ② GDPR 硬删让这一行真的消失 ⇒ found=false，证据不再回查得到原文。
// 也就是说本文件不需要新谓词：B-08 把「行在不在」交给硬删，把「账还算不算」交给状态位。
//
// 分层：本文件只做类型搬运与语句调用，没有任何判定（V3 07:183-186）。
// 「查不到算不算拒绝、主人不对算不算越权」全在 internal/domain/source_pointer.go。
package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// sourceOwnerQueries 的键必须与 domain.AdmissibleSourceTables **同集合**。
// 闸在 source_owner_test.go：白名单加一格而这里少一格，等于「准入面开了、
// 回查面查不到」⇒ 每一条正常候选都会被读成 ErrUnknownSourceRow 而永久拒绝，
// 且理由看起来像「数据不存在」。
var sourceOwnerQueries = map[string]string{
	domain.SourceTableMemories: `SELECT user_id FROM memories WHERE id = $1`,
}

// SourceRowOwner 回查 (sourceTable, sourceID) 那一行的 user_id。
//
// found=false 只代表「这一行查不到」，与 error 是两件事：前者要让 domain 给出
// ErrUnknownSourceRow（永久），后者必须让它在重试阶梯上走（瞬态）。把「查不到」
// 写成 error 会把一条真正的毒消息读成「库抖了一下」，反过来则是把一次抖动记账成永久拒绝。
func (t *canonicalTx) SourceRowOwner(ctx context.Context,
	sourceTable, sourceID string) (string, bool, error) {
	query, ok := sourceOwnerQueries[sourceTable]
	if !ok {
		return "", false, fmt.Errorf(
			"store: source_table=%q 没有归属回查语句（准入面=%v）；两张表漂了，"+
				"这是接线错误而不是数据问题，所以报 error 让重试阶梯去响，不由这里定性",
			sourceTable, domain.AdmissibleSourceTables)
	}
	if t.tx == nil {
		return "", false, errors.New(
			"store: SourceRowOwner 需要事务连接，canonicalTx 未持有 pgx.Tx")
	}
	var owner string
	if err := t.tx.QueryRow(ctx, query, sourceID).Scan(&owner); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return "", false, nil
		}
		return "", false, wrap("source row owner", err)
	}
	return owner, true, nil
}
