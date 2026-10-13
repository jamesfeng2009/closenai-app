package httpapi

import (
	"net/http"
	"regexp"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
)

// Field specs mirror backend/models/schemas.py::RegisterRequest / LoginRequest,
// including declaration order (pydantic reports errors in that order).
var (
	emailPattern    = regexp.MustCompile(`^[^@\s]+@[^@\s]+\.[^@\s]+$`)
	usernamePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)

	registerSpecs = []FieldSpec{
		{Name: "email", Required: true, MaxLength: 255, Pattern: emailPattern, PatternSource: `^[^@\s]+@[^@\s]+\.[^@\s]+$`},
		{Name: "username", Required: true, MinLength: 3, MaxLength: 50, Pattern: usernamePattern, PatternSource: `^[A-Za-z0-9_.-]+$`},
		{Name: "password", Required: true, MinLength: 8, MaxLength: 128},
		{Name: "language", OneOf: supportedLanguages},
		{Name: "ref", MaxLength: 16},
	}

	loginSpecs = []FieldSpec{
		{Name: "identifier", Required: true, MinLength: 1, MaxLength: 255},
		{Name: "password", Required: true, MinLength: 1},
	}
)

// supportedLanguages is backend/config.py::Settings.supported_languages.
var supportedLanguages = []string{
	"en", "zh", "es", "ko", "ja", "ru",
	"id", "ms", "th", "de", "fr", "it", "pt",
	"vi", "km",
}

func (s *Server) handleRegister(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil {
		writeJSONError(w, r, JSONInvalid().AsError())
		return
	}
	parsed, verr := DecodeBody(body)
	if verr != nil {
		writeJSONError(w, r, asAPIError(verr))
		return
	}
	if err := parsed.Validate(registerSpecs...); err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	result, err := s.auth.Register(r.Context(), auth.RegisterInput{
		Email:    parsed.String("email", ""),
		Username: parsed.String("username", ""),
		Password: parsed.String("password", ""),
		Language: parsed.String("language", "en"),
		Ref:      parsed.String("ref", ""),
	})
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, result, rc.traceID, rc.userID)
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil {
		writeJSONError(w, r, JSONInvalid().AsError())
		return
	}
	parsed, verr := DecodeBody(body)
	if verr != nil {
		writeJSONError(w, r, asAPIError(verr))
		return
	}
	if err := parsed.Validate(loginSpecs...); err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	result, err := s.auth.Login(r.Context(),
		parsed.String("identifier", ""), parsed.String("password", ""))
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, result, rc.traceID, rc.userID)
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.auth.Me(r.Context(), userID)
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, view, rc.traceID, rc.userID)
}
