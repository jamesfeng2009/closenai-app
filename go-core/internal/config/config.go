// Package config reads the runtime configuration from environment variables.
//
// The variable names are the cross-language contract: backend/config.py is a
// pydantic BaseSettings whose fields map to upper-case env vars, so Go reads
// the same JWT_SECRET / JWT_ALGORITHM / ... instead of inventing its own names.
package config

import (
	"errors"
	"os"
	"strconv"
)

type Config struct {
	AppName                string
	Environment            string
	DatabaseURL            string
	JWTSecret              string
	JWTAlgorithm           string
	JWTAccessExpireMinutes int
	JWTRefreshExpireDays   int
	HTTPAddr               string
	ShutdownTimeoutSeconds int
}

const defaultDatabaseURL = "sqlite+aiosqlite:///./closenai.db"

func Load() (Config, error) {
	cfg := Config{
		AppName:                envString("APP_NAME", "ClosenAI"),
		Environment:            envString("ENVIRONMENT", "development"),
		DatabaseURL:            envString("DATABASE_URL", defaultDatabaseURL),
		JWTSecret:              envString("JWT_SECRET", ""),
		JWTAlgorithm:           envString("JWT_ALGORITHM", "HS256"),
		JWTAccessExpireMinutes: envInt("JWT_ACCESS_EXPIRE_MINUTES", 30),
		JWTRefreshExpireDays:   envInt("JWT_REFRESH_EXPIRE_DAYS", 30),
		HTTPAddr:               envString("HTTP_ADDR", ":8000"),
		ShutdownTimeoutSeconds: envInt("SHUTDOWN_TIMEOUT_SECONDS", 15),
	}
	if cfg.JWTAlgorithm != "HS256" {
		return cfg, errors.New("unsupported JWT_ALGORITHM: cross-language baseline is HS256")
	}
	if cfg.JWTAccessExpireMinutes <= 0 || cfg.JWTRefreshExpireDays <= 0 {
		return cfg, errors.New("JWT expiry settings must be positive")
	}
	return cfg, nil
}

// RequireAuthSecret mirrors backend/main.py::_validate_startup_config: production
// refuses to boot without a secret, development starts and lets tokens fail closed.
func (c Config) RequireAuthSecret() error {
	if c.IsProduction() && c.JWTSecret == "" {
		return errors.New("JWT_SECRET is required in production")
	}
	return nil
}

func (c Config) IsProduction() bool {
	switch c.Environment {
	case "production", "prod":
		return true
	}
	return false
}

func envString(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	raw, ok := os.LookupEnv(key)
	if !ok || raw == "" {
		return fallback
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return v
}
