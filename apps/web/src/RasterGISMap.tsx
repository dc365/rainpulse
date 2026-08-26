import { useEffect, useMemo, useRef, useState } from 'react'

import Feature from 'ol/Feature.js'
import Attribution from 'ol/control/Attribution.js'
import ScaleLine from 'ol/control/ScaleLine.js'
import { defaults as defaultControls } from 'ol/control/defaults.js'
import { getCenter } from 'ol/extent.js'
import Point from 'ol/geom/Point.js'
import Polygon from 'ol/geom/Polygon.js'
import LineString from 'ol/geom/LineString.js'
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
export type GISMapExtent = [number, number, number, number]
export type GISLegendEntry = { label: string, color: string }
export type GISReferenceContext = {
  coastline?: { url: string, extent: GISMapExtent }
  places?: readonly { name: string, coordinate: readonly [number, number] }[]
}
export type GISMotionVector = {
  longitude: number
  latitude: number
  end_longitude: number
  end_latitude: number
}

const DEFAULT_OPACITY = 0.72

const basemapUrl = import.meta.env.VITE_BASEMAP_URL?.trim()
  || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const basemapAttribution = import.meta.env.VITE_BASEMAP_ATTRIBUTION?.trim()
  || '© OpenStreetMap 贡献者'
const basemapLabel = import.meta.env.VITE_BASEMAP_LABEL?.trim() || 'OPENSTREETMAP'

function createReferenceLayer(places: NonNullable<GISReferenceContext['places']>) {
  const features = places.map((place) => new Feature({
    geometry: new Point([...place.coordinate]),
    kind: 'city',
    name: place.name,
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

function createMotionLayer(vectors: readonly GISMotionVector[]) {
  const features = vectors.flatMap((vector) => [
    new Feature({
      geometry: new LineString([
        [vector.longitude, vector.latitude],
        [vector.end_longitude, vector.end_latitude],
      ]),
      kind: 'motion-line',
    }),
    new Feature({
      geometry: new Point([vector.end_longitude, vector.end_latitude]),
      kind: 'motion-tip',
    }),
  ])
  return new VectorLayer({
    source: new VectorSource({ features }),
    style: (feature) => feature.get('kind') === 'motion-tip'
      ? new Style({
          image: new CircleStyle({
            radius: 2.2,
            fill: new Fill({ color: '#0b665a' }),
            stroke: new Stroke({ color: 'rgba(250,252,250,.92)', width: 1 }),
          }),
        })
      : new Style({
          stroke: new Stroke({ color: 'rgba(11,102,90,.82)', width: 1.4 }),
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

function clampCoordinate(coordinate: number[], extent: GISMapExtent) {
  return {
    longitude: Math.min(extent[2], Math.max(extent[0], coordinate[0])),
    latitude: Math.min(extent[3], Math.max(extent[1], coordinate[1])),
  }
}

function expandExtent(extent: GISMapExtent, ratio = 0.12): GISMapExtent {
  const longitudePadding = (extent[2] - extent[0]) * ratio
  const latitudePadding = (extent[3] - extent[1]) * ratio
  return [
    extent[0] - longitudePadding,
    extent[1] - latitudePadding,
    extent[2] + longitudePadding,
    extent[3] + latitudePadding,
  ]
}

interface RasterGISMapProps {
  imageUrl?: string
  imageDescription: string
  imageExtent: GISMapExtent
  fitExtent?: GISMapExtent
  validTimeLabel: string
  contextLabel: string
  productLabel: string
  legend: readonly GISLegendEntry[]
  legendUnit?: string
  legendMode?: 'scale' | 'categorical'
  footerNote: string
  mapLabel: string
  resetViewLabel: string
  point?: MapCoordinate
  pointValueLabel?: string
  bbox?: readonly number[]
  loading: boolean
  layerError: boolean
  onLayerError: (failed: boolean) => void
  onSelectPoint?: (point: MapCoordinate) => void
  referenceContext?: GISReferenceContext
  className?: string
  sharedView?: View
  comparisonMode?: boolean
  basemapVisible?: boolean
  smoothRaster?: boolean
  rasterOpacity?: number
  motionVectors?: readonly GISMotionVector[]
  motionVisible?: boolean
}

export function RasterGISMap({
  imageUrl,
  imageDescription,
  imageExtent,
  fitExtent = imageExtent,
  validTimeLabel,
  contextLabel,
  productLabel,
  legend,
  legendUnit = '',
  legendMode = 'scale',
  footerNote,
  mapLabel,
  resetViewLabel,
  point,
  pointValueLabel,
  bbox,
  loading,
  layerError,
  onLayerError,
  onSelectPoint,
  referenceContext,
  className = '',
  sharedView,
  comparisonMode = false,
  basemapVisible: controlledBasemapVisible,
  smoothRaster: controlledSmoothRaster,
  rasterOpacity: controlledRasterOpacity,
  motionVectors = [],
  motionVisible = false,
}: RasterGISMapProps) {
  const targetRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<OLMap | null>(null)
  const basemapLayerRef = useRef<TileLayer<XYZ> | null>(null)
  const coastlineLayerRef = useRef<ImageLayer<ImageStatic> | null>(null)
  const rasterLayerRef = useRef<ImageLayer<ImageStatic> | null>(null)
  const selectionLayerRef = useRef<VectorLayer<VectorSource> | null>(null)
  const motionLayerRef = useRef<VectorLayer<VectorSource> | null>(null)
  const onSelectPointRef = useRef(onSelectPoint)
  const imageExtentRef = useRef(imageExtent)
  const fitExtentRef = useRef(fitExtent)
  const referenceContextRef = useRef(referenceContext)
  const [localBasemapVisible, setLocalBasemapVisible] = useState(true)
  const [coastlineVisible, setCoastlineVisible] = useState(true)
  const [localSmoothRaster, setLocalSmoothRaster] = useState(true)
  const [localRasterOpacity, setLocalRasterOpacity] = useState(DEFAULT_OPACITY)
  const [hoverCoordinate, setHoverCoordinate] = useState<MapCoordinate | null>(null)
  const basemapVisible = controlledBasemapVisible ?? localBasemapVisible
  const smoothRaster = controlledSmoothRaster ?? localSmoothRaster
  const rasterOpacity = controlledRasterOpacity ?? localRasterOpacity

  useEffect(() => {
    onSelectPointRef.current = onSelectPoint
  }, [onSelectPoint])

  useEffect(() => {
    imageExtentRef.current = imageExtent
  }, [imageExtent])

  const fitExtentKey = fitExtent.join(',')

  useEffect(() => {
    fitExtentRef.current = fitExtent
  }, [fitExtent, fitExtentKey])

  useEffect(() => {
    referenceContextRef.current = referenceContext
  }, [referenceContext])

  useEffect(() => {
    if (!targetRef.current || typeof ResizeObserver === 'undefined') return

    const domainExtent = fitExtentRef.current
    const mapReference = referenceContextRef.current

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
    const coastlineLayer = mapReference?.coastline
      ? new ImageLayer<ImageStatic>({
          opacity: 0.78,
          source: new ImageStatic({
            url: mapReference.coastline.url,
            projection: 'EPSG:4326',
            imageExtent: [...mapReference.coastline.extent],
            interpolate: true,
          }),
        })
      : null
    const rasterLayer = new ImageLayer<ImageStatic>({ opacity: DEFAULT_OPACITY })
    const motionLayer = createMotionLayer(motionVectors)
    motionLayer.setVisible(motionVisible)
    const selectionLayer = createSelectionLayer()
    const referenceLayer = mapReference?.places?.length
      ? createReferenceLayer(mapReference.places)
      : null
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
    const viewExtent = expandExtent(domainExtent)
    const view = sharedView ?? new View({
      projection: 'EPSG:4326',
      center: getCenter(domainExtent),
      extent: viewExtent,
      constrainOnlyCenter: true,
      smoothExtentConstraint: true,
      minZoom: 5,
      maxZoom: 14,
    })
    const map = new OLMap({
      target: targetRef.current,
      layers: [
        basemapLayer,
        ...(coastlineLayer ? [coastlineLayer] : []),
        rasterLayer,
        motionLayer,
        graticule,
        ...(referenceLayer ? [referenceLayer] : []),
        selectionLayer,
      ],
      view,
      controls: defaultControls({ attribution: false, rotate: false, zoom: false }).extend([
        new Attribution({ collapsible: false }),
        new ScaleLine({ units: 'metric', minWidth: 92 }),
      ]),
    })

    view.fit(domainExtent, { padding: [54, 54, 54, 54], duration: 0 })
    const clickKey = map.on('click', (event) => {
      onSelectPointRef.current?.(clampCoordinate(event.coordinate, imageExtentRef.current))
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
    motionLayerRef.current = motionLayer
    selectionLayerRef.current = selectionLayer

    return () => {
      unByKey([clickKey, pointerKey])
      viewport.removeEventListener('pointerleave', clearHover)
      map.setTarget(undefined)
      mapRef.current = null
      basemapLayerRef.current = null
      coastlineLayerRef.current = null
      rasterLayerRef.current = null
      motionLayerRef.current = null
      selectionLayerRef.current = null
    }
  // Motion features and visibility are updated by the dedicated effect below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitExtentKey, referenceContext, sharedView])

  useEffect(() => {
    const target = targetRef.current
    const map = mapRef.current
    if (!target || !map || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => map.updateSize())
    observer.observe(target)
    return () => observer.disconnect()
  }, [fitExtentKey, referenceContext, sharedView])

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
      imageExtent: [...imageExtent],
      interpolate: smoothRaster,
    })
    source.once('imageloaderror', () => onLayerError(true))
    layer.setSource(source)
  }, [fitExtentKey, imageExtent, imageUrl, onLayerError, referenceContext, smoothRaster])

  useEffect(() => {
    const source = selectionLayerRef.current?.getSource()
    if (!source) return
    if (point && bbox?.length === 4) updateSelectionLayer(source, point, bbox)
    else source.clear()
  }, [bbox, fitExtentKey, point, referenceContext])

  useEffect(() => {
    basemapLayerRef.current?.setVisible(basemapVisible)
  }, [basemapVisible, fitExtentKey, referenceContext])

  useEffect(() => {
    coastlineLayerRef.current?.setVisible(coastlineVisible)
  }, [coastlineVisible, fitExtentKey, referenceContext])

  useEffect(() => {
    rasterLayerRef.current?.setOpacity(rasterOpacity)
  }, [fitExtentKey, rasterOpacity, referenceContext])

  useEffect(() => {
    const layer = motionLayerRef.current
    if (!layer) return
    const next = createMotionLayer(motionVectors)
    layer.setSource(next.getSource())
    layer.setVisible(motionVisible)
  }, [fitExtentKey, motionVectors, motionVisible, referenceContext])

  const coordinateLabel = useMemo(() => {
    if (!hoverCoordinate) {
      const coordinate = point ?? {
        longitude: (imageExtent[0] + imageExtent[2]) / 2,
        latitude: (imageExtent[1] + imageExtent[3]) / 2,
      }
      return `${coordinate.longitude.toFixed(2)}°E  ${coordinate.latitude.toFixed(2)}°N`
    }
    return `${hoverCoordinate.longitude.toFixed(4)}°E  ${hoverCoordinate.latitude.toFixed(4)}°N`
  }, [hoverCoordinate, imageExtent, point])

  const zoomBy = (delta: number) => {
    const view = mapRef.current?.getView()
    const zoom = view?.getZoom()
    if (view && zoom != null) view.animate({ zoom: zoom + delta, duration: 180 })
  }

  const resetView = () => {
    mapRef.current?.getView().fit(fitExtent, { padding: [54, 54, 54, 54], duration: 220 })
  }

  return (
    <div className={`nowcast-gis-shell ${onSelectPoint ? '' : 'readonly'} ${comparisonMode ? 'comparison' : ''} ${className}`.trim()}>
      <div
        ref={targetRef}
        className="nowcast-gis-map"
        role="application"
        aria-label={mapLabel}
        tabIndex={0}
      />
      <span className="sr-only" role="img" aria-label={imageDescription} data-source={imageUrl} data-extent={imageExtent.join(',')} />

      {!comparisonMode ? <div className="gis-display-controls" role="group" aria-label="地图图层控制">
        <button type="button" aria-pressed={basemapVisible} onClick={() => setLocalBasemapVisible((value) => !value)}>底图</button>
        {referenceContext?.coastline ? <button type="button" aria-pressed={coastlineVisible} onClick={() => setCoastlineVisible((value) => !value)}>海岸线</button> : null}
        <button type="button" aria-pressed={smoothRaster} onClick={() => setLocalSmoothRaster((value) => !value)}>{smoothRaster ? '平滑' : '格点'}</button>
        <label>
          <span>图层 {Math.round(rasterOpacity * 100)}%</span>
          <input aria-label="栅格图层透明度" type="range" min="0.35" max="0.95" step="0.05" value={rasterOpacity} onChange={(event) => setLocalRasterOpacity(Number(event.target.value))} />
        </label>
      </div> : null}

      {!comparisonMode ? <div className="gis-valid-time" aria-label={`数据有效时间 ${validTimeLabel}`}>
        <span>DATA VALID TIME</span>
        <code>{contextLabel}</code>
        <strong>{validTimeLabel}</strong>
      </div> : null}

      {!comparisonMode ? <div className="gis-map-controls" aria-label="地图导航">
        <button type="button" aria-label="地图放大" onClick={() => zoomBy(1)}>+</button>
        <button type="button" aria-label="地图缩小" onClick={() => zoomBy(-1)}>−</button>
        <button type="button" aria-label={resetViewLabel} onClick={resetView}>⌖</button>
      </div> : null}

      {!comparisonMode ? <div className="gis-coordinate-readout" aria-live="polite">
        <span>{hoverCoordinate ? '指针坐标' : point ? '当前选点' : '图层中心'}</span>
        <strong>{coordinateLabel}</strong>
        {!hoverCoordinate
          ? <small>{pointValueLabel ?? '移动指针读取经纬度'}</small>
          : <small>{onSelectPoint ? '点击选择该格点' : 'EPSG:4326'}</small>}
      </div> : null}

      {!comparisonMode ? <div className={`gis-legend ${legendMode}`} aria-label={`${productLabel}图例`}>
        <header><span>{productLabel}</span><strong>{legendUnit}</strong></header>
        {legendMode === 'categorical' ? (
          <div className="gis-legend-list">
            {legend.map((item) => <span key={`${item.label}-${item.color}`}><i style={{ backgroundColor: item.color }} /><small>{item.label}</small></span>)}
          </div>
        ) : (
          <div className="gis-legend-cells" style={{ gridTemplateColumns: `repeat(${legend.length}, minmax(0, 1fr))` }}>
            {legend.map((item) => (
              <span key={`${item.label}-${item.color}`}><i style={{ backgroundColor: item.color }} /><small>{item.label}</small></span>
            ))}
          </div>
        )}
        <footer><span>{basemapLabel}</span><small>{footerNote}</small></footer>
      </div> : null}

      {(!imageUrl || layerError) ? (
        <div className="gis-layer-empty" role="status">
          <strong>{loading ? '正在读取降水图层' : '降水图层暂不可用'}</strong>
          <small>{layerError ? '图层校验或网络请求失败' : '等待已发布的透明 PNG 产品'}</small>
        </div>
      ) : null}
    </div>
  )
}
