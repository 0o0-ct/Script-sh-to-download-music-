#!/usr/bin/env bats
#
# Pruebas BATS de OtterRip v7.3 (Down-Linux-music_v7.sh).
#
# Ejecutar:
#   bats tests/test_otterrip.bats
#
# Las pruebas usan un HOME y directorios temporales propios: nunca tocan la
# configuración real del usuario.

setup() {
    SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../Down-Linux-music_v7.sh"
    # shellcheck source=/dev/null
    source "$SCRIPT_UNDER_TEST"

    # El script deja `set -Eeuo pipefail` y un trap de EXIT al sourcearse:
    # se restauran para que las pruebas no se cierren al terminar.
    set +e +u +E
    set +o pipefail 2>/dev/null
    trap - EXIT INT TERM HUP

    TEST_ROOT="$BATS_TEST_TMPDIR"
    export HOME="$TEST_ROOT/home"
    mkdir -p "$HOME"
    OTTERRIP_CONFIG_DIR="$TEST_ROOT/config"
    OTTERRIP_DATA_DIR="$TEST_ROOT/data"
    OTTERRIP_STATE_DIR="$TEST_ROOT/state"
    OTTERRIP_CACHE_DIR="$TEST_ROOT/cache"
    mkdir -p "$OTTERRIP_CONFIG_DIR" "$OTTERRIP_DATA_DIR" "$OTTERRIP_STATE_DIR" \
        "$OTTERRIP_CACHE_DIR" "$OTTERRIP_STATE_DIR/sessions"
    chmod 700 "$OTTERRIP_CONFIG_DIR" "$OTTERRIP_DATA_DIR" "$OTTERRIP_STATE_DIR" "$OTTERRIP_CACHE_DIR"
    CONFIG_FILE="$OTTERRIP_CONFIG_DIR/config"
    DEPS_CACHE_FILE="$OTTERRIP_CACHE_DIR/deps_verified.env"
    LOG_FILE="$OTTERRIP_STATE_DIR/otterrip.log"
    SESSIONS_DIR="$OTTERRIP_STATE_DIR/sessions"
    VENV_DIR="$OTTERRIP_DATA_DIR/venv"
    APP_ROOT="$OTTERRIP_DATA_DIR/app"
    APP_INSTALL_DIR="$APP_ROOT/v$OTTERRIP_VERSION"
    DOWNLOADS="$TEST_ROOT/downloads"
    mkdir -p "$DOWNLOADS"
    LAST_DOWNLOAD_FOLDER="Music"
    OTTERRIP_PROXY=""
    DETECTION_FALLBACK=false
}

# ── safe_name ───────────────────────────────────────────────────

@test "safe_name elimina separadores de ruta y caracteres peligrosos" {
    run safe_name 'a/b\\c:d*e?f"g<h>i|j'
    [ "$status" -eq 0 ]
    [ "$output" = "a_b_c_d_e_f_g_h_i_j" ]
}

@test "safe_name elimina caracteres de control" {
    run safe_name $'canción\tcon\tnuevas\nlíneas'
    [ "$status" -eq 0 ]
    [[ "$output" != *$'\t'* ]]
    [[ "$output" != *$'\n'* ]]
    [[ "$output" == "canción con nuevas líneas" ]]
}

@test "safe_name conserva Unicode y espacios" {
    run safe_name '  Añadir 🦦 Música  '
    [ "$status" -eq 0 ]
    [ "$output" = "Añadir 🦦 Música" ]
}

@test "safe_name elimina los puntos iniciales (path traversal)" {
    run safe_name '../../etc/passwd'
    [ "$status" -eq 0 ]
    [[ "$output" != *..* ]]
    [[ "$output" != */* ]]
}

@test "safe_name de una cadena vacía devuelve el marcador" {
    run safe_name ''
    [ "$status" -eq 0 ]
    [ "$output" = "_" ]
}

@test "safe_name recorta los nombres excessively largos" {
    long="$(printf 'a%.0s' {1..500})"
    run safe_name "$long"
    [ "$status" -eq 0 ]
    [ "${#output}" -le 200 ]
}

# ── resolve_destination ─────────────────────────────────────────

@test "resolve_destination resuelve una ruta relativa bajo DOWNLOADS" {
    run resolve_destination 'Musica/Álbum'
    [ "$status" -eq 0 ]
    [ "$output" = "$DOWNLOADS/Musica/Álbum" ]
}

@test "resolve_destination mantiene una ruta absoluta" {
    run resolve_destination '/tmp/musica/otterrip'
    [ "$status" -eq 0 ]
    [ "$output" = "/tmp/musica/otterrip" ]
}

@test "resolve_destination rechaza la traversed de directorios" {
    run resolve_destination '../fuera'
    [ "$status" -eq 1 ]
    [ -z "$output" ]
}

@test "resolve_destination ignora los componentes Current" {
    run resolve_destination './Musica'
    [ "$status" -eq 0 ]
    [ "$output" = "$DOWNLOADS/Musica" ]
}

@test "resolve_destination con entrada vacía usa Music" {
    run resolve_destination ''
    [ "$status" -eq 0 ]
    [ "$output" = "$DOWNLOADS/Music" ]
}

@test "destination_display devuelve una ruta válida" {
    run destination_display
    [ "$status" -eq 0 ]
    [[ "$output" == "$DOWNLOADS"* ]]
}

# ── normalize_jobs / is_positive_int ────────────────────────────

@test "normalize_jobs limita la entrada a 32 workers" {
    run normalize_jobs '33'
    [ "$status" -eq 0 ]
    [ "$output" = "32" ]
}

@test "normalize_jobs acepta el valor máximo" {
    run normalize_jobs '32'
    [ "$status" -eq 0 ]
    [ "$output" = "32" ]
}

@test "normalize_jobs conserva el valor mínimo" {
    run normalize_jobs '1'
    [ "$status" -eq 0 ]
    [ "$output" = "1" ]
}

@test "normalize_jobs sustituye el cero por el valor por defecto" {
    run normalize_jobs '0'
    [ "$status" -eq 0 ]
    [ "$output" = "3" ]
}

@test "normalize_jobs rechaza un valor no numérico" {
    run normalize_jobs 'abc'
    [ "$status" -eq 0 ]
    [ "$output" = "3" ]
}

@test "normalize_jobs normaliza los ceros iniciales" {
    run normalize_jobs '007'
    [ "$status" -eq 0 ]
    [ "$output" = "7" ]
}

@test "normalize_jobs recorta los valores de más de dos dígitos" {
    run normalize_jobs '100'
    [ "$status" -eq 0 ]
    [ "$output" = "32" ]
}

@test "is_positive_int acepta enteros positivos con ceros iniciales" {
    run is_positive_int '007'
    [ "$status" -eq 0 ]
}

@test "is_positive_int rechaza el cero" {
    run is_positive_int '0'
    [ "$status" -eq 1 ]
}

@test "is_positive_int rechaza los valores no numéricos" {
    run is_positive_int 'abc'
    [ "$status" -eq 1 ]
}

@test "is_positive_int rechaza los negativos" {
    run is_positive_int '-3'
    [ "$status" -eq 1 ]
}

@test "is_positive_int rechaza la cadena vacía" {
    run is_positive_int ''
    [ "$status" -eq 1 ]
}

@test "is_nonneg_int acepta el cero pero no los negativos" {
    run is_nonneg_int '0'
    [ "$status" -eq 0 ]
    run is_nonneg_int '-1'
    [ "$status" -eq 1 ]
}

# ── apply_quality_choice ────────────────────────────────────────

@test "apply_quality_choice aplica el perfil MP3" {
    run apply_quality_choice '1'
    [ "$status" -eq 0 ]
    [ "$AFMT" = "mp3" ]
    [ "$AQ" = "320K" ]
}

@test "apply_quality_choice aplica el perfil M4A" {
    run apply_quality_choice '3'
    [ "$status" -eq 0 ]
    [ "$AFMT" = "m4a" ]
    [ "$AQ" = "320K" ]
}

@test "apply_quality_choice aplica el perfil FLAC con postprocesado" {
    run apply_quality_choice '5'
    [ "$status" -eq 0 ]
    [ "$AFMT" = "flac" ]
    [[ "$PP_VALUE" == *"sample_fmt s32"* ]]
}

@test "apply_quality_choice rechaza un valor fuera de rango" {
    run apply_quality_choice '6'
    [ "$status" -eq 1 ]
}

@test "apply_quality_choice rechaza un valor no numérico" {
    run apply_quality_choice 'abc'
    [ "$status" -eq 1 ]
}

# ── Configuración persistente ───────────────────────────────────

@test "save_config y load_config conservan los valores" {
    QUALITY_CHOICE=2
    DUR_MIN=30
    DUR_MAX=300
    ALBUM_LAYOUT="split"
    LYRICS_MODE="file"
    LYRICS_LANG="en"
    save_config
    [ -f "$CONFIG_FILE" ]

    QUALITY_CHOICE=1
    DUR_MIN=60
    DUR_MAX=480
    ALBUM_LAYOUT="single"
    LYRICS_MODE="embed"
    LYRICS_LANG=""
    load_config

    [ "$QUALITY_CHOICE" = "2" ]
    [ "$AFMT" = "m4a" ]
    [ "$DUR_MIN" = "30" ]
    [ "$DUR_MAX" = "300" ]
    [ "$ALBUM_LAYOUT" = "split" ]
    [ "$LYRICS_MODE" = "file" ]
    [ "$LYRICS_LANG" = "en" ]
}

@test "load_config ignora los valores inválidos" {
    printf 'DUR_MIN=abc\nALBUM_LAYOUT=invalido\nFETCH_LYRICS=quizá\n' > "$CONFIG_FILE"
    DUR_MIN=60
    ALBUM_LAYOUT="single"
    FETCH_LYRICS="true"
    load_config
    [ "$DUR_MIN" = "60" ]
    [ "$ALBUM_LAYOUT" = "single" ]
    [ "$FETCH_LYRICS" = "true" ]
}

@test "load_config ignora las claves desconocidas" {
    printf 'CLAVE_FICTICIA=valor\nDUR_MIN=45\n' > "$CONFIG_FILE"
    DUR_MIN=60
    load_config
    [ "$DUR_MIN" = "45" ]
}

@test "load_config aplica el proxy válido y descarta el inválido" {
    printf 'OTTERRIP_PROXY=socks5://127.0.0.1:1080\n' > "$CONFIG_FILE"
    load_config
    [ "$OTTERRIP_PROXY" = "socks5://127.0.0.1:1080" ]

    printf 'OTTERRIP_PROXY=no-es-un-proxy\n' > "$CONFIG_FILE"
    OTTERRIP_PROXY=""
    load_config
    [ -z "$OTTERRIP_PROXY" ]
}

@test "save_config escribe un archivo legible con las claves del esquema" {
    save_config
    run grep -c '^QUALITY_CHOICE=' "$CONFIG_FILE"
    [ "$status" -eq 0 ]
    [ "$output" -ge 1 ]
    run grep -c '^OTTERRIP_PROXY=' "$CONFIG_FILE"
    [ "$status" -eq 0 ]
}

# ── Flags de yt-dlp ─────────────────────────────────────────────

@test "build_common_flags incluye las opciones esperadas" {
    build_common_flags '/tmp/archivo'
    [ "${COMMON_FLAGS[0]}" = "--ignore-errors" ]
    [[ " ${COMMON_FLAGS[*]} " == *" --audio-format "* ]]
    [[ " ${COMMON_FLAGS[*]} " == *" --audio-quality "* ]]
    [[ " ${COMMON_FLAGS[*]} " == *" --embed-metadata "* ]]
    [[ " ${COMMON_FLAGS[*]} " == *" --download-archive /tmp/archivo "* ]]
    [[ " ${COMMON_FLAGS[*]} " == *" --match-filter "* ]]
}

@test "build_common_flags incluye el proxy configurado" {
    OTTERRIP_PROXY="http://127.0.0.1:3128"
    build_common_flags '/tmp/archivo'
    [[ " ${COMMON_FLAGS[*]} " == *" --proxy http://127.0.0.1:3128 "* ]]
}

@test "build_common_flags omite el proxy si no está configurado" {
    OTTERRIP_PROXY=""
    build_common_flags '/tmp/archivo'
    [[ " ${COMMON_FLAGS[*]} " != *" --proxy "* ]]
}

@test "build_common_flags incluye las cookies del navegador" {
    COOKIES_BROWSER="firefox"
    build_common_flags '/tmp/archivo'
    [[ " ${COMMON_FLAGS[*]} " == *" --cookies-from-browser firefox "* ]]
}

# ── Seguridad ───────────────────────────────────────────────────

@test "is_safe_url rechaza una URL que empieza por guion" {
    run is_safe_url '--exec=malicioso'
    [ "$status" -eq 1 ]
}

@test "is_safe_url rechaza esquemas no http" {
    run is_safe_url 'file:///etc/passwd'
    [ "$status" -eq 1 ]
}

@test "is_safe_url acepta una URL https normal" {
    run is_safe_url 'https://www.youtube.com/watch?v=abc'
    [ "$status" -eq 0 ]
}

@test "normalize_proxy acepta http, https y socks5" {
    run normalize_proxy 'http://127.0.0.1:3128'
    [ "$status" -eq 0 ]
    run normalize_proxy 'socks5://127.0.0.1:1080'
    [ "$status" -eq 0 ]
    run normalize_proxy 'https://proxy.example.com:8443'
    [ "$status" -eq 0 ]
}

@test "normalize_proxy rechaza un valor no válido" {
    run normalize_proxy 'no-es-un-proxy'
    [ "$status" -eq 1 ]
    run normalize_proxy 'ftp://servidor/archivo'
    [ "$status" -eq 1 ]
}

@test "validate_session_template acepta la plantilla por defecto" {
    run validate_session_template "$SESSION_PRINT_TEMPLATE_DEFAULT"
    [ "$status" -eq 0 ]
}

@test "validate_session_template rechaza los caracteres de control" {
    run validate_session_template "$SESSION_PRINT_TEMPLATE_DEFAULT
inyeccion"
    [ "$status" -eq 1 ]
}

@test "validate_session_template rechaza una plantilla vacía" {
    run validate_session_template ''
    [ "$status" -eq 1 ]
}

@test "validate_session_template rechaza una plantilla sin los tokens esperados" {
    run validate_session_template 'after_move:{}'
    [ "$status" -eq 1 ]
}

@test "validate_binary_path rechaza una ruta relativa" {
    run validate_binary_path 'python3'
    [ "$status" -eq 1 ]
}

@test "validate_binary_path acepta un binario del sistema" {
    run validate_binary_path '/usr/bin/python3'
    [ "$status" -eq 0 ]
}

@test "validate_binary_path rechaza un fichero inexistente" {
    run validate_binary_path '/usr/bin/este-no-existe'
    [ "$status" -eq 1 ]
}

@test "canonicalize_path resuelve los elementos Current y Parent" {
    run canonicalize_path '/a/b/../c/./d'
    [ "$status" -eq 0 ]
    [ "$output" = "/a/c/d" ]
}

@test "is_private_dir rechaza los directorios escribibles por otros" {
    mkdir -p "$TEST_ROOT/permisos"
    chmod 777 "$TEST_ROOT/permisos"
    run is_private_dir "$TEST_ROOT/permisos"
    [ "$status" -eq 1 ]
    chmod 700 "$TEST_ROOT/permisos"
    run is_private_dir "$TEST_ROOT/permisos"
    [ "$status" -eq 0 ]
}

@test "secure_mktemp crea un archivo regular en un directorio privado" {
    run secure_mktemp "$OTTERRIP_CACHE_DIR" "prueba"
    [ "$status" -eq 0 ]
    [ -f "$output" ]
    [ ! -L "$output" ]
    rm -f "$output"
}

@test "secure_mktemp falla en un directorio escribible por otros" {
    mkdir -p "$TEST_ROOT/publico"
    chmod 777 "$TEST_ROOT/publico"
    run secure_mktemp "$TEST_ROOT/publico" "prueba"
    [ "$status" -eq 1 ]
}

# ── Caché de dependencias (sin source) ──────────────────────────

@test "load_deps_cache lee las claves permitidas" {
    printf 'CACHED_OTTERRIP_VERSION=7.2.0\nCACHED_LIB_DIR=/tmp/lib\n' > "$DEPS_CACHE_FILE"
    run load_deps_cache
    [ "$status" -eq 0 ]
    [ "$CACHED_OTTERRIP_VERSION" = "7.2.0" ]
    [ "$CACHED_LIB_DIR" = "/tmp/lib" ]
}

@test "load_deps_cache rechaza una clave no permitida" {
    printf 'CACHED_OTTERRIP_VERSION=7.2.0\nMALICIOSO=valor\n' > "$DEPS_CACHE_FILE"
    run load_deps_cache
    [ "$status" -eq 1 ]
}

@test "load_deps_cache no ejecuta el contenido del archivo" {
    marca="$TEST_ROOT/ejecutado"
    rm -f "$marca"
    printf 'CACHED_LIB_DIR=$(touch %s)\n' "$marca" > "$DEPS_CACHE_FILE"
    run load_deps_cache
    [ ! -f "$marca" ]
    [ "$CACHED_LIB_DIR" = "\$(touch $marca)" ]
}

@test "load_deps_cache rechaza una línea sin signo igual" {
    printf 'CACHED_OTTERRIP_VERSION 7.2.0\n' > "$DEPS_CACHE_FILE"
    run load_deps_cache
    [ "$status" -eq 1 ]
}

@test "write_deps_cache escribe un archivo con las claves esperadas" {
    write_deps_cache
    [ -f "$DEPS_CACHE_FILE" ]
    run grep -c '^CACHED_OTTERRIP_VERSION=' "$DEPS_CACHE_FILE"
    [ "$status" -eq 0 ]
    run grep -c '^CACHED_PYBIN=' "$DEPS_CACHE_FILE"
    [ "$status" -eq 0 ]
}

@test "write_deps_cache y load_deps_cache funcionan en conjunto" {
    PYBIN="/usr/bin/python3"
    YTDLP_BIN="/usr/bin/yt-dlp"
    write_deps_cache
    unset CACHED_LIB_DIR
    run load_deps_cache
    [ "$status" -eq 0 ]
    [ "$CACHED_PYBIN" = "/usr/bin/python3" ]
}

# ── Progreso, conteo y utilidades ──────────────────────────────

@test "draw_download_progress con total cero no falla" {
    run draw_download_progress 0 0 0 0 0 1
    [ "$status" -eq 0 ]
}

@test "draw_download_progress con valores no numéricos no falla" {
    run draw_download_progress 'abc' 'xyz' '' '-' 'x' 'y'
    [ "$status" -eq 0 ]
}

@test "count_playlist_entries devuelve cero para una URL insegura" {
    run count_playlist_entries '--exec=malicioso'
    [ "$status" -eq 0 ]
    [ "$output" = "0" ]
}

@test "count_playlist_entries devuelve cero para una URL vacía" {
    run count_playlist_entries ''
    [ "$status" -eq 0 ]
    [ "$output" = "0" ]
}

@test "version_gt compara versiones por partes" {
    run version_gt '7.3.0' '7.2.0'
    [ "$status" -eq 0 ]
    run version_gt '7.2.0' '7.2.0'
    [ "$status" -eq 1 ]
    run version_gt '7.2.0' '7.10.0'
    [ "$status" -eq 1 ]
}

@test "run_batch falla si el archivo no existe" {
    run run_batch "$TEST_ROOT/no-existe.txt"
    [ "$status" -eq 2 ]
}

@test "append_history escribe una línea TSV" {
    append_history 'https://ejemplo/video' 'Título' '/destino' 'ok' '42'
    [ -f "$OTTERRIP_DATA_DIR/history.tsv" ]
    run cat "$OTTERRIP_DATA_DIR/history.tsv"
    [ "$status" -eq 0 ]
    [[ "$output" == *"https://ejemplo/video"* ]]
    [[ "$output" == *"42"* ]]
}

@test "show_history no falla cuando el historial está vacío" {
    run show_history 10
    [ "$status" -eq 0 ]
}

@test "logs_total_size suma el tamaño de varios archivos" {
    printf 'aaaa' > "$TEST_ROOT/log1"
    printf 'bbbbbb' > "$TEST_ROOT/log2"
    run logs_total_size "$TEST_ROOT/log1" "$TEST_ROOT/log2"
    [ "$status" -eq 0 ]
    [ "$output" = "10" ]
}

@test "url_cache_key genera una clave estable" {
    run url_cache_key 'https://ejemplo/video'
    [ "$status" -eq 0 ]
    [ -n "$output" ]
    first="$output"
    run url_cache_key 'https://ejemplo/video'
    [ "$output" = "$first" ]
}

@test "normalize_host acepta y rechaza URLs inválidas" {
    run normalize_host 'https://lrclib.net'
    [ "$status" -eq 0 ]
    run normalize_host 'no-es-una-url'
    [ "$status" -eq 1 ]
}

@test "is_http_url distingue URLs de texto arbitrario" {
    run is_http_url 'https://ejemplo.com'
    [ "$status" -eq 0 ]
    run is_http_url 'ejemplo.com'
    [ "$status" -eq 1 ]
}

# ── Manifiesto de sesión (formato que espera el backend) ───────

@test "session_to_ids extrae el ID del JSON de sesión" {
    printf '{"filepath":"/tmp/x/Canción [abc123].m4a","title":"Canción","id":"abc123","duration":12}\n' \
        > "$TEST_ROOT/sesion.json"
    run session_to_ids "$TEST_ROOT/sesion.json" "$TEST_ROOT/sesion.ids"
    [ "$status" -eq 0 ]
    [ "$(cat "$TEST_ROOT/sesion.ids")" = "abc123" ]
}

@test "session_to_ids acepta un archivo con IDs sueltos" {
    printf 'abc123\ndef456\n' > "$TEST_ROOT/sesion_plana.txt"
    run session_to_ids "$TEST_ROOT/sesion_plana.txt" "$TEST_ROOT/sesion_plana.ids"
    [ "$status" -eq 0 ]
    [ "$(cat "$TEST_ROOT/sesion_plana.ids")" = "$(printf 'abc123\ndef456')" ]
}

@test "session_to_ids deduplica los IDs repetidos" {
    printf '{"id":"abc123"}\n{"id":"abc123"}\n{"id":"def456"}\n' > "$TEST_ROOT/sesion_dup.json"
    run session_to_ids "$TEST_ROOT/sesion_dup.json" "$TEST_ROOT/sesion_dup.ids"
    [ "$status" -eq 0 ]
    [ "$(wc -l < "$TEST_ROOT/sesion_dup.ids")" -eq 2 ]
}

@test "session_to_ids conserva las líneas que no son JSON" {
    printf 'abc123\nno-es-json\n' > "$TEST_ROOT/sesion_mix.txt"
    run session_to_ids "$TEST_ROOT/sesion_mix.txt" "$TEST_ROOT/sesion_mix.ids"
    [ "$status" -eq 0 ]
    [ "$(wc -l < "$TEST_ROOT/sesion_mix.ids")" -eq 2 ]
}

@test "session_to_ids falla si el archivo de origen no existe" {
    run session_to_ids "$TEST_ROOT/no-existe.json" "$TEST_ROOT/salida.ids"
    [ "$status" -eq 1 ]
}

@test "el backend verifica los archivos con el manifiesto normalizado" {
    # Regresión: con el JSON rico el backend no encontraba ningún archivo y
    # se descartaban todas las pistas (por eso no se incrustaban las letras).
    mkdir -p "$TEST_ROOT/dest"
    ffmpeg -v quiet -f lavfi -i "sine=frequency=440:duration=3" -c:a aac \
        -metadata title="Pista de prueba" -metadata artist="Artista de prueba" \
        "$TEST_ROOT/dest/Pista [zz999].m4a" 2>/dev/null || skip "ffmpeg no disponible"
    printf '{"filepath":"%s","title":"Pista de prueba","id":"zz999","duration":3}\n' \
        "$TEST_ROOT/dest/Pista [zz999].m4a" > "$TEST_ROOT/sesion_regresion.json"
    session_to_ids "$TEST_ROOT/sesion_regresion.json" "$TEST_ROOT/sesion_regresion.ids"
    PYTHONPATH="$BATS_TEST_DIRNAME/../lib" python3 -m otterrip_core verify-session \
        --session "$TEST_ROOT/sesion_regresion.ids" --ext m4a --dest "$TEST_ROOT/dest" \
        --archive /dev/null --out-ok "$TEST_ROOT/ok.txt" --out-failed "$TEST_ROOT/fail.txt" \
        >/dev/null 2>&1 || true
    [ -s "$TEST_ROOT/ok.txt" ]
    [ ! -s "$TEST_ROOT/fail.txt" ]
}

# ── Cascada de proveedores de letras ───────────────────────────

@test "los proveedores por defecto son lrclib y multiweb" {
    [ "$LYRICS_PROVIDERS" = "lrclib,multiweb" ]
}

@test "merge_lyrics_summary combina los dos resúmenes" {
    printf 'found=2\nnotfound=3\nalready=1\n' > "$TEST_ROOT/res1.txt"
    printf 'found=4\nnotfound=1\nalready=3\n' > "$TEST_ROOT/res2.txt"
    merge_lyrics_summary "$TEST_ROOT/res1.txt" "$TEST_ROOT/res2.txt"
    [ "$(summary_value "$TEST_ROOT/res1.txt" found 0)" = "6" ]
    [ "$(summary_value "$TEST_ROOT/res1.txt" notfound 0)" = "1" ]
    [ "$(summary_value "$TEST_ROOT/res1.txt" already 0)" = "4" ]
}

@test "merge_lyrics_summary no rompe si falta el segundo resumen" {
    printf 'found=2\nnotfound=3\nalready=1\n' > "$TEST_ROOT/res3.txt"
    merge_lyrics_summary "$TEST_ROOT/res3.txt" "$TEST_ROOT/no-existe.txt"
    [ "$(summary_value "$TEST_ROOT/res3.txt" found 0)" = "2" ]
    [ "$(summary_value "$TEST_ROOT/res3.txt" notfound 0)" = "3" ]
}

@test "process_lyrics no hace nada si las letras están desactivadas" {
    FETCH_LYRICS="false"
    run process_lyrics "$TEST_ROOT" "m4a" "" "$TEST_ROOT/sum-off.txt"
    [ "$status" -eq 0 ]
    [ "$(summary_value "$TEST_ROOT/sum-off.txt" found 0)" = "0" ]
}

# ── Detección de URL (fallback sin red) ─────────────────────────

@test "detect_url_type con URL de álbum de YouTube Music (fallback)" {
    # Sin backend real, la detección cae en el fallback por patrón URL.
    detect_url_type "https://music.youtube.com/album/OLAK5uy_abc"
    [ "$DETECTED_TYPE" = "album" ]
    [ "$DETECTION_FALLBACK" = "true" ]
}

@test "detect_url_type con URL de playlist de YouTube (fallback)" {
    detect_url_type "https://www.youtube.com/playlist?list=PLxxx"
    [ "$DETECTED_TYPE" = "playlist" ]
    [ "$DETECTION_FALLBACK" = "true" ]
}

@test "detect_url_type con URL de canción individual (fallback)" {
    detect_url_type "https://www.youtube.com/watch?v=abc123"
    [ "$DETECTED_TYPE" = "song" ]
    [ "$DETECTED_COUNT" = "1" ]
}

@test "detect_url_type con URL de YouTube Music watch (fallback)" {
    detect_url_type "https://music.youtube.com/watch?v=abc123"
    [ "$DETECTED_TYPE" = "song" ]
}

@test "detect_url_type con URL insegura devuelve fallback" {
    detect_url_type "--exec=malicioso"
    [ "$DETECTED_TYPE" = "song" ]
    [ "$DETECTION_FALLBACK" = "true" ]
}

# ── Validación de versión de yt-dlp ─────────────────────────────

@test "YTDLP_MIN_VERSION está definido" {
    [ -n "$YTDLP_MIN_VERSION" ]
    [[ "$YTDLP_MIN_VERSION" =~ ^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$ ]]
}

@test "version_gt detecta versión de yt-dlp antigua" {
    # 2024.02.01 > 2023.10.01 → versión antigua
    run version_gt "$YTDLP_MIN_VERSION" "2023.10.01"
    [ "$status" -eq 0 ]
}

@test "version_gt acepta versión de yt-dlp reciente" {
    # 2024.02.01 no es > 2025.01.01 → versión OK
    run version_gt "$YTDLP_MIN_VERSION" "2025.01.01"
    [ "$status" -eq 1 ]
}

@test "version_gt con versión igual devuelve 1" {
    run version_gt "$YTDLP_MIN_VERSION" "$YTDLP_MIN_VERSION"
    [ "$status" -eq 1 ]
}

# ── Parseo de opciones rápidas ──────────────────────────────────

@test "apply_quality_choice con perfil 1 (MP3)" {
    run apply_quality_choice '1'
    [ "$status" -eq 0 ]
    [ "$AFMT" = "mp3" ]
}

@test "apply_quality_choice con perfil 2 (M4A)" {
    run apply_quality_choice '2'
    [ "$status" -eq 0 ]
    [ "$AFMT" = "m4a" ]
}

@test "normalize_proxy con proxy socks5" {
    run normalize_proxy 'socks5h://user:pass@proxy.example.com:1080'
    [ "$status" -eq 0 ]
}

@test "is_http_url acepta http y https" {
    run is_http_url 'http://ejemplo.com/video'
    [ "$status" -eq 0 ]
    run is_http_url 'https://ejemplo.com/video'
    [ "$status" -eq 0 ]
}

@test "is_http_url rechaza texto sin esquema" {
    run is_http_url 'solo texto'
    [ "$status" -eq 1 ]
}
