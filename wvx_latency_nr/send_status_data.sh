#!/usr/bin/env bash
# Envía estado/conexión por agente a Zabbix como valores NUMÉRICOS (con value map
# en Zabbix para mostrar texto en Grafana): status, connection_type, platform, version.
# El datasource Zabbix de Grafana no soporta graficar items de texto, por eso se
# codifica aquí antes de enviar. El campo "ip" se omite a propósito (ver
# create_status_items.py).
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
WOLKVOX_OPERATION="${WOLKVOX_OPERATION:-unknown_operation}"
_WVX_BASE="${WOLKVOX_URL:-https://wv${WOLKVOX_SERVER}.wolkvox.com/api/v2/real_time.php}"
API_URL="${_WVX_BASE}?api=latency"

BASE_DIR="${LATENCY_BASE_DIR:-/etc/zabbix/scripts/wvx_latency_agent}"
STATE_FILE="${BASE_DIR}/agent_status_state.json"
TMP_FILE="${BASE_DIR}/agent_status_batch.txt"
CURL_OUTPUT="${BASE_DIR}/agent_status_curl.json"
MAX_RETRIES=2
RETRY_DELAY=3
CURL_TIMEOUT=10

mkdir -p "$BASE_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando status/connection monitor..."
# 1) Consulta API
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

# 2) Cargar estado previo (clave = "${code}_${field}")
declare -A LAST_VALUES
if [[ -f "$STATE_FILE" ]]; then
  while IFS="=" read -r k v; do
    [[ -z "$k" ]] && continue
    LAST_VALUES["$k"]="$v"
  done < <(jq -r 'to_entries[] | "\(.key)=\(.value)"' "$STATE_FILE" 2>/dev/null || echo "")
fi

# Mapea texto crudo del API -> codigo numerico (case-insensitive, substring match)
map_status() {
  local v="${1,,}"
  case "$v" in
    *disconnect*|*desconectado*) echo 2 ;;
    *connected*|*conectado*) echo 1 ;;
    *) echo 0 ;;
  esac
}
map_platform() {
  local v="${1,,}"
  case "$v" in
    *app*) echo 1 ;;
    *web*) echo 2 ;;
    *) echo 0 ;;
  esac
}
map_connection() {
  local v="${1,,}"
  case "$v" in
    *wifi*) echo 1 ;;
    *ethernet*|*cable*|*wired*) echo 2 ;;
    *) echo 0 ;;
  esac
}
map_version() {
  local digits="${1//[^0-9]/}"
  [[ -z "$digits" ]] && digits="0"
  echo "$digits"
}

# 3) Procesar agentes
agent_count=0
total_changes=0
while IFS=$'\t' read -r agent_id agent_status connection_type platform version; do
  [[ -z "$agent_id" ]] && continue
  if [[ "$agent_id" =~ ^([0-9]+)- ]]; then
    code="${BASH_REMATCH[1]}"
  else
    continue
  fi
  ((agent_count++))
  status_num=$(map_status "$agent_status")
  platform_num=$(map_platform "$platform")
  conn_num=$(map_connection "$connection_type")
  version_num=$(map_version "$version")

  for pair in "status:${status_num}" "platform:${platform_num}" "connection_type:${conn_num}" "version:${version_num}"; do
    field="${pair%%:*}"
    value="${pair#*:}"
    statekey="${code}_${field}"
    last="${LAST_VALUES[$statekey]:-}"
    if [[ "$value" != "$last" ]] || [[ -z "$last" ]]; then
      echo "${ZBX_HOST} ${WOLKVOX_OPERATION}.agent.${field}[${code}] ${value}" >> "$TMP_FILE"
      LAST_VALUES["$statekey"]="$value"
      ((total_changes++))
    fi
  done
done < <(jq -r '.data[]?.by_agent[]? | [.agent_id, .agent_status, .connection_type, .platform, .version] | @tsv' "$CURL_OUTPUT" 2>/dev/null)

echo ""
echo "[INFO] Total: ${agent_count} agentes | Cambios: ${total_changes}"

# 4) Envío
if [[ -s "$TMP_FILE" ]]; then
  total_items=$(wc -l < "$TMP_FILE")
  echo "[INFO] Enviando $total_items items..."
  SENDER_OUT=$(zabbix_sender -z "$ZBX_SERVER" -p "$ZBX_PORT" -i "$TMP_FILE" 2>&1)
  echo "$SENDER_OUT" | tail -n 3
  PROCESSED=$(echo "$SENDER_OUT" | grep -oP 'processed:\s*\K[0-9]+' | tail -1)
  FAILED=$(echo "$SENDER_OUT" | grep -oP 'failed:\s*\K[0-9]+' | tail -1)
  echo "[RESULT] processed=${PROCESSED:-?} failed=${FAILED:-?}"
  if [[ "${FAILED:-0}" -gt 0 ]]; then
    echo "[WARN] Hay ${FAILED} items rechazados (probablemente agentes sin item creado). Corre create_status_items.py"
  fi
else
  echo "[INFO] Sin cambios"
fi

# 5) Guardar estado
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
