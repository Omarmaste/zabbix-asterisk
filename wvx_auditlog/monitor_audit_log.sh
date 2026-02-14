#!/usr/bin/env bash
# Monitor de Audit Log con soporte para múltiples operaciones Wolkvox
# Uso: bash monitor_audit_log.sh <wvx_operacion> [--debug]



set -uo pipefail

# ========= VALIDACIÓN DE ARGUMENTOS =========
if [ $# -lt 1 ]; then
    echo "Error: Debe proporcionar el nombre de la operación Wolkvox"
    echo ""
    echo "Uso: bash monitor_audit_log.sh <wvx_operacion> [--debug]"
    echo ""
    echo "Ejemplos:"
    echo "  bash monitor_audit_log.sh stargroup"
    echo "  bash monitor_audit_log.sh alo-proyectos --debug"
    exit 1
fi

WVX_OPERACION="$1"
DEBUG=0
[[ "${2:-}" == "--debug" ]] && DEBUG=1

# ========= CONFIGURACIÓN BASE =========
ZBX_SERVER="172.27.127.89"
ZBX_PORT="10051"
ZBX_HOST="monitoralo"

# ========= CONFIGURACIÓN POR OPERACIÓN =========
case "$WVX_OPERACION" in
    stargroup)
        WOLKVOX_SERVER="00XX"
        WOLKVOX_TOKEN="TOKEN1"
        ;;
    alo-proyectos)
        WOLKVOX_SERVER="00XX"
        WOLKVOX_TOKEN="TOKEN2"
        ;;
    *)
        echo "Error: Operación '$WVX_OPERACION' no configurada"
        exit 1
        ;;
esac

API_URL="https://wv${WOLKVOX_SERVER}.wolkvox.com/api/v2/information.php"

# ========= ARCHIVOS =========
SCRIPT_DIR="/etc/zabbix/scripts/wvx/${WVX_OPERACION}"
STATE_FILE="${SCRIPT_DIR}/audit_log_state.json"
COUNTER_FILE="${SCRIPT_DIR}/audit_log_counters.json"
TMP_FILE="${SCRIPT_DIR}/audit_log_batch.txt"
CURL_OUTPUT="${SCRIPT_DIR}/audit_log_curl.json"
DEBUG_LOG="${SCRIPT_DIR}/audit_log_debug.log"

mkdir -p "$SCRIPT_DIR"

# ========= PARÁMETROS =========
CURL_TIMEOUT=15
MAX_RETRIES=2
RETRY_DELAY=3

# ========= PATRONES DE FILTRO =========
IGNORE_PATTERNS=(
    "logged in to"
    "API Campaign add_record"
    "has been consumed"
    "logged off"
)

# ========= FUNCIONES =========

log_msg() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    [[ $DEBUG -eq 1 ]] && echo "$msg" >> "$DEBUG_LOG"
}

debug_msg() {
    [[ $DEBUG -eq 1 ]] && echo "[DEBUG $(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$DEBUG_LOG"
}

get_time_range() {
    local date_end=$(date '+%Y%m%d%H%M%S')
    local date_ini=$(date -d '5 minutes ago' '+%Y%m%d%H%M%S')
    echo "${date_ini}|${date_end}"
}

should_ignore() {
    local action="$1"
    for pattern in "${IGNORE_PATTERNS[@]}"; do
        [[ "$action" == *"$pattern"* ]] && return 0
    done
    return 1
}

classify_alert() {
    local action="$1"
    local action_lower=$(echo "$action" | tr '[:upper:]' '[:lower:]')
    local prefix="${WVX_OPERACION}.audit"
    
    if [[ "$action_lower" == *"wolkvox studio: compile"* ]]; then
        echo "${prefix}.studio_compile"
        return
    fi
    
    if [[ "$action_lower" == *"diagram studio:"* ]]; then
        echo "${prefix}.diagram_studio"
        return
    fi
    
    if [[ "$action_lower" == *"refix:"* ]]; then
        echo "${prefix}.refix"
        return
    fi
    
    if [[ "$action_lower" == *"api configuration"* ]]; then
        echo "${prefix}.api_configuration"
        return
    fi
    
    if [[ "$action_lower" == *"the tts component has been activated"* ]]; then
        echo "${prefix}.tts_activated"
        return
    fi
    
    if [[ "$action_lower" == *"the nlp ai component has been activated"* ]]; then
        echo "${prefix}.nlp_ai_activated"
        return
    fi
    
    if [[ "$action_lower" == *"the general nlp component has been activated"* ]]; then
        echo "${prefix}.general_nlp_activated"
        return
    fi
    
    if [[ "$action_lower" == *"predictive: stop campaign"* ]]; then
        echo "${prefix}.predictive_stop"
        return
    fi
    
    if [[ "$action_lower" == *"delete"* ]]; then
        echo "${prefix}.delete_action"
        return
    fi
    
    if [[ "$action_lower" == *"changed their profile"* ]]; then
        echo "${prefix}.profile_change"
        return
    fi
    
    echo ""
}

fetch_audit_log() {
    local time_range
    time_range=$(get_time_range)
    local date_ini="${time_range%|*}"
    local date_end="${time_range#*|}"
    
    local url="${API_URL}?api=audit_log&date_ini=${date_ini}&date_end=${date_end}"
    
    log_msg "Consultando audit log: ${date_ini} -> ${date_end}"
    debug_msg "Operación: ${WVX_OPERACION}"
    debug_msg "Server: wv${WOLKVOX_SERVER}"
    
    for attempt in $(seq 1 $MAX_RETRIES); do
        debug_msg "Intento ${attempt}/${MAX_RETRIES}"
        
        timeout ${CURL_TIMEOUT} curl -sS -m ${CURL_TIMEOUT} \
            -o "$CURL_OUTPUT" \
            -H "wolkvox_server: ${WOLKVOX_SERVER}" \
            -H "wolkvox-token: ${WOLKVOX_TOKEN}" \
            "${url}" 2>/dev/null
        
        if jq -e '.' "$CURL_OUTPUT" >/dev/null 2>&1; then
            local record_count=$(jq -r '.msg' "$CURL_OUTPUT" 2>/dev/null | grep -oP '\d+' | head -1)
            local code=$(jq -r '.code' "$CURL_OUTPUT" 2>/dev/null)
            
            debug_msg "API code: ${code}"
            debug_msg "Record count: ${record_count:-0}"
            
            if [[ "$code" == "0" ]]; then
                log_msg "  ✓ OK - ${record_count:-0} registros obtenidos"
                return 0
            fi
        fi
        
        if [ $attempt -lt $MAX_RETRIES ]; then
            log_msg "  ⚠ Reintentando ($attempt/$MAX_RETRIES)..."
            sleep $RETRY_DELAY
        fi
    done
    
    log_msg "  ✗ ERROR: No se pudo obtener audit log"
    return 1
}

load_state() {
    # IMPORTANTE: Declarar el array GLOBAL primero
    declare -gA PROCESSED_IDS
    
    if [[ -f "$STATE_FILE" ]]; then
        local loaded=0
        while IFS="=" read -r k v; do
            [[ -z "$k" ]] && continue
            PROCESSED_IDS["$k"]="$v"
            ((loaded++))
        done < <(jq -r 'to_entries[] | "\(.key)=\(.value)"' "$STATE_FILE" 2>/dev/null || true)
        
        if [ $loaded -gt 0 ]; then
            log_msg "  Estado cargado: $loaded eventos previos"
        else
            log_msg "  Estado vacío"
        fi
    else
        log_msg "  Primer ejecución - sin estado previo"
    fi
}

load_counters() {
    # IMPORTANTE: Declarar el array GLOBAL primero
    declare -gA COUNTERS
    
    if [[ -f "$COUNTER_FILE" ]]; then
        local loaded=0
        while IFS="=" read -r k v; do
            [[ -z "$k" ]] && continue
            COUNTERS["$k"]="$v"
            ((loaded++))
        done < <(jq -r 'to_entries[] | "\(.key)=\(.value)"' "$COUNTER_FILE" 2>/dev/null || true)
        
        if [ $loaded -gt 0 ]; then
            log_msg "  Contadores cargados: $loaded tipos"
        else
            log_msg "  Inicializando contadores"
            init_counters
        fi
    else
        log_msg "  Inicializando contadores"
        init_counters
    fi
}

init_counters() {
    local prefix="${WVX_OPERACION}.audit"
    COUNTERS=(
        ["${prefix}.studio_compile"]="0"
        ["${prefix}.diagram_studio"]="0"
        ["${prefix}.refix"]="0"
        ["${prefix}.api_configuration"]="0"
        ["${prefix}.tts_activated"]="0"
        ["${prefix}.nlp_ai_activated"]="0"
        ["${prefix}.general_nlp_activated"]="0"
        ["${prefix}.predictive_stop"]="0"
        ["${prefix}.delete_action"]="0"
        ["${prefix}.profile_change"]="0"
    )
}

save_state() {
    if [ ${#PROCESSED_IDS[@]} -eq 0 ]; then
        echo "{}" > "$STATE_FILE"
        return
    fi
    
    {
        echo "{"
        local first=true
        for key in "${!PROCESSED_IDS[@]}"; do
            [ "$first" = true ] && first=false || echo ","
            printf '  "%s": "%s"' "$key" "${PROCESSED_IDS[$key]}"
        done
        echo ""
        echo "}"
    } > "$STATE_FILE"
    debug_msg "Estado guardado: ${#PROCESSED_IDS[@]} eventos"
}

save_counters() {
    {
        echo "{"
        local first=true
        for key in "${!COUNTERS[@]}"; do
            [ "$first" = true ] && first=false || echo ","
            printf '  "%s": %s' "$key" "${COUNTERS[$key]}"
        done
        echo ""
        echo "}"
    } > "$COUNTER_FILE"
    debug_msg "Contadores guardados"
}

process_audit_events() {
    > "$TMP_FILE"
    
    local total_events=0
    local ignored_events=0
    local new_alerts=0
    local unclassified=0
    
    if ! jq -e '.data' "$CURL_OUTPUT" >/dev/null 2>&1; then
        log_msg "Sin eventos para procesar"
        return 0
    fi
    
    while IFS='|' read -r date_time ip user action workstation; do
        [[ -z "$action" ]] && continue
        
        ((total_events++))
        debug_msg "Evento #${total_events}: ${action:0:50}..."
        
        if should_ignore "$action"; then
            ((ignored_events++))
            debug_msg "  → Ignorado"
            continue
        fi
        
        local alert_type
        alert_type=$(classify_alert "$action")
        
        if [[ -z "$alert_type" ]]; then
            ((unclassified++))
            debug_msg "  → No clasificado"
            continue
        fi
        
        local event_id
        event_id=$(echo "${date_time}|${user}|${action}" | md5sum | cut -d' ' -f1)
        
        if [[ -n "${PROCESSED_IDS[$event_id]:-}" ]]; then
            debug_msg "  → Ya procesado"
            continue
        fi
        
        local current_count="${COUNTERS[$alert_type]:-0}"
        ((current_count++))
        COUNTERS["$alert_type"]="$current_count"
        
        local origen="${user}"
        if [[ -n "$workstation" && "$workstation" != "null" && "$workstation" != "" ]]; then
            origen="${user} - ${workstation}"
        fi
        
        local alert_json
        alert_json=$(jq -n \
            --arg dt "$date_time" \
            --arg usr "$user" \
            --arg act "$action" \
            --arg ip "$ip" \
            --arg ws "$workstation" \
            --arg org "$origen" \
            '{timestamp:$dt,user:$usr,action:$act,ip:$ip,workstation:$ws,origen:$org}' | tr -d '\n')
        
        echo "${ZBX_HOST} ${alert_type}.data ${alert_json}" >> "$TMP_FILE"
        echo "${ZBX_HOST} ${alert_type}.count ${current_count}" >> "$TMP_FILE"
        
        PROCESSED_IDS["$event_id"]="$(date '+%Y%m%d%H%M%S')"
        
        ((new_alerts++))
        
        log_msg "  → ALERTA #${current_count}: [${alert_type}] ${origen}"
        log_msg "      ${action:0:70}"
        
    done < <(jq -r '.data[]? | "\(.date)|\(.ip)|\(.user)|\(.action)|\(.workstation)"' "$CURL_OUTPUT" 2>/dev/null)
    
    echo ""
    log_msg "Procesamiento:"
    log_msg "  Total: ${total_events} | Ignorados: ${ignored_events}"
    log_msg "  No clasificados: ${unclassified} | Nuevas alertas: ${new_alerts}"
}

send_to_zabbix() {
    if [[ -s "$TMP_FILE" ]]; then
        local item_count
        item_count=$(wc -l < "$TMP_FILE")
        
        log_msg "Enviando ${item_count} items a Zabbix..."
        [[ $DEBUG -eq 1 ]] && head -5 "$TMP_FILE" >> "$DEBUG_LOG"
        
        local sender_output
        sender_output=$(zabbix_sender -z "$ZBX_SERVER" -p "$ZBX_PORT" -i "$TMP_FILE" 2>&1)
        local sender_exit=$?
        
        debug_msg "Exit: ${sender_exit}"
        debug_msg "Output: ${sender_output}"
        
        if [ $sender_exit -eq 0 ]; then
            log_msg "  ✓ Enviado correctamente"
        else
            log_msg "  ✗ ERROR al enviar"
            log_msg "  ${sender_output}"
            return 1
        fi
    else
        log_msg "Sin alertas para enviar"
    fi
}

cleanup_old_state() {
    local cutoff_time
    cutoff_time=$(date -d '24 hours ago' '+%Y%m%d%H%M%S')
    
    local cleaned=0
    for key in "${!PROCESSED_IDS[@]}"; do
        local timestamp="${PROCESSED_IDS[$key]}"
        if [[ "$timestamp" < "$cutoff_time" ]]; then
            unset PROCESSED_IDS["$key"]
            ((cleaned++))
        fi
    done
    
    [ $cleaned -gt 0 ] && log_msg "  Limpiados $cleaned eventos antiguos"
}

# ========= MAIN =========

main() {
    [[ $DEBUG -eq 1 ]] && echo "=== DEBUG START $(date) ===" >> "$DEBUG_LOG"
    
    log_msg "=========================================="
    log_msg "AUDIT LOG MONITOR - ${WVX_OPERACION^^}"
    [[ $DEBUG -eq 1 ]] && log_msg "MODO DEBUG ACTIVADO"
    log_msg "=========================================="
    echo ""
    
    log_msg "Configuración:"
    log_msg "  Operación: ${WVX_OPERACION}"
    log_msg "  Server: wv${WOLKVOX_SERVER}"
    log_msg "  Host Zabbix: ${ZBX_HOST}"
    echo ""
    
    if ! fetch_audit_log; then
        exit 1
    fi
    
    echo ""
    load_state
    load_counters
    
    echo ""
    log_msg "Procesando eventos..."
    process_audit_events
    
    echo ""
    send_to_zabbix
    
    echo ""
    cleanup_old_state
    
    save_state
    save_counters
    
    rm -f "$TMP_FILE" "$CURL_OUTPUT"
    
    echo ""
    log_msg "Fin de ejecución"
    log_msg "=========================================="
    
    [[ $DEBUG -eq 1 ]] && echo "=== DEBUG END $(date) ===" >> "$DEBUG_LOG"
}

main "$@"
