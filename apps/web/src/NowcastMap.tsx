import { useEffect, useMemo, useRef, useState } from 'react'

import Feature from 'ol/Feature.js'
import Attribution from 'ol/control/Attribution.js'
import ScaleLine from 'ol/control/ScaleLine.js'
import { defaults as defaultControls } from 'ol/control/defaults.js'
import { getCenter } from 'ol/extent.js'
import Point from 'ol/geom/Point.js'
import Polygon from 'ol/geom/Polygon.js'
import Graticule from 'ol/layer/Graticule.js'
import ImageLayer from 'ol/layer/Image.js'
import TileLayer from 'ol/layer/Tile.js'
import VectorLayer from 'ol/layer/Vector.js'
import OLMap from 'ol/Map.js'
import { unByKey } from 'ol/Observable.js'
import ImageStatic from 'ol/source/ImageStatic.js'
import VectorSource from 'ol/source/Vector.js'
import XYZ from 'ol/source/XYZ.js'
import { Circle as CircleStyle, Fill, Stroke, Style, Text } from 'ol/style.js'
import View from 'ol/View.js'
import 'ol/ol.css'

export type MapCoordinate = { longitude: number, latitude: number }

const GRID_EXTENT: [number, number, number, number] = [118, 25, 123, 27]
const PRODUCT_IMAGE_EXTENT: [number, number, number, number] = [117.995, 24.995, 123.005, 27.005]
const VIEW_EXTENT: [number, number, number, number] = [117.35, 24.65, 123.65, 27.35]
const DEFAULT_OPACITY = 0.72

const basemapUrl = import.meta.env.VITE_BASEMAP_URL?.trim()
  || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const basemapAttribution = import.meta.env.VITE_BASEMAP_ATTRIBUTION?.trim()
  || '© OpenStreetMap 贡献者'
const basemapLabel = import.meta.env.VITE_BASEMAP_LABEL?.trim() || 'OPENSTREETMAP'

const cities = [
  { name: '福州', coordinate: [119.2965, 26.0745] },
  { name: '宁德', coordinate: [119.527, 26.66] },
  { name: '南平', coordinate: [118.178, 26.642] },
  { name: '莆田', coordinate: [119.007, 25.454] },
] as const

function createReferenceLayer() {
  const features = cities.map((city) => new Feature({
    geometry: new Point([...city.coordinate]),
    kind: 'city',
    name: city.name,
  }))

  return new VectorLayer({
    source: new VectorSource({ features }),
    declutter: 'rainpulse-place-labels',
    style: (feature) => new Style({
      image: new CircleStyle({
        radius: 3.5,
        fill: new Fill({ color: '#123d36' }),
        stroke: new Stroke({ color: 'rgba(250,252,250,.95)', width: 1.5 }),
      }),
      text: new Text({
        text: String(feature.get('name')),
        offsetY: -14,
        fill: new Fill({ color: '#183d37' }),
        stroke: new Stroke({ color: 'rgba(250,252,250,.96)', width: 3 }),
        font: '700 11px -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans SC", sans-serif',
      }),
    }),
  })
}

function createSelectionLayer() {
  return new VectorLayer({
    source: new VectorSource(),
    style: (feature) => feature.get('kind') === 'point'
      ? new Style({
          image: new CircleStyle({
            radius: 7,
            fill: new Fill({ color: 'rgba(19,117,104,.24)' }),
            stroke: new Stroke({ color: '#f8fbfa', width: 3 }),
          }),
        })
      : new Style({
          fill: new Fill({ color: 'rgba(19,117,104,.10)' }),
          stroke: new Stroke({ color: '#0b665a', width: 1.5, lineDash: [5, 4] }),
        }),
  })
}

function updateSelectionLayer(
  source: VectorSource,
  point: MapCoordinate,
  bbox: readonly number[],
) {
  const [west, south, east, north] = bbox
  source.clear()
  source.addFeatures([
    new Feature({
      geometry: new Polygon([[
        [west, south], [east, south], [east, north], [west, north], [west, south],
      ]]),
      kind: 'area',
    }),
    new Feature({
      geometry: new Point([point.longitude, point.latitude]),
      kind: 'point',
    }),
  ])
}

function clampCoordinate(coordinate: number[]) {
  return {
    longitude: Math.min(GRID_EXTENT[2], Math.max(GRID_EXTENT[0], coordinate[0])),
    latitude: Math.min(GRID_EXTENT[3], Math.max(GRID_EXTENT[1], coordinate[1])),
  }
}

interface NowcastMapProps {
  imageUrl?: string
  imageDescription: string
  validTimeLabel: string
  leadLabel: string
  productLabel: string
  legend: readonly (readonly [number, string])[]
  legendUnit: string
  point: MapCoordinate
  pointValueLabel: string
  bbox: readonly number[]
  loading: boolean
  layerError: boolean
  onLayerError: (failed: boolean) => void
  onSelectPoint: (point: MapCoordinate) => void
}

export function NowcastMap({
  imageUrl,
  imageDescription,
  validTimeLabel,
  leadLabel,
  productLabel,
  legend,
  legendUnit,
  point,
  pointValueLabel,
  bbox,
  loading,
  layerError,
  onLayerError,
  onSelectPoint,
}: NowcastMapProps) {
  const targetRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<OLMap | null>(null)
  const basemapLayerRef = useRef<TileLayer<XYZ> | null>(null)
  const coastlineLayerRef = useRef<ImageLayer<ImageStatic> | null>(null)
  const rasterLayerRef = useRef<ImageLayer<ImageStatic> | null>(null)
  const selectionLayerRef = useRef<VectorLayer<VectorSource> | null>(null)
  const onSelectPointRef = useRef(onSelectPoint)
  const [basemapVisible, setBasemapVisible] = useState(true)
  const [coastlineVisible, setCoastlineVisible] = useState(true)
  const [smoothRaster, setSmoothRaster] = useState(true)
  const [rasterOpacity, setRasterOpacity] = useState(DEFAULT_OPACITY)
  const [hoverCoordinate, setHoverCoordinate] = useState<MapCoordinate | null>(null)

  useEffect(() => {
    onSelectPointRef.current = onSelectPoint
  }, [onSelectPoint])

  useEffect(() => {
    if (!targetRef.current || typeof ResizeObserver === 'undefined') return

    const basemapLayer = new TileLayer({
      className: 'rainpulse-basemap-layer',
      opacity: 0.84,
      preload: 0,
      source: new XYZ({
        attributions: basemapAttribution,
        crossOrigin: 'anonymous',
        url: basemapUrl,
      }),
    })
    const coastlineLayer = new ImageLayer<ImageStatic>({
      opacity: 0.78,
      source: new ImageStatic({
        url: '/coastline-fuzhou.svg',
        projection: 'EPSG:4326',
        imageExtent: GRID_EXTENT,
        interpolate: true,
      }),
    })
    const rasterLayer = new ImageLayer<ImageStatic>({ opacity: DEFAULT_OPACITY })
    const selectionLayer = createSelectionLayer()
    const graticule = new Graticule({
      showLabels: true,
      wrapX: false,
      strokeStyle: new Stroke({ color: 'rgba(20,74,66,.20)', width: 1 }),
      lonLabelStyle: new Text({
        font: '9px ui-monospace, SFMono-Regular, Menlo, monospace',
        fill: new Fill({ color: 'rgba(28,65,59,.68)' }),
        stroke: new Stroke({ color: 'rgba(250,252,250,.94)', width: 3 }),
      }),
      latLabelStyle: new Text({
        font: '9px ui-monospace, SFMono-Regular, Menlo, monospace',
        fill: new Fill({ color: 'rgba(28,65,59,.68)' }),
        stroke: new Stroke({ color: 'rgba(250,252,250,.94)', width: 3 }),
      }),
    })
    const view = new View({
      projection: 'EPSG:4326',
      center: getCenter(GRID_EXTENT),
      extent: VIEW_EXTENT,
      constrainOnlyCenter: true,
      smoothExtentConstraint: true,
      minZoom: 5,
      maxZoom: 14,
    })
    const map = new OLMap({
      target: targetRef.current,
      layers: [basemapLayer, coastlineLayer, rasterLayer, graticule, createReferenceLayer(), selectionLayer],
      view,
      controls: defaultControls({ attribution: false, rotate: false, zoom: false }).extend([
        new Attribution({ collapsible: false }),
        new ScaleLine({ units: 'metric', minWidth: 92 }),
      ]),
    })

    view.fit(GRID_EXTENT, { padding: [54, 54, 54, 54], duration: 0 })
    const clickKey = map.on('click', (event) => {
      onSelectPointRef.current(clampCoordinate(event.coordinate))
    })
    const pointerKey = map.on('pointermove', (event) => {
      if (event.dragging) return
      const [longitude, latitude] = event.coordinate
      setHoverCoordinate({ longitude, latitude })
    })
    const viewport = map.getViewport()
    const clearHover = () => setHoverCoordinate(null)
    viewport.addEventListener('pointerleave', clearHover)

    mapRef.current = map
    basemapLayerRef.current = basemapLayer
    coastlineLayerRef.current = coastlineLayer
    rasterLayerRef.current = rasterLayer
    selectionLayerRef.current = selectionLayer

    return () => {
      unByKey([clickKey, pointerKey])
      viewport.removeEventListener('pointerleave', clearHover)
      map.setTarget(undefined)
      mapRef.current = null
      basemapLayerRef.current = null
      coastlineLayerRef.current = null
      rasterLayerRef.current = null
      selectionLayerRef.current = null
    }
  }, [])

  useEffect(() => {
    const layer = rasterLayerRef.current
    if (!layer || !imageUrl) {
      layer?.setSource(null)
      return
    }
    onLayerError(false)
    const source = new ImageStatic({
      url: imageUrl,
      projection: 'EPSG:4326',
      imageExtent: PRODUCT_IMAGE_EXTENT,
      interpolate: smoothRaster,
    })
    source.once('imageloaderror', () => onLayerError(true))
    layer.setSource(source)
  }, [imageUrl, onLayerError, smoothRaster])

  useEffect(() => {
    const source = selectionLayerRef.current?.getSource()
    if (source) updateSelectionLayer(source, point, bbox)
  }, [bbox, point])

  useEffect(() => {
    basemapLayerRef.current?.setVisible(basemapVisible)
  }, [basemapVisible])

  useEffect(() => {
    coastlineLayerRef.current?.setVisible(coastlineVisible)
  }, [coastlineVisible])

  useEffect(() => {
    rasterLayerRef.current?.setOpacity(rasterOpacity)
  }, [rasterOpacity])

  const coordinateLabel = useMemo(() => {
    if (!hoverCoordinate) return `${point.longitude.toFixed(2)}°E  ${point.latitude.toFixed(2)}°N`
    return `${hoverCoordinate.longitude.toFixed(4)}°E  ${hoverCoordinate.latitude.toFixed(4)}°N`
  }, [hoverCoordinate, point])

  const zoomBy = (delta: number) => {
    const view = mapRef.current?.getView()
    const zoom = view?.getZoom()
    if (view && zoom != null) view.animate({ zoom: zoom + delta, duration: 180 })
  }

  const resetView = () => {
    mapRef.current?.getView().fit(GRID_EXTENT, { padding: [54, 54, 54, 54], duration: 220 })
  }

  return (
    <div className="nowcast-gis-shell">
      <div
        ref={targetRef}
        className="nowcast-gis-map"
        role="application"
        aria-label={`可交互降水 GIS 地图，${imageDescription}，点击地图选择预报点`}
        tabIndex={0}
      />
      <span className="sr-only" role="img" aria-label={imageDescription} data-source={imageUrl} />

      <div className="gis-display-controls" role="group" aria-label="地图图层控制">
        <button type="button" aria-pressed={basemapVisible} onClick={() => setBasemapVisible((value) => !value)}>底图</button>
        <button type="button" aria-pressed={coastlineVisible} onClick={() => setCoastlineVisible((value) => !value)}>海岸线</button>
        <button type="button" aria-pressed={smoothRaster} onClick={() => setSmoothRaster((value) => !value)}>{smoothRaster ? '平滑' : '格点'}</button>
        <label>
          <span>色斑 {Math.round(rasterOpacity * 100)}%</span>
          <input aria-label="降水色斑透明度" type="range" min="0.35" max="0.95" step="0.05" value={rasterOpacity} onChange={(event) => setRasterOpacity(Number(event.target.value))} />
        </label>
      </div>

      <div className="gis-valid-time" aria-label={`数据有效时间 ${validTimeLabel}`}>
        <span>DATA VALID TIME</span>
        <code>{leadLabel}</code>
        <strong>{validTimeLabel}</strong>
      </div>

      <div className="gis-map-controls" aria-label="地图导航">
        <button type="button" aria-label="地图放大" onClick={() => zoomBy(1)}>+</button>
        <button type="button" aria-label="地图缩小" onClick={() => zoomBy(-1)}>−</button>
        <button type="button" aria-label="复位福州网格范围" onClick={resetView}>⌖</button>
      </div>

      <div className="gis-coordinate-readout" aria-live="polite">
        <span>{hoverCoordinate ? '指针坐标' : '当前选点'}</span>
        <strong>{coordinateLabel}</strong>
        {!hoverCoordinate ? <small>{pointValueLabel}</small> : <small>点击选择该格点</small>}
      </div>

      <div className="gis-legend" aria-label="降水图例">
        <header><span>{productLabel}</span><strong>{legendUnit}</strong></header>
        <div className="gis-legend-cells" style={{ gridTemplateColumns: `repeat(${legend.length}, minmax(0, 1fr))` }}>
          {legend.map(([minimum, color]) => (
            <span key={minimum}><i style={{ backgroundColor: color }} /><small>{minimum}</small></span>
          ))}
        </div>
        <footer><span>{basemapLabel}</span><small>透明含缺测与 &lt;0.1</small></footer>
      </div>

      {(!imageUrl || layerError) ? (
        <div className="gis-layer-empty" role="status">
          <strong>{loading ? '正在读取降水图层' : '降水图层暂不可用'}</strong>
          <small>{layerError ? '图层校验或网络请求失败' : '等待已发布的透明 PNG 产品'}</small>
        </div>
      ) : null}
    </div>
  )
}
