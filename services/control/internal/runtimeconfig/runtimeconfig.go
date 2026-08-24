package runtimeconfig

import (
	"fmt"
	"net"
	"net/url"
	"os"
)

func DatabaseURL() (string, error) {
	if value := os.Getenv("RAINPULSE_DATABASE_URL"); value != "" {
		return value, nil
	}
	password := os.Getenv("RAINPULSE_DATABASE_PASSWORD")
	if password == "" {
		return "", fmt.Errorf("RAINPULSE_DATABASE_PASSWORD is required")
	}
	host := environmentOrDefault("RAINPULSE_DATABASE_HOST", "127.0.0.1")
	port := environmentOrDefault("RAINPULSE_DATABASE_PORT", "5432")
	database := environmentOrDefault("RAINPULSE_DATABASE_NAME", "rainpulse")
	user := environmentOrDefault("RAINPULSE_DATABASE_USER", "rainpulse")

	connectionURL := &url.URL{
		Scheme: "postgres",
		User:   url.UserPassword(user, password),
		Host:   net.JoinHostPort(host, port),
		Path:   database,
	}
	query := connectionURL.Query()
	query.Set("sslmode", environmentOrDefault("RAINPULSE_DATABASE_SSLMODE", "disable"))
	connectionURL.RawQuery = query.Encode()
	return connectionURL.String(), nil
}

func NATSURL() string {
	return environmentOrDefault("RAINPULSE_NATS_URL", "nats://127.0.0.1:4222")
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
