package radaringest

import (
	"bytes"
	"compress/bzip2"
	"encoding/binary"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

type HistoricalFile struct {
	Path        string    `json:"path"`
	RadarID     string    `json:"radar_id"`
	HeaderCode  string    `json:"header_site_code"`
	GenericType uint32    `json:"generic_type"`
	Start       time.Time `json:"volume_start_time_utc"`
	End         time.Time `json:"volume_end_time_utc"`
	SizeBytes   int64     `json:"size_bytes"`
}

var historicalHeaderAliases = map[string]string{
	"zf602": "xmkg1", "zf603": "xmkg2", "zf604": "xmkg3",
}
var historicalFilenameStamp = regexp.MustCompile(`_(\d{14})_`)

// DiscoverHistoricalFiles inspects an explicit UTC day and accepts only direct
// station folders. File names and immutable headers must identify the source.
func DiscoverHistoricalFiles(root, radarID string, day time.Time, from, until time.Time) ([]HistoricalFile, error) {
	if !filepath.IsAbs(root) || !from.Before(until) ||
		!day.Equal(time.Date(day.Year(), day.Month(), day.Day(), 0, 0, 0, 0, time.UTC)) {
		return nil, fmt.Errorf("invalid historical radar discovery parameters")
	}
	day = day.UTC()
	if !from.Before(day.Add(24*time.Hour)) || !until.After(day) {
		return nil, fmt.Errorf("historical time window does not intersect UTC day")
	}
	station := strings.ToLower(strings.TrimSpace(radarID))
	dateRoot := filepath.Join(root, day.Format("2006"), day.Format("20060102"))
	if info, err := os.Stat(dateRoot); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("historical date directory is unavailable: %s", dateRoot)
	}
	result := make([]HistoricalFile, 0)
	err := filepath.WalkDir(dateRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || !isRadarFile(path) {
			return nil
		}
		relative, err := filepath.Rel(dateRoot, path)
		if err != nil {
			return err
		}
		parts := strings.Split(relative, string(filepath.Separator))
		if len(parts) != 2 || strings.ToLower(parts[0]) != station {
			return nil
		}
		if !strings.Contains(strings.ToLower(parts[1]), "_i_"+station+"_") {
			return nil
		}
		match := historicalFilenameStamp.FindStringSubmatch(parts[1])
		if match == nil {
			return fmt.Errorf("%s: missing filename UTC timestamp", path)
		}
		filenameTime, err := time.ParseInLocation("20060102150405", match[1], time.UTC)
		if err != nil {
			return fmt.Errorf("%s: invalid filename UTC timestamp: %w", path, err)
		}
		// Bound expensive decompression to the requested window. The ten-minute
		// margin permits normal scan offsets; header UTC decides final inclusion.
		if filenameTime.Before(from.Add(-10*time.Minute)) || !filenameTime.Before(until.Add(10*time.Minute)) {
			return nil
		}
		code, genericType, err := ProbeHeaderIdentity(path)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if strings.ToLower(code) != station && strings.ToLower(code) != historicalHeaderAliases[station] {
			return fmt.Errorf("%s: header site %s does not match %s", path, code, radarID)
		}
		start, end, err := ProbeVolumeTimes(path)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if start.Sub(filenameTime) > 10*time.Minute || filenameTime.Sub(start) > 10*time.Minute {
			return fmt.Errorf("%s: filename and header UTC differ by more than ten minutes", path)
		}
		if start.Before(from) || !start.Before(until) {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		result = append(result, HistoricalFile{Path: path, RadarID: station, HeaderCode: code, GenericType: genericType, Start: start, End: end, SizeBytes: info.Size()})
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("discover historical radar files: %w", err)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Start.Equal(result[j].Start) {
			return result[i].Path < result[j].Path
		}
		return result[i].Start.Before(result[j].Start)
	})
	return result, nil
}

func ProbeHeaderIdentity(path string) (string, uint32, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer file.Close()
	var reader io.Reader = file
	if strings.HasSuffix(strings.ToLower(path), ".bz2") {
		reader = bzip2.NewReader(file)
	}
	generic, err := readExact(reader, genericHeaderSize, "generic header")
	if err != nil {
		return "", 0, err
	}
	if binary.LittleEndian.Uint32(generic[:4]) != rstmMagic {
		return "", 0, fmt.Errorf("invalid RSTM magic number")
	}
	if binary.LittleEndian.Uint16(generic[4:6]) != 2 || binary.LittleEndian.Uint16(generic[6:8]) != 0 {
		return "", 0, fmt.Errorf("unsupported RSTM version")
	}
	genericType := binary.LittleEndian.Uint32(generic[8:12])
	if genericType != 1 && genericType != 16 {
		return "", 0, fmt.Errorf("unsupported RSTM generic type %d", genericType)
	}
	site, err := readExact(reader, siteConfigSize, "site configuration")
	if err != nil {
		return "", 0, err
	}
	code := strings.TrimSpace(string(bytes.SplitN(site[:8], []byte{0}, 2)[0]))
	if code == "" {
		return "", 0, fmt.Errorf("empty RSTM site code")
	}
	return code, genericType, nil
}
