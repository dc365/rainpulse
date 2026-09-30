package operations

import (
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// basemapTilesPrefix proxies configured XYZ tile sources through the control
// plane. Clients stay same-origin (works without client internet egress) and
// upstream URLs — including the Tianditu browser key via {token} — never leave
// the server. Path: {sourceKey}/{z}/{x}/{y}.png; a "-annot" suffix selects the
// source's annotation overlay.
const basemapTilesPrefix = radarWorkspacePrefix + "basemap-tiles/"

const basemapTileMaxBytes = 8 << 20

var basemapTileClient = &http.Client{Timeout: 15 * time.Second}

const tiandituBrowserUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"

func (h *Handler) basemapTile(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, basemapTilesPrefix)
	parts := strings.Split(rest, "/")
	if len(parts) != 4 || !strings.HasSuffix(parts[3], ".png") {
		writeProblem(w, problem(400, "invalid_request", "瓦片路径应为 {源}/{z}/{x}/{y}.png"))
		return
	}
	key := parts[0]
	z, errZ := strconv.Atoi(parts[1])
	x, errX := strconv.Atoi(parts[2])
	y, errY := strconv.Atoi(strings.TrimSuffix(parts[3], ".png"))
	if errZ != nil || errX != nil || errY != nil || z < 1 || z > 18 || x < 0 || y < 0 || x >= 1<<z || y >= 1<<z {
		writeProblem(w, problem(400, "invalid_request", "瓦片坐标超出范围"))
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		writeProblem(w, problem(405, "invalid_request", "仅支持 GET/HEAD"))
		return
	}
	template, isTianditu, found := h.basemapSourceByKey(key)
	if !found {
		writeProblem(w, problem(404, "not_found", "未配置的底图源："+strings.TrimSuffix(key, basemapAnnotationSuffix)))
		return
	}
	upstream, err := renderBasemapTemplate(template, z, x, y, strings.TrimSpace(h.options.TiandituToken))
	if err != nil {
		writeProblem(w, problem(503, "unavailable", "该底图源需要天地图 token（RAINPULSE_TIANDITU_TOKEN 或 deploy/tianditu-token）"))
		return
	}

	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, upstream, nil)
	if err != nil {
		writeProblem(w, problem(502, "upstream", "构造瓦片请求失败"))
		return
	}
	req.Header.Set("User-Agent", tiandituBrowserUserAgent)
	if isTianditu {
		// The Tianditu key is browser-type: it checks Referer.
		req.Header.Set("Referer", "http://"+r.Host+"/")
	}

	resp, err := basemapTileClient.Do(req)
	if err != nil {
		writeProblem(w, problem(502, "upstream", "瓦片上游请求失败"))
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		writeProblem(w, problem(502, "upstream", fmt.Sprintf("瓦片上游返回 %d", resp.StatusCode)))
		return
	}

	contentType := resp.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "image/png"
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Cache-Control", "public, max-age=86400, stale-while-revalidate=604800")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, _ = io.Copy(w, io.LimitReader(resp.Body, basemapTileMaxBytes))
}
