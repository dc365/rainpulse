import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

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
import Overlay from 'ol/Overlay.js'
import { unByKey } from 'ol/Observable.js'
import ImageStatic from 'ol/source/ImageStatic.js'
import VectorSource from 'ol/source/Vector.js'
import XYZ from 'ol/source/XYZ.js'
import { Circle as CircleStyle, Fill, Stroke, Style, Text } from 'ol/style.js'
import View from 'ol/View.js'
import 'ol/ol.css'

import {
  rasterValueAtCell,
  rasterValueAtCoordinate,
  type RasterPixels,
  type RasterValue,
} from './rasterSampling'
import { radarRangeGeometry, type RadarSiteMetadata } from './radarSites'

export type MapCoordinate = { longitude: number, latitude: number }
export type GISMapExtent = [number, number, number, number]
export type GISLegendEntry = {
  label: string
  color: string
  minimum?: number
  sourceLabel?: string
}
export type GISRasterStyle = 'grid' | 'smooth'
export type GISReferenceContext = {
  coastline?: { url: string, extent: GISMapExtent }
  places?: readonly { name: string, coordinate: readonly [number, number] }[]
}
export type GISRadarContext = RadarSiteMetadata & {
  timeOffsetSeconds?: number
  meanQualityIndex?: number
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

function createRadarReferenceLayer(radar: GISRadarContext) {
  const geometry = radarRangeGeometry(radar)
  const features = [
    ...geometry.rings.map((ring) => new Feature({
      geometry: new Polygon([ring.coordinates.map((coordinate) => [...coordinate])]),
      kind: 'radar-range-ring',
    })),
    ...geometry.axes.map((coordinates) => new Feature({
      geometry: new LineString(coordinates.map((coordinate) => [...coordinate])),
      kind: 'radar-range-axis',
    })),
    ...geometry.labels.map((label) => new Feature({
      geometry: new Point([...label.coordinate]),
      kind: 'radar-range-label',
      label: `${label.radiusKM} km`,
    })),
    new Feature({
      geometry: new Point([radar.longitude, radar.latitude]),
      kind: 'radar-center',
    }),
  ]

  return new VectorLayer({
    source: new VectorSource({ features }),
    declutter: 'rainpulse-radar-reference',
    style: (feature) => {
      const kind = feature.get('kind')
      if (kind === 'radar-center') {
        return new Style({
          image: new CircleStyle({
            radius: 4.5,
            fill: new Fill({ color: '#073f38' }),
            stroke: new Stroke({ color: 'rgba(250,252,250,.96)', width: 2 }),
          }),
        })
      }
      if (kind === 'radar-range-label') {
        return new Style({
          text: new Text({
            text: String(feature.get('label')),
            offsetX: 5,
            textAlign: 'left',
            font: '700 9px ui-monospace, SFMono-Regular, Menlo, monospace',
            fill: new Fill({ color: 'rgba(15,54,48,.88)' }),
            stroke: new Stroke({ color: 'rgba(250,252,250,.96)', width: 3 }),
            backgroundFill: new Fill({ color: 'rgba(250,252,250,.64)' }),
            padding: [1, 2, 1, 2],
          }),
        })
      }
      return new Style({
        stroke: new Stroke({
          color: kind === 'radar-range-axis'
            ? 'rgba(14,65,58,.38)'
            : 'rgba(14,65,58,.48)',
          width: kind === 'radar-range-axis' ? 1 : 1.15,
        }),
      })
    },
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

function createRasterValueLayer() {
  return new VectorLayer({
    source: new VectorSource(),
    declutter: 'rainpulse-raster-values',
    style: (feature) => new Style({
      text: new Text({
        text: String(feature.get('label') ?? ''),
        font: '650 9px ui-monospace, SFMono-Regular, Menlo, monospace',
        fill: new Fill({ color: '#173a34' }),
        stroke: new Stroke({ color: 'rgba(250,252,250,.94)', width: 3 }),
        overflow: true,
      }),
    }),
  })
}

function updateRasterValueLayer(
  map: OLMap,
  source: VectorSource,
  pixels: RasterPixels | null,
  imageExtent: GISMapExtent,
  legend: readonly GISLegendEntry[],
  visible: boolean,
) {
  source.clear()
  if (!visible || !pixels || legend.length === 0) return
  const size = map.getSize()
  const resolution = map.getView().getResolution()
  if (!size || resolution == null) return

  const [west, south, east, north] = imageExtent
  const [viewWest, viewSouth, viewEast, viewNorth] = map.getView().calculateExtent(size)
  const cellWidth = (east - west) / pixels.width
  const cellHeight = (north - south) / pixels.height
  const columnStart = Math.max(0, Math.floor((viewWest - west) / cellWidth))
  const columnEnd = Math.min(pixels.width - 1, Math.ceil((viewEast - west) / cellWidth))
  const rowStart = Math.max(0, Math.floor((north - viewNorth) / cellHeight))
  const rowEnd = Math.min(pixels.height - 1, Math.ceil((north - viewSouth) / cellHeight))
  if (columnStart > columnEnd || rowStart > rowEnd) return

  const columnStep = Math.max(1, Math.ceil((resolution * 38) / cellWidth))
  const rowStep = Math.max(1, Math.ceil((resolution * 24) / cellHeight))
  const firstColumn = Math.ceil(columnStart / columnStep) * columnStep
  const firstRow = Math.ceil(rowStart / rowStep) * rowStep
  const features: Feature<Point>[] = []
  for (let row = firstRow; row <= rowEnd && features.length < 900; row += rowStep) {
    for (let column = firstColumn; column <= columnEnd && features.length < 900; column += columnStep) {
      const value = rasterValueAtCell(pixels, column, row, legend)
      if (!value) continue
      features.push(new Feature({
        geometry: new Point([
          west + (column + 0.5) * cellWidth,
          north - (row + 0.5) * cellHeight,
        ]),
        kind: 'raster-value',
        label: value.compactLabel,
      }))
    }
  }
  source.addFeatures(features)
}

async function readRasterPixels(imageUrl: string, signal: AbortSignal): Promise<RasterPixels> {
  const response = await fetch(imageUrl, { signal })
  if (!response.ok) throw new Error(`raster pixel request failed: ${response.status}`)
  const blob = await response.blob()
  let source: CanvasImageSource
  let width: number
  let height: number
  let release: (() => void) | undefined

  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(blob)
    source = bitmap
    width = bitmap.width
    height = bitmap.height
    release = () => bitmap.close()
  } else {
    const objectUrl = URL.createObjectURL(blob)
    const image = new Image()
    image.src = objectUrl
    await image.decode()
    source = image
    width = image.naturalWidth
    height = image.naturalHeight
    release = () => URL.revokeObjectURL(objectUrl)
  }

  try {
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d', { willReadFrequently: true })
    if (!context) throw new Error('raster pixel canvas unavailable')
    context.drawImage(source, 0, 0)
    return { width, height, data: context.getImageData(0, 0, width, height).data }
  } finally {
    release?.()
  }
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
  emptyStateHint?: string
  bbox?: readonly number[]
  loading: boolean
  layerError: boolean
  onLayerError: (failed: boolean) => void
  onSelectPoint?: (point: MapCoordinate) => void
  referenceContext?: GISReferenceContext
  radarContext?: GISRadarContext
  className?: string
  sharedView?: View
  comparisonMode?: boolean
  basemapVisible?: boolean
  rasterStyle?: GISRasterStyle
  showRasterValues?: boolean
  smoothRaster?: boolean
  rasterOpacity?: number
  motionVectors?: readonly GISMotionVector[]
  motionVisible?: boolean
  picker?: ReactNode
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
  emptyStateHint,
  bbox,
  loading,
  layerError,
  onLayerError,
  onSelectPoint,
  referenceContext,
  radarContext,
  className = '',
  sharedView,
  comparisonMode = false,
  basemapVisible: controlledBasemapVisible,
  rasterStyle: controlledRasterStyle,
  showRasterValues: controlledShowRasterValues,
  smoothRaster: controlledSmoothRaster,
  rasterOpacity: controlledRasterOpacity,
  motionVectors = [],
  motionVisible = false,
  picker,
}: RasterGISMapProps) {
  const targetRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<OLMap | null>(null)
  const basemapLayerRef = useRef<TileLayer<XYZ> | null>(null)
  const coastlineLayerRef = useRef<ImageLayer<ImageStatic> | null>(null)
  const rasterLayerRef = useRef<ImageLayer<ImageStatic> | null>(null)
  const rasterValueLayerRef = useRef<VectorLayer<VectorSource> | null>(null)
  const selectionLayerRef = useRef<VectorLayer<VectorSource> | null>(null)
  const motionLayerRef = useRef<VectorLayer<VectorSource> | null>(null)
  const pickerRef = useRef<HTMLDivElement>(null)
  const pickerOverlayRef = useRef<Overlay | null>(null)
  const probeRef = useRef<HTMLDivElement>(null)
  const probeOverlayRef = useRef<Overlay | null>(null)
  const onSelectPointRef = useRef(onSelectPoint)
  const onLayerErrorRef = useRef(onLayerError)
  const imageExtentRef = useRef(imageExtent)
  const fitExtentRef = useRef(fitExtent)
  const referenceContextRef = useRef(referenceContext)
  const radarContextRef = useRef(radarContext)
  const legendRef = useRef(legend)
  const rasterPixelsRef = useRef<RasterPixels | null>(null)
  const rasterStyleRef = useRef<GISRasterStyle>('smooth')
  const showRasterValuesRef = useRef(false)
  const refreshRasterValuesRef = useRef<(() => void) | null>(null)
  const [localBasemapVisible, setLocalBasemapVisible] = useState(true)
  const [coastlineVisible, setCoastlineVisible] = useState(true)
  const [localRasterStyle, setLocalRasterStyle] = useState<GISRasterStyle>('smooth')
  const [localShowRasterValues, setLocalShowRasterValues] = useState(false)
  const [localRasterOpacity, setLocalRasterOpacity] = useState(DEFAULT_OPACITY)
  const [hoverCoordinate, setHoverCoordinate] = useState<MapCoordinate | null>(null)
  const [hoverRasterValue, setHoverRasterValue] = useState<RasterValue | null>(null)
  const basemapVisible = controlledBasemapVisible ?? localBasemapVisible
  const rasterStyle = controlledRasterStyle
    ?? (controlledSmoothRaster == null
      ? localRasterStyle
      : controlledSmoothRaster ? 'smooth' : 'grid')
  const showRasterValues = controlledShowRasterValues ?? localShowRasterValues
  const rasterOpacity = controlledRasterOpacity ?? localRasterOpacity
  const rasterOpacityRef = useRef(rasterOpacity)

  useEffect(() => {
    onSelectPointRef.current = onSelectPoint
  }, [onSelectPoint])

  useEffect(() => {
    onLayerErrorRef.current = onLayerError
  }, [onLayerError])

  useEffect(() => {
    imageExtentRef.current = imageExtent
  }, [imageExtent])

  useEffect(() => {
    legendRef.current = legend
    refreshRasterValuesRef.current?.()
  }, [legend])

  useEffect(() => {
    rasterStyleRef.current = rasterStyle
    refreshRasterValuesRef.current?.()
  }, [rasterStyle])

  useEffect(() => {
    showRasterValuesRef.current = showRasterValues
    rasterValueLayerRef.current?.setVisible(showRasterValues)
    refreshRasterValuesRef.current?.()
  }, [showRasterValues])

  const fitExtentKey = fitExtent.join(',')
  const imageExtentKey = imageExtent.join(',')
  const radarContextKey = radarContext
    ? `${radarContext.radarID}:${radarContext.longitude}:${radarContext.latitude}:${radarContext.displayRangeRadiiKM.join(',')}`
    : ''

  useEffect(() => {
    fitExtentRef.current = fitExtent
  }, [fitExtent, fitExtentKey])

  useEffect(() => {
    referenceContextRef.current = referenceContext
  }, [referenceContext])

  useEffect(() => {
    radarContextRef.current = radarContext
  }, [radarContext, radarContextKey])

  useEffect(() => {
    if (!targetRef.current || typeof ResizeObserver === 'undefined') return

    const domainExtent = fitExtentRef.current
    const mapReference = referenceContextRef.current
    const radarReference = radarContextRef.current

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
    const rasterValueLayer = createRasterValueLayer()
    rasterValueLayer.setVisible(showRasterValuesRef.current)
    const selectionLayer = createSelectionLayer()
    const referenceLayer = mapReference?.places?.length
      ? createReferenceLayer(mapReference.places)
      : null
    const radarReferenceLayer = radarReference
      ? createRadarReferenceLayer(radarReference)
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
        ...(radarReferenceLayer ? [radarReferenceLayer] : []),
        ...(referenceLayer ? [referenceLayer] : []),
        rasterValueLayer,
        selectionLayer,
      ],
      view,
      controls: defaultControls({ attribution: false, rotate: false, zoom: false }).extend([
        new Attribution({ collapsible: false }),
        new ScaleLine({ units: 'metric', minWidth: 92 }),
      ]),
    })

    view.fit(domainExtent, { padding: [54, 54, 54, 54], duration: 0 })
    const refreshRasterValues = () => updateRasterValueLayer(
      map,
      rasterValueLayer.getSource() ?? new VectorSource(),
      rasterPixelsRef.current,
      imageExtentRef.current,
      legendRef.current,
      showRasterValuesRef.current,
    )
    refreshRasterValuesRef.current = refreshRasterValues
    const clickKey = map.on('click', (event) => {
      onSelectPointRef.current?.(clampCoordinate(event.coordinate, imageExtentRef.current))
    })
    const pointerKey = map.on('pointermove', (event) => {
      if (event.dragging) return
      const [longitude, latitude] = event.coordinate
      setHoverCoordinate({ longitude, latitude })
      const sampled = rasterPixelsRef.current
        ? rasterValueAtCoordinate(
            rasterPixelsRef.current,
            event.coordinate,
            imageExtentRef.current,
            legendRef.current,
          )
        : null
      setHoverRasterValue(sampled?.value ?? null)
      probeOverlayRef.current?.setPosition(sampled ? event.coordinate : undefined)
    })
    const moveKey = map.on('moveend', refreshRasterValues)
    const viewport = map.getViewport()
    const clearHover = () => {
      setHoverCoordinate(null)
      setHoverRasterValue(null)
      probeOverlayRef.current?.setPosition(undefined)
    }
    viewport.addEventListener('pointerleave', clearHover)

    if (pickerRef.current) {
      const pickerOverlay = new Overlay({
        element: pickerRef.current,
        positioning: 'bottom-center',
        offset: [0, -16],
        stopEvent: true,
      })
      map.addOverlay(pickerOverlay)
      pickerOverlayRef.current = pickerOverlay
    }

    if (probeRef.current) {
      const probeOverlay = new Overlay({
        element: probeRef.current,
        positioning: 'bottom-left',
        offset: [11, -9],
        stopEvent: false,
      })
      map.addOverlay(probeOverlay)
      probeOverlayRef.current = probeOverlay
    }

    mapRef.current = map
    basemapLayerRef.current = basemapLayer
    coastlineLayerRef.current = coastlineLayer
    rasterLayerRef.current = rasterLayer
    rasterValueLayerRef.current = rasterValueLayer
    motionLayerRef.current = motionLayer
    selectionLayerRef.current = selectionLayer

    return () => {
      unByKey([clickKey, pointerKey, moveKey])
      viewport.removeEventListener('pointerleave', clearHover)
      map.setTarget(undefined)
      mapRef.current = null
      basemapLayerRef.current = null
      coastlineLayerRef.current = null
      rasterLayerRef.current = null
      rasterValueLayerRef.current = null
      motionLayerRef.current = null
      selectionLayerRef.current = null
      pickerOverlayRef.current = null
      probeOverlayRef.current = null
      refreshRasterValuesRef.current = null
    }
  // Motion features and visibility are updated by the dedicated effect below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitExtentKey, radarContextKey, referenceContext, sharedView])

  useEffect(() => {
    const target = targetRef.current
    const map = mapRef.current
    if (!target || !map || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => map.updateSize())
    observer.observe(target)
    return () => observer.disconnect()
  }, [fitExtentKey, radarContextKey, referenceContext, sharedView])

  useEffect(() => {
    const controller = new AbortController()
    rasterPixelsRef.current = null
    probeOverlayRef.current?.setPosition(undefined)
    refreshRasterValuesRef.current?.()
    if (!imageUrl) return () => controller.abort()

    void readRasterPixels(imageUrl, controller.signal)
      .then((pixels) => {
        if (controller.signal.aborted) return
        rasterPixelsRef.current = pixels
        refreshRasterValuesRef.current?.()
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === 'AbortError')) {
          rasterPixelsRef.current = null
          refreshRasterValuesRef.current?.()
        }
      })
    return () => controller.abort()
  }, [imageExtentKey, imageUrl])

  useEffect(() => {
    const layer = rasterLayerRef.current
    if (!layer || !imageUrl) {
      layer?.setSource(null)
      return
    }
    onLayerErrorRef.current(false)
    const source = new ImageStatic({
      url: imageUrl,
      projection: 'EPSG:4326',
      imageExtent: [...imageExtent],
      interpolate: rasterStyle === 'smooth',
    })
    source.once('imageloaderror', () => onLayerErrorRef.current(true))
    layer.setSource(source)

    const reduceMotion = typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (comparisonMode || reduceMotion) {
      layer.setOpacity(rasterOpacityRef.current)
    } else {
      const from = Math.min(0.25, rasterOpacityRef.current)
      const startAt = performance.now()
      layer.setOpacity(from)
      const step = (now: number) => {
        if (rasterLayerRef.current !== layer || layer.getSource() !== source) return
        const ratio = Math.min(1, (now - startAt) / 160)
        const targetOpacity = rasterOpacityRef.current
        layer.setOpacity(from + (targetOpacity - from) * ratio)
        if (ratio < 1) requestAnimationFrame(step)
      }
      requestAnimationFrame(step)
    }
  }, [comparisonMode, fitExtentKey, imageExtent, imageUrl, rasterStyle, referenceContext])

  useEffect(() => {
    const source = selectionLayerRef.current?.getSource()
    if (!source) return
    if (point && bbox?.length === 4) updateSelectionLayer(source, point, bbox)
    else source.clear()
  }, [bbox, fitExtentKey, point, referenceContext])

  useEffect(() => {
    pickerOverlayRef.current?.setPosition(
      point && !comparisonMode ? [point.longitude, point.latitude] : undefined,
    )
  }, [comparisonMode, fitExtentKey, point, referenceContext])

  useEffect(() => {
    basemapLayerRef.current?.setVisible(basemapVisible)
  }, [basemapVisible, fitExtentKey, referenceContext])

  useEffect(() => {
    coastlineLayerRef.current?.setVisible(coastlineVisible)
  }, [coastlineVisible, fitExtentKey, referenceContext])

  useEffect(() => {
    rasterOpacityRef.current = rasterOpacity
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
    <div className={`nowcast-gis-shell ${onSelectPoint ? '' : 'readonly'} ${comparisonMode ? 'comparison' : ''} raster-${rasterStyle} ${showRasterValues ? 'raster-values-visible' : ''} ${imageUrl ? 'raster-probe-enabled' : ''} ${className}`.trim()}>
      <div
        ref={targetRef}
        className="nowcast-gis-map"
        role="application"
        aria-label={mapLabel}
        tabIndex={0}
      />
      <span className="sr-only" role="img" aria-label={imageDescription} data-source={imageUrl} data-extent={imageExtent.join(',')} />

      {radarContext ? (
        <aside
          className="gis-radar-meta"
          aria-label={`${radarContext.displayName} ${radarContext.radarID.toUpperCase()} 雷达信息`}
        >
          <header>
            <strong>{radarContext.displayName}</strong>
            <b>{radarContext.radarID.toUpperCase()}</b>
            <span>{radarContext.radarBand}波段</span>
          </header>
          <p>经度 {radarContext.longitude.toFixed(3)}°E · 纬度 {radarContext.latitude.toFixed(3)}°N</p>
          <p>站高 {radarContext.siteAltitudeM} m · 天线 {radarContext.antennaAltitudeM} m · 范围 {radarContext.maximumRangeKM} km</p>
          <small>
            {radarContext.scanStrategy} · {radarContext.frequencyMHz} MHz · 体扫 {radarContext.expectedUpdateSeconds} s
            {radarContext.timeOffsetSeconds == null ? '' : ` · 时差 ${radarContext.timeOffsetSeconds > 0 ? '+' : ''}${radarContext.timeOffsetSeconds} s`}
            {radarContext.meanQualityIndex == null ? '' : ` · QI ${radarContext.meanQualityIndex.toFixed(3)}`}
          </small>
        </aside>
      ) : null}

      {!comparisonMode ? <div className="gis-display-controls" role="group" aria-label="地图图层控制">
        <button type="button" aria-pressed={basemapVisible} onClick={() => setLocalBasemapVisible((value) => !value)}>底图</button>
        {referenceContext?.coastline ? <button type="button" aria-pressed={coastlineVisible} onClick={() => setCoastlineVisible((value) => !value)}>海岸线</button> : null}
        <div className="gis-raster-style" role="group" aria-label="栅格显示样式">
          {(['grid', 'smooth'] as const).map((style) => (
            <button
              type="button"
              key={style}
              aria-pressed={rasterStyle === style}
              onClick={() => setLocalRasterStyle(style)}
            >{{ grid: '格点', smooth: '平滑' }[style]}</button>
          ))}
        </div>
        <button type="button" aria-pressed={showRasterValues} onClick={() => setLocalShowRasterValues((value) => !value)}>点值</button>
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
        <small>{hoverRasterValue?.label ?? (hoverCoordinate
          ? (onSelectPoint ? '点击选择该格点' : 'EPSG:4326')
          : '移动指针读取经纬度和色阶值')}</small>
      </div> : null}

      {comparisonMode ? (
        <div
          ref={probeRef}
          className={`gis-raster-probe${hoverRasterValue ? ' visible' : ''}`}
          aria-hidden={!hoverRasterValue}
        >
          <i style={hoverRasterValue ? { backgroundColor: hoverRasterValue.color } : undefined} />
          <span>
            <strong>{hoverRasterValue?.label ?? ''}</strong>
            <small>{hoverRasterValue ? coordinateLabel : ''}</small>
          </span>
        </div>
      ) : null}

      {!comparisonMode ? <div ref={pickerRef} className="gis-picker-wrap">{point ? picker : null}</div> : null}

      {comparisonMode && legend.length ? (
        <div className={`gis-comparison-legend ${legendMode}`} aria-label={`${productLabel}图例`} tabIndex={0}>
          {legendUnit ? <header><strong>{legendUnit}</strong></header> : null}
          <div className="gis-comparison-legend-scroll">
            {legendMode === 'categorical' ? (
              <div className="gis-comparison-legend-list">
                {legend.map((item) => (
                  <span
                    key={`${item.label}-${item.color}`}
                    title={item.sourceLabel ? `${item.label}（${item.sourceLabel}）` : item.label}
                  >
                    <i style={{ backgroundColor: item.color }} />
                    <small>{item.label}</small>
                  </span>
                ))}
              </div>
            ) : (
              <div
                className="gis-comparison-legend-scale"
                style={{
                  gridTemplateColumns: `repeat(${legend.length}, minmax(26px, 1fr))`,
                  minWidth: `${legend.length * 26}px`,
                }}
              >
                {legend.map((item) => (
                  <span
                    key={`${item.label}-${item.color}`}
                    title={item.sourceLabel ? `${item.label}（${item.sourceLabel}）` : item.label}
                  >
                    <i style={{ backgroundColor: item.color }} />
                    <small>{item.label}</small>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}

      {!comparisonMode ? <div className={`gis-legend ${legendMode}`} aria-label={`${productLabel}图例`} tabIndex={0}>
        <header><span>{productLabel}</span><strong>{legendUnit}</strong></header>
        {legendMode === 'categorical' ? (
          <div className="gis-legend-list">
            {legend.map((item) => <span key={`${item.label}-${item.color}`} title={item.sourceLabel ? `${item.label}（${item.sourceLabel}）` : item.label}><i style={{ backgroundColor: item.color }} /><small>{item.label}</small></span>)}
          </div>
        ) : (
          <div className="gis-legend-cells" style={{ gridTemplateColumns: `repeat(${legend.length}, minmax(0, 1fr))` }}>
            {legend.map((item) => (
              <span key={`${item.label}-${item.color}`} title={item.sourceLabel ? `${item.label}（${item.sourceLabel}）` : item.label}><i style={{ backgroundColor: item.color }} /><small>{item.label}</small></span>
            ))}
          </div>
        )}
        <footer><span>{basemapLabel}</span><small>{footerNote}</small></footer>
      </div> : null}

      {(!imageUrl || layerError) ? (
        <div className="gis-layer-empty" role="status">
          <strong>{loading ? '正在读取降水图层' : '降水图层暂不可用'}</strong>
          <small>{layerError ? '图层校验或网络请求失败' : (emptyStateHint ?? '等待已发布的透明 PNG 产品')}</small>
        </div>
      ) : null}
    </div>
  )
}
