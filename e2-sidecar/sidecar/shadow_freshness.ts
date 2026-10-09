/**
 * shadow_freshness.ts — record age tagging + payload-header summary for the
 * shadow collectors.
 *
 * Why this exists (평가 리포트 D1, 2026-09-03): OpenAQ `/parameters/{id}/latest`
 * is by design "the last value", not "the current value". 51.7 % of global rows
 * were ≥24 h old and ~30 % were over a year old — and the sidecar held both
 * `datetime_utc` and `ingested_at` the whole time without ever computing the
 * difference or putting a summary in the payload header. Downstream
 * `max_age_hours` gates absorb it, so nothing broke loudly; what broke quietly
 * is the *health signal* — "the feed is fine" was being satisfied by file count
 * and station count, both of which dead rows satisfy just as well.
 *
 * Two rules this module encodes, both learned the hard way:
 *
 * 1. **A missing timestamp is not a fresh timestamp.** `stale_fraction_24h` is
 *    computed over rows whose age is *known*, and `age_unknown` is published
 *    alongside it. Folding unknown ages into the denominator would let a
 *    timestamp outage read as a freshness improvement.
 *
 * 2. **Sentinels are marked, never dropped.** `value >= 999.9` is a dead-sensor
 *    marker, not a measurement. The rows stay in the payload (they are the
 *    audit trail for the upstream feed) but they are flagged, counted, and
 *    excluded from `n_fresh_locations` — that exclusion is what stops the
 *    "620 KR stations" style overcount the report flagged.
 */

/** OpenAQ publishes dead sensors as an out-of-range constant, not as null. */
export const SENTINEL_MIN = 999.9

/** Cumulative bucket edges in hours: 1h, 6h, 24h, 7d, 1y. */
export const AGE_BUCKET_EDGES_H = [1, 6, 24, 168, 8760] as const

export type AgeAnnotation = {
  /** hours between the record's own timestamp and this run's ingest time. */
  age_h: number | null
  /** present only when the row is a sentinel — absent keys keep the payload small. */
  flag?: 'sentinel'
}

export type AgeableRow = {
  value: number
  datetime_utc: string | null
  locations_id: number | null
}

export type FreshnessSummary = {
  rows: number
  age_known: number
  age_unknown: number
  /** fraction of *known-age* rows older than 24 h. null when nothing is known. */
  stale_fraction_24h: number | null
  age_p50_h: number | null
  age_p90_h: number | null
  age_buckets_h: Record<string, number>
  n_locations: number
  /** distinct locations with a ≤24 h, non-sentinel row. The honest station count. */
  n_fresh_locations: number
  sentinel_count: number
  n_sentinel_locations: number
}

export function isSentinel(value: number): boolean {
  return Number.isFinite(value) && value >= SENTINEL_MIN
}

/**
 * Hours from the record timestamp to `ingestedAt`. `null` when the record has
 * no parseable timestamp — the caller must not substitute 0.
 *
 * Negative ages (a source clock ahead of ours) are returned as-is rather than
 * clamped: a systematically negative age is itself a finding, and clamping to 0
 * would file it under "fresh".
 */
export function ageHours(datetimeUtc: string | null, ingestedAt: Date): number | null {
  if (!datetimeUtc) return null
  const t = Date.parse(datetimeUtc)
  if (Number.isNaN(t)) return null
  return (ingestedAt.getTime() - t) / 3_600_000
}

export function annotate<T extends AgeableRow>(row: T, ingestedAt: Date): T & AgeAnnotation {
  const age = ageHours(row.datetime_utc, ingestedAt)
  const annotated: T & AgeAnnotation = {
    ...row,
    age_h: age === null ? null : Math.round(age * 10) / 10,
  }
  if (isSentinel(row.value)) annotated.flag = 'sentinel'
  return annotated
}

function percentile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null
  const idx = Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))
  return Math.round(sorted[idx] * 10) / 10
}

/**
 * Payload-header summary. Every field here is meant to be readable *without
 * downloading the rows* — the audit script reads headers only.
 */
export function summarize(rows: (AgeableRow & AgeAnnotation)[]): FreshnessSummary {
  const known: number[] = []
  const locations = new Set<number>()
  const freshLocations = new Set<number>()
  const sentinelLocations = new Set<number>()
  const buckets: Record<string, number> = {}
  for (const edge of AGE_BUCKET_EDGES_H) buckets[`<=${edge}`] = 0
  buckets[`>${AGE_BUCKET_EDGES_H[AGE_BUCKET_EDGES_H.length - 1]}`] = 0

  let sentinelCount = 0
  let stale24 = 0

  for (const row of rows) {
    const sentinel = row.flag === 'sentinel' || isSentinel(row.value)
    if (sentinel) sentinelCount++
    if (row.locations_id !== null) {
      locations.add(row.locations_id)
      if (sentinel) sentinelLocations.add(row.locations_id)
    }

    if (row.age_h === null) continue
    known.push(row.age_h)
    if (row.age_h > 24) stale24++

    const edge = AGE_BUCKET_EDGES_H.find((e) => row.age_h! <= e)
    buckets[edge === undefined ? `>${AGE_BUCKET_EDGES_H[AGE_BUCKET_EDGES_H.length - 1]}` : `<=${edge}`]++

    // A sentinel row keeps its location out of the fresh count even when its
    // timestamp is recent — a dead sensor reporting on schedule is still dead.
    if (row.age_h <= 24 && !sentinel && row.locations_id !== null) {
      freshLocations.add(row.locations_id)
    }
  }

  known.sort((a, b) => a - b)

  return {
    rows: rows.length,
    age_known: known.length,
    age_unknown: rows.length - known.length,
    stale_fraction_24h: known.length === 0 ? null : Math.round((stale24 / known.length) * 10000) / 10000,
    age_p50_h: percentile(known, 0.5),
    age_p90_h: percentile(known, 0.9),
    age_buckets_h: buckets,
    n_locations: locations.size,
    n_fresh_locations: freshLocations.size,
    sentinel_count: sentinelCount,
    n_sentinel_locations: sentinelLocations.size,
  }
}
