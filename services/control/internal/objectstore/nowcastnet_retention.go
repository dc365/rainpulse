package objectstore

import (
	"context"
	"fmt"
	"github.com/google/uuid"
	"github.com/minio/minio-go/v7"
	"net/url"
	"path"
	"strings"
)

func nowcastNetDeletionPrefix(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "s3" || u.Host != "rainpulse" || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || path.Clean(u.Path) != u.Path {
		return "", fmt.Errorf("unsafe product URI")
	}
	parts := strings.Split(strings.TrimPrefix(u.Path, "/"), "/")
	if (len(parts) != 6 && len(parts) != 7) || parts[0] != "products" || parts[2] != "nowcastnet" || parts[len(parts)-1] != "nowcastnet-shadow-products" {
		return "", fmt.Errorf("not a bounded NowcastNet bundle")
	}
	if _, err := uuid.Parse(parts[1]); err != nil {
		return "", err
	}
	if len(parts) == 7 {
		if _, err := uuid.Parse(parts[5]); err != nil {
			return "", err
		}
	}
	return strings.Join(parts, "/") + "/", nil
}

// Caller proves this bundle is superseded; never accepts radar/QPE/bucket roots.
func (reader *Reader) DeleteNowcastNetBundle(ctx context.Context, raw string) error {
	prefix, err := nowcastNetDeletionPrefix(raw)
	if err != nil {
		return err
	}
	keys := []string{}
	for item := range reader.client.ListObjects(ctx, "rainpulse", minio.ListObjectsOptions{Prefix: prefix, Recursive: true}) {
		if item.Err != nil {
			return item.Err
		}
		if !strings.HasPrefix(item.Key, prefix) {
			return fmt.Errorf("object leaves bundle prefix")
		}
		keys = append(keys, item.Key)
	}
	for _, key := range keys {
		if err := reader.client.RemoveObject(ctx, "rainpulse", key, minio.RemoveObjectOptions{}); err != nil {
			return err
		}
	}
	return nil
}
