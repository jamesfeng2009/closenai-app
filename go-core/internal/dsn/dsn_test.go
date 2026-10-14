package dsn

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
)

func TestPostgresStripsSQLAlchemyDriverSegment(t *testing.T) {
	cases := []struct{ name, raw, want string }{
		{"bare libpq URL passes through", "postgresql://u:p@127.0.0.1:55432/closenai",
			"postgresql://u:p@127.0.0.1:55432/closenai"},
		{"psycopg3", "postgresql+psycopg://u:p@127.0.0.1:55432/closenai",
			"postgresql://u:p@127.0.0.1:55432/closenai"},
		{"asyncpg", "postgresql+asyncpg://u:p@127.0.0.1:55432/closenai",
			"postgresql://u:p@127.0.0.1:55432/closenai"},
		{"psycopg2", "postgresql+psycopg2://u:p@127.0.0.1:55432/closenai",
			"postgresql://u:p@127.0.0.1:55432/closenai"},
		{"legacy postgres scheme keeps its own scheme", "postgres+asyncpg://u:p@127.0.0.1:55432/closenai",
			"postgres://u:p@127.0.0.1:55432/closenai"},
		{"driver segment must not eat the credential after ://",
			"postgresql+psycopg://u:p+w@127.0.0.1:55432/closenai?sslmode=disable",
			"postgresql://u:p+w@127.0.0.1:55432/closenai?sslmode=disable"},
		{"non-postgres URL is left alone", "amqp://guest:guest@rabbit:5672/",
			"amqp://guest:guest@rabbit:5672/"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := Postgres(tc.raw); got != tc.want {
				t.Fatalf("Postgres(%q) = %q, want %q", tc.raw, got, tc.want)
			}
		})
	}
}

func TestPostgresPoolAcceptsEveryPythonScheme(t *testing.T) {
	// The reason this package exists: pgx rejects the SQLAlchemy form, so the
	// translation has to happen before pgxpool sees the string.
	for _, raw := range []string{
		"postgresql://u:p@127.0.0.1:55432/closenai",
		"postgresql+psycopg://u:p@127.0.0.1:55432/closenai",
		"postgresql+asyncpg://u:p@127.0.0.1:55432/closenai",
	} {
		t.Run(raw, func(t *testing.T) {
			cfg, err := pgxpool.ParseConfig(Postgres(raw))
			if err != nil {
				t.Fatalf("pgx rejected translated URL: %v", err)
			}
			if cfg.ConnConfig.Host != "127.0.0.1" || cfg.ConnConfig.Database != "closenai" {
				t.Fatalf("translation lost the target: host=%q db=%q",
					cfg.ConnConfig.Host, cfg.ConnConfig.Database)
			}
		})
	}
}

func TestTranslationIsRequiredForSQLAlchemySchemes(t *testing.T) {
	// Negative anchor for the two cases above: if the raw Python URL parsed fine
	// on its own, this package would be dead weight and its tests would pass
	// vacuously.
	for _, raw := range []string{
		"postgresql+psycopg://u:p@127.0.0.1:55432/closenai",
		"postgresql+asyncpg://u:p@127.0.0.1:55432/closenai",
	} {
		t.Run(raw, func(t *testing.T) {
			if _, err := pgxpool.ParseConfig(raw); err == nil {
				t.Fatalf("pgx accepted the SQLAlchemy scheme %q — 归一闸失去对象", raw)
			}
		})
	}
}

var poolConstructor = regexp.MustCompile(`pgxpool\.(New|NewWithConfig|ParseConfig)\(`)

// TestEveryPoolConstructorTakesATranslatedURL keeps the translation on the only
// path a pgx connection can be opened: a site that reads DATABASE_URL straight
// into pgxpool re-creates the "one variable cannot feed both stacks" trap.
func TestEveryPoolConstructorTakesATranslatedURL(t *testing.T) {
	root := filepath.Join("..", "..")
	checked := 0
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			switch d.Name() {
			case "testdata", ".git", "node_modules":
				return fs.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.Contains(path, filepath.Join("internal", "dsn")) {
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		text := string(src)
		if !poolConstructor.MatchString(text) {
			return nil
		}
		checked++
		if !strings.Contains(text, "dsn.Postgres") {
			t.Errorf("%s 把连接串直接交给 pgxpool：必须过 dsn.Postgres，否则 Python 侧的 "+
				"postgresql+psycopg:// 形态在这里必然解析失败", path)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s: %v", root, err)
	}
	// cmd/go-api、internal/store 集成测、tools/crosscheck 三处，少一处说明遍历本身坏了。
	if checked < 3 {
		t.Fatalf("只检查了 %d 个建池点，应为 3 处——遍历失效的闸门不会红", checked)
	}
}
