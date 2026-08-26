import type { GISReferenceContext } from './RasterGISMap'

export const FUZHOU_GIS_CONTEXT: GISReferenceContext = {
  coastline: { url: '/coastline-fuzhou.svg', extent: [118, 25, 123, 27] },
  places: [
    { name: '福州', coordinate: [119.2965, 26.0745] },
    { name: '宁德', coordinate: [119.527, 26.66] },
    { name: '南平', coordinate: [118.178, 26.642] },
    { name: '莆田', coordinate: [119.007, 25.454] },
  ],
}
