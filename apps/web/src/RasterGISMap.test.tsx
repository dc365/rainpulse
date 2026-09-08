import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ImageLayer from 'ol/layer/Image.js'
import ImageStatic from 'ol/source/ImageStatic.js'
import type View from 'ol/View.js'
import { RasterGISMap } from './RasterGISMap'

vi.mock('ol/Map.js', async () => {
  const { default: Observable } = await import('ol/Observable.js')
  return { default: class extends Observable {
    view: View
    viewport = document.createElement('div')
    constructor(options: { view: View }) { super(); this.view = options.view }
    getView() { return this.view }
    getViewport() { return this.viewport }
    setTarget() {}
    updateSize() {}
    addOverlay() {}
  } }
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('keeps the source on equivalent bounds and errors, replaces changed frames, and selects by keyboard', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const sources = vi.spyOn(ImageLayer.prototype, 'setSource')
  const onLayerError = vi.fn()
  const onSelectPoint = vi.fn()
  const props = { imageDescription: 'rain', validTimeLabel: 'T0', contextLabel: 'test',
    productLabel: 'rain', legend: [], footerNote: '', mapLabel: 'rain map', resetViewLabel: 'reset',
    loading: false, layerError: false, onLayerError, onSelectPoint, onProbe: vi.fn(),
    showRasterValues: false, comparisonMode: true }
  const { rerender } = render(<RasterGISMap {...props} imageUrl="/A.png" imageExtent={[118,25,123,27]} />)
  const imageSources = () => sources.mock.calls.map(([source]) => source).filter(source => source instanceof ImageStatic)
  const first = imageSources()[0]!
  expect(imageSources()).toHaveLength(1)
  rerender(<RasterGISMap {...props} imageUrl="/A.png" imageExtent={[118,25,123,27]} />)
  expect(imageSources()).toHaveLength(1)
  act(() => first.dispatchEvent('imageloaderror'))
  expect(onLayerError).toHaveBeenLastCalledWith(true)
  rerender(<RasterGISMap {...props} layerError imageUrl="/A.png" imageExtent={[118,25,123,27]} />)
  expect(imageSources()).toHaveLength(1)
  rerender(<RasterGISMap {...props} imageUrl="/B.png" imageExtent={[118,25,123,27]} />)
  expect(imageSources()).toHaveLength(2)
  expect(onLayerError).toHaveBeenLastCalledWith(false)
  const map = screen.getByRole('application')
  fireEvent.keyDown(map, { key: 'Enter' })
  expect(onSelectPoint).toHaveBeenLastCalledWith({ longitude: 120.5, latitude: 26 })
  fireEvent.keyDown(map, { key: ' ' })
  expect(onSelectPoint).toHaveBeenCalledTimes(2)
})
