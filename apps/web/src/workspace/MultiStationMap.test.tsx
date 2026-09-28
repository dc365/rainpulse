import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import View from 'ol/View.js'
import { MultiStationMap } from './MultiStationMap'
const bounds = [112, 21, 125, 32]
vi.mock('./CompositeMap', () => ({ useComposite: () => ({ data: { manifest: { comparison: { products: ['s_only', 'sx_composite'].map(product_id => ({ product_id, map: { bounds: [112,21,125,32], object_path: '/full.png' } })) } } } }) }))
vi.mock('./CompositeCoverage', () => ({ CompositeCoverage: () => null }))
vi.mock('../RasterGISMap', () => ({ RasterGISMap: ({ fitExtent }: {fitExtent: number[]}) => <div data-testid="extent">{fitExtent.join(',')}</div> }))
afterEach(cleanup)
it('fits S and S+X to the full generated product bounds', () => {
 render(<MultiStationMap sIDs={[]} xStations={[]} time="2026-08-28T00:12:00Z" day="2026-08-28" revision={0} sharedView={new View()} layout="single" composite onTimes={()=>{}} />)
 expect(screen.getByTestId('extent').textContent).toBe(bounds.join(','))
 fireEvent.change(screen.getByLabelText('组合产品'), {target:{value:'compare'}})
 expect(screen.getAllByTestId('extent').map(e=>e.textContent)).toEqual([bounds.join(','),bounds.join(',')])
})
