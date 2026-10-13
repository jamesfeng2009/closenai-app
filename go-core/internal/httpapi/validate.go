package httpapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

// FieldError is one pydantic-core validation entry. Key order and the exact
// message strings are part of the response contract: they were captured from
// the running FastAPI app on 2026-10-13 (see backend/tests/test_go_contract_parity.py).
type FieldError struct {
	Type  string         `json:"type"`
	Loc   []any          `json:"loc"`
	Msg   string         `json:"msg"`
	Input any            `json:"input"`
	Ctx   map[string]any `json:"ctx,omitempty"`
}

// FieldSpec describes one constrained string field of a request model.
type FieldSpec struct {
	Name      string
	Required  bool
	MinLength int
	MaxLength int
	Pattern   *regexp.Regexp
	// PatternSource is the pattern as written in backend/models/schemas.py; the
	// message quotes the source text, not regexp.String().
	PatternSource string
	// OneOf is the supported-value check (language codes); it reports the same
	// shape as a pydantic field_validator raising ValueError.
	OneOf []string
}

// ValidationError carries every failing field, in field-declaration order.
// The handler only quotes the first entry in `message` but ships the whole list in `data`.
type ValidationError struct {
	Entries []FieldError
}

func (e *ValidationError) Error() string { return e.Message() }

func (e *ValidationError) AsError() *apierr.Error {
	return &apierr.Error{
		Code:    apierr.CodeInvalidParams,
		Message: e.Message(),
		Data:    e.Entries,
		Status:  apierr.HTTPStatus(apierr.CodeInvalidParams),
	}
}

func (e *ValidationError) Message() string {
	first := FieldError{Msg: "参数校验失败"}
	if len(e.Entries) > 0 {
		first = e.Entries[0]
	}
	parts := make([]string, 0, len(first.Loc))
	for _, p := range first.Loc {
		if s, ok := p.(string); ok && s == "body" {
			continue
		}
		parts = append(parts, fmt.Sprint(p))
	}
	loc := strings.Join(parts, ".")
	if loc == "" {
		return fmt.Sprintf("参数错误: %s / Invalid parameters", first.Msg)
	}
	return fmt.Sprintf("参数错误: %s %s / Invalid parameters", loc, first.Msg)
}

// Body is the decoded JSON object plus its raw bytes (needed to echo `input`).
type Body struct {
	raw     json.RawMessage
	obj     map[string]json.RawMessage
	present map[string]bool
}

// DecodeBody parses the request body the way FastAPI does: an empty body is a
// missing body, non-object JSON is a model_attributes_type error, and malformed
// JSON is a json_invalid error at offset 0.
func DecodeBody(content []byte) (*Body, error) {
	if len(bytes.TrimSpace(content)) == 0 {
		return nil, &ValidationError{Entries: []FieldError{{
			Type: "missing", Loc: []any{"body"}, Msg: "Field required", Input: nil,
		}}}
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(content, &obj); err == nil {
		present := make(map[string]bool, len(obj))
		for k := range obj {
			present[k] = true
		}
		return &Body{raw: json.RawMessage(content), obj: obj, present: present}, nil
	}
	if isJSONValue(content) {
		return nil, &ValidationError{Entries: []FieldError{{
			Type:  "model_attributes_type",
			Loc:   []any{"body"},
			Msg:   "Input should be a valid dictionary or object to extract fields from",
			Input: json.RawMessage(content),
		}}}
	}
	return nil, jsonInvalidError(content)
}

// jsonInvalidError reproduces the two CPython/json messages pydantic-core echoes:
// "Expecting value" when the document does not open with a value, and "Extra data"
// at the offset of the trailing token. Other scanner messages ("Expecting ','
// delimiter", "Unterminated string starting at", ...) are not reproduced; the
// envelope still carries code 40001 and the same shape.
func jsonInvalidError(content []byte) *ValidationError {
	trimmed := bytes.TrimLeft(content, " \t\n\r")
	lead := utf8.RuneCount(content[:len(content)-len(trimmed)])
	dec := json.NewDecoder(bytes.NewReader(trimmed))
	dec.UseNumber()
	var probe any
	if err := dec.Decode(&probe); err != nil {
		return &ValidationError{Entries: []FieldError{jsonInvalidEntry("Expecting value", lead)}}
	}
	rest, err := io.ReadAll(dec.Buffered())
	if err != nil {
		return &ValidationError{Entries: []FieldError{jsonInvalidEntry("Expecting value", lead)}}
	}
	if len(bytes.TrimSpace(rest)) == 0 {
		// Go parsed a complete value the strict parser rejected; keep the shape.
		return &ValidationError{Entries: []FieldError{jsonInvalidEntry("Expecting value", lead)}}
	}
	tail := bytes.TrimLeft(rest, " \t\n\r")
	offset := lead + utf8.RuneCount(trimmed[:len(trimmed)-len(tail)])
	return &ValidationError{Entries: []FieldError{jsonInvalidEntry("Extra data", offset)}}
}

func jsonInvalidEntry(msg string, pos int) FieldError {
	return FieldError{
		Type:  "json_invalid",
		Loc:   []any{"body", pos},
		Msg:   "JSON decode error",
		Input: map[string]any{},
		Ctx:   map[string]any{"error": msg},
	}
}

func isJSONValue(content []byte) bool {
	trimmed := bytes.TrimSpace(content)
	var v any
	return json.Unmarshal(trimmed, &v) == nil
}

// JSONInvalid is the body-level error for a payload that could not be read at all.
func JSONInvalid() *ValidationError {
	return &ValidationError{Entries: []FieldError{jsonInvalidEntry("Expecting value", 0)}}
}

// Validate applies the specs in declaration order and returns nil or the error list.
func (b *Body) Validate(specs ...FieldSpec) error {
	var entries []FieldError
	for _, spec := range specs {
		if e := b.check(spec); e != nil {
			entries = append(entries, *e)
		}
	}
	if len(entries) > 0 {
		return &ValidationError{Entries: entries}
	}
	return nil
}

func (b *Body) check(spec FieldSpec) *FieldError {
	loc := []any{"body", spec.Name}
	raw, ok := b.obj[spec.Name]
	if !ok || !b.present[spec.Name] {
		if spec.Required {
			return &FieldError{Type: "missing", Loc: loc, Msg: "Field required", Input: b.raw}
		}
		return nil
	}
	if bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		if spec.Required {
			return &FieldError{Type: "string_type", Loc: loc, Msg: "Input should be a valid string", Input: raw}
		}
		return nil
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return &FieldError{Type: "string_type", Loc: loc, Msg: "Input should be a valid string", Input: raw}
	}
	n := utf8.RuneCountInString(s)
	if spec.MinLength > 0 && n < spec.MinLength {
		return &FieldError{
			Type:  "string_too_short",
			Loc:   loc,
			Msg:   pluralize("String should have at least", spec.MinLength, "character"),
			Input: raw,
			Ctx:   map[string]any{"min_length": spec.MinLength},
		}
	}
	if spec.MaxLength > 0 && n > spec.MaxLength {
		return &FieldError{
			Type:  "string_too_long",
			Loc:   loc,
			Msg:   pluralize("String should have at most", spec.MaxLength, "character"),
			Input: raw,
			Ctx:   map[string]any{"max_length": spec.MaxLength},
		}
	}
	if spec.Pattern != nil && !spec.Pattern.MatchString(s) {
		return &FieldError{
			Type:  "string_pattern_mismatch",
			Loc:   loc,
			Msg:   fmt.Sprintf("String should match pattern '%s'", spec.PatternSource),
			Input: raw,
			Ctx:   map[string]any{"pattern": spec.PatternSource},
		}
	}
	if len(spec.OneOf) > 0 && !contains(spec.OneOf, s) {
		detail := fmt.Sprintf("不支持的语言码: %s", s)
		return &FieldError{
			Type:  "value_error",
			Loc:   loc,
			Msg:   "Value error, " + detail,
			Input: raw,
			Ctx:   map[string]any{"error": detail},
		}
	}
	return nil
}

func (b *Body) String(name, fallback string) string {
	raw, ok := b.obj[name]
	if !ok || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return fallback
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return fallback
	}
	return s
}

// pluralize matches pydantic-core, which singularises the noun when the limit is 1.
func pluralize(prefix string, limit int, noun string) string {
	unit := noun + "s"
	if limit == 1 {
		unit = noun
	}
	return fmt.Sprintf("%s %d %s", prefix, limit, unit)
}

func contains(values []string, v string) bool {
	for _, candidate := range values {
		if candidate == v {
			return true
		}
	}
	return false
}
