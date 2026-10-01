package operations

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Basemap sources are server-side tile URL templates so clients only ever
// request this origin through the basemap-tiles proxy. The intranet mirror is
// the default; Tianditu stays available and keeps its token server-side via
// the {token} placeholder.
type BasemapSource struct {
	Key     string `json:"key"`
	Label   string `json:"label"`
	URL     string `json:"url"`
	Overlay string `json:"overlay,omitempty"`
}

type BasemapConfig struct {
	Default string          `json:"default"`
	Sources []BasemapSource `json:"sources"`
}

const basemapAnnotationSuffix = "-annot"

func intranetTiandituBaseURL() string {
	return "http://192.168.18.101:20001/Files/MapFile/TIANDITU/QUANGUO"
}

func DefaultBasemapConfig() BasemapConfig {
	base := intranetTiandituBaseURL()
	tianditu := "https://t{0-7}.tianditu.gov.cn/DataServer?T=%s&x={x}&y={y}&l={z}&tk={token}"
	return BasemapConfig{
		Default: "XZ",
		Sources: []BasemapSource{
			{Key: "XZ", Label: "行政", URL: base + "/XINGZHENG/Mercator/{z}/{x}/{y}.png"},
			{Key: "DX", Label: "地形", URL: base + "/DIXING/Mercator/{z}/{x}/{y}.png"},
			{Key: "WX", Label: "卫星", URL: base + "/WEIXING/Mercator/{z}/{x}/{y}.png"},
			{Key: "TDT", Label: "天地图", URL: fmt.Sprintf(tianditu, "vec_w"), Overlay: fmt.Sprintf(tianditu, "cva_w")},
		},
	}
}

var basemapKeyPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,16}$`)
var basemapRangePattern = regexp.MustCompile(`\{(\d)-(\d)\}`)

func validateBasemapConfig(config BasemapConfig) error {
	if len(config.Sources) == 0 {
		return Invalid("至少需要一个底图源")
	}
	if len(config.Sources) > 16 {
		return Invalid("底图源最多 16 个")
	}
	seen := map[string]bool{}
	for _, source := range config.Sources {
		if !basemapKeyPattern.MatchString(source.Key) {
			return Invalid("底图源 key 仅限 1-16 位字母数字、下划线或连字符：" + source.Key)
		}
		if seen[source.Key] {
			return Invalid("底图源 key 重复：" + source.Key)
		}
		seen[source.Key] = true
		if strings.TrimSpace(source.Label) == "" || len([]rune(source.Label)) > 16 {
			return Invalid("底图源名称不能为空且不超过 16 字：" + source.Key)
		}
		if err := validateBasemapTemplate(source.URL, source.Key); err != nil {
			return err
		}
		if source.Overlay != "" {
			if err := validateBasemapTemplate(source.Overlay, source.Key+" 注记"); err != nil {
				return err
			}
		}
	}
	if !seen[config.Default] {
		return Invalid("默认底图源必须在配置列表中：" + config.Default)
	}
	return nil
}

func validateBasemapTemplate(template, name string) error {
	if !strings.HasPrefix(template, "http://") && !strings.HasPrefix(template, "https://") {
		return Invalid("底图地址必须以 http(s):// 开头：" + name)
	}
	for _, placeholder := range []string{"{z}", "{x}", "{y}"} {
		if !strings.Contains(template, placeholder) {
			return Invalid("底图地址缺少 " + placeholder + " 占位符：" + name)
		}
	}
	return nil
}

// renderBasemapTemplate substitutes tile coordinates, the server-side Tianditu
// token and one {n-m} subdomain range (deterministic per tile for cache
// friendliness).
func renderBasemapTemplate(template string, z, x, y int, token string) (string, error) {
	rendered := strings.NewReplacer("{z}", fmt.Sprint(z), "{x}", fmt.Sprint(x), "{y}", fmt.Sprint(y)).Replace(template)
	if m := basemapRangePattern.FindString(rendered); m != "" {
		var from, to int
		if _, err := fmt.Sscanf(m, "{%d-%d}", &from, &to); err == nil && from <= to && to-from < 16 {
			pick := from + (x*7+y*3)%(to-from+1)
			rendered = strings.Replace(rendered, m, fmt.Sprint(pick), 1)
		}
	}
	if strings.Contains(rendered, "{token}") {
		if token == "" {
			return "", errors.New("tianditu token not configured")
		}
		rendered = strings.ReplaceAll(rendered, "{token}", token)
	}
	return rendered, nil
}

func (h *Handler) loadBasemapConfig() BasemapConfig {
	h.basemapOnce.Do(func() {
		config := DefaultBasemapConfig()
		path := h.options.BasemapConfigPath
		if data, err := os.ReadFile(path); err == nil {
			var loaded BasemapConfig
			if err := json.Unmarshal(data, &loaded); err == nil && validateBasemapConfig(loaded) == nil {
				config = loaded
			} else if err != nil {
				slog.Warn("RainPulse basemap config is invalid; using defaults", "path", path, "error", err)
			} else {
				slog.Warn("RainPulse basemap config is invalid; using defaults", "path", path)
			}
		}
		h.basemapMu.Lock()
		h.basemapCfg = config
		h.basemapMu.Unlock()
	})
	h.basemapMu.RLock()
	defer h.basemapMu.RUnlock()
	return h.basemapCfg
}

func (h *Handler) saveBasemapConfig(config BasemapConfig) error {
	if err := validateBasemapConfig(config); err != nil {
		return err
	}
	data, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	path := h.options.BasemapConfigPath
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return problem(500, "storage", "无法创建配置目录："+err.Error())
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o644); err != nil {
		return problem(500, "storage", "无法写入配置文件："+err.Error())
	}
	if err := os.Rename(tmp, path); err != nil {
		return problem(500, "storage", "无法替换配置文件："+err.Error())
	}
	h.basemapMu.Lock()
	h.basemapCfg = config
	h.basemapMu.Unlock()
	return nil
}

// basemapSourceByKey resolves a proxy key to its template. The "-annot" suffix
// selects the annotation overlay. Legacy Tianditu layer names (vec/cva/img/cia)
// keep older builds working without a config entry.
func (h *Handler) basemapSourceByKey(key string) (url string, isTianditu bool, ok bool) {
	annot := strings.HasSuffix(key, basemapAnnotationSuffix)
	base := strings.TrimSuffix(key, basemapAnnotationSuffix)
	config := h.loadBasemapConfig()
	for _, source := range config.Sources {
		if source.Key != base {
			continue
		}
		template := source.URL
		if annot {
			if source.Overlay == "" {
				return "", false, false
			}
			template = source.Overlay
		}
		return template, strings.Contains(template, "tianditu.gov.cn"), true
	}
	if legacy, found := map[string]string{
		"vec": "vec_w", "cva": "cva_w", "img": "img_w", "cia": "cia_w",
	}[base]; found && !annot {
		return fmt.Sprintf("https://t{0-7}.tianditu.gov.cn/DataServer?T=%s&x={x}&y={y}&l={z}&tk={token}", legacy), true, true
	}
	return "", false, false
}

func (h *Handler) basemapConfigPublic(w http.ResponseWriter, r *http.Request) {
	config := h.loadBasemapConfig()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	type option struct {
		Key        string `json:"key"`
		Label      string `json:"label"`
		HasOverlay bool   `json:"hasOverlay"`
	}
	options := make([]option, 0, len(config.Sources))
	for _, source := range config.Sources {
		options = append(options, option{Key: source.Key, Label: source.Label, HasOverlay: source.Overlay != ""})
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"default": config.Default, "sources": options})
}

func (h *Handler) basemapConfigAdmin(w http.ResponseWriter, r *http.Request) (any, error) {
	if r.Method == http.MethodGet {
		return h.loadBasemapConfig(), nil
	}
	if r.Method == http.MethodPut || r.Method == http.MethodPost {
		var config BasemapConfig
		if err := decodeBody(r, &config); err != nil {
			return nil, err
		}
		if err := h.saveBasemapConfig(config); err != nil {
			return nil, err
		}
		return h.loadBasemapConfig(), nil
	}
	w.Header().Set("Allow", "GET, PUT, POST")
	return nil, problem(405, "invalid_request", "仅支持 GET/PUT")
}
