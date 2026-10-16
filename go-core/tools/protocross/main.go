// Command protocross 是 R1 的跨语言契约工具面（沿 tools/crosscheck 的形态：
// Python 测试用 `go run ./tools/protocross` 拿 JSON，不做 in-process 绑定）。
//
// 三个模式，对应 V3 15 §12「Contract Tests」的五条里的三条：
//
//	-mode emit                 → 读 testdata/proto_contract_cases.json，把每条期望值
//	                             protojson 解码成生成物、再 proto.Marshal 成 wire 字节（hex）。
//	                             ＝「Go encode → Python decode」的 Go 那一半。
//	-mode check -input f.json  → 读 Python 侧 Marshal 出来的 hex，反向 proto.Unmarshal，
//	                             回吐 {hex_roundtrip, json}。
//	                             ＝「Python encode → Go decode」，hex_roundtrip 用来证明
//	                             反向重编码与 Python 的字节逐位相同（两侧都是确定性编码，
//	                             本契约没有 map 字段）。
//	-mode reject -input f.json → 对畸形字节必须**报错**，不许静默吞掉
//	                             ＝「invalid message → rejection path」。
//
// 「old message → new consumer」与「new additive message → old tolerant consumer」
// 两条在 Python 侧测（backend/tests/test_proto_cross_language.py），因为它们的夹具是
// 手工拼出来的 wire 字节，不需要 Go 参与。
//
// 跑法：
//
//	go run ./tools/protocross -mode emit
//	go run ./tools/protocross -mode check -input /tmp/py_emitted.json
package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
)

type fixtureCase struct {
	Name    string          `json:"name"`
	Message string          `json:"message"`
	JSON    json.RawMessage `json:"json"`
}

type fixtureFile struct {
	Note  string        `json:"note"`
	Cases []fixtureCase `json:"cases"`
}

type wireCase struct {
	Name         string          `json:"name"`
	Message      string          `json:"message"`
	Hex          string          `json:"hex"`
	HexRoundtrip string          `json:"hex_roundtrip,omitempty"`
	JSON         json.RawMessage `json:"json,omitempty"`
	OK           *bool           `json:"ok,omitempty"`
	Error        string          `json:"error,omitempty"`
}

// constructors 是「契约全名 → 生成物构造函数」的表。
// ⚠️ 这张表**必须列尽 registry 里所有非值对象消息**：漏一条，emit 就少测一条，
// 而 Python 测试会因为在 fixture 里找不到对应 case 而静默少测 ⇒ 有一条闸专门数这个
// （test_cross_language_fixture_covers_every_message）。
func constructors() map[string]func() proto.Message {
	return map[string]func() proto.Message{
		"closenai.v1.Envelope":                 func() proto.Message { return &closenaiv1.Envelope{} },
		"closenai.v1.EventEnvelope":            func() proto.Message { return &closenaiv1.EventEnvelope{} },
		"closenai.v1.GateOutcome":              func() proto.Message { return &closenaiv1.GateOutcome{} },
		"closenai.v1.PatternLifecycleChanged":  func() proto.Message { return &closenaiv1.PatternLifecycleChanged{} },
		"closenai.v1.InsightStatusChanged":     func() proto.Message { return &closenaiv1.InsightStatusChanged{} },
		"closenai.v1.OutcomeRecordedPayload":   func() proto.Message { return &closenaiv1.OutcomeRecordedPayload{} },
		"closenai.v1.FeedbackRecordedPayload":  func() proto.Message { return &closenaiv1.FeedbackRecordedPayload{} },
		"closenai.v1.EvidenceWithdrawnPayload": func() proto.Message { return &closenaiv1.EvidenceWithdrawnPayload{} },
		"closenai.v1.TaskRequested":            func() proto.Message { return &closenaiv1.TaskRequested{} },
		"closenai.v1.Candidate":                func() proto.Message { return &closenaiv1.Candidate{} },
		"closenai.v1.EvidenceCandidate":        func() proto.Message { return &closenaiv1.EvidenceCandidate{} },
		"closenai.v1.ProactiveDraft":           func() proto.Message { return &closenaiv1.ProactiveDraft{} },
		"closenai.v1.OutcomeRecord":            func() proto.Message { return &closenaiv1.OutcomeRecord{} },
		"closenai.v1.EmbedRequest":             func() proto.Message { return &closenaiv1.EmbedRequest{} },
		"closenai.v1.EmbedResponse":            func() proto.Message { return &closenaiv1.EmbedResponse{} },
		"closenai.v1.TextInput":                func() proto.Message { return &closenaiv1.TextInput{} },
		"closenai.v1.SafetyResult":             func() proto.Message { return &closenaiv1.SafetyResult{} },
		"closenai.v1.StreamChatRequest":        func() proto.Message { return &closenaiv1.StreamChatRequest{} },
		"closenai.v1.ChatChunk":                func() proto.Message { return &closenaiv1.ChatChunk{} },
	}
}

func readFixture(path string) ([]fixtureCase, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var f fixtureFile
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("夹具不是合法 JSON（%s）：%w", path, err)
	}
	return f.Cases, nil
}

func writeJSON(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func main() {
	mode := flag.String("mode", "emit", "emit | check | reject")
	fixtures := flag.String("fixtures", "testdata/proto_contract_cases.json", "期望值夹具")
	input := flag.String("input", "", "check/reject 模式的输入 JSON（Python 侧产出）")
	flag.Parse()

	ctors := constructors()
	out := struct {
		Cases []wireCase `json:"cases"`
	}{}

	switch *mode {
	case "emit":
		cases, err := readFixture(*fixtures)
		if err != nil {
			fail(err)
		}
		for _, c := range cases {
			ctor, ok := ctors[c.Message]
			if !ok {
				fail(fmt.Errorf("夹具里的消息 %q（case %s）没有构造函数：生成物表漏加", c.Message, c.Name))
			}
			msg := ctor()
			if err := protojson.Unmarshal(c.JSON, msg); err != nil {
				fail(fmt.Errorf("case %s：%q 解不进 %s：%w", c.Name, string(c.JSON), c.Message, err))
			}
			raw, err := proto.Marshal(msg)
			if err != nil {
				fail(fmt.Errorf("case %s marshal 失败：%w", c.Name, err))
			}
			out.Cases = append(out.Cases, wireCase{Name: c.Name, Message: c.Message, Hex: hex.EncodeToString(raw)})
		}
	case "check":
		cases, err := readFixture(*fixtures)
		if err != nil {
			fail(err)
		}
		byName := map[string]fixtureCase{}
		for _, c := range cases {
			byName[c.Name] = c
		}
		in, err := readWireFile(*input, *mode)
		if err != nil {
			fail(err)
		}
		for _, w := range in {
			fc, ok := byName[w.Name]
			if !ok {
				fail(fmt.Errorf("check 收到未知 case %q：夹具里没有", w.Name))
			}
			msg := ctors[fc.Message]()
			raw, err := hex.DecodeString(w.Hex)
			if err != nil {
				fail(fmt.Errorf("case %s 的 hex 不合法：%w", w.Name, err))
			}
			if err := proto.Unmarshal(raw, msg); err != nil {
				fail(fmt.Errorf("case %s：%s 解不开 Python 的字节（Go decode 失败）：%w", w.Name, fc.Message, err))
			}
			roundtrip, err := proto.Marshal(msg)
			if err != nil {
				fail(fmt.Errorf("case %s 反向 marshal 失败：%w", w.Name, err))
			}
			asJSON, err := protojson.MarshalOptions{EmitUnpopulated: false}.Marshal(msg)
			if err != nil {
				fail(fmt.Errorf("case %s 转 JSON 失败：%w", w.Name, err))
			}
			out.Cases = append(out.Cases, wireCase{
				Name: w.Name, Message: fc.Message, Hex: w.Hex,
				HexRoundtrip: hex.EncodeToString(roundtrip), JSON: asJSON,
			})
		}
	case "reject":
		in, err := readWireFile(*input, *mode)
		if err != nil {
			fail(err)
		}
		for _, w := range in {
			msg, ok := ctors[w.Message]
			if !ok {
				fail(fmt.Errorf("reject 收到未知消息 %q", w.Message))
			}
			raw, err := hex.DecodeString(w.Hex)
			if err != nil {
				fail(fmt.Errorf("case %s 的 hex 不合法：%w", w.Name, err))
			}
			m := msg()
			decodeErr := proto.Unmarshal(raw, m)
			okv := decodeErr == nil
			entry := wireCase{Name: w.Name, Message: w.Message, Hex: w.Hex, OK: &okv}
			if decodeErr != nil {
				entry.Error = decodeErr.Error()
			}
			out.Cases = append(out.Cases, entry)
		}
	default:
		fail(fmt.Errorf("未知 -mode %q（emit|check|reject）", *mode))
	}

	if err := writeJSON(out); err != nil {
		fail(err)
	}
}

func readWireFile(path, mode string) ([]wireCase, error) {
	if path == "" {
		return nil, fmt.Errorf("-mode %s 需要 -input", mode)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var f struct {
		Cases []wireCase `json:"cases"`
	}
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("输入不是合法 JSON：%w", err)
	}
	return f.Cases, nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "protocross: "+err.Error())
	os.Exit(1)
}
