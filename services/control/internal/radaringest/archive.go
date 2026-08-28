package radaringest

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

var unsafeFilename = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

type Archive struct {
	client *minio.Client
	bucket string
}

func NewArchive(endpoint, accessKey, secretKey, bucket string) (*Archive, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, fmt.Errorf("object-store endpoint must be an HTTP(S) URL")
	}
	if accessKey == "" || secretKey == "" || bucket == "" {
		return nil, fmt.Errorf("object-store credentials and bucket are required")
	}
	client, err := minio.New(parsed.Host, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: parsed.Scheme == "https",
	})
	if err != nil {
		return nil, fmt.Errorf("create object-store client: %w", err)
	}
	return &Archive{client: client, bucket: bucket}, nil
}

func (archive *Archive) File(
	ctx context.Context,
	radarID string,
	observedAt time.Time,
	sha256 string,
	path string,
) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", fmt.Errorf("stat radar arrival file: %w", err)
	}
	if !info.Mode().IsRegular() {
		return "", fmt.Errorf("radar arrival path is not a regular file")
	}
	key, err := ObjectKey(radarID, observedAt, sha256, filepath.Base(path))
	if err != nil {
		return "", err
	}

	if existing, statErr := archive.client.StatObject(
		ctx, archive.bucket, key, minio.StatObjectOptions{},
	); statErr == nil {
		if existing.Size != info.Size() {
			return "", fmt.Errorf("archived radar object size differs from arrival file")
		}
		return archiveURI(archive.bucket, key), nil
	} else if code := minio.ToErrorResponse(statErr).Code; code != "NoSuchKey" && code != "NoSuchObject" {
		return "", fmt.Errorf("inspect raw radar archive object: %w", statErr)
	}

	options := minio.PutObjectOptions{
		ContentType:  mediaType(path),
		UserMetadata: map[string]string{"sha256": sha256, "source": "radar-arrival"},
	}
	options.SetMatchETagExcept("*")
	if _, err := archive.client.FPutObject(ctx, archive.bucket, key, path, options); err != nil {
		code := minio.ToErrorResponse(err).Code
		if code != "PreconditionFailed" && code != "ConditionalRequestConflict" {
			return "", fmt.Errorf("archive raw radar object: %w", err)
		}
	}
	published, err := archive.client.StatObject(
		ctx, archive.bucket, key, minio.StatObjectOptions{},
	)
	if err != nil {
		return "", fmt.Errorf("verify raw radar archive object: %w", err)
	}
	if published.Size != info.Size() {
		return "", errors.New("archived radar object failed size verification")
	}
	return archiveURI(archive.bucket, key), nil
}

func ObjectKey(radarID string, observedAt time.Time, sha256, filename string) (string, error) {
	if radarID == "" || strings.ContainsAny(radarID, `/\\`) {
		return "", fmt.Errorf("radar ID is not safe for an archive key")
	}
	if len(sha256) != 64 {
		return "", fmt.Errorf("radar object SHA-256 is invalid")
	}
	for _, character := range sha256 {
		if !strings.ContainsRune("0123456789abcdef", character) {
			return "", fmt.Errorf("radar object SHA-256 is invalid")
		}
	}
	name := strings.Trim(unsafeFilename.ReplaceAllString(filepath.Base(filename), "_"), "._")
	if name == "" {
		name = "volume.bin"
	}
	stamp := observedAt.UTC()
	return fmt.Sprintf(
		"radar/raw/%s/%04d/%02d/%02d/%s/%s/%s",
		radarID,
		stamp.Year(), stamp.Month(), stamp.Day(), stamp.Format("150405.000000000Z"),
		sha256,
		name,
	), nil
}

func archiveURI(bucket, key string) string {
	return (&url.URL{Scheme: "s3", Host: bucket, Path: "/" + key}).String()
}

func mediaType(path string) string {
	if strings.HasSuffix(strings.ToLower(path), ".bz2") {
		return "application/x-bzip2"
	}
	return "application/octet-stream"
}
