#!/usr/bin/env bash
# Sincroniza items de agentes Wolkvox en Zabbix (latencia + network rejection)
# Ejecutar diariamente a las 01:00 vía cron

set -uo pipefail

# Carga .env del proyecto si existe (retrocompatible: si no existe, usa los defaults)
_ENV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${_ENV_ROOT}/.env" ]] && { set -a; source "${_ENV_ROOT}/.env"; set +a; }
unset _ENV_ROOT

# Directorio de los scripts Python (mismo directorio que este script)
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LATENCY_LOG_DIR:-/var/log/zabbix}"
LOG_FILE="${LOG_DIR}/sync_agents.log"

mkdir -p "$LOG_DIR"

{
  echo ""
  echo "========================================================"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] INICIO SYNC AGENTES WOLKVOX"
  echo "========================================================"

  echo ""
  echo ">>> Sincronizando items de LATENCIA..."
  /usr/bin/python3 "${SCRIPTS_DIR}/create_latency_items.py"
  RC1=$?
  echo ">>> Exit code latency: $RC1"

  echo ""
  echo ">>> Sincronizando items de NETWORK REJECTION..."
  /usr/bin/python3 "${SCRIPTS_DIR}/create_nr_items.py"
  RC2=$?
  echo ">>> Exit code NR: $RC2"

  echo ""
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] FIN SYNC AGENTES (latency=$RC1 nr=$RC2)"
} >> "$LOG_FILE" 2>&1

# Rotación simple: si log > 5MB, lo trunca dejando últimas 1000 líneas
if [[ -f "$LOG_FILE" ]] && [[ $(stat -c%s "$LOG_FILE") -gt 5242880 ]]; then
  tail -n 1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
