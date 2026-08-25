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
const MaximumProductAssetBytes = 32 << 20

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

func (reader *Reader) ReadObject(
	ctx context.Context,
	objectURI string,
	maximumBytes int64,
) ([]byte, string, error) {
	bucket, objectName, err := parseObjectURI(objectURI)
	if err != nil {
		return nil, "", err
	}
	if maximumBytes <= 0 || maximumBytes > MaximumProductAssetBytes {
		return nil, "", fmt.Errorf("product object size limit is invalid")
	}
	info, err := reader.client.StatObject(ctx, bucket, objectName, minio.StatObjectOptions{})
	if err != nil {
		if isNotFound(err) {
			return nil, "", ErrNotFound
		}
		return nil, "", fmt.Errorf("stat product object: %w", err)
	}
	if info.Size < 0 || info.Size > maximumBytes {
		return nil, "", fmt.Errorf("product object exceeds the API size limit")
	}
	object, err := reader.client.GetObject(ctx, bucket, objectName, minio.GetObjectOptions{})
	if err != nil {
		return nil, "", fmt.Errorf("open product object: %w", err)
	}
	defer object.Close()
	data, err := io.ReadAll(io.LimitReader(object, maximumBytes+1))
	if err != nil {
		return nil, "", fmt.Errorf("read product object: %w", err)
	}
	if int64(len(data)) > maximumBytes {
		return nil, "", fmt.Errorf("product object exceeds the API size limit")
	}
	return data, strings.Trim(info.ETag, "\""), nil
}

func (reader *Reader) ReadRange(
	ctx context.Context,
	objectURI string,
	offset int64,
	length int64,
) ([]byte, int64, string, error) {
	bucket, objectName, err := parseObjectURI(objectURI)
	if err != nil {
		return nil, 0, "", err
	}
	if offset < 0 || length <= 0 || length > MaximumProductAssetBytes {
		return nil, 0, "", fmt.Errorf("product object range is invalid")
	}
	info, err := reader.client.StatObject(ctx, bucket, objectName, minio.StatObjectOptions{})
	if err != nil {
		if isNotFound(err) {
			return nil, 0, "", ErrNotFound
		}
		return nil, 0, "", fmt.Errorf("stat product object range: %w", err)
	}
	if offset > info.Size || length > info.Size-offset {
		return nil, 0, "", fmt.Errorf("product object range exceeds object size")
	}
	options := minio.GetObjectOptions{}
	if err := options.SetRange(offset, offset+length-1); err != nil {
		return nil, 0, "", fmt.Errorf("set product object range: %w", err)
	}
	object, err := reader.client.GetObject(ctx, bucket, objectName, options)
	if err != nil {
		return nil, 0, "", fmt.Errorf("open product object range: %w", err)
	}
	defer object.Close()
	data, err := io.ReadAll(io.LimitReader(object, length+1))
	if err != nil {
		return nil, 0, "", fmt.Errorf("read product object range: %w", err)
	}
	if int64(len(data)) != length {
		return nil, 0, "", fmt.Errorf("product object range byte length differs")
	}
	return data, info.Size, strings.Trim(info.ETag, "\""), nil
}

func parseObjectURI(objectURI string) (string, string, error) {
	parsed, err := url.Parse(objectURI)
	if err != nil || parsed.Scheme != "s3" || parsed.Host == "" ||
		parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", "", fmt.Errorf("product object URI is invalid")
	}
	objectName := strings.Trim(parsed.Path, "/")
	if objectName == "" || path.Clean(objectName) != objectName ||
		strings.HasPrefix(objectName, "../") {
		return "", "", fmt.Errorf("product object key is invalid")
	}
	return parsed.Host, objectName, nil
}

func isNotFound(err error) bool {
	response := minio.ToErrorResponse(err)
	return response.Code == "NoSuchKey" || response.Code == "NoSuchObject" ||
		response.Code == "NoSuchBucket"
}
