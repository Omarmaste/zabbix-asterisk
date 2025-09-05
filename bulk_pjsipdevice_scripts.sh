#!/usr/bin/env bash
# Autogenera scripts por endpoint PJSIP y registra UserParameters en Zabbix Agent
# Ejecutar como root en el servidor Asterisk (PJSIP).

set -euo pipefail

# ======= Variables ajustables (solo aquí) =======
ASTERISK_BIN="${ASTERISK_BIN:-/usr/sbin/asterisk}"           # binario de asterisk
ZABBIX_CONF="${ZABBIX_CONF:-/etc/zabbix/zabbix_agentd.conf}"  # conf del agente
SCRIPTS_DIR="${SCRIPTS_DIR:-/etc/zabbix/scripts}"             # carpeta para scripts

# Usuario bajo el que se ejecutará asterisk vía sudo dentro de CADA script generado:
# (pon 'asterisk' si tu servicio corre como ese usuario; déjalo 'root' si corre como root).
ASTERISK_USER_DEFAULT="${ASTERISK_USER_DEFAULT:-root}"

# Prefijo para diferenciar de scripts chan_sip existentes
SCRIPT_PREFIX="pjsip-"

# ======= Preparación =======
mkdir -p "$SCRIPTS_DIR"
chmod 755 "$SCRIPTS_DIR"
cp -a "$ZABBIX_CONF" "${ZABBIX_CONF}.bak.$(date +%Y%m%d%H%M%S)"

# ======= Obtener endpoints PJSIP (nombre antes de "/"), evitar cabeceras y resúmenes =======
TMP_EPS="$(mktemp)"
trap 'rm -f "$TMP_EPS"' EXIT

"$ASTERISK_BIN" -rx "pjsip show endpoints" 2>/dev/null | \
awk '
  BEGIN { IGNORECASE=1 }
  # Saltar la línea de plantilla: "Endpoint:  <Endpoint/CID....> ..."
  /^[[:space:]]*Endpoint:[[:space:]]*</ { next }
  /^[[:space:]]*Endpoint:/ {
    sub(/^[[:space:]]*Endpoint:[[:space:]]*/, "", $0)
    # Primer token es el nombre (puede venir "100/100"); nos quedamos con lo anterior a "/"
    split($0, parts, /[[:space:]]+/)
    ep = parts[1]
    split(ep, a, "/")
    # Extra guard: no aceptar nombres que empiecen con "<" por si acaso
    if (a[1] != "" && a[1] !~ /^</) print a[1]
  }
' | sort -u > "$TMP_EPS"


# ======= Generar script por endpoint y registrar UserParameter =======
while IFS= read -r EP; do
  [[ -z "$EP" ]] && continue
  SCRIPT_PATH="${SCRIPTS_DIR}/${SCRIPT_PREFIX}${EP}.sh"

  # Script por endpoint: intenta con sudo y fallback directo; imprime solo número (float o 0)
  cat > "$SCRIPT_PATH" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

EP="__EP__"
ASTERISK_BIN="/usr/sbin/asterisk"
SUDO_BIN="/usr/bin/sudo"
ASTERISK_USER="__ASTERISK_USER__"

# 1) Intentar SIEMPRE con sudo (sin TTY ni password). Silenciar stderr.
RAW="$("$SUDO_BIN" -n -u "$ASTERISK_USER" "$ASTERISK_BIN" -rx "pjsip show endpoint ${EP}" 2>/dev/null || true)"

# 2) Fallback directo (por si el agente tiene acceso sin sudo)
if [[ -z "$RAW" ]]; then
  RAW="$("$ASTERISK_BIN" -rx "pjsip show endpoint ${EP}" 2>/dev/null || true)"
fi

# 3) Si no hay salida, devolver 0
if [[ -z "$RAW" ]]; then
  echo "0"
  exit 0
fi

# 4) Extraer RTT (ms) desde líneas Contact. Puede haber múltiples contactos; tomar el MÍNIMO > 0
#    Formatos típicos en distintas versiones:
#    - "Contact:  <...>  Avail        19.846"
#    - "Contact:  <...>  Status: Available, RTT: 19.846"
min=""
while IFS= read -r line; do
  # Solo líneas de Contacto
  if [[ "$line" =~ ^[[:space:]]*Contact: ]]; then
    # Intento 1: buscar patrón "RTT: <float>"
    if [[ "$line" =~ RTT:[[:space:]]*([0-9]+(\.[0-9]+)?) ]]; then
      val="${BASH_REMATCH[1]}"
    else
      # Intento 2: si contiene "Avail", tomar el ÚLTIMO número flotante de la línea
      if [[ "$line" =~ [Aa]vail ]]; then
        # Extraer último número flotante de la línea
        last_num="$(echo "$line" | awk '
          {
            for (i=NF; i>=1; i--) {
              if ($i ~ /^[0-9]+(\.[0-9]+)?$/) { print $i; exit }
            }
          }
        ')"
        val="${last_num:-}"
      else
        val=""
      fi
    fi

    # Actualizar mínimo si procede
    if [[ -n "${val:-}" ]]; then
      # Normalizar a formato 0.000 (por si viene con enteros)
      if [[ -z "$min" ]]; then
        min="$val"
      else
        # Comparación numérica con awk para soportar floats
        cmp=$(awk -v a="$val" -v b="$min" 'BEGIN{ if (a<b) print 1; else print 0 }')
        if [[ "$cmp" -eq 1 ]]; then
          min="$val"
        fi
      fi
    fi
  fi
done <<< "$RAW"

# 5) Si obtuvimos algún RTT, imprimirlo. Si no, revisar si está Unavailable; si sí, 0; si no, 0.
if [[ -n "${min:-}" ]]; then
  # Imprimir tal cual (Zabbix acepta float). Si prefieres entero, usa printf "%.0f\n" "$min"
  echo "$min"
  exit 0
fi

# Chequear si el encabezado del endpoint indica Unavailable
if echo "$RAW" | awk -v ep="$EP" 'BEGIN{IGNORECASE=1}
  /^[[:space:]]*Endpoint:/ {
    sub(/^[[:space:]]*Endpoint:[[:space:]]*/, "", $0)
    split($0, parts, /[[:space:]]+/)
    split(parts[1], a, "/")
    name = a[1]
    if (name == ep) {
      for (i=2; i<=NF; i++) {
        if (tolower($i) ~ /unavailable/) { print "UNAV"; break }
      }
    }
  }' | grep -q UNAV; then
  echo "0"
  exit 0
fi

# Por defecto
echo "0"
EOS

  # Inyectar valores
  sed -i "s/__EP__/${EP//\//\\/}/g" "$SCRIPT_PATH"
  sed -i "s/__ASTERISK_USER__/${ASTERISK_USER_DEFAULT}/g" "$SCRIPT_PATH"
  chmod 755 "$SCRIPT_PATH"

  # Registrar UserParameter si no existe (namespace separado pjsip)
  USERPARAM="UserParameter=asterisk.pjsip.${EP}, ${SCRIPT_PATH}"
  if ! grep -Fq "UserParameter=asterisk.pjsip.${EP}," "$ZABBIX_CONF"; then
    printf "\n%s\n" "$USERPARAM" >> "$ZABBIX_CONF"
    echo "Añadido UserParameter PJSIP para ${EP}"
  else
    echo "UserParameter PJSIP para ${EP} ya existe, se omite."
  fi
done < "$TMP_EPS"

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

echo "Proceso PJSIP completado."
