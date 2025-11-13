#!/usr/bin/env bash
# Autogenera scripts de conteo de llamadas por endpoint PJSIP y registra UserParameters en Zabbix Agent

set -euo pipefail

# ======= Variables configurables =======
ASTERISK_BIN="${ASTERISK_BIN:-/usr/sbin/asterisk}"           # binario de asterisk
ZABBIX_CONF="${ZABBIX_CONF:-/etc/zabbix/zabbix_agentd.conf}" # config Zabbix Agent
SCRIPTS_DIR="${SCRIPTS_DIR:-/etc/zabbix/scripts}"            # carpeta para scripts

ASTERISK_USER_DEFAULT="${ASTERISK_USER_DEFAULT:-root}"       # usuario para ejecutar Asterisk

# Prefijo para claves y scripts
UP_PREFIX="asterisk.calls.pjsip"
SCRIPT_PREFIX="countcalls_tpjsip_"

# ======= Preparación =======
mkdir -p "$SCRIPTS_DIR"
chmod 755 "$SCRIPTS_DIR"
cp -a "$ZABBIX_CONF" "${ZABBIX_CONF}.bak.$(date +%Y%m%d%H%M%S)"

# ======= Obtener endpoints PJSIP =======
TMP_PEERS="$(mktemp)"
trap 'rm -f "$TMP_PEERS"' EXIT

"$ASTERISK_BIN" -rx "pjsip show endpoints" 2>/dev/null \
  | awk '/^ Endpoint:/ { print $2 }' \
  | sort -u > "$TMP_PEERS"

# ======= Generar script por endpoint y registrar UserParameter =======
while IFS= read -r ENDPOINT; do
  [[ -z "$ENDPOINT" ]] && continue

  SAFE_ENDPOINT=$(echo "$ENDPOINT" | sed 's#[^a-zA-Z0-9._-]#_#g')
  SCRIPT_PATH="${SCRIPTS_DIR}/${SCRIPT_PREFIX}${SAFE_ENDPOINT}"
  USERPARAM_KEY="${UP_PREFIX}.${SAFE_ENDPOINT}"

  # Script por endpoint: cuenta canales que contienen "PJSIP/<endpoint>-" y divide por 2
  cat > "$SCRIPT_PATH" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

ENDPOINT="__ENDPOINT__"
ASTERISK_BIN="/usr/sbin/asterisk"
SUDO_BIN="/usr/bin/sudo"
ASTERISK_USER="__ASTERISK_USER__"

RAW="$("$SUDO_BIN" -n -u "$ASTERISK_USER" "$ASTERISK_BIN" -rx "core show channels concise" 2>/dev/null || true)"
if [[ -z "$RAW" ]]; then
  RAW="$("$ASTERISK_BIN" -rx "core show channels concise" 2>/dev/null || true)"
fi

# Contar canales PJSIP/<endpoint>- activos
CHANNEL_COUNT=$(printf '%s\n' "$RAW" | grep -E "PJSIP/${ENDPOINT}-" | wc -l || true)

# Aproximar llamadas: 2 canales ≈ 1 llamada
CALL_COUNT=$(( (CHANNEL_COUNT + 1) / 2 ))

echo "${CALL_COUNT}"
EOS

  # Inyectar valores
  sed -i "s/__ENDPOINT__/${ENDPOINT//\//\\/}/g" "$SCRIPT_PATH"
  sed -i "s/__ASTERISK_USER__/${ASTERISK_USER_DEFAULT}/g" "$SCRIPT_PATH"
  chmod 755 "$SCRIPT_PATH"

  # Agregar UserParameter si no existe
  USERPARAM_LINE="UserParameter=${USERPARAM_KEY},${SCRIPT_PATH}"
  if ! grep -Fq "UserParameter=${USERPARAM_KEY}," "$ZABBIX_CONF"; then
    printf "\n%s\n" "$USERPARAM_LINE" >> "$ZABBIX_CONF"
    echo "Añadido UserParameter para ${ENDPOINT} -> ${USERPARAM_KEY}"
  else
    echo "UserParameter para ${ENDPOINT} ya existe, se omite."
  fi
done < "$TMP_PEERS"

# ======= Reiniciar Zabbix Agent =======
echo "Reiniciando agente Zabbix para
