// Package apierr holds the business error codes shared by the HTTP layer and the
// services, so neither has to import the other.
//
// The int values are copied from backend/core/errors.py::ErrorCode and are part of
// the frozen contract (contracts/endpoints.md §四): clients branch on them, so they
// must never be renumbered.
package apierr

import (
	"fmt"
	"net/http"
)

const (
	CodeOK                  = 200
	CodeBadRequest          = 400
	CodeUnauthorized        = 401
	CodeForbidden           = 403
	CodeNotFound            = 404
	CodeConflict            = 409
	CodeRateLimited         = 429
	CodeInvalidParams       = 40001
	CodePasswordTooWeak     = 40007
	CodeTokenExpired        = 40101
	CodeTokenInvalid        = 40102
	CodeMissingToken        = 40103
	CodeInvalidCredentials  = 40104
	CodeRefreshTokenInvalid = 40105
	CodeUserNotFound        = 40401
	CodeResourceNotFound    = 40402
	CodeEmailAlreadyUsed    = 40903
	CodeUsernameAlreadyUsed = 40904
	CodeInternalError       = 500
	CodeDatabaseError       = 50003
)

type Error struct {
	Code    int
	Message string
	Data    any
	Status  int
}

func New(code int, message string) *Error {
	return &Error{Code: code, Message: message, Status: HTTPStatus(code)}
}

func (e *Error) Error() string {
	return fmt.Sprintf("business error %d: %s", e.Code, e.Message)
}

// HTTPStatus mirrors backend/core/errors.py::_derive_http_status: a 4xxxx business
// code collapses onto its 4xx family, 5xxxx onto 500, everything else passes through.
func HTTPStatus(code int) int {
	if code >= 40000 && code < 50000 {
		return code / 100
	}
	if code >= 50000 && code < 60000 {
		return http.StatusInternalServerError
	}
	return code
}
