// G-7 包级依赖闸（closenai-prd/V3方案裁决与分层-2026-10-12.md 七-2）。
//
// 分层不变量 `Python proposes. Go decides. PostgreSQL records.` 要在代码里成立，
// 就得让「谁能碰 canonical 写路径」成为可执行约束，而不是 review 时的口头约定：
//   - internal/domain 不 import 本项目其它包（idgen 除外），也不 import HTTP / gRPC /
//     消息队列 / DB 驱动这类传输与持久化面——它是纯决策层，
//     换 HTTP 框架、换消息队列都不该动它；
//   - sqlc 生成物 internal/store/canonicaldb 只能被 internal/store 引用——
//     handler / consumer 绕过 domain.Service 直接写 canonical 表就是破坏不变量 7。
//
// 用 go/parser 读 import 声明，不 exec `go list`：测试在任何人机器上都能跑，
// 不依赖 GOROOT 配好（本机 Go 工具链是混合安装，PATH 上的 go 一编译就报版本不符）。
package domain

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const modulePath = "github.com/jamesfeng2009/closenai-app/go-core"

const (
	// canonicalDBImport 是 sqlc 生成物的导入路径（V3 08 §4：生成物禁手改，也只准仓储用）。
	canonicalDBImport = modulePath + "/internal/store/canonicaldb"
	domainPkg         = modulePath + "/internal/domain"
	storePkg          = modulePath + "/internal/store"
)

func TestDomainDoesNotDependOnTransportLayers(t *testing.T) {
	seen := 0
	violations := scanImports(t, moduleRoot(t), func(pkg, imp string) bool {
		if pkg != domainPkg {
			return false
		}
		seen++
		if strings.HasPrefix(imp, modulePath+"/internal/") {
			// idgen 只产 uuid4 字符串，不承载任何业务判断，是领域层唯一允许的本项目依赖。
			return imp != modulePath+"/internal/idgen"
		}
		for _, prefix := range transportImports {
			if imp == prefix || strings.HasPrefix(imp, prefix+"/") {
				return true
			}
		}
		return false
	})
	for _, v := range violations {
		t.Errorf("领域层越界依赖：%s", v)
	}
	if seen == 0 {
		t.Error("闸未生效：internal/domain 没扫到任何 import")
	}
}

// transportImports 是「换 HTTP 框架、换消息队列、换持久化驱动就要动领域层」的那批包。
// 领域层是纯决策层（07:183-186），这些一律不许出现在它的 import 里。
//
// ⚠️ R1 加 protobuf 进来时**只加不减**（contracts/rpc/ownership.md §5「不改这个闸」的读法＝
// 不许为了放出生成物而放松它）：V3 07:8 逐字 "It must not depend on PostgreSQL, RabbitMQ,
// Protobuf or an LLM."，Protobuf 本来就在黑名单上，只是 R1 之前仓里没有任何 protobuf 依赖，
// 这一条是空转的。现在 go-core/gen 里有了 protobuf 生成物 ⇒ 不补这一行，07:8 的 Protobuf
// 半句就永远测不到。生成物在 go-core/gen（不在 internal/ 下），谓词先按 pkg==domainPkg 过滤，
// 所以这一行只约束领域层，不会误伤 codegen。
var transportImports = []string{
	"net/http", "net/rpc", "database/sql",
	"google.golang.org/grpc",
	"google.golang.org/protobuf",
	"github.com/go-chi", "github.com/gin-gonic",
	"github.com/rabbitmq", "github.com/redis",
	"github.com/jackc",
}

func TestOnlyStoreTouchesCanonicalDB(t *testing.T) {
	hits := 0
	violations := scanImports(t, moduleRoot(t), func(pkg, imp string) bool {
		if imp != canonicalDBImport {
			return false
		}
		hits++
		// 允许：internal/store 本体（含其测试）与生成物自己。
		return pkg != storePkg && pkg != canonicalDBImport
	})
	for _, v := range violations {
		t.Errorf("绕过 domain.Service 直接引用 sqlc 生成物：%s（不变量 7）", v)
	}
	if hits == 0 {
		t.Error("闸未生效：全模块没扫到任何 canonicaldb import")
	}
}

// TestGateProducesOnlyRegisteredReasons 把 G-1 词表钉住：Gate 能产出的每个码都必须在
// RejectReasons 里，且塞得进 reject_reason VARCHAR(64)。加码不进表＝契约漂移，
// N9 读路径与 O 侧评测会各自猜一套口径。
func TestGateProducesOnlyRegisteredReasons(t *testing.T) {
	inTable := map[RejectReason]bool{}
	for _, reason := range RejectReasons {
		if reason == "" {
			t.Error("词表里有空码")
		}
		if len(reason) > 64 {
			t.Errorf("%s 长 %d，超 reject_reason VARCHAR(64)", reason, len(reason))
		}
		if inTable[reason] {
			t.Errorf("词表重复登记 %s", reason)
		}
		inTable[reason] = true
	}

	mutations := []func(*Candidate){
		func(c *Candidate) { c.GenerationRunID = "" },
		func(c *Candidate) { c.EvidenceIDs = nil },
		func(c *Candidate) { c.EvidenceIDs = []string{"missing"} },
		func(c *Candidate) { c.EvidenceIDs = []string{"e3"} },
		func(c *Candidate) { c.Confidence = 0.01 },
		func(c *Candidate) { c.ClaimType = "cause" },
		func(c *Candidate) { c.PatternType = "diagnosis" },
		func(c *Candidate) { c.Statement = "你总是在深夜记录" },
		func(c *Candidate) { c.Safety = nil },
		func(c *Candidate) {
			c.Confidence = 0.95
			c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
		},
	}
	seen := map[RejectReason]bool{}
	for _, build := range []func() Candidate{okFact, okPattern, okInsight} {
		for _, mutate := range mutations {
			c := build()
			mutate(&c)
			out := run(t, c, newReader())
			if out.Action != ActionReject {
				continue
			}
			seen[out.Reason] = true
			if !inTable[out.Reason] {
				t.Errorf("Gate 产出了未登记的码 %q（detail=%q）", out.Reason, out.Detail)
			}
		}
	}
	// 变异样本必须真的触达这些码，否则上面那句断言是空转。
	for _, want := range []RejectReason{ReasonSchemaInvalid, ReasonEvidenceMissing,
		ReasonEvidenceNotFound, ReasonEvidenceNotOwned, ReasonConfidenceBelowThresh,
		ReasonClaimTypeRestricted, ReasonTemporalOverreach, ReasonSafetyBlocked,
		ReasonSafetyUnavailable} {
		if !seen[want] {
			t.Errorf("这轮变异没触达 %s，词表覆盖闸空转", want)
		}
	}
}

// moduleRoot 从测试工作目录（本包目录）上溯两级；go.mod 存在是定位正确的自检。
func moduleRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatalf("定位模块根: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "go.mod")); err != nil {
		t.Fatalf("模块根上没有 go.mod: %v", err)
	}
	return root
}

// scanImports 遍历 root 下每个 Go 包目录，返回 predicate 命中的 "文件: 包 -> 导入"。
// 扫描失败或零文件一律 t.Fatal：失效的闸必须红，不能默默放行（N2 的假绿教训之一）。
func scanImports(t *testing.T, root string, predicate func(pkg, imp string) bool) []string {
	t.Helper()
	base := moduleRoot(t)
	fset := token.NewFileSet()
	var hits []string
	files := 0
	err := filepath.Walk(root, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if !info.IsDir() {
			return nil
		}
		switch info.Name() {
		case "vendor", "testdata", ".git":
			return filepath.SkipDir
		}
		rel, err := filepath.Rel(base, path)
		if err != nil {
			return err
		}
		importPath := modulePath
		if !strings.HasPrefix(rel, "..") {
			importPath += "/" + filepath.ToSlash(rel)
		}
		pkgs, err := parser.ParseDir(fset, path, func(fi os.FileInfo) bool {
			return strings.HasSuffix(fi.Name(), ".go")
		}, parser.ImportsOnly)
		if err != nil {
			return err
		}
		for _, pkg := range pkgs {
			for _, file := range pkg.Files {
				files++
				for _, spec := range file.Imports {
					imp := strings.Trim(spec.Path.Value, `"`)
					if predicate(importPath, imp) {
						hits = append(hits, filepath.Base(fset.Position(file.Pos()).Filename)+
							": "+importPath+" -> "+imp)
					}
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("扫描 %s 失败，依赖闸未生效: %v", root, err)
	}
	if files == 0 {
		t.Fatalf("扫描 %s 没看到任何 .go 文件，依赖闸未生效", root)
	}
	return hits
}
