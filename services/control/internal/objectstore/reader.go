package objectstore

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path"
	"strings"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

const maximumDiagnosticLayerBytes = 16 << 20

var ErrNotFound = errors.New("object not found")

type Reader struct {
	client *minio.Client
}

func NewFromEnvironment() (*Reader, error) {
	endpoint := os.Getenv("RAINPULSE_OBJECT_STORE_ENDPOINT")
	accessKey := os.Getenv("RAINPULSE_OBJECT_STORE_ACCESS_KEY")
	secretKey := os.Getenv("RAINPULSE_OBJECT_STORE_SECRET_KEY")
	if endpoint == "" || accessKey == "" || secretKey == "" {
		return nil, fmt.Errorf("object-store endpoint and read credentials are required")
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, fmt.Errorf("object-store endpoint must be an HTTP URL")
	}
	client, err := minio.New(parsed.Host, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: parsed.Scheme == "https",
	})
	if err != nil {
		return nil, fmt.Errorf("create object-store reader: %w", err)
	}
	return &Reader{client: client}, nil
}

func (reader *Reader) Read(
	ctx context.Context,
	artifactURI string,
	relativePath string,
) ([]byte, string, error) {
	parsed, err := url.Parse(artifactURI)
	if err != nil || parsed.Scheme != "s3" || parsed.Host == "" {
		return nil, "", fmt.Errorf("diagnostic artifact URI is invalid")
	}
	clean := path.Clean(relativePath)
	if clean != relativePath || clean == "." || strings.HasPrefix(clean, "../") ||
		strings.HasPrefix(clean, "/") {
		return nil, "", fmt.Errorf("diagnostic layer path is invalid")
	}
	objectName := strings.Trim(parsed.Path, "/") + "/" + clean
	info, err := reader.client.StatObject(ctx, parsed.Host, objectName, minio.StatObjectOptions{})
	if err != nil {
		if isNotFound(err) {
			return nil, "", ErrNotFound
		}
		return nil, "", fmt.Errorf("stat diagnostic layer: %w", err)
	}
	if info.Size < 0 || info.Size > maximumDiagnosticLayerBytes {
		return nil, "", fmt.Errorf("diagnostic layer exceeds the API size limit")
	}
	object, err := reader.client.GetObject(ctx, parsed.Host, objectName, minio.GetObjectOptions{})
	if err != nil {
		return nil, "", fmt.Errorf("open diagnostic layer: %w", err)
	}
	defer object.Close()
	data, err := io.ReadAll(io.LimitReader(object, maximumDiagnosticLayerBytes+1))
	if err != nil {
		return nil, "", fmt.Errorf("read diagnostic layer: %w", err)
	}
	if len(data) > maximumDiagnosticLayerBytes {
		return nil, "", fmt.Errorf("diagnostic layer exceeds the API size limit")
	}
	return data, strings.Trim(info.ETag, "\""), nil
}

func isNotFound(err error) bool {
	response := minio.ToErrorResponse(err)
	return response.Code == "NoSuchKey" || response.Code == "NoSuchObject" ||
		response.Code == "NoSuchBucket"
}
