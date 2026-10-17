// 批 3 D-G14-② 的闸：候选入库后那条「下一段」链表。
//
// 这条链是把「谁下发任务」从口头变成一张表，所以它的三条硬约束（文件头登记的
// DAG／确定性 id／体内无用户身份）必须有人盯——三格里任何一格歪掉都不是编译错误，
// 而是现网里「一条候选永久自转」或「按 A 跑、记到 B 头上」这种事后才能发现的故障。
//
// 跨语言那一向（`TestTaskTypeMatchesBothContractSources`）的判据来自实测：
// 同一个 `taskType` 串此刻存在于三处——`domain.taskTypeOf`（本包，写入命令体）、
// `backend/ai/mq/contract.py` 的 `ROUTING_KEY_TO_TASK_TYPE`（Python 按它拒投递）、
// `contracts/proto/closenai_ai.proto` 的 `TaskType` 枚举（生成物的字面量）。
// 三处各写一遍就迟早漂，而漂了的后果是 `task_type_mismatch` 永久拒绝＝链在这一段断掉。
package domain

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// TestFollowUpChainIsAcyclic 钉约束 1：链表不许有环。
//
// 破坏形状举例：把 insight 也指回 observation。那样一条候选会被无限重放，
// 而 `DeriveTaskOperationID` 每次带上新来源 id ⇒ outbox 的 UNIQUE 拦不住，
// 于是「一次记录 → 永久 LLM 调用」是可达的。这条闸就是为了让它不可达。
func TestFollowUpChainIsAcyclic(t *testing.T) {
	if len(followUpAfter) == 0 {
		t.Fatal("链表为空 ⇒ 「候选入库后请下一段」这件事又回到只有口头约定的状态")
	}
	for start := range followUpAfter {
		seen := map[Kind]bool{start: true}
		kind := start
		for hop := 0; ; hop++ {
			next, ok := NextAITaskForKind(kind)
			if !ok {
				break
			}
			// 任务的种类要能翻译回候选的种类，否则链走不下去。
			back, ok := kindProducedByTask(next)
			if !ok {
				t.Fatalf("从 %s 走 %d 步到任务 %s，但没有任何候选种类由它产出 ⇒ 这条边是死路",
					start, hop+1, next)
			}
			if seen[back] {
				t.Fatalf("链上有环：%s → … → %s（第 %d 步）⇒ 一条候选会无限重放下去",
					start, back, hop+1)
			}
			seen[back] = true
			kind = back
			if hop > len(followUpAfter)+2 {
				t.Fatalf("从 %s 出发走了 %d 步还没到头：步数超过表长就是环的另一种形态", start, hop)
			}
		}
	}
}

// kindProducedByTask 是「这个任务产哪一种候选」的反向表。
//
// 出处（这条是**设计口径**，两侧都还没有可解析的机器表，所以写在测试里当模型假设）：
// `contracts/rpc/ownership.md:94` 把 `ai.observation.requested` 指向
// `memory_service.extract_and_store`（抽事实），pattern／insight 两队列各对应自己那一类；
// `backend/ai/mq/contract.py:314-323` 的 `CANDIDATE_KIND_TO_ENTRY` 则规定候选只有
// fact／pattern／insight 三种能进 `Admit`。proactive 不产 canonical 候选（它是主动消息，
// 走 proactive gate 那条触发面），所以它故意没有反向边——
// 上面那条「没有任何候选种类由它产出 ⇒ 这条边是死路」就是在判这件事。
func kindProducedByTask(task AITaskKind) (Kind, bool) {
	switch task {
	case AITaskObservation:
		return KindFact, true
	case AITaskPattern:
		return KindPattern, true
	case AITaskInsight:
		return KindInsight, true
	case AITaskProactive:
		return "", false
	}
	return "", false
}

// TestFollowUpTableCoversExactlyTheAgreedEdges 钉这张表的**边界**：
// 只接工作单 D-G14 点名的那条无争议链路，其余五种候选形态一律不接。
//
// 为什么把「不在表里」也写成断言：D-G14 明确其余五件（RecordOutcome 等）留给 R6。
// 有人顺手加一条边就是在本批里发明第二条触发面，而那种边没有对应的调用方审计。
func TestFollowUpTableCoversExactlyTheAgreedEdges(t *testing.T) {
	want := map[Kind]AITaskKind{KindFact: AITaskPattern, KindPattern: AITaskInsight}
	if len(followUpAfter) != len(want) {
		t.Fatalf("链表有 %d 条边，登记的是 %d 条：%v vs %v", len(followUpAfter), len(want), followUpAfter, want)
	}
	for kind, next := range want {
		if got, ok := followUpAfter[kind]; !ok || got != next {
			t.Fatalf("%s 的下一段应当是 %s，表里是 %q(ok=%v)", kind, next, got, ok)
		}
	}
	for _, kind := range []Kind{KindInsight} {
		if next, ok := NextAITaskForKind(kind); ok {
			t.Fatalf("%s 不该有下一段（洞察是这条链的产物），表里给了 %q", kind, next)
		}
	}
}

// TestTaskTypeMatchesBothContractSources 是跨语言对账：三处字面量必须一模一样。
func TestTaskTypeMatchesBothContractSources(t *testing.T) {
	pySrc := readSibling(t, "backend", "ai", "mq", "contract.py")
	protoSrc := readSibling(t, "contracts", "proto", "closenai_ai.proto")

	// Python：ROUTING_KEY_TO_TASK_TYPE = { "ai.x.requested": "TASK_TYPE_..." , ... }
	pyBlock := sliceBetween(t, pySrc, "ROUTING_KEY_TO_TASK_TYPE: Mapping[str, str] = {", "}")
	pyMap := mapPairs(t, pyBlock)

	// proto：enum TaskType { TASK_TYPE_X = n; ... }
	protoBlock := sliceBetween(t, protoSrc, "enum TaskType {", "\n}")
	protoNames := map[string]bool{}
	for _, line := range strings.Split(protoBlock, "\n") {
		if m := regexp.MustCompile(`(TASK_TYPE_[A-Z_]+)\s*=\s*\d+;`).FindStringSubmatch(line); m != nil {
			if m[1] != "TASK_TYPE_UNSPECIFIED" {
				protoNames[m[1]] = true
			}
		}
	}

	if len(taskTypeOf) == 0 {
		t.Fatal("taskTypeOf 为空 ⇒ 命令体的 taskType 一个都写不出来")
	}
	for kind, ourName := range taskTypeOf {
		key := kind.RoutingKey()
		if pyName := pyMap[key]; pyName != ourName {
			t.Fatalf("%s：domain 写 %q，Python 的 ROUTING_KEY_TO_TASK_TYPE 写 %q ⇒ "+
				"投出去会被 task_type_mismatch 永久拒绝", key, ourName, pyName)
		}
		if !protoNames[ourName] {
			t.Fatalf("%q 不在 contracts/proto/closenai_ai.proto 的 TaskType 枚举里 ⇒ 这个串是从哪来的？", ourName)
		}
	}
	// 反向：Python 登记的四个键 domain 都要能路由出来，少一个就是「对面有队列、我方发不出」。
	for key := range pyMap {
		var found bool
		for _, one := range AITaskKinds {
			if one.RoutingKey() == key {
				found = true
			}
		}
		if !found {
			t.Fatalf("Python 登记了命令键 %q，但 domain.AITaskKinds 里没有这一类 ⇒ 双向对不上", key)
		}
	}
	if len(pyMap) != len(taskTypeOf) {
		t.Fatalf("Python 有 %d 个命令键、domain 有 %d 个 taskType ⇒ 数量也要对得上", len(pyMap), len(taskTypeOf))
	}
}

// TestDeriveTaskOperationIDIsDeterministicAndBounded 钉约束 2：重试不许换新 id。
func TestDeriveTaskOperationIDIsDeterministicAndBounded(t *testing.T) {
	a := DeriveTaskOperationID(AITaskPattern, "cand-abc")
	b := DeriveTaskOperationID(AITaskPattern, "cand-abc")
	if a != b {
		t.Fatalf("同一来源＋同一段两次派生出不同 id（%q vs %q）⇒ 重试会换新 id，幂等键形同虚设", a, b)
	}
	if other := DeriveTaskOperationID(AITaskInsight, "cand-abc"); other == a {
		t.Fatalf("不同段派生出同一个 id（%q）⇒ 同一来源的两条命令会互相吃掉", other)
	}
	if other := DeriveTaskOperationID(AITaskPattern, "cand-def"); other == a {
		t.Fatalf("不同来源派生出同一个 id（%q）⇒ 两条不同的候选被当成同一次操作", other)
	}
	if len(a) != 45 || !strings.HasPrefix(a, "task-") {
		t.Fatalf("id 形状是 %q（长度 %d），应当是 task-+40 位摘要＝45：超出就撞 "+
			"outbox_messages.message_id 的 128 列宽校验（validateAITaskRequest）", a, len(a))
	}
	if strings.Contains(a, "cand-abc") {
		t.Fatalf("id 里带着来源原文（%q）⇒ 来源串一长就越界，这里必须只放摘要", a)
	}
	// 与 RequestAITask 的入参校验对账：派生出来的 id 必须真的能过。
	if err := validateAITaskRequest(AITaskRequest{
		Kind: AITaskPattern, UserID: "u-1", OperationID: a, Payload: "{}",
	}); err != nil {
		t.Fatalf("派生的 id 过不了自家校验：%v", err)
	}
}

// TestTaskRequestBodyCarriesNoUserIdentity 钉约束 3：归属只有 header 一个来源。
//
// 判据不是「键名里别出现 user」这种模糊写法，而是**逐字复刻** Python 的归一化规则
// （`contract.py:287`：`k.lower().replace("_","") == "userid"`）——
// 对面就是按这条把体内任何 user-id 形态永久拒绝的，绕开它才算数。
func TestTaskRequestBodyCarriesNoUserIdentity(t *testing.T) {
	body, err := TaskRequestBody(AITaskPattern, KindFact, "cand-abc")
	if err != nil {
		t.Fatalf("组装命令体失败：%v", err)
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(body), &parsed); err != nil {
		t.Fatalf("命令体不是合法 JSON（%q）：%v ⇒ 对面 parse_command 会按 bad_payload 拒", body, err)
	}
	for k := range parsed {
		if strings.ToLower(strings.ReplaceAll(k, "_", "")) == "userid" {
			t.Fatalf("命令体里出现了用户身份键 %q（%s）⇒ Python 侧 foreign_user_id 会永久拒绝，"+
				"而这里本来就是唯一该负责的地方", k, body)
		}
	}
	// schemaVersion 只该写一处（header）：两处都写就多一个可能对不上的来源。
	if _, ok := parsed["schemaVersion"]; ok {
		t.Fatalf("命令体里写了 schemaVersion（%s）⇒ 与 header 的版本任一处漂移就是 schema_version_mismatch", body)
	}
}

// TestTaskRequestBodyIsByteStable 钉「同输入必同字节」：幂等重放与夹具都比得出。
func TestTaskRequestBodyIsByteStable(t *testing.T) {
	first, err := TaskRequestBody(AITaskInsight, KindPattern, "cand-x1")
	if err != nil {
		t.Fatalf("组装失败：%v", err)
	}
	second, err := TaskRequestBody(AITaskInsight, KindPattern, "cand-x1")
	if err != nil {
		t.Fatalf("组装失败：%v", err)
	}
	if first != second {
		t.Fatalf("同输入两次字节不同：%q vs %q ⇒ map 无序会让夹具每跑一次漂一版", first, second)
	}
	// 键名面也是契约：taskType 这个串在 Python 侧是常量 TASK_TYPE_FIELD。
	pySrc := readSibling(t, "backend", "ai", "mq", "contract.py")
	m := regexp.MustCompile(`TASK_TYPE_FIELD\s*=\s*"([^"]+)"`).FindStringSubmatch(pySrc)
	if m == nil {
		t.Fatal("contract.py 里读不到 TASK_TYPE_FIELD ⇒ 对账的判据没了")
	}
	if !strings.Contains(first, `"`+m[1]+`":"TASK_TYPE_INSIGHT_DRAFT"`) {
		t.Fatalf("命令体 %q 没有按 Python 的 TASK_TYPE_FIELD=%q 写出 taskType ⇒ 对面找不到任务种类", first, m[1])
	}
	want := `{"sourceKind":"pattern","sourceMessageId":"cand-x1","taskType":"TASK_TYPE_INSIGHT_DRAFT"}`
	if first != want {
		t.Fatalf("命令体应当逐字是 %q，实测 %q（键序由 json.Marshal 排序，改一个键就要重录）", want, first)
	}
}

// TestEveryFollowUpStageProducesTheRightKind 是链与类型的联合判据：
// 边上的 stage 要能产出「下一条边的键」，否则链走到第二步就断。
func TestEveryFollowUpStageProducesTheRightKind(t *testing.T) {
	for admitted, stage := range followUpAfter {
		produced, ok := kindProducedByTask(stage)
		if !ok {
			t.Fatalf("%s 的下一段任务 %s 不产任何候选 ⇒ 这一步跑完没有东西入库，链白走", admitted, stage)
		}
		if _, ok := NextAITaskForKind(produced); ok {
			continue // 还在链上，下一步由另一个用例走
		}
		if produced != KindInsight {
			t.Fatalf("%s → %s 产 %s，而 %s 既不在链表也不是 insight ⇒ 出现了一个没人登记终点的种类",
				admitted, stage, produced, produced)
		}
	}
}

// ── 读源辅助 ────────────────────────────────────────────────────────────────

// readSibling 从仓库根取文件：domain 在 go-core/internal/domain，向上三级是仓库根。
// 读不到就 Fatal——「跨语言对账因为路径没找到而静默跳过」是这种闸最没用的形态。
func readSibling(t *testing.T, parts ...string) string {
	t.Helper()
	path := filepath.Join(append([]string{"..", "..", ".."}, parts...)...)
	src, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("读 %s 失败：%v ⇒ 这一条对账没有真的比过", path, err)
	}
	return string(src)
}

func sliceBetween(t *testing.T, src, start, end string) string {
	t.Helper()
	i := strings.Index(src, start)
	if i < 0 {
		t.Fatalf("源里找不到 %q ⇒ 对面的形状变了，闸要跟着改而不是放行", start)
	}
	rest := src[i+len(start):]
	j := strings.Index(rest, end)
	if j < 0 {
		t.Fatalf("%s 之后找不到结束符 %q", start, end)
	}
	return rest[:j]
}

var pairRE = regexp.MustCompile(`"([^"]+)"\s*:\s*"([^"]+)"`)

func mapPairs(t *testing.T, block string) map[string]string {
	t.Helper()
	out := map[string]string{}
	for _, m := range pairRE.FindAllStringSubmatch(block, -1) {
		out[m[1]] = m[2]
	}
	if len(out) == 0 {
		t.Fatal("这段里一个键值对都没抓到 ⇒ 正则配错了靶，不许算过")
	}
	return out
}
