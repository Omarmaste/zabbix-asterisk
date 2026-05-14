#!/usr/bin/env bash
set -uo pipefail

# Carga .env del proyecto si existe (retrocompatible: si no existe, usa los defaults)
_ENV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${_ENV_ROOT}/.env" ]] && { set -a; source "${_ENV_ROOT}/.env"; set +a; }
unset _ENV_ROOT

ZBX_SERVER="${ZBX_SERVER:-68.183.116.34}"
ZBX_PORT="${ZBX_PORT:-10051}"
ZBX_HOST="${LATENCY_ZBX_HOST:-${ZBX_HOST:-ippbx-cloud-issa5-redplus}}"
WOLKVOX_SERVER="${WOLKVOX_SERVER:-00XX}"
WOLKVOX_TOKEN="${WOLKVOX_TOKEN:-TOKEN}"
_WVX_BASE="${WOLKVOX_URL:-https://wv${WOLKVOX_SERVER}.wolkvox.com/api/v2/real_time.php}"
API_URL="${_WVX_BASE}?api=latency"

BASE_DIR="${LATENCY_BASE_DIR:-/etc/zabbix/scripts/wvx_latency_agent}"
STATE_FILE="${BASE_DIR}/agent_latency_state.json"
TMP_FILE="${BASE_DIR}/agent_latency_batch.txt"
CURL_OUTPUT="${BASE_DIR}/agent_latency_curl.json"

MAX_RETRIES=2
RETRY_DELAY=3
CURL_TIMEOUT=10

mkdir -p "$BASE_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando latency monitor..."

for attempt in $(seq 1 $MAX_RETRIES); do
  timeout ${CURL_TIMEOUT} curl -sS -m ${CURL_TIMEOUT} \
    -o "$CURL_OUTPUT" \
    -H "wolkvox_server: ${WOLKVOX_SERVER}" \
    -H "wolkvox-token: ${WOLKVOX_TOKEN}" \
    "${API_URL}" 2>/dev/null
  if [ $? -eq 0 ] && jq -e '.data[]?.by_agent[]?' "$CURL_OUTPUT" >/dev/null 2>&1; then
    echo "[OK] Agentes obtenidos"
    break
  fi
  if [ $attempt -lt $MAX_RETRIES ]; then
    echo "[WARN] Reintentando..."
    sleep $RETRY_DELAY
  else
    echo "[ERR] Fallo al obtener agentes"
    exit 1
  fi
done

> "$TMP_FILE"

declare -A LAST_VALUES
if [[ -f "$STATE_FILE" ]]; then
  while IFS="=" read -r k v; do
    [[ -z "$k" || -z "$v" ]] && continue
    LAST_VALUES["$k"]="$v"
  done < <(jq -r 'to_entries[] | "\(.key)=\(.value)"' "$STATE_FILE" 2>/dev/null || echo "")
fi

agent_count=0
total_changes=0

while IFS='|' read -r agent_id latency_ms; do
  [[ -z "$agent_id" || -z "$latency_ms" ]] && continue
  if [[ "$agent_id" =~ ^([0-9]+)- ]]; then
    code="${BASH_REMATCH[1]}"
  else
    continue
  fi
  [[ "$latency_ms" =~ ^[0-9]+$ ]] || latency_ms="0"
  ((agent_count++))
  last="${LAST_VALUES[$code]:-}"
  if [[ "$latency_ms" != "$last" ]] || [[ -z "$last" ]]; then
    echo "${ZBX_HOST} agent.latency[${code}] ${latency_ms}" >> "$TMP_FILE"
    echo "  ✓ Agent ${code}: ${last:-N/A} -> ${latency_ms} ms"
    LAST_VALUES["$code"]="$latency_ms"
    ((total_changes++))
  fi
done < <(jq -r '.data[]?.by_agent[]? | "\(.agent_id)|\(.latency_ms)"' "$CURL_OUTPUT" 2>/dev/null)

echo ""
echo "[INFO] Total: ${agent_count} agentes | Cambios: ${total_changes}"

if [[ -s "$TMP_FILE" ]]; then
  total_items=$(wc -l < "$TMP_FILE")
  echo "[INFO] Enviando $total_items items..."
  # Capturamos la salida real para diagnosticar
  SENDER_OUT=$(zabbix_sender -z "$ZBX_SERVER" -p "$ZBX_PORT" -i "$TMP_FILE" 2>&1)
  echo "$SENDER_OUT" | tail -n 3
  PROCESSED=$(echo "$SENDER_OUT" | grep -oP 'processed:\s*\K[0-9]+' | tail -1)
  FAILED=$(echo "$SENDER_OUT" | grep -oP 'failed:\s*\K[0-9]+' | tail -1)
  echo "[RESULT] processed=${PROCESSED:-?} failed=${FAILED:-?}"
  if [[ "${FAILED:-0}" -gt 0 ]]; then
    echo "[WARN] Hay ${FAILED} items rechazados (probablemente agentes sin item creado). Corre create_latency_items.py"
  fi
else
  echo "[INFO] Sin cambios"
fi

{
  echo "{"
  first=true
  for key in "${!LAST_VALUES[@]}"; do
    [ "$first" = true ] && first=false || echo ","
    echo -n "  \"${key}\": \"${LAST_VALUES[$key]}\""
  done
  echo ""
  echo "}"
} > "$STATE_FILE"

rm -f "$TMP_FILE" "$CURL_OUTPUT"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Fin"
