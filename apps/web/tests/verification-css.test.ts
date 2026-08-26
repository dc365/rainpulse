import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const verificationCss = readFileSync(resolve(process.cwd(), 'src/verification.css'), 'utf8')

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const zeroMinimumTrack = (selector: string) => new RegExp(
  `${escapeRegExp(selector)}\\s*\\{[^}]*grid-template-columns:\\s*minmax\\(0,\\s*1fr\\)`,
  's',
)

describe('RP-017 responsive grid constraints', () => {
  it('allows every single-column evidence track to shrink to the mobile viewport', () => {
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-page-rp017'))
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-workbench'))
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-conclusion'))
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-filterbar'))
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-map-grid-rp017'))
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-evidence-grid-rp017'))
    expect(verificationCss).toMatch(zeroMinimumTrack('.verification-current-metrics'))
  })
})
