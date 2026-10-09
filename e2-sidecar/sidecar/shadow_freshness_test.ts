/**
 * shadow_freshness_test.ts — AAA unit tests. No network.
 *
 * Run: `deno test e2-sidecar/sidecar/shadow_freshness_test.ts`
 *
 * The regressions worth pinning are the two ways a freshness number can lie:
 * a missing timestamp counted as fresh, and a dead sensor counted as a station.
 */
import { assertEquals } from 'jsr:@std/assert@1'
import {
  ageHours,
  annotate,
  isSentinel,
  SENTINEL_MIN,
  summarize,
} from './shadow_freshness.ts'

const INGESTED = new Date('2026-09-03T12:00:00Z')

function row(
  overrides: Partial<{ value: number; datetime_utc: string | null; locations_id: number | null }> = {},
) {
  return { value: 12.3, datetime_utc: '2026-09-03T11:00:00Z', locations_id: 1, ...overrides }
}

Deno.test('ageHours measures against the ingest time', () => {
  // Arrange / Act
  const age = ageHours('2026-09-03T06:00:00Z', INGESTED)

  // Assert
  assertEquals(age, 6)
})

Deno.test('a missing or unparseable timestamp yields null, never zero', () => {
  // Arrange — 0 would file an unknown age under "fresh". That is the whole bug.
  // Act / Assert
  assertEquals(ageHours(null, INGESTED), null)
  assertEquals(ageHours('not-a-date', INGESTED), null)
})

Deno.test('a source clock ahead of ours stays negative instead of clamping to fresh', () => {
  // Arrange / Act
  const age = ageHours('2026-09-03T14:00:00Z', INGESTED)

  // Assert — a systematically negative age is a finding, not a rounding artifact
  assertEquals(age, -2)
})

Deno.test('sentinel rows are flagged but kept', () => {
  // Arrange
  const dead = row({ value: SENTINEL_MIN })

  // Act
  const annotated = annotate(dead, INGESTED)

  // Assert — the value survives verbatim; only a marker is added
  assertEquals(annotated.flag, 'sentinel')
  assertEquals(annotated.value, SENTINEL_MIN)
  assertEquals(isSentinel(999.8), false)
})

Deno.test('a healthy row carries no flag key at all', () => {
  // Arrange / Act
  const annotated = annotate(row(), INGESTED)

  // Assert — absent rather than false, so the payload does not grow per row
  assertEquals('flag' in annotated, false)
  assertEquals(annotated.age_h, 1)
})

Deno.test('stale_fraction_24h is computed over known ages only', () => {
  // Arrange — 2 stale, 1 fresh, 1 unknown. Folding the unknown into the
  // denominator would report 0.5 and make a timestamp outage look like an
  // improvement.
  const rows = [
    annotate(row({ datetime_utc: '2026-09-01T00:00:00Z' }), INGESTED),
    annotate(row({ datetime_utc: '2026-08-01T00:00:00Z' }), INGESTED),
    annotate(row(), INGESTED),
    annotate(row({ datetime_utc: null }), INGESTED),
  ]

  // Act
  const s = summarize(rows)

  // Assert
  assertEquals(s.rows, 4)
  assertEquals(s.age_known, 3)
  assertEquals(s.age_unknown, 1)
  assertEquals(s.stale_fraction_24h, 0.6667)
})

Deno.test('a dead sensor reporting on schedule is not a fresh location', () => {
  // Arrange — this exclusion is what stops the "620 KR stations" overcount:
  // the sentinel row is recent (1 h) but the sensor is dead.
  const rows = [
    annotate(row({ locations_id: 10 }), INGESTED),
    annotate(row({ locations_id: 11, value: SENTINEL_MIN }), INGESTED),
  ]

  // Act
  const s = summarize(rows)

  // Assert
  assertEquals(s.n_locations, 2)
  assertEquals(s.n_fresh_locations, 1)
  assertEquals(s.sentinel_count, 1)
  assertEquals(s.n_sentinel_locations, 1)
})

Deno.test('age buckets are cumulative edges and cover the over-a-year tail', () => {
  // Arrange — one row per bucket, including the ≥1y tail the report called out
  const rows = [
    annotate(row({ datetime_utc: '2026-09-03T11:30:00Z' }), INGESTED), // 0.5h
    annotate(row({ datetime_utc: '2026-09-03T08:00:00Z' }), INGESTED), // 4h
    annotate(row({ datetime_utc: '2026-09-03T00:00:00Z' }), INGESTED), // 12h
    annotate(row({ datetime_utc: '2026-08-30T12:00:00Z' }), INGESTED), // 96h
    annotate(row({ datetime_utc: '2026-06-03T12:00:00Z' }), INGESTED), // ~2200h
    annotate(row({ datetime_utc: '2024-01-01T00:00:00Z' }), INGESTED), // >1y
  ]

  // Act
  const s = summarize(rows)

  // Assert
  assertEquals(s.age_buckets_h, {
    '<=1': 1,
    '<=6': 1,
    '<=24': 1,
    '<=168': 1,
    '<=8760': 1,
    '>8760': 1,
  })
})

Deno.test('an empty set reports null rather than a fabricated zero', () => {
  // Arrange / Act
  const s = summarize([])

  // Assert — 0.0 would read as "nothing is stale"
  assertEquals(s.stale_fraction_24h, null)
  assertEquals(s.age_p50_h, null)
  assertEquals(s.rows, 0)
})

Deno.test('percentiles describe the known-age distribution', () => {
  // Arrange
  const hours = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
  const rows = hours.map((h) =>
    annotate(row({ datetime_utc: new Date(INGESTED.getTime() - h * 3_600_000).toISOString() }), INGESTED)
  )

  // Act
  const s = summarize(rows)

  // Assert
  assertEquals(s.age_p50_h, 5)
  assertEquals(s.age_p90_h, 9)
})
