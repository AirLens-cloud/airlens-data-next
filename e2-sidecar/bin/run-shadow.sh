#!/usr/bin/env bash
#
# run-shadow.sh — one shadow collection cycle: collect, then publish to HF.
#
# Wave 1 of plan `polymorphic-kindling-pebble.md`. Publishing reuses the repo's
# existing hf_publish.py rather than reimplementing HF upload, and targets the
# `airkorea-shadow/` prefix only — the canonical live paths are never written.
#
# Secrets arrive through systemd LoadCredential (files under $CREDENTIALS_DIRECTORY),
# never as literals in this file or in the unit.
set -euo pipefail

ROOT="${AIRLENS_ROOT:-/opt/airlens}"
REPO="$ROOT/repo"
SIDECAR="$ROOT/sidecar"
OUT="${AIRLENS_SHADOW_OUT:-/var/lib/airlens/shadow}"
VENV="$ROOT/venv"

# systemd hands credentials as files; fall back to the ambient env for manual runs.
if [ -n "${CREDENTIALS_DIRECTORY:-}" ]; then
  [ -r "$CREDENTIALS_DIRECTORY/airkorea_api_key" ] && \
    AIRKOREA_API_KEY="$(cat "$CREDENTIALS_DIRECTORY/airkorea_api_key")"
  [ -r "$CREDENTIALS_DIRECTORY/hf_token" ] && \
    HF_TOKEN="$(cat "$CREDENTIALS_DIRECTORY/hf_token")"
fi
export AIRKOREA_API_KEY HF_TOKEN

: "${AIRKOREA_API_KEY:?AIRKOREA_API_KEY is required}"
: "${HF_TOKEN:?HF_TOKEN is required}"

mkdir -p "$OUT"

# --- collect -----------------------------------------------------------------
# Permissions are scoped deliberately: read only under $ROOT, write only to $OUT,
# network only to the two hosts this step actually talks to. --lock + the
# pre-warmed DENO_DIR cache make a silent dependency drift fail loudly instead.
COLLECT_JSON="$(
  deno run \
    --allow-read="$ROOT" \
    --allow-write="$OUT" \
    --allow-env=AIRKOREA_API_KEY \
    --allow-net=apis.data.go.kr,esm.sh \
    --lock="$SIDECAR/deno.lock" \
    "$SIDECAR/airkorea_shadow.ts" --out "$OUT"
)"
echo "$COLLECT_JSON"

OUT_FILE="$(printf '%s' "$COLLECT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["out"])')"
[ -s "$OUT_FILE" ] || { echo "run-shadow: collector produced no file" >&2; exit 1; }

# --- publish -----------------------------------------------------------------
# Shadow prefix only. The canonical aq-data/ and wind-data/ paths stay untouched
# so this can run alongside the GitHub Actions collector.
"$VENV/bin/python" "$REPO/scripts/etl/hf_publish.py" upload \
  --src "$OUT_FILE" \
  --dest "airkorea-shadow/$(basename "$OUT_FILE")"

# Local copies are a disposable cache — HF holds the shadow record. Trim so the
# boot volume cannot fill on an unattended box.
find "$OUT" -name 'airkorea-*.json' -mtime +7 -delete

echo "{\"event\":\"run-shadow:published\",\"file\":\"$(basename "$OUT_FILE")\"}"
