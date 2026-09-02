import type { GISMapExtent } from './RasterGISMap'

export type RadarSiteMetadata = {
  radarID: string
  displayName: string
  longitude: number
  latitude: number
  siteAltitudeM: number
  antennaAltitudeM: number
  radarBand: string
  frequencyMHz: number
  scanStrategy: string
  expectedUpdateSeconds: number
  maximumRangeKM: number
  displayRangeRadiiKM: readonly number[]
}

export type RadarRangeGeometry = {
  rings: readonly { radiusKM: number, coordinates: readonly [number, number][] }[]
  axes: readonly (readonly [number, number][])[]
  labels: readonly { radiusKM: number, coordinate: readonly [number, number] }[]
}

const EARTH_RADIUS_KM = 6371.0088
const DISPLAY_RANGE_RADII_KM = [50, 100, 150, 200, 250] as const

// Values mirror configs/radars/fujian-20260828/*.yaml. Chinese names are UI aliases.
const FUJIAN_RADAR_SITES: Record<string, RadarSiteMetadata> = {
  z9591: {
    radarID: 'z9591',
    displayName: '福州长乐',
    longitude: 119.5405578613281,
    latitude: 25.99138832092285,
    siteAltitudeM: 625,
    antennaAltitudeM: 641,
    radarBand: 'S',
    frequencyMHz: 2880,
    scanStrategy: 'VCP21D',
    expectedUpdateSeconds: 338,
    maximumRangeKM: 460,
    displayRangeRadiiKM: DISPLAY_RANGE_RADII_KM,
  },
  z9593: {
    radarID: 'z9593',
    displayName: '宁德',
    longitude: 120.21778106689453,
    latitude: 26.95222282409668,
    siteAltitudeM: 525,
    antennaAltitudeM: 546,
    radarBand: 'S',
    frequencyMHz: 2950,
    scanStrategy: 'VCP21D',
    expectedUpdateSeconds: 338,
    maximumRangeKM: 460,
    displayRangeRadiiKM: DISPLAY_RANGE_RADII_KM,
  },
  z9598: {
    radarID: 'z9598',
    displayName: '三明',
    longitude: 117.08055877685547,
    latitude: 27.00861167907715,
    siteAltitudeM: 1692,
    antennaAltitudeM: 1740,
    radarBand: 'S',
    frequencyMHz: 2730,
    scanStrategy: 'VCP21D',
    expectedUpdateSeconds: 338,
    maximumRangeKM: 460,
    displayRangeRadiiKM: DISPLAY_RANGE_RADII_KM,
  },
  z9599: {
    radarID: 'z9599',
    displayName: '南平建阳',
    longitude: 118.19388580322266,
    latitude: 27.327777862548828,
    siteAltitudeM: 1005,
    antennaAltitudeM: 1047,
    radarBand: 'S',
    frequencyMHz: 2830,
    scanStrategy: 'VCP21D',
    expectedUpdateSeconds: 338,
    maximumRangeKM: 460,
    displayRangeRadiiKM: DISPLAY_RANGE_RADII_KM,
  },
}

export function radarSiteFor(radarID?: string | null) {
  return radarID ? FUJIAN_RADAR_SITES[radarID.toLowerCase()] : undefined
}

export function destinationCoordinate(
  origin: readonly [number, number],
  distanceKM: number,
  bearingDegrees: number,
): [number, number] {
  const angularDistance = distanceKM / EARTH_RADIUS_KM
  const bearing = bearingDegrees * Math.PI / 180
  const longitude = origin[0] * Math.PI / 180
  const latitude = origin[1] * Math.PI / 180
  const destinationLatitude = Math.asin(
    Math.sin(latitude) * Math.cos(angularDistance)
      + Math.cos(latitude) * Math.sin(angularDistance) * Math.cos(bearing),
  )
  const destinationLongitude = longitude + Math.atan2(
    Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latitude),
    Math.cos(angularDistance) - Math.sin(latitude) * Math.sin(destinationLatitude),
  )
  return [
    destinationLongitude * 180 / Math.PI,
    destinationLatitude * 180 / Math.PI,
  ]
}

export function radarRangeGeometry(site: RadarSiteMetadata): RadarRangeGeometry {
  const center: [number, number] = [site.longitude, site.latitude]
  const radii = site.displayRangeRadiiKM.filter((radius) => radius <= site.maximumRangeKM)
  const outerRadius = radii.at(-1) ?? Math.min(250, site.maximumRangeKM)
  return {
    rings: radii.map((radiusKM) => ({
      radiusKM,
      coordinates: Array.from({ length: 97 }, (_, index) => (
        destinationCoordinate(center, radiusKM, index === 96 ? 0 : index * 360 / 96)
      )),
    })),
    axes: [
      [
        destinationCoordinate(center, outerRadius, 270),
        destinationCoordinate(center, outerRadius, 90),
      ],
      [
        destinationCoordinate(center, outerRadius, 180),
        destinationCoordinate(center, outerRadius, 0),
      ],
    ],
    labels: radii.map((radiusKM) => ({
      radiusKM,
      coordinate: destinationCoordinate(center, radiusKM, 8),
    })),
  }
}

export function radarDisplayExtent(site: RadarSiteMetadata, radiusKM = 250): GISMapExtent {
  const center: [number, number] = [site.longitude, site.latitude]
  const west = destinationCoordinate(center, radiusKM, 270)
  const south = destinationCoordinate(center, radiusKM, 180)
  const east = destinationCoordinate(center, radiusKM, 90)
  const north = destinationCoordinate(center, radiusKM, 0)
  return [west[0], south[1], east[0], north[1]]
}
