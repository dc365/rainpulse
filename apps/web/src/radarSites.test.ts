import { describe, expect, it } from 'vitest'

import { destinationCoordinate, radarDisplayExtent, radarRangeGeometry, radarSiteFor } from './radarSites'

describe('radar site references', () => {
  it('uses the versioned Z9591 station metadata', () => {
    const site = radarSiteFor('Z9591')
    expect(site).toMatchObject({
      displayName: '福州长乐',
      longitude: 119.5405578613281,
      latitude: 25.99138832092285,
      antennaAltitudeM: 641,
      maximumRangeKM: 460,
    })
  })

  it('builds closed range rings and orthogonal axes', () => {
    const site = radarSiteFor('z9591')!
    const geometry = radarRangeGeometry(site)
    expect(geometry.rings.map((ring) => ring.radiusKM)).toEqual([50, 100, 150, 200, 250])
    expect(geometry.rings[0].coordinates).toHaveLength(97)
    expect(geometry.rings[0].coordinates[0]).toEqual(geometry.rings[0].coordinates.at(-1))
    expect(geometry.axes).toHaveLength(2)
    expect(geometry.labels).toHaveLength(5)
  })

  it('calculates geodesic display bounds around a site', () => {
    const site = radarSiteFor('z9591')!
    const extent = radarDisplayExtent(site)
    expect(extent[0]).toBeLessThan(site.longitude)
    expect(extent[1]).toBeLessThan(site.latitude)
    expect(extent[2]).toBeGreaterThan(site.longitude)
    expect(extent[3]).toBeGreaterThan(site.latitude)
    expect(destinationCoordinate([0, 0], 111.2, 90)[0]).toBeCloseTo(1, 2)
  })
})
