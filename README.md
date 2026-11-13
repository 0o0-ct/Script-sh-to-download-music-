# OtterRip

> [!IMPORTANT]
> Este es el archivo principal del proyecto y el que debes usar para arrancar OtterRip:
> 
> [Down-Linux-music_v7.sh](https://github.com/0o0-ct/Script-sh-to-download-music-/blob/main/Down-Linux-music_v7.sh)

OtterRip es un descargador de audio para Linux con letras, cola de descargas,
recuperación de sesiones y herramientas de biblioteca.

## Inicio

### ⚠️ Importante: Cómo descargar el programa (paso a paso)

**Si es la primera vez que usas OtterRip**, clona el repositorio completo para
tener todos los archivos necesarios (script, backend, tests, configuración):

```bash
git clone https://github.com/0o0-ct/Script-sh-to-download-music-.git
cd Script-sh-to-download-music-
chmod +x Down-Linux-music_v7.sh
```

**Si ya tienes una copia anterior**, actualiza siempre antes de ejecutar:

```bash
cd Script-sh-to-download-music-
git pull origin main
```

> [!WARNING]
> **No descargues solo el archivo `Down-Linux-music_v7.sh`** desde GitHub
> (por ejemplo, con `wget` o descargando el archivo individual). El script
> necesita el directorio `lib/` junto a él para funcionar correctamente.
> Si descargas solo el `.sh`, el backend se obtendrá desde Internet, pero
> cualquier modificación local o trabajo offline no funcionará.
>
> Si en tu otra PC ves una versión antigua (ej. "v7.2" en el banner),
> significa que necesitas ejecutar `git pull origin main` para actualizar.

### Ejecución

Una vez que tienes el repositorio, ejecuta con Bash 4.4 o superior:

```bash
./Down-Linux-music_v7.sh
```

Cuando el script no encuentre un backend compatible junto a sí mismo, descargará
el paquete de su versión desde la etiqueta correspondiente de este repositorio.
Verificará el SHA-256 antes de extraerlo y lo instalará en
`$XDG_DATA_HOME/otterrip/app/v7.3.0` (o `~/.local/share/otterrip/app/v7.3.0`).
El backend y el entorno virtual son por usuario; no se modifican la biblioteca
musical, la configuración, las sesiones ni los demás datos personales. Si ya hay
un backend compatible instalado, el inicio no necesita conexión a Internet.

El entorno virtual se crea en `$XDG_DATA_HOME/otterrip/venv`. OtterRip no instala
paquetes Python en el entorno global del sistema. Algunas dependencias externas,
como `ffmpeg`, pueden requerir el gestor de paquetes de Linux.

## Backend y versiones

El archivo de distribución `packages/otterrip-backend-v7.3.0.tar.gz` contiene
`lib/` y `requirements.txt`. El script solo acepta el SHA-256 incluido para esa
versión y obtiene el paquete desde la etiqueta inmutable `v7.3.0`.

La búsqueda de letras usa LRCLIB y, como respaldo, Musixmatch, NetEase,
Megalobiz y Genius mediante `syncedlyrics`. Las fuentes Multi-Web buscan letras
sincronizadas o planas según la preferencia seleccionada; LRCLIB también puede
devolver ambos formatos. La configuración predeterminada busca e
incrusta letras al terminar las descargas, con concurrencia limitada por
`PARALLEL_JOBS` (1-32). KuGou, BiniLyrics, PaxSenix, Unison y YouTube
Music/Captions no se anuncian como compatibles porque no están expuestos por
la versión integrada de `syncedlyrics`.

Para publicar una versión nueva:

1. Actualiza `OTTERRIP_VERSION` en el script y `lib/otterrip_core/VERSION`.
2. Construye el paquete desde la raíz del repositorio:

   ```bash
   mkdir -p packages
   tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
     --exclude='__pycache__' -cf - lib requirements.txt \
     | gzip -n > "packages/otterrip-backend-v${VERSION}.tar.gz"
   ```

3. Calcula su digest con `sha256sum` y actualiza `BACKEND_SHA256` en el script.
4. Verifica las pruebas, confirma el paquete y el script, y crea la etiqueta
   `v${VERSION}` sobre ese commit. La etiqueta debe existir en GitHub para que
   un usuario con solo el script pueda obtener el backend.

No apuntes el bootstrapper a `main`: la URL del backend y el digest se fijan por
versión.

## Línea de comandos

```bash
otterrip                                  # menú interactivo
otterrip <URL> [carpeta]                  # descarga directa (código de salida)
otterrip -q 3 -j 8 --proxy socks5://127.0.0.1:1080 <URL>
otterrip --batch lista.txt                # una URL por línea ("#" comenta)
otterrip --dry-run <URL>                  # muestra el plan, no descarga nada
otterrip --verbose <URL>                  # muestra los comandos de yt-dlp
otterrip --debug <URL>                    # traza y conserva los temporales
otterrip --history                        # historial de descargas
otterrip --add <URL>...                   # encola sin abrir el menú
otterrip --queue-run                      # procesa la cola
otterrip --check-deps                     # verificación completa de dependencias
```

Variables de entorno: `OTTERRIP_DOWNLOADS`, `PARALLEL_JOBS`, `OTTERRIP_PROXY`,
`OTTERRIP_NOTIFY`, `OTTERRIP_CHECK_UPDATES`, `OTTERRIP_ASSUME_YES`,
`OTTERRIP_YTDLP`, `OTTERRIP_COOKIES_BROWSER` y `OTTERRIP_COOKIES_FILE`.

## Configuración de letras

Las siguientes variables se guardan en `$XDG_CONFIG_HOME/otterrip/config` y se
pueden ajustar desde el menú "Configurar LETRAS" (opción 7) o el
"Gestor avanzado de letras" (opción 13):

| Variable | Valores | Por defecto | Descripción |
|---|---|---|---|
| `LYRICS_SYNC_LEVEL` | `line`, `word`, `auto` | `line` | Nivel de sincronización |
| `LYRICS_SIDECAR` | `true`, `false` | `true` | Generar `.lrc` junto al audio |
| `LYRICS_EMBED_STYLE` | `lrc`, `plain` | `lrc` | Formato incrustado en tags |
| `LYRICS_MIN_CONFIDENCE` | 0–1000 (0–100 %) | 0 | Umbral mínimo de confianza |
| `LYRICS_OFFSET_MS` | entero (+/- ms) | 0 | Desfase global de timestamps |

### Formatos generados

- **`.lrc`**: archivo sidecar con timestamps por línea `[mm:ss.xx] texto`.
  Siempre compatible con reproductores estándar.
- **`.elrc`**: Enhanced LRC con tiempos por palabra
  `<mm:ss.xx>palabra</mm:ss.xx>`. Solo se genera cuando
  `LYRICS_SYNC_LEVEL=word` y `LYRICS_SIDECAR=true`.
- **Tags MP3**: `TXXX:LYRICS` (LRC sincronizado para PowerAmp), `SYLT`
  (timestamps binarios), `USLT` (texto plano).
- **Tags FLAC**: `SYNCEDLYRICS` (LRC sincronizado), `LYRICS` (texto plano).
- **Tags M4A**: `----:com.apple.iTunes:LYRICS` + `©lyr`.
- El incrustado usa siempre formato por línea; para palabra a palabra
  en tags se requiere `LYRICS_EMBED_STYLE` explícito (por verificar).

### Tabla de reproductores

| Reproductor | `.lrc` sidecar | Tags MP3 (sinc.) | Tags FLAC | Tags M4A | `.elrc` |
|---|---|---|---|---|---|
| **PowerAmp** | ✔ | ✔ (TXXX:LYRICS) | — | — | — |
| **Musicolet** | ✔ | ✔ (USLT LRC) | — | — | — |
| **Samsung Music** | ✔ | ❌ (plana) | — | — | — |
| **BlackPlayer** | ✔ | ✔ (USLT LRC) | — | — | — |
| **Retro Music** | ✔ | ✔ (USLT LRC) | — | — | — |
| **Oto Music** | ✔ | ❓ | — | — | — |
| **FiiO Music** | ✔ | ❓ | — | — | — |
| VLC | ✔ | ✔ | ✔ | ✔ | Por verificar |
| MusicBee | ✔ | ✔ | ✔ | Por verificar | Por verificar |
| Foobar2000 | ✔ | ✔ | ✔ | Por verificar | Por verificar |
| Audacious | ✔ | Por verificar | Por verificar | N/A | Por verificar |
| Apple Music | N/A | N/A | N/A | ✔ | Por verificar |
| mpv | ✔ | ✔ | ✔ | ✔ | Por verificar |

> [!TIP]
> **El sidecar `.lrc` es el formato más universal.** Todos los reproductores
> listados leen archivos `.lrc` externos. OtterRip genera sidecars por defecto
> (`LYRICS_SIDECAR=true`), así que las letras sincronizadas deberían funcionar
> en la mayoría de reproductores sin configuración adicional.
>
> Para **PowerAmp**, las letras sincronizadas embebidas usan el tag `TXXX:LYRICS`
> con formato LRC `[mm:ss.xx]`. Verifica en PowerAmp:
> `Ajustes` → `Apariencia` → `Letras` → `Escanear archivos LRC` → **Habilitado**.

*Las marcas "❓" indican compatibilidad teórica que no se ha
comprobado empíricamente. Las marcas "❌" indican que el reproductor no
interpreta ese formato como letra sincronizada.*

El historial de descargas se guarda en
`$XDG_DATA_HOME/otterrip/history.tsv` (`timestamp`, `url`, `title`, `dest`,
`status`, `duration_secs`).

## Pruebas

```bash
python3 -m pytest tests/ -q
bats tests/test_otterrip.bats
bash -n Down-Linux-music_v7.sh
shellcheck -x -s bash Down-Linux-music_v7.sh
```

Con `pyproject.toml` incluido, no es necesario configurar `PYTHONPATH`
manualmente para ejecutar `pytest`.

## Seguridad

- La caché de dependencias se **lee**, nunca se ejecuta: solo se aceptan líneas
  `CLAVE=valor` con una lista blanca de claves.
- Las URL se pasan a yt-dlp tras `--` y se rechaza cualquier URL que empiece
  por `-`, de modo que no pueden inyectarse opciones.
- Los binarios (`python`, `yt-dlp`) se validan antes de ejecutarse: ruta
  absoluta, ejecutable y fuera de directorios escribibles por cualquiera.
- El paquete del backend se rechaza si contiene enlaces simbólicos o duros,
  más de 500 miembros o más de 50 MB descomprimidos.
- Los temporales se crean con `mktemp` en directorios privados y se verifican
  para descartar ataques por symlink.

Los cambios detallados están en [CHANGELOG.md](CHANGELOG.md).
