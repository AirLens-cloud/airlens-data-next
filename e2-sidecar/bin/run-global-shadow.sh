#!/usr/bin/env bash
#
# run-global-shadow.sh <sc|openaq> — one global shadow cycle: collect, publish to HF.
#
# Wave 1 pivot (atomic-gathering-reddy.md): global sources replace AirKorea.
# Same shape as run-shadow.sh — publishing reuses the repo's hf_publish.py and
# writes only to shadow prefixes; canonical live paths are never touched.
# Secrets arrive through systemd LoadCredential, never as literals.
set -euo pipefail

MODE="${1:?usage: run-global-shadow.sh <sc|openaq>}"

ROOT="${AIRLENS_ROOT:-/opt/airlens}"
# hf_publish.py + contracts/ are shipped by deploy.sh from this repo (no clone on the VM).
LIB="$ROOT/lib"
SIDECAR="$ROOT/sidecar"
OUT="${AIRLENS_SHADOW_OUT:-/var/lib/airlens/shadow}"
VENV="$ROOT/venv"

if [ -n "${CREDENTIALS_DIRECTORY:-}" ]; then
  [ -r "$CREDENTIALS_DIRECTORY/hf_token" ] && \
    HF_TOKEN="$(cat "$CREDENTIALS_DIRECTORY/hf_token")"
  [ -r "$CREDENTIALS_DIRECTORY/openaq_api_key" ] && \
    OPENAQ_API_KEY="$(cat "$CREDENTIALS_DIRECTORY/openaq_api_key")"
fi
export HF_TOKEN OPENAQ_API_KEY
: "${HF_TOKEN:?HF_TOKEN is required}"

mkdir -p "$OUT"

case "$MODE" in
  sc)
    COLLECT_JSON="$(
      deno run \
        --allow-write="$OUT" \
        --allow-net=data.sensor.community \
        "$SIDECAR/sensor_community_shadow.ts" --out "$OUT"
    )"
    DEST_PREFIX="sensor-community-shadow"
    TRIM_GLOB='sensor-community-*.json.gz'
    ;;
  openaq)
    : "${OPENAQ_API_KEY:?OPENAQ_API_KEY is required}"
    COLLECT_JSON="$(
      deno run \
        --allow-write="$OUT" \
        --allow-env=OPENAQ_API_KEY \
        --allow-net=api.openaq.org \
        "$SIDECAR/openaq_shadow.ts" --out "$OUT"
    )"
    DEST_PREFIX="openaq-shadow"
    TRIM_GLOB='openaq-*.json.gz'
    ;;
  *)
    echo "unknown mode: $MODE" >&2; exit 2 ;;
esac

echo "$COLLECT_JSON"
OUT_FILE="$(printf '%s' "$COLLECT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["out"])')"
[ -s "$OUT_FILE" ] || { echo "run-global-shadow: collector produced no file" >&2; exit 1; }

"$VENV/bin/python" "$LIB/scripts/etl/hf_publish.py" upload \
  --src "$OUT_FILE" \
  --dest "$DEST_PREFIX/$(basename "$OUT_FILE")"

# Local copies are a disposable cache — HF holds the shadow record.
find "$OUT" -name "$TRIM_GLOB" -mtime +7 -delete

echo "{\"event\":\"run-global-shadow:published\",\"mode\":\"$MODE\",\"file\":\"$(basename "$OUT_FILE")\"}"
