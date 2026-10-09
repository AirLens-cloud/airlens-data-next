/**
 * openaq_shadow.ts — global reference+low-cost observation snapshot (OpenAQ v3).
 *
 * Wave 1 pivot (plan atomic-gathering-reddy.md, 2026-08-25): OpenAQ replaces
 * AirKorea as the observation source — Korean official stations arrive via
 * OpenAQ's aggregation, with provenance kept (source=openaq).
 *
 * API contract (docs.openaq.org, checked 2026-08-25): auth header X-API-Key,
 * GET /v3/parameters/{id}/latest with page/limit paging, free tier 60 req/min
 * and 2,000 req/hour. Parameter ids are DISCOVERED from /v3/parameters by name
 * (pm25/pm10) instead of hardcoded. Pages are spaced 1.2s apart => worst case
 * ~120 req/run, far inside both limits.
 *
 * Fail-loud: missing key, unknown parameter, or zero rows => exit 1.
 *
 * Record age (평가 리포트 D1, 2026-09-03): `/latest` returns the *last* value,
 * not the *current* one — 51.7 % of global rows were ≥24 h old. Rows now carry
 * `age_h` and a `flag: 'sentinel'` marker (kept, never dropped), and the payload
 * header carries a `freshness` summary readable without downloading the rows.
 * See shadow_freshness.ts for why the denominators are what they are.
 */

import { annotate, summarize } from './shadow_freshness.ts'

const BASE = 'https://api.openaq.org/v3'
const PARAM_NAMES = ['pm25', 'pm10']
const PAGE_LIMIT = 1000
const MAX_PAGES_PER_PARAM = 60
const PAGE_DELAY_MS = 1200

function argValue(flag: string, fallback: string): string {
  const i = Deno.args.indexOf(flag)
  return i >= 0 && Deno.args[i + 1] ? Deno.args[i + 1] : fallback
}

const apiKey = Deno.env.get('OPENAQ_API_KEY')
if (!apiKey) {
  console.error(JSON.stringify({ event: 'openaq-shadow:no-api-key' }))
  Deno.exit(1)
}
const outDir = argValue('--out', '/var/lib/airlens/shadow')
const startedAt = new Date()

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function getJson(path: string): Promise<Record<string, unknown>> {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'X-API-Key': apiKey!, 'user-agent': 'airlens-e2-sidecar/1.0' },
    })
    if (res.status === 429) {
      await res.body?.cancel()
      await sleep(15_000 * attempt)
      continue
    }
    if (!res.ok) {
      const body = (await res.text()).slice(0, 200)
      throw new Error(`openaq ${path} -> ${res.status} ${body}`)
    }
    return await res.json()
  }
  throw new Error(`openaq ${path} -> 429 after retries`)
}

// --- discover parameter ids by name (no hardcoded ids) -----------------------
const paramsResp = await getJson(`/parameters?limit=1000`)
const paramRows = (paramsResp.results ?? []) as { id: number; name: string }[]
const paramIds = new Map<string, number>()
for (const p of paramRows) if (PARAM_NAMES.includes(p.name)) paramIds.set(p.name, p.id)
for (const name of PARAM_NAMES) {
  if (!paramIds.has(name)) {
    console.error(JSON.stringify({ event: 'openaq-shadow:param-missing', name }))
    Deno.exit(1)
  }
}

// --- page latest values per parameter ----------------------------------------
type LatestRow = {
  value: number
  datetime?: { utc?: string; local?: string }
  coordinates?: { latitude?: number; longitude?: number }
  sensorsId?: number
  locationsId?: number
}

type Compact = {
  parameter: string
  value: number
  datetime_utc: string | null
  lat: number | null
  lon: number | null
  sensors_id: number | null
  locations_id: number | null
}

const rows: Compact[] = []
const pageCounts: Record<string, number> = {}
for (const [name, id] of paramIds) {
  let pages = 0
  for (let page = 1; page <= MAX_PAGES_PER_PARAM; page++) {
    await sleep(PAGE_DELAY_MS)
    const resp = await getJson(`/parameters/${id}/latest?limit=${PAGE_LIMIT}&page=${page}`)
    const results = (resp.results ?? []) as LatestRow[]
    pages++
    for (const r of results) {
      if (typeof r.value !== 'number') continue
      rows.push({
        parameter: name,
        value: r.value,
        datetime_utc: r.datetime?.utc ?? null,
        lat: r.coordinates?.latitude ?? null,
        lon: r.coordinates?.longitude ?? null,
        sensors_id: r.sensorsId ?? null,
        locations_id: r.locationsId ?? null,
      })
    }
    if (results.length < PAGE_LIMIT) break
  }
  pageCounts[name] = pages
}

if (rows.length === 0) {
  console.error(JSON.stringify({ event: 'openaq-shadow:empty', pageCounts }))
  Deno.exit(1)
}

const stamp = startedAt.toISOString().slice(0, 13).replace(/[-T]/g, '') // UTC YYYYMMDDHH
const outFile = `${outDir}/openaq-${stamp}.json.gz`

// Age is computed against this run's ingest time, so a row's `age_h` is the
// number the downstream `max_age_hours` gate would see — not a re-derivation.
const aged = rows.map((r) => annotate(r, startedAt))
const freshness = summarize(aged)

const payload = {
  source: 'openaq',
  source_url: `${BASE}/parameters/{id}/latest`,
  nature: 'observation',
  ingested_at: startedAt.toISOString(),
  records: rows.length,
  pages: pageCounts,
  freshness,
  data: aged,
}

const gz = new Blob([JSON.stringify(payload)])
  .stream()
  .pipeThrough(new CompressionStream('gzip'))
await Deno.writeFile(outFile, new Uint8Array(await new Response(gz).arrayBuffer()))

console.log(JSON.stringify({
  event: 'openaq-shadow:collected',
  out: outFile,
  records: rows.length,
  pages: pageCounts,
  stale_fraction_24h: freshness.stale_fraction_24h,
  n_fresh_locations: freshness.n_fresh_locations,
  sentinel_count: freshness.sentinel_count,
}))
