import { expect, it } from 'vitest'
import VectorSource from 'ol/source/Vector.js'
import Point from 'ol/geom/Point.js'
import { updateSelectionLayer } from './RasterGISMap'

it('renders a verification point without requiring an area selection', () => {
  const source = new VectorSource()
  updateSelectionLayer(source, { longitude: 120, latitude: 26 })
  expect(source.getFeatures()).toHaveLength(1)
  expect((source.getFeatures()[0].getGeometry() as Point).getCoordinates()).toEqual([120, 26])
  updateSelectionLayer(source, { longitude: 121, latitude: 25 })
  expect(source.getFeatures()).toHaveLength(1)
  expect((source.getFeatures()[0].getGeometry() as Point).getCoordinates()).toEqual([121, 25])
})
it('preserves the existing area and point selection when a bounding box is provided', () => {
  const source = new VectorSource()
  updateSelectionLayer(source, { longitude: 120, latitude: 26 }, [119, 25, 121, 27])
  expect(source.getFeatures().map(feature => feature.get('kind')).sort()).toEqual(['area', 'point'])
})
