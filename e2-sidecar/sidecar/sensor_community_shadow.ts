/**
 * sensor_community_shadow.ts — global low-cost sensor snapshot (Sensor.Community).
 *
 * Wave 1 pivot (plan atomic-gathering-reddy.md, 2026-08-25): the sidecar now
 * collects global sources instead of AirKorea. Sensor.Community is keyless and
 * publishes a rotating last-5-minutes snapshot (~8.7MB global, measured
 * 2026-08-25). One pull per hour archives an hourly slice; coverage honesty:
 * Korea has 0 sensors (probed country=KR and a 33.0,124.0–39.5,132.0 box —
 * both empty), so this feeds global surfaces only.
 *
 * No external deps — plain fetch + web CompressionStream (Deno 2.x builtin).
 * Fail-loud: zero outdoor records => exit 1, no file left behind.
 */

const SC_URL = 'https://data.sensor.community/static/v2/data.json'

function argValue(flag: string, fallback: string): string {
  const i = Deno.args.indexOf(flag)
  return i >= 0 && Deno.args[i + 1] ? Deno.args[i + 1] : fallback
}

interface ScValue { value_type: string; value: string }
interface ScRecord {
  timestamp: string
  location: {
    latitude: string; longitude: string; country: string
    indoor: number; altitude?: string
  }
  sensor: { id: number; sensor_type: { name: string } }
  sensordatavalues: ScValue[]
}

const KEEP_VALUES = new Set([
  'P0', 'P1', 'P2', 'P4', // PM1 / PM10 / PM2.5 / PM4
  'temperature', 'humidity', 'pressure', 'pressure_at_sealevel',
])

const outDir = argValue('--out', '/var/lib/airlens/shadow')
const startedAt = new Date()

const res = await fetch(SC_URL, {
  headers: { 'user-agent': 'airlens-e2-sidecar/1.0 (shadow archive)' },
})
if (!res.ok) {
  console.error(JSON.stringify({ event: 'sc-shadow:http-error', status: res.status }))
  Deno.exit(1)
}
const raw: ScRecord[] = await res.json()

type Compact = {
  sensor_id: number
  sensor_type: string
  ts: string // upstream measurement timestamp (UTC, as published)
  lat: number
  lon: number
  country: string
  values: Record<string, number>
}

const rows: Compact[] = []
for (const r of raw) {
  if (!r?.sensor || !r?.location || r.location.indoor === 1) continue
  const values: Record<string, number> = {}
  for (const v of r.sensordatavalues ?? []) {
    if (!KEEP_VALUES.has(v.value_type)) continue
    const n = Number(v.value)
    if (Number.isFinite(n)) values[v.value_type] = n
  }
  if (Object.keys(values).length === 0) continue
  const lat = Number(r.location.latitude)
  const lon = Number(r.location.longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue
  rows.push({
    sensor_id: r.sensor.id,
    sensor_type: r.sensor.sensor_type?.name ?? 'unknown',
    ts: r.timestamp,
    lat, lon,
    country: r.location.country ?? '',
    values,
  })
}

if (rows.length === 0) {
  console.error(JSON.stringify({ event: 'sc-shadow:empty', upstream_records: raw.length }))
  Deno.exit(1)
}

const stamp = startedAt.toISOString().slice(0, 13).replace(/[-T]/g, '') // UTC YYYYMMDDHH
const outFile = `${outDir}/sensor-community-${stamp}.json.gz`

const payload = {
  source: 'sensor.community',
  source_url: SC_URL,
  nature: 'observation',
  quality_tier: 'low-cost-sensor',
  window: 'last-5-minutes-snapshot',
  ingested_at: startedAt.toISOString(),
  records: rows.length,
  data: rows,
}

const gz = new Blob([JSON.stringify(payload)])
  .stream()
  .pipeThrough(new CompressionStream('gzip'))
await Deno.writeFile(outFile, new Uint8Array(await new Response(gz).arrayBuffer()))

const countries = new Set(rows.map((r) => r.country)).size
console.log(JSON.stringify({
  event: 'sc-shadow:collected',
  out: outFile,
  records: rows.length,
  countries,
  upstream_records: raw.length,
}))
