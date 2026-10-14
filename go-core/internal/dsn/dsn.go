// Package dsn translates SQLAlchemy-style database URLs into the plain libpq
// scheme pgx accepts, so one DATABASE_URL / TEST_DATABASE_URL serves both the
// Python and the Go stack.
package dsn

import "regexp"

// A SQLAlchemy URL carries the driver in the scheme (postgresql+psycopg://…),
// which pgx rejects. Python's declared driver is psycopg3 (pyproject.toml) and
// it also lands there via backend/tests/_pg_env.py, so both stacks accept
// whichever form the operator exported.
var driverScheme = regexp.MustCompile(`^(postgres(?:ql)?)\+[^:/]+://`)

// Postgres strips the driver segment from a postgres URL and returns the input
// unchanged when there is none.
func Postgres(raw string) string {
	return driverScheme.ReplaceAllString(raw, "$1://")
}
