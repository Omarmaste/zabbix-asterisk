#!/usr/bin/env bash
# Autogenera scripts por peer SIP y registra UserParameters en Zabbix Agent
# Ejecutar como root en el servidor Asterisk (chan_sip).

set -euo pipefail

# ======= Variables ajustables (solo aquí) =======
ASTERISK_BIN="${ASTERISK_BIN:-/usr/sbin/asterisk}"           # para listar peers
ZABBIX_CONF="${ZABBIX_CONF:-/etc/zabbix/zabbix_agentd.conf}"
SCRIPTS_DIR="${SCRIPTS_DIR:-/etc/zabbix/scripts}"

# Usuario bajo el que se ejecutará asterisk vía sudo dentro de CADA script generado:
# (déjalo en root si Asterisk corre como root)
ASTERISK_USER_DEFAULT="${ASTERISK_USER_DEFAULT:-root}"

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
  SCRIPT_PATH="${SCRIPTS_DIR}/${PEER}.sh"

  # Script por peer: usa sudo con rutas absolutas y fallback sin sudo; siempre imprime solo número
  cat > "$SCRIPT_PATH" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

PEER="__PEER__"
ASTERISK_BIN="/usr/sbin/asterisk"
SUDO_BIN="/usr/bin/sudo"
ASTERISK_USER="__ASTERISK_USER__"

# 1) Intentar SIEMPRE con sudo (sin TTY ni password). Silenciar stderr.
RAW="$("$SUDO_BIN" -n -u "$ASTERISK_USER" "$ASTERISK_BIN" -rx "sip show peer ${PEER}" 2>/dev/null || true)"

# 2) Fallback directo (por si el agente tiene acceso sin sudo)
if [[ -z "$RAW" ]]; then
  RAW="$("$ASTERISK_BIN" -rx "sip show peer ${PEER}" 2>/dev/null || true)"
fi

# 3) Extraer la línea Status y devolver solo ms
STATUS_LINE="$(printf '%s\n' "$RAW" | awk -F: '/^[[:space:]]*Status/{print $0; exit}')"
if [[ "$STATUS_LINE" =~ \(([0-9]+)[[:space:]]*ms\) ]]; then
  echo "${BASH_REMATCH[1]}"
else
  echo "0"
fi
EOS

  # Inyectar valores
  sed -i "s/__PEER__/${PEER//\//\\/}/g" "$SCRIPT_PATH"
  sed -i "s/__ASTERISK_USER__/${ASTERISK_USER_DEFAULT}/g" "$SCRIPT_PATH"
  chmod 755 "$SCRIPT_PATH"

  # Registrar UserParameter si no existe
  USERPARAM="UserParameter=asterisk.${PEER}, ${SCRIPT_PATH}"
  if ! grep -Fq "UserParameter=asterisk.${PEER}," "$ZABBIX_CONF"; then
    printf "\n%s\n" "$USERPARAM" >> "$ZABBIX_CONF"
    echo "Añadido UserParameter para ${PEER}"
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
