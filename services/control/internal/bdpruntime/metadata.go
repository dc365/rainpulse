//go:build ruiyun_bdp

package bdpruntime

import (
	"fmt"
	"path/filepath"
	"strings"

	"bdp-publiccode-puremanage/pureconfig/credential"
	"bdp-publiccode-puremanage/purestorage/metadata"
)

type MetadataFetcher func(dataCode string) (*metadata.MetaDataInfo, error)
type FileCredentialFetcher func(configCode string) (*credential.FileService, error)

func ResolveOriginalFileSource(dataCode string, sourceIndex int) (OriginalFileSource, error) {
	return resolveOriginalFileSource(
		dataCode,
		sourceIndex,
		metadata.GetMetaDataInfoByDataCode,
		credential.GetFileServiceCredential,
	)
}

func resolveOriginalFileSource(
	dataCode string,
	sourceIndex int,
	metadataFetcher MetadataFetcher,
	credentialFetcher FileCredentialFetcher,
) (OriginalFileSource, error) {
	dataCode = strings.TrimSpace(dataCode)
	if dataCode == "" {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP radar data code is required")
	}
	if sourceIndex < 0 {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP radar source index cannot be negative")
	}
	info, err := metadataFetcher(dataCode)
	if err != nil {
		return OriginalFileSource{}, fmt.Errorf("load Ruiyun BDP metadata %s: %w", dataCode, err)
	}
	if info == nil || info.DataSource == nil {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP metadata %s has no original data source", dataCode)
	}
	if len(info.DataSource.DataSourceInfos) == 0 {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP metadata %s has no original data source entries", dataCode)
	}
	if sourceIndex >= len(info.DataSource.DataSourceInfos) {
		return OriginalFileSource{}, fmt.Errorf(
			"Ruiyun BDP metadata %s source index %d is outside 0..%d",
			dataCode, sourceIndex, len(info.DataSource.DataSourceInfos)-1,
		)
	}
	source := info.DataSource.DataSourceInfos[sourceIndex]
	credentialCode := strings.TrimSpace(source.CredentialConfigCode)
	if credentialCode == "" {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP metadata %s source %d has no file credential", dataCode, sourceIndex)
	}
	if len(source.FileDirs) == 0 || strings.TrimSpace(source.FileDirs[0]) == "" {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP metadata %s source %d has no file directory", dataCode, sourceIndex)
	}
	fileCredential, err := credentialFetcher(credentialCode)
	if err != nil {
		return OriginalFileSource{}, fmt.Errorf("load Ruiyun BDP file credential %s: %w", credentialCode, err)
	}
	if fileCredential == nil || strings.TrimSpace(fileCredential.FsHomeDir) == "" {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP file credential %s has no home directory", credentialCode)
	}

	directoryPrefix, err := staticDirectoryPrefix(source.FileDirs[0])
	if err != nil {
		return OriginalFileSource{}, fmt.Errorf("resolve Ruiyun BDP metadata %s source directory: %w", dataCode, err)
	}
	root := filepath.Clean(filepath.Join(
		filepath.FromSlash(strings.TrimSpace(fileCredential.FsHomeDir)),
		filepath.FromSlash(directoryPrefix),
	))
	if !filepath.IsAbs(root) {
		return OriginalFileSource{}, fmt.Errorf("Ruiyun BDP metadata %s resolved a non-absolute root %q", dataCode, root)
	}
	return OriginalFileSource{
		DataCode:             dataCode,
		DataFormat:           strings.TrimSpace(info.DataFormat),
		SourceIndex:          sourceIndex,
		SourceType:           strings.TrimSpace(source.DataSourceType),
		CredentialConfigCode: credentialCode,
		FileSystemType:       strings.TrimSpace(fileCredential.FsType),
		Root:                 root,
	}, nil
}

func staticDirectoryPrefix(template string) (string, error) {
	normalized := strings.ReplaceAll(strings.TrimSpace(template), "\\", "/")
	parts := strings.Split(normalized, "/")
	static := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if strings.ContainsAny(part, "{}") {
			break
		}
		static = append(static, part)
	}
	if len(static) == 0 {
		return "", fmt.Errorf("file directory template %q has no static prefix", template)
	}
	return filepath.Join(static...), nil
}
