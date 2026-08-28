package webgateway

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

type Options struct {
	WebRoot    string
	APIBaseURL string
}

func NewHandler(options Options) (http.Handler, error) {
	target, err := url.Parse(options.APIBaseURL)
	if err != nil {
		return nil, fmt.Errorf("parse API base URL: %w", err)
	}
	if target.Scheme == "" || target.Host == "" {
		return nil, fmt.Errorf("API base URL must include scheme and host")
	}

	proxy := httputil.NewSingleHostReverseProxy(target)
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set(
			"Content-Security-Policy",
			"default-src 'self'; img-src 'self' data: blob: https://tile.openstreetmap.org; "+
				"style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "+
				"font-src 'self' data:; worker-src 'self' blob:; frame-ancestors 'none'",
		)
		response.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")
		response.Header().Set("X-Content-Type-Options", "nosniff")
		response.Header().Set("X-Frame-Options", "DENY")
		response.Header().Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
		switch {
		case request.URL.Path == "/healthz":
			response.Header().Set("Content-Type", "text/plain; charset=utf-8")
			response.WriteHeader(http.StatusOK)
			_, _ = response.Write([]byte("ok\n"))
		case strings.HasPrefix(request.URL.Path, "/api/v1/admin/"):
			http.NotFound(response, request)
		case strings.HasPrefix(request.URL.Path, "/api/"):
			proxy.ServeHTTP(response, request)
		default:
			serveSPA(options.WebRoot, response, request)
		}
	}), nil
}

func serveSPA(webRoot string, response http.ResponseWriter, request *http.Request) {
	cleanPath := filepath.Clean("/" + request.URL.Path)
	requestedPath := filepath.Join(webRoot, filepath.FromSlash(strings.TrimPrefix(cleanPath, "/")))
	if info, err := os.Stat(requestedPath); err == nil && info.Mode().IsRegular() {
		http.ServeFile(response, request, requestedPath)
		return
	}

	http.ServeFile(response, request, filepath.Join(webRoot, "index.html"))
}
