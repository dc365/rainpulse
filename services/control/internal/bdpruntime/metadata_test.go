//go:build ruiyun_bdp

package bdpruntime

import (
	"errors"
	"testing"

	"bdp-publiccode-puremanage/pureconfig/credential"
	"bdp-publiccode-puremanage/purestorage/metadata"
)

func TestResolveOriginalFileSourceFromRuiyunMetadata(t *testing.T) {
	metadataFetcher := func(dataCode string) (*metadata.MetaDataInfo, error) {
		if dataCode != DefaultDataCode {
			t.Fatalf("data code = %q", dataCode)
		}
		return &metadata.MetaDataInfo{
			DataCode:   dataCode,
			DataFormat: "bin",
			DataSource: &metadata.DataSource{DataSourceInfos: []metadata.DataSourceInfo{{
				DataSourceType:       "disk",
				CredentialConfigCode: "bdp_pm_config_common_credential_fs_nas",
				FileDirs: []string{
					"RADA/RADA_L2_FMT/OBS_TEMP/{yyyy}/{yyyy}{MM}{dd}/{station}",
				},
			}}},
		}, nil
	}
	credentialFetcher := func(configCode string) (*credential.FileService, error) {
		if configCode != "bdp_pm_config_common_credential_fs_nas" {
			t.Fatalf("credential code = %q", configCode)
		}
		return &credential.FileService{FsType: "nas", FsHomeDir: "/data/Weather/"}, nil
	}

	source, err := resolveOriginalFileSource(DefaultDataCode, 0, metadataFetcher, credentialFetcher)
	if err != nil {
		t.Fatal(err)
	}
	if source.Root != "/data/Weather/RADA/RADA_L2_FMT/OBS_TEMP" {
		t.Fatalf("resolved root = %q", source.Root)
	}
	if source.DataFormat != "bin" || source.FileSystemType != "nas" || source.SourceType != "disk" {
		t.Fatalf("unexpected source metadata: %#v", source)
	}
}

func TestResolveOriginalFileSourceRejectsIncompleteMetadata(t *testing.T) {
	_, err := resolveOriginalFileSource(
		DefaultDataCode,
		0,
		func(string) (*metadata.MetaDataInfo, error) {
			return &metadata.MetaDataInfo{DataSource: &metadata.DataSource{}}, nil
		},
		func(string) (*credential.FileService, error) {
			return nil, errors.New("must not be called")
		},
	)
	if err == nil {
		t.Fatal("expected incomplete metadata to fail")
	}
}

func TestStaticDirectoryPrefix(t *testing.T) {
	got, err := staticDirectoryPrefix(`RADA\RADA_L2_FMT\OBS_TEMP\{yyyy}\{station}`)
	if err != nil {
		t.Fatal(err)
	}
	if got != "RADA/RADA_L2_FMT/OBS_TEMP" {
		t.Fatalf("static prefix = %q", got)
	}
}
