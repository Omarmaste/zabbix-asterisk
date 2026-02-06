#!/usr/bin/env bash
# Envía métricas de latencia por agente a Zabbix

set -uo pipefail

ZBX_SERVER="IP"
ZBX_PORT="10051"
ZBX_HOST="startgroup"
WOLKVOX_SERVER="0041"
WOLKVOX_TOKEN="token"
API_URL="https://wv${WOLKVOX_SERVER}.wolkvox.com/api/v2/real_time.php?api=latency"

STATE_FILE="/etc/zabbix/scripts/agent_latency_state.json"
TMP_FILE="/etc/zabbix/scripts/agent_latency_batch.txt"
CURL_OUTPUT="/etc/zabbix/scripts/agent_latency_curl.json"

MAX_RETRIES=2
RETRY_DELAY=3
CURL_TIMEOUT=10

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando latency monitor..."

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

# 2) Cargar estado previo
declare -A LAST_VALUES
if [[ -f "$STATE_FILE" ]]; then
  while IFS="=" read -r k v; do
    [[ -z "$k" || -z "$v" ]] && continue
    LAST_VALUES["$k"]="$v"
  done < <(jq -r 'to_entries[] | "\(.key)=\(.value)"' "$STATE_FILE" 2>/dev/null || echo "")
fi

# 3) Procesar agentes
agent_count=0
total_changes=0

while IFS='|' read -r agent_id latency_ms; do
  [[ -z "$agent_id" || -z "$latency_ms" ]] && continue
  
  # Extraer código de agente (antes del guión)
  if [[ "$agent_id" =~ ^([0-9]+)- ]]; then
    code="${BASH_REMATCH[1]}"
  else
    continue
  fi
  
  # Validar latencia
  [[ "$latency_ms" =~ ^[0-9]+$ ]] || latency_ms="0"
  
  ((agent_count++))
  
  # Verificar cambio
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

# 4) Envío
if [[ -s "$TMP_FILE" ]]; then
  total_items=$(wc -l < "$TMP_FILE")
  echo "[INFO] Enviando $total_items items..."
  zabbix_sender -z "$ZBX_SERVER" -p "$ZBX_PORT" -i "$TMP_FILE" >/dev/null 2>&1 && echo "[OK] Enviado" || echo "[ERR] Fallo"
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
