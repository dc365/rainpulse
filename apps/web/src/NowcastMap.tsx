import {
  RasterGISMap,
  type MapCoordinate,
} from './RasterGISMap'
import { FUZHOU_GIS_CONTEXT } from './GISMapContexts'

const GRID_EXTENT: [number, number, number, number] = [118, 25, 123, 27]
const PRODUCT_IMAGE_EXTENT: [number, number, number, number] = [117.995, 24.995, 123.005, 27.005]

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

export function NowcastMap(props: NowcastMapProps) {
  return (
    <RasterGISMap
      imageUrl={props.imageUrl}
      imageDescription={props.imageDescription}
      imageExtent={PRODUCT_IMAGE_EXTENT}
      fitExtent={GRID_EXTENT}
      validTimeLabel={props.validTimeLabel}
      contextLabel={props.leadLabel}
      productLabel={props.productLabel}
      legend={props.legend.map(([minimum, color]) => ({ label: String(minimum), color }))}
      legendUnit={props.legendUnit}
      footerNote="透明含缺测与 <0.1"
      mapLabel={`可交互降水 GIS 地图，${props.imageDescription}，点击地图选择预报点`}
      resetViewLabel="复位福州网格范围"
      point={props.point}
      pointValueLabel={props.pointValueLabel}
      bbox={props.bbox}
      loading={props.loading}
      layerError={props.layerError}
      onLayerError={props.onLayerError}
      onSelectPoint={props.onSelectPoint}
      referenceContext={FUZHOU_GIS_CONTEXT}
    />
  )
}
