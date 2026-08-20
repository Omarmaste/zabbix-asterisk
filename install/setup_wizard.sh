# =============================================================
# setup_wizard.sh — Asistente interactivo para generar .env
#
# Se invoca (source) desde install_zabbix.sh cuando no existe .env,
# o cuando se corre con --wizard. Pregunta todo en español simple,
# por bloques, y SOLO pide lo que hace falta segun los modulos que
# el usuario decida activar. Al terminar:
#   - Escribe SCRIPT_DIR/.env
#   - Deja seteadas las variables SKIP_AST_FAIL2BAN / SKIP_AST_SIP /
#     SKIP_AST_PJSIP / SKIP_AST_COUNTCALLS_LATENCY / SKIP_WVX_LATENCY_NR
#     para que install_zabbix.sh siga de largo con la instalacion real
#     sin tener que volver a pasar flags --skip-* por linea de comandos.
#
# No usar 'set -e' aca: los prompts deben poder repetirse sin matar
# el script ante un Ctrl+C accidental de una sola pregunta, etc.
# =============================================================

# ─── Helpers de prompt ──────────────────────────────────────────
wiz_ask() {
    # wiz_ask "pregunta" "default" -> imprime el valor final por stdout
    local prompt="$1" default="${2:-}" ans
    if [[ -n "$default" ]]; then
        read -rp "$prompt [$default]: " ans
        printf '%s' "${ans:-$default}"
    else
        read -rp "$prompt: " ans
        printf '%s' "$ans"
    fi
}

wiz_ask_required() {
    # Igual que wiz_ask pero insiste hasta recibir algo no vacio
    local prompt="$1" default="${2:-}" ans
    while true; do
        ans="$(wiz_ask "$prompt" "$default")"
        [[ -n "$ans" ]] && { printf '%s' "$ans"; return; }
        echo -e "  ${R}-> este dato es obligatorio${N}" >&2
    done
}

wiz_yn() {
    # wiz_yn "pregunta" "s|n(default)" -> return 0 si "si"
    local prompt="$1" default="${2:-s}" ans hint
    [[ "$default" == "n" ]] && hint="[s/N]" || hint="[S/n]"
    read -rp "$prompt $hint: " ans
    ans="${ans:-$default}"
    [[ "$ans" =~ ^[sSyY] ]]
}

wiz_section() {
    echo ""
    echo -e "${B}${W}── $1 ──${N}"
}

run_wizard() {
    echo ""
    echo -e "${B}${W}"
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║   ASISTENTE DE CONFIGURACIÓN — zabbix-asterisk       ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo -e "${N}"
    echo "  No se encontró .env en $(basename "$SCRIPT_DIR") — vamos a crearlo juntos."
    echo "  En cada pregunta podés apretar ENTER para aceptar el valor entre [corchetes]."
    echo ""

    # ── Bloque 1: qué módulos activar ───────────────────────────
    wiz_section "1/5 — ¿Qué querés instalar en este servidor?"
    wiz_yn "¿Activar Fail2ban de Asterisk (bloqueos de seguridad)?" n && W_FAIL2BAN=1 || W_FAIL2BAN=0
    wiz_yn "¿Activar monitoreo de troncales SIP?"                   n && W_SIP=1      || W_SIP=0
    wiz_yn "¿Activar monitoreo de troncales PJSIP?"                 n && W_PJSIP=1    || W_PJSIP=0
    wiz_yn "¿Activar conteo de llamadas (SIP/PJSIP)?"                n && W_COUNTCALLS=1 || W_COUNTCALLS=0
    wiz_yn "¿Activar Latencia + Network Rejection de agentes Wolkvox (lo mas usual)?" s && W_LATENCY=1 || W_LATENCY=0

    SKIP_AST_FAIL2BAN=$((1 - W_FAIL2BAN))
    SKIP_AST_SIP=$((1 - W_SIP))
    SKIP_AST_PJSIP=$((1 - W_PJSIP))
    SKIP_AST_COUNTCALLS_LATENCY=$((1 - W_COUNTCALLS))
    SKIP_WVX_LATENCY_NR=$((1 - W_LATENCY))

    if [[ $((W_FAIL2BAN + W_SIP + W_PJSIP + W_COUNTCALLS + W_LATENCY)) -eq 0 ]]; then
        echo -e "  ${R}No activaste ningún módulo — no hay nada para configurar. Saliendo.${N}"
        exit 1
    fi

    # ── Bloque 2: Zabbix (siempre hace falta) ───────────────────
    wiz_section "2/5 — Servidor Zabbix"
    echo "  Si este servidor ya monitorea otros clientes (npls, continente, etc.)"
    echo "  seguramente sea el MISMO Zabbix — podés aceptar los valores por defecto."
    W_ZBX_URL="$(wiz_ask "URL de la API de Zabbix" "http://68.183.116.34/zabbix/api_jsonrpc.php")"
    W_ZBX_USER="$(wiz_ask "Usuario de Zabbix" "Admin")"
    W_ZBX_PASS="$(wiz_ask_required "Password de Zabbix")"
    W_ZBX_SERVER="$(wiz_ask "IP del Zabbix server (para zabbix_sender, puerto trapper)" "68.183.116.34")"
    W_ZBX_PORT="$(wiz_ask "Puerto trapper de Zabbix" "10051")"
    W_ZBX_HOST="$(wiz_ask_required "Nombre EXACTO del host en Zabbix donde se crean los items (Data collection > Hosts)")"

    # Overrides por módulo — solo se preguntan si ese módulo esta activo
    W_ZBX_HOST_SIP=""; W_ZBX_HOST_PJSIP=""; W_ZBX_HOST_COUNTCALLS=""; W_ZBX_HOST_COUNTCALLS_PJSIP=""; W_ZBX_HOST_FAIL2BAN=""
    if [[ $W_SIP -eq 1 ]]; then
        W_ZBX_HOST_SIP="$(wiz_ask "Host Zabbix para SIP (ENTER = usar el mismo de arriba)" "$W_ZBX_HOST")"
    fi
    if [[ $W_PJSIP -eq 1 ]]; then
        W_ZBX_HOST_PJSIP="$(wiz_ask "Host Zabbix para PJSIP (ENTER = usar el mismo de arriba)" "$W_ZBX_HOST")"
    fi
    if [[ $W_COUNTCALLS -eq 1 ]]; then
        W_ZBX_HOST_COUNTCALLS="$(wiz_ask "Host Zabbix para conteo SIP (ENTER = usar el mismo de arriba)" "$W_ZBX_HOST")"
        W_ZBX_HOST_COUNTCALLS_PJSIP="$(wiz_ask "Host Zabbix para conteo PJSIP (ENTER = usar el mismo de arriba)" "$W_ZBX_HOST")"
    fi
    if [[ $W_FAIL2BAN -eq 1 ]]; then
        W_ZBX_HOST_FAIL2BAN="$(wiz_ask "Host Zabbix para Fail2ban (ENTER = usar el mismo de arriba)" "$W_ZBX_HOST")"
    fi

    # ── Bloque 3: Wolkvox + Grafana (solo si se activo Latencia+NR) ─
    W_WOLKVOX_SERVER=""; W_WOLKVOX_URL=""; W_WOLKVOX_TOKEN=""; W_WOLKVOX_OPERATION=""
    W_TIMEZONE="America/Bogota"
    W_GRAFANA_URL=""; W_GRAFANA_DASHBOARD_UID=""; W_GRAFANA_FOLDER_TITLE=""; W_GRAFANA_DASHBOARD_TITLE=""
    W_GRAFANA_DS_UID=""; W_GRAFANA_TOKEN=""
    if [[ $W_LATENCY -eq 1 ]]; then
        wiz_section "3/5 — Wolkvox (consulta de latencia/NR de agentes)"
        W_WOLKVOX_SERVER="$(wiz_ask_required "Número de servidor Wolkvox del cliente (ej. 0024, es el 'wvXXXX' de su URL)")"
        W_WOLKVOX_URL="https://wv${W_WOLKVOX_SERVER}.wolkvox.com/api/v2/real_time.php"
        echo "  -> URL de Wolkvox construida automáticamente: $W_WOLKVOX_URL"
        W_WOLKVOX_TOKEN="$(wiz_ask_required "Token de la API de Wolkvox (header wolkvox-token)")"
        W_WOLKVOX_OPERATION="$(wiz_ask_required "Nombre corto de la operación/cliente (ej. expreso-demonte, sin espacios)")"
        W_TIMEZONE="$(wiz_ask "Zona horaria del cliente (para el tablero de Grafana)" "America/Bogota")"

        wiz_section "4/5 — Grafana"
        echo "  Si este cliente comparte el Grafana de npls/continente/etc. (tablero.aloglobal.com),"
        echo "  aceptá los valores por defecto — el datasource de Zabbix ya está creado ahí."
        W_GRAFANA_URL="$(wiz_ask "URL de Grafana" "https://tablero.aloglobal.com")"
        W_GRAFANA_DS_UID="$(wiz_ask "UID del datasource Zabbix en Grafana (compartido entre clientes de ese Grafana)" "bep9lrd00y5fkd")"
        W_GRAFANA_TOKEN="$(wiz_ask_required "Service account token de Grafana (empieza con glsa_)")"

        if wiz_yn "¿Ya existe un tablero armado a mano en Grafana para este cliente?" n; then
            W_GRAFANA_DASHBOARD_UID="$(wiz_ask_required "UID del tablero (se saca de la URL /d/<UID>/... en Grafana)")"
        else
            echo "  -> OK, el tablero y su carpeta se crean solos la primera vez que corra la instalación."
        fi
        W_DEFAULT_FOLDER_TITLE="wvx - ${W_WOLKVOX_OPERATION}"
        W_DEFAULT_DASHBOARD_TITLE="wvx - ${W_WOLKVOX_OPERATION} - Latencia Agentes"
        if wiz_yn "¿Querés personalizar el nombre de la carpeta/tablero en Grafana? (ENTER = usar '${W_DEFAULT_FOLDER_TITLE}')" n; then
            W_GRAFANA_FOLDER_TITLE="$(wiz_ask "Nombre de la carpeta en Grafana" "$W_DEFAULT_FOLDER_TITLE")"
            W_GRAFANA_DASHBOARD_TITLE="$(wiz_ask "Nombre del tablero en Grafana" "$W_DEFAULT_DASHBOARD_TITLE")"
        fi
    else
        wiz_section "3-4/5 — Wolkvox / Grafana"
        echo "  (omitido: no activaste Latencia + NR)"
    fi

    # LATENCY_BASE_DIR / LATENCY_LOG_DIR: se derivan solos de donde vive
    # este script — NUNCA se le pregunta al usuario (fuente frecuente de
    # error: apuntar a una subcarpeta que no es donde se clonó el repo).
    W_LATENCY_BASE_DIR="$SCRIPT_DIR"

    # ── Bloque 5: revisión final ─────────────────────────────────
    wiz_section "5/5 — Revisá antes de guardar"
    echo "  Módulos a instalar:"
    [[ $W_FAIL2BAN    -eq 1 ]] && echo "    - Fail2ban"
    [[ $W_SIP         -eq 1 ]] && echo "    - SIP"
    [[ $W_PJSIP       -eq 1 ]] && echo "    - PJSIP"
    [[ $W_COUNTCALLS  -eq 1 ]] && echo "    - Conteo de llamadas"
    [[ $W_LATENCY     -eq 1 ]] && echo "    - Latencia + NR (Wolkvox)"
    echo ""
    echo "  Zabbix   : $W_ZBX_URL (host: $W_ZBX_HOST)"
    if [[ $W_LATENCY -eq 1 ]]; then
        echo "  Wolkvox  : $W_WOLKVOX_URL (operación: $W_WOLKVOX_OPERATION)"
        echo "  Grafana  : $W_GRAFANA_URL"
    fi
    echo ""
    if ! wiz_yn "¿Guardar esta configuración en .env y continuar con la instalación?" s; then
        echo "  Cancelado. No se escribió ningún archivo."
        exit 1
    fi

    # ── Escribir .env ────────────────────────────────────────────
    {
        echo "# ============================================================="
        echo "# CONFIGURACIÓN GLOBAL — zabbix-asterisk"
        echo "# Generado por el asistente interactivo (install_zabbix.sh) el $(date '+%Y-%m-%d %H:%M:%S')"
        echo "# NUNCA commitear este archivo (está en .gitignore)."
        echo "# ============================================================="
        echo ""
        echo "# ── ZABBIX ──"
        echo "ZBX_URL=\"$W_ZBX_URL\""
        echo "ZBX_USER=\"$W_ZBX_USER\""
        echo "ZBX_PASS=\"$W_ZBX_PASS\""
        echo "ZBX_SERVER=\"$W_ZBX_SERVER\""
        echo "ZBX_PORT=\"$W_ZBX_PORT\""
        echo "ZBX_VERIFY_TLS=\"false\""
        echo "GRAFANA_HOST_FILTER=\"Zabbix server\""
        echo "GRAFANA_GROUP_FILTER=\"Zabbix servers\""
        echo ""
        echo "# ── HOST ZABBIX ──"
        echo "ZBX_HOST=\"$W_ZBX_HOST\""
        [[ -n "$W_ZBX_HOST_SIP"             && "$W_ZBX_HOST_SIP"             != "$W_ZBX_HOST" ]] && echo "ZBX_HOST_SIP=\"$W_ZBX_HOST_SIP\""
        [[ -n "$W_ZBX_HOST_PJSIP"           && "$W_ZBX_HOST_PJSIP"           != "$W_ZBX_HOST" ]] && echo "ZBX_HOST_PJSIP=\"$W_ZBX_HOST_PJSIP\""
        [[ -n "$W_ZBX_HOST_COUNTCALLS"      && "$W_ZBX_HOST_COUNTCALLS"      != "$W_ZBX_HOST" ]] && echo "ZBX_HOST_COUNTCALLS=\"$W_ZBX_HOST_COUNTCALLS\""
        [[ -n "$W_ZBX_HOST_COUNTCALLS_PJSIP" && "$W_ZBX_HOST_COUNTCALLS_PJSIP" != "$W_ZBX_HOST" ]] && echo "ZBX_HOST_COUNTCALLS_PJSIP=\"$W_ZBX_HOST_COUNTCALLS_PJSIP\""
        [[ -n "$W_ZBX_HOST_FAIL2BAN"        && "$W_ZBX_HOST_FAIL2BAN"        != "$W_ZBX_HOST" ]] && echo "ZBX_HOST_FAIL2BAN=\"$W_ZBX_HOST_FAIL2BAN\""

        if [[ $W_LATENCY -eq 1 ]]; then
            echo ""
            echo "# ── WOLKVOX API ──"
            echo "WOLKVOX_URL=\"$W_WOLKVOX_URL\""
            echo "WOLKVOX_SERVER=\"$W_WOLKVOX_SERVER\""
            echo "WOLKVOX_TOKEN=\"$W_WOLKVOX_TOKEN\""
            echo "WOLKVOX_OPERATION=\"$W_WOLKVOX_OPERATION\""
            echo ""
            echo "# ── TIMEZONE ──"
            echo "TIMEZONE_DEFAULT=\"$W_TIMEZONE\""
            echo ""
            echo "# ── LATENCY AGENT — rutas (derivadas automáticamente) ──"
            echo "LATENCY_BASE_DIR=\"$W_LATENCY_BASE_DIR\""
            echo "LATENCY_LOG_DIR=\"$W_LATENCY_BASE_DIR\""
            echo ""
            echo "# ── GRAFANA ──"
            echo "GRAFANA_URL=\"$W_GRAFANA_URL\""
            echo "GRAFANA_DASHBOARD_UID=\"$W_GRAFANA_DASHBOARD_UID\""
            [[ -n "$W_GRAFANA_FOLDER_TITLE"    ]] && echo "GRAFANA_FOLDER_TITLE=\"$W_GRAFANA_FOLDER_TITLE\""
            [[ -n "$W_GRAFANA_DASHBOARD_TITLE" ]] && echo "GRAFANA_DASHBOARD_TITLE=\"$W_GRAFANA_DASHBOARD_TITLE\""
            echo "GRAFANA_DS_UID=\"$W_GRAFANA_DS_UID\""
            echo "GRAFANA_TOKEN=\"$W_GRAFANA_TOKEN\""
        fi

        echo ""
        echo "# ── ASTERISK / ZABBIX AGENT ──"
        echo "ASTERISK_BIN=\"/usr/sbin/asterisk\""
        echo "ZABBIX_CONF=\"/etc/zabbix/zabbix_agentd.conf\""
        echo "SCRIPTS_DIR=\"/etc/zabbix/scripts\""
        echo ""
        echo "# ── GENERAL ──"
        echo "DEBUG=\"false\""
        echo "PEER_SOURCE=\"agent_conf\""
        echo "EXTRA_PEERS=\"\""
    } > "${SCRIPT_DIR}/.env"
    chmod 600 "${SCRIPT_DIR}/.env"

    echo ""
    echo -e "  ${G}✓${N} .env guardado en ${SCRIPT_DIR}/.env (permisos 600)"
    echo ""
}
