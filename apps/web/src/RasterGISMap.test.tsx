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
afterEach(() => { vi.useRealTimers(); cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('shows accumulation progress instead of unavailable, but retains real failure feedback', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const props = { imageDescription: 'rain', validTimeLabel: 'T0', contextLabel: 'test',
    productLabel: 'rain', legend: [], footerNote: '', mapLabel: 'rain map', resetViewLabel: 'reset',
    loading: true, loadingLabel: '正在计算累积…', layerError: false, onLayerError: vi.fn(),
    imageExtent: [118,25,123,27] as [number,number,number,number] }
  const { rerender } = render(<RasterGISMap {...props} />)
  expect(screen.getByText('正在计算累积…')).toBeTruthy()
  expect(screen.queryByText('降水图层暂不可用')).toBeNull()
  rerender(<RasterGISMap {...props} loading={false} />)
  expect(screen.getByText('降水图层暂不可用')).toBeTruthy()
})
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

it('retains frames and delays slow-load feedback without flashing during quick switches', () => {
  vi.useFakeTimers()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const images = vi.spyOn(ImageStatic.prototype, 'getImage')
  const sources = vi.spyOn(ImageLayer.prototype, 'setSource')
  const props = { imageDescription: 'rain', validTimeLabel: 'T0', contextLabel: 'test',
    productLabel: 'rain', legend: [], footerNote: '', mapLabel: 'map', resetViewLabel: 'reset',
    loading: false, layerError: false, onLayerError: vi.fn(), onProbe: vi.fn(),
    showRasterValues: false, comparisonMode: true, imageExtent: [118,25,123,27] as [number,number,number,number] }
  const { rerender } = render(<RasterGISMap {...props} imageUrl="/A.png" />)
  const sourceFor = (url: string) => {
    const candidates = [...images.mock.instances, ...sources.mock.calls.map(([s]) => s)]
    return candidates.find(s => s instanceof ImageStatic && s.getUrl() === url) as ImageStatic
  }
  const a = sourceFor('/A.png')
  expect(a).toBeInstanceOf(ImageStatic)
  act(() => a.dispatchEvent('imageloadend'))
  const layer = sources.mock.contexts.find(l => (l as ImageLayer<ImageStatic>).getSource() === a) as ImageLayer<ImageStatic>
  rerender(<RasterGISMap {...props} imageUrl="/B.png" />)
  expect(layer.getSource()).toBe(a)
  expect(screen.queryByText(/正在切换时效/)).toBeNull()
  act(() => vi.advanceTimersByTime(400))
  expect(screen.queryByText(/正在切换时效/)).toBeNull()
  const b = sourceFor('/B.png')
  rerender(<RasterGISMap {...props} imageUrl="/C.png" />)
  act(() => b.dispatchEvent('imageloadend'))
  expect(layer.getSource()).toBe(a)
  act(() => vi.advanceTimersByTime(799))
  expect(screen.queryByText(/正在切换时效/)).toBeNull()
  act(() => vi.advanceTimersByTime(1))
  expect(screen.getByText(/正在切换时效/)).toBeTruthy()
  const c = sourceFor('/C.png')
  act(() => c.dispatchEvent('imageloadend'))
  expect(layer.getSource()).toBe(c)
  expect(screen.queryByText(/正在切换时效/)).toBeNull()
  rerender(<RasterGISMap {...props} imageUrl="/A.png" />)
  expect(layer.getSource()).toBe(a)
  rerender(<RasterGISMap {...props} imageUrl="/D.png" />)
  act(() => sourceFor('/D.png').dispatchEvent('imageloaderror'))
  expect(layer.getSource()).toBeNull()
  expect(props.onLayerError).toHaveBeenLastCalledWith(true)
})
