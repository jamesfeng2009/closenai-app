// 来源指针的准入面（T2-03）：候选说「这条观察来自哪一行业务数据」，本文件判
// 「那一张表我们认不认、那一行是不是这个用户的」。
//
// 为什么必须有这一格：`source_records` 是 D-2 的**薄指针**登记（只指不复制），
// 而 `AdmitEvidence` 拿到的 `source_table`/`source_id` 全部来自提案体。在那之前
// 这一对值只过了「非空」（service_evidence.go:197）与「列宽」（:235），于是
// 一条把 `source_id` 写成别人记忆板的候选，会安安静静地登记成一行
// `source_records(user_id=攻击者, source_table=memories, source_id=别人的行)`
// 并产出一条 evidence——账面违反 01 §22 不变量 9「拒绝跨用户 id 引用」，
// 而用户在依据回链里看到的是一个他从来不知道的 id。`verifyIndependent`
// （service_evidence.go:255）管的是**被引用的证据**，不是来源行，两格不重叠。
//
// 两道判据分家（次序有理由）：
//   - 表名白名单在 `checkEvidenceCandidate` 里（纯判定，不开事务）：不认的表
//     连回查都做不了，把它放在读库之前，reason 才是「这张表没开」而不是「查库失败」。
//   - 归属回查在事务里、写在 `UpsertSourceRecord` 之前（与 :131-133 那条
//     「核验在前、写在后」同一格次序）。
package domain

import (
	"context"
	"errors"
	"fmt"
)

// AdmissibleSourceTables 是候选体里 `source_table` 允许出现的取值面。
//
// 为什么只有一格：T2-01 在命令面开的来源就是 `memories`（ADR-003），而
// `backend/ai/mq/contract.py` 的 `SOURCE_REF_TABLES` 里 `reflections`／
// `mood_checkins`／`chat_messages` 三格是 `False`。⚠️ 那三格里
// `reflections` **不是一张表**——`GET /reflections` 读的是 `growth_records`
// （`internal/store/legacy_batch5.go:127-129` 实测），所以扩来源时不许照抄
// Python 那张表的键，要按「实际表名」登记，且每一格都要配一条归属回查
// （internal/store 里那张 probe 表与本表由 source_owner_test.go 钉成同集合）。
var AdmissibleSourceTables = []string{SourceTableMemories}

// 三个哨兵各自的语义（它们必须是**值类型哨兵**而不是普通 error：
// inbound 的 `classify` 只按 `errors.Is` 分道，判错方向就是把毒消息读成瞬态、
// 让它在 30s/2m/10m 阶梯上无限撞库——那正是 candidate_decode.go:330-344 注释里
// 防的事）。
var (
	// ErrUnsupportedSourceTable：这张表本期没开准入。重试不会让没开的表变开。
	ErrUnsupportedSourceTable = errors.New("domain: source_table not admissible")
	// ErrForeignSource：那一行的 user_id 不是提案归属。**永久拒绝**（同一个 id
	// 永远不属于这个人）。Detail 里绝不回显真正的主人是谁（那是别人的标识符）。
	ErrForeignSource = errors.New("domain: source row belongs to another user")
	// ErrUnknownSourceRow：那一行查不到（从没存在过，或被 GDPR purge 硬删了）。
	// 与 foreign 分开是运维需要：前者多半是「候选晚于清理到达」的时序，
	// 后者是引用面出 bug。
	ErrUnknownSourceRow = errors.New("domain: source row not found")
)

// verifySourceTable 是纯判定那一半（不开事务、不读库）。
func verifySourceTable(c EvidenceCandidate) error {
	if !contains(AdmissibleSourceTables, c.SourceTable) {
		return fmt.Errorf("%w: source_table=%q，本期准入面=%v",
			ErrUnsupportedSourceTable, c.SourceTable, AdmissibleSourceTables)
	}
	return nil
}

// verifySourceOwnership 回查来源行的归属。它经 Store 说话（domain 不碰 pgx，
// `arch_test.go:68-75` 的禁集），SQL 住在 internal/store/source_owner.go。
func (s *Service) verifySourceOwnership(ctx context.Context, st Store, c EvidenceCandidate) error {
	owner, found, err := st.SourceRowOwner(ctx, c.SourceTable, c.SourceID)
	if err != nil {
		return fmt.Errorf("domain: 回查 source=(%s,%s) 的归属: %w", c.SourceTable, c.SourceID, err)
	}
	if !found {
		return fmt.Errorf("%w: source_table=%q source_id=%s 那一行读不到",
			ErrUnknownSourceRow, c.SourceTable, c.SourceID)
	}
	if owner != c.UserID {
		return fmt.Errorf("%w: source_table=%q source_id=%s 不在提案归属 %q 的名下",
			ErrForeignSource, c.SourceTable, c.SourceID, c.UserID)
	}
	return nil
}
