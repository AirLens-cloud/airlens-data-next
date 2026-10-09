#!/bin/bash
# run_pipeline.sh — 무료($0) 정책 수집 파이프라인 (구조화 소스 → policy_registry.json)
#
# Usage:
#   bash scripts/etl/collect_policies/run_pipeline.sh            # WHO + Major + Merge + Export
#   RUN_CPR=1 bash scripts/etl/collect_policies/run_pipeline.sh  # + Climate Policy Radar (~3.59GB HF download)
#
# Publish (separate step — needs HF_TOKEN):
#   python3 scripts/etl/hf_publish.py upload --src ... --dest insights-data/policy/...
#
# The weekly cron (.github/workflows/policy-collect.yml) runs this script, then
# publishes the registry + frontend exports to HF (Supabase retired 2026-08).
# CPR is gated behind RUN_CPR so the weekly tick stays light.

set -e
cd "$(dirname "$0")/../../.."   # repo root (scripts/etl/collect_policies → ../../..)

PYTHON="${PYTHON:-python3}"
P=scripts/etl/collect_policies

# Retry a fetch step up to 3x with exponential backoff (2s, 4s) so a transient
# upstream/network blip doesn't fail the weekly run. Collectors overwrite their
# registry JSON idempotently, so re-running a step is safe. On exhaustion the
# non-zero return propagates to `set -e` → fail loud (persistent outage).
# Applied to the external-fetch steps only; merge/export are local and fail loud.
retry() {
  local n=1 max=3 code=0
  until "$@"; do
    code=$?
    if [ "$n" -lt "$max" ]; then
      echo "  ↻ retry ${n}/$((max - 1)) after exit ${code}; sleep $((2 * n))s..." >&2
      sleep $((2 * n)); n=$((n + 1))
    else
      return "$code"
    fi
  done
}

echo "=== Step 1: WHO Standards ==="
retry $PYTHON "$P/collect_who_standards.py"

echo ""
echo "=== Step 2: Major Policies ==="
retry $PYTHON "$P/collect_major_policies.py"

echo ""
echo "=== Step 2c: US Federal Register ==="
retry $PYTHON "$P/collect_federal_register.py"

if [ "${RUN_CPR:-0}" = "1" ]; then
  echo ""
  echo "=== Step 2b: Climate Policy Radar (CPR) ==="
  retry $PYTHON "$P/collect_cpr.py" ${CPR_SKIP_DOWNLOAD:+--skip-download}
fi

if [ "${RUN_LLM_EXTRACT:-0}" = "1" ]; then
  echo ""
  echo "=== Step 2d: LLM extraction (gpt-4o-mini, opt-in — needs OPENAI_API_KEY) ==="
  retry $PYTHON "$P/collect_llm_extract.py"
fi

echo ""
echo "=== Step 3: Merge ==="
$PYTHON "$P/merge_policies.py"

echo ""
echo "=== Step 4: Export Frontend ==="
$PYTHON "$P/export_frontend.py"

echo ""
echo "=== Pipeline complete — registry built. Run upsert_supabase.py to publish. ==="
