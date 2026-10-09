/**
 * openaq_backfill.ts — one-shot hourly backfill for a bbox gap (OpenAQ v3).
 *
 * Purpose (plan atomic-gathering-reddy.md, 2026-08-25): the AirKorea path
 * stopped collecting around 2026-08-15; the live OpenAQ shadow only starts
 * 2026-08-25T07Z. This script fills that hole from OpenAQ's hourly rollups:
 *   1. page /v3/locations?bbox=... and collect pm25/pm10 sensors
 *   2. per sensor, GET /v3/sensors/{id}/hours?datetime_from&datetime_to
 *      (gap is ~247h, under the 1000-row page limit => one request/sensor)
 *
 * Rate budget (docs.openaq.org free tier 60 req/min, 2,000 req/h): requests
 * are spaced 1.2s apart => ~50/min; ~1,630 total requests fits one hour.
 *
 * Fail-loud: missing key, zero sensors, or zero rows => exit 1. Per-sensor
 * fetch errors are tolerated up to MAX_SENSOR_FAILURES, then abort.
 */
const BASE = 'https://api.openaq.org/v3'
const PARAM_NAMES = ['pm25', 'pm10']
const PAGE_LIMIT = 1000
const REQUEST_DELAY_MS = 1200
const MAX_SENSOR_FAILURES = 50
function argValue(flag: string, fallback: string): string {
  const i = Deno.args.indexOf(flag)
  return i >= 0 && Deno.args[i + 1] ? Deno.args[i + 1] : fallback
}
const apiKey = Deno.env.get('OPENAQ_API_KEY')
if (!apiKey) {
  console.error(JSON.stringify({ event: 'openaq-backfill:no-api-key' }))
  Deno.exit(1)
}
const outDir = argValue('--out', '/var/lib/airlens/shadow')
const bbox = argValue('--bbox', '124.5,33.0,132.0,38.7') // South Korea
const fromIso = argValue('--from', '2026-08-15T00:00:00Z')
const toIso = argValue('--to', '2026-08-25T07:00:00Z')
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
// --- discover sensors in bbox -------------------------------------------------
type SensorRef = {
  sensors_id: number
  parameter: string
  locations_id: number
  lat: number | null
  lon: number | null
}
const sensors: SensorRef[] = []
for (let page = 1; page <= 10; page++) {
  await sleep(REQUEST_DELAY_MS)
  const resp = await getJson(`/locations?bbox=${bbox}&limit=${PAGE_LIMIT}&page=${page}`)
  const results = (resp.results ?? []) as {
    id: number
    coordinates?: { latitude?: number; longitude?: number }
    sensors?: { id: number; parameter: { name: string } }[]
  }[]
  for (const loc of results) {
    for (const s of loc.sensors ?? []) {
      if (!PARAM_NAMES.includes(s.parameter.name)) continue
      sensors.push({
        sensors_id: s.id,
        parameter: s.parameter.name,
        locations_id: loc.id,
        lat: loc.coordinates?.latitude ?? null,
        lon: loc.coordinates?.longitude ?? null,
      })
    }
  }
  if (results.length < PAGE_LIMIT) break
}
if (sensors.length === 0) {
  console.error(JSON.stringify({ event: 'openaq-backfill:no-sensors', bbox }))
  Deno.exit(1)
}
console.log(JSON.stringify({ event: 'openaq-backfill:sensors', count: sensors.length }))
// --- per-sensor hourly history ------------------------------------------------
type Compact = {
  parameter: string
  value: number
  datetime_utc: string | null
  lat: number | null
  lon: number | null
  sensors_id: number | null
  locations_id: number | null
}
type HourRow = { value: number; period?: { datetimeFrom?: { utc?: string } } }
const rows: Compact[] = []
let failures = 0
let done = 0
const range = `datetime_from=${encodeURIComponent(fromIso)}&datetime_to=${encodeURIComponent(toIso)}`
for (const s of sensors) {
  await sleep(REQUEST_DELAY_MS)
  try {
    const resp = await getJson(`/sensors/${s.sensors_id}/hours?${range}&limit=${PAGE_LIMIT}`)
    for (const r of (resp.results ?? []) as HourRow[]) {
      if (typeof r.value !== 'number') continue
      rows.push({
        parameter: s.parameter,
        value: r.value,
        datetime_utc: r.period?.datetimeFrom?.utc ?? null,
        lat: s.lat,
        lon: s.lon,
        sensors_id: s.sensors_id,
        locations_id: s.locations_id,
      })
    }
  } catch (e) {
    failures++
    console.error(JSON.stringify({
      event: 'openaq-backfill:sensor-error',
      sensors_id: s.sensors_id,
      error: String(e).slice(0, 200),
    }))
    if (failures > MAX_SENSOR_FAILURES) {
      console.error(JSON.stringify({ event: 'openaq-backfill:too-many-failures', failures }))
      Deno.exit(1)
    }
  }
  done++
  if (done % 200 === 0) {
    console.log(JSON.stringify({ event: 'openaq-backfill:progress', done, total: sensors.length, rows: rows.length }))
  }
}
if (rows.length === 0) {
  console.error(JSON.stringify({ event: 'openaq-backfill:empty' }))
  Deno.exit(1)
}
// --- write single artifact ----------------------------------------------------
const tag = `${fromIso.slice(0, 10).replaceAll('-', '')}-${toIso.slice(0, 10).replaceAll('-', '')}`
const outFile = `${outDir}/openaq-backfill-kr-${tag}.json.gz`
const payload = {
  source: 'openaq',
  source_url: `${BASE}/sensors/{id}/hours`,
  nature: 'observation-backfill',
  bbox,
  datetime_from: fromIso,
  datetime_to: toIso,
  ingested_at: startedAt.toISOString(),
  sensors: sensors.length,
  sensor_failures: failures,
  records: rows.length,
  data: rows,
}
const gz = new Blob([JSON.stringify(payload)])
  .stream()
  .pipeThrough(new CompressionStream('gzip'))
await Deno.writeFile(outFile, new Uint8Array(await new Response(gz).arrayBuffer()))
console.log(JSON.stringify({
  event: 'openaq-backfill:collected',
  out: outFile,
  sensors: sensors.length,
  sensor_failures: failures,
  records: rows.length,
}))
