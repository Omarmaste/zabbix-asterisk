#!/usr/bin/env bash
# Autogenera scripts de conteo de llamadas por peer SIP y registra UserParameters en Zabbix Agent
# Ejecutar como root en el servidor Asterisk (chan_sip).

set -euo pipefail

# ======= Variables ajustables (solo aquí) =======
ASTERISK_BIN="${ASTERISK_BIN:-/usr/sbin/asterisk}"           # binario de asterisk
ZABBIX_CONF="${ZABBIX_CONF:-/etc/zabbix/zabbix_agentd.conf}" # conf del agente
SCRIPTS_DIR="${SCRIPTS_DIR:-/etc/zabbix/scripts}"            # carpeta para scripts autogenerados

# Usuario bajo el que se ejecutará Asterisk vía sudo en CADA script generado
# (déjalo en root si Asterisk corre como root)
ASTERISK_USER_DEFAULT="${ASTERISK_USER_DEFAULT:-root}"

# Prefijos y llaves de UserParameter
UP_PREFIX="asterisk.calls"    # quedará: asterisk.calls.<peer>
SCRIPT_PREFIX="countcalls_tsip_"  # nombre de archivo base

# ======= Preparación =======
mkdir -p "$SCRIPTS_DIR"
chmod 755 "$SCRIPTS_DIR"
cp -a "$ZABBIX_CONF" "${ZABBIX_CONF}.bak.$(date +%Y%m%d%H%M%S)"

# ======= Obtener peers (columna 1 antes de "/"), evitando cabeceras y resúmenes =======
TMP_PEERS="$(mktemp)"
trap 'rm -f "$TMP_PEERS"' EXIT

"$ASTERISK_BIN" -rx "sip show peers" 2>/dev/null | awk '
  BEGIN{IGNORECASE=1}
  /^[[:space:]]*Name\/username/ { next }   # cabecera
  /Monitored:/            { next }         # resumen
  /objects?[[:space:]]+found/ { next }     # resumen final
  /sip[[:space:]]+peers/  { next }
  /sip[[:space:]]+devices/ { next }
  NF==0 { next }
  {
    split($1,a,"/");
    if (a[1] != "") print a[1];
  }
' | sort -u > "$TMP_PEERS"

# ======= Generar script por peer y registrar UserParameter =======
while IFS= read -r PEER; do
  [[ -z "$PEER" ]] && continue

  SCRIPT_PATH="${SCRIPTS_DIR}/${SCRIPT_PREFIX}${PEER}"   # SIN extensión, como pediste
  USERPARAM_KEY="${UP_PREFIX}.${PEER}"

  # Script por peer: corre "core show channels concise", filtra sus canales y estima llamadas (canales/2 redondeando hacia arriba)
  cat > "$SCRIPT_PATH" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

PEER="__PEER__"
ASTERISK_BIN="/usr/sbin/asterisk"
SUDO_BIN="/usr/bin/sudo"
ASTERISK_USER="__ASTERISK_USER__"

# 1) Intentar con sudo (sin TTY/clave). Silenciar stderr.
RAW="$("$SUDO_BIN" -n -u "$ASTERISK_USER" "$ASTERISK_BIN" -rx "core show channels concise" 2>/dev/null || true)"

# 2) Fallback directo (si el agente puede ejecutar Asterisk sin sudo)
if [[ -z "$RAW" ]]; then
  RAW="$("$ASTERISK_BIN" -rx "core show channels concise" 2>/dev/null || true)"
fi

# 3) Contar canales del peer (formato típico en chan_sip: SIP/<peer>-XXXXXXXX)
CHANNEL_COUNT=$(printf '%s\n' "$RAW" | grep -E "SIP/${PEER}-" | wc -l || true)

# 4) Aproximar número de llamadas (2 canales ~ 1 llamada)
CALL_COUNT=$(( (CHANNEL_COUNT + 1) / 2 ))

echo "${CALL_COUNT}"
EOS

  # Inyectar valores
  sed -i "s/__PEER__/${PEER//\//\\/}/g" "$SCRIPT_PATH"
  sed -i "s/__ASTERISK_USER__/${ASTERISK_USER_DEFAULT}/g" "$SCRIPT_PATH"
  chmod 755 "$SCRIPT_PATH"

  # Registrar UserParameter si no existe (formato: UserParameter=asterisk.calls.<peer>, /etc/zabbix/scripts/countcalls_tsip_<peer>)
  USERPARAM_LINE="UserParameter=${USERPARAM_KEY}, ${SCRIPT_PATH}"
  if ! grep -Fq "UserParameter=${USERPARAM_KEY}," "$ZABBIX_CONF"; then
    printf "\n%s\n" "$USERPARAM_LINE" >> "$ZABBIX_CONF"
    echo "Añadido UserParameter para ${PEER} -> ${USERPARAM_KEY}"
  else
    echo "UserParameter para ${PEER} ya existe, se omite."
  fi
done < "$TMP_PEERS"

# ======= Reiniciar Zabbix Agent =======
if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files | grep -q '^zabbix-agent2\.service'; then
    systemctl restart zabbix-agent2 || true
  elif systemctl list-unit-files | grep -q '^zabbix-agent\.service'; then
    systemctl restart zabbix-agent || true
  else
    service zabbix-agent restart || service zabbix-agent2 restart || true
  fi
else
  service zabbix-agent restart || service zabbix-agent2 restart || true
fi

echo "Proceso completado."

