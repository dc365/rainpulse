import type { GISLegendEntry } from './RasterGISMap'
import { REFLECTIVITY_LEGEND } from './reflectivityPalette'

export function ReflectivityLegend({ entries = REFLECTIVITY_LEGEND }: { entries?: GISLegendEntry[] }) {
  return <div className="gis-comparison-legend-scale reflectivity-segments" style={{gridTemplateColumns:`repeat(${entries.length}, minmax(20px, 1fr))`, minWidth:`${entries.length * 22}px`}}>
    {entries.map(item => <span key={`${item.label}-${item.color}`} title={item.label}><i style={{backgroundColor:item.color}} /><small>{item.minimum ?? item.label.match(/[-+]?\d+(?:\.\d+)?/)?.[0]}</small></span>)}
  </div>
}
