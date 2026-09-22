package releaseguard

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

func FileSHA256(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	stat, err := file.Stat()
	if err != nil {
		return "", err
	}
	if !stat.Mode().IsRegular() || stat.Size() > 4*1024*1024 {
		return "", fmt.Errorf("release config must be a regular file <= 4 MiB")
	}
	hash := sha256.New()
	if _, err = io.Copy(hash, file); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hash.Sum(nil)), nil
}

// PublishPlannerIdentity reflects the resolved BDP/environment config path, not
// a guessed .env value. It is refreshed even while admission is paused.
func PublishPlannerIdentity(configPath, loadedHash, mode string) error {
	current, err := FileSHA256(configPath)
	if err != nil {
		return err
	}
	gate, err := filepath.Abs(Path())
	if err != nil {
		return err
	}
	configPath, err = filepath.Abs(configPath)
	if err != nil {
		return err
	}
	body, err := json.Marshal(map[string]any{
		"schema_version": 1, "pid": os.Getpid(), "updated_at": time.Now().UTC(),
		"gate_path": gate, "qc_config_path": configPath, "execution_mode": mode,
		"loaded_qc_sha256": loadedHash, "current_qc_sha256": current,
	})
	if err != nil {
		return err
	}
	dir := filepath.Dir(gate)
	if err := os.MkdirAll(dir, 0750); err != nil {
		return err
	}
	temp, err := os.CreateTemp(dir, ".planner-release-*")
	if err != nil {
		return err
	}
	name := temp.Name()
	defer os.Remove(name)
	if err := temp.Chmod(0640); err != nil {
		temp.Close()
		return err
	}
	if _, err := temp.Write(body); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(name, filepath.Join(dir, "planner-release.json")); err != nil {
		return err
	}
	if loadedHash == "" || current != loadedHash {
		return fmt.Errorf("QC configuration changed after planner startup; pause and coordinate a restart before admitting new work")
	}
	return nil
}
