# OtterRip

> [!IMPORTANT]
> Este es el archivo principal del proyecto y el que debes usar para arrancar OtterRip:
> 
> [Down-Linux-music_v7.sh](https://github.com/0o0-ct/Script-sh-to-download-music-/blob/main/Down-Linux-music_v7.sh)

OtterRip es un descargador de audio para Linux con letras, cola de descargas,
recuperación de sesiones y herramientas de biblioteca.

## Inicio

Descarga `Down-Linux-music_v7.sh` y ejecútalo con Bash 4.4 o superior:

```bash
bash Down-Linux-music_v7.sh
```

Cuando el script no encuentre un backend compatible junto a sí mismo, descargará
el paquete de su versión desde la etiqueta correspondiente de este repositorio.
Verificará el SHA-256 antes de extraerlo y lo instalará en
`$XDG_DATA_HOME/otterrip/app/v7.2.0` (o `~/.local/share/otterrip/app/v7.2.0`).
El backend y el entorno virtual son por usuario; no se modifican la biblioteca
musical, la configuración, las sesiones ni los demás datos personales. Si ya hay
un backend compatible instalado, el inicio no necesita conexión a Internet.

El entorno virtual se crea en `$XDG_DATA_HOME/otterrip/venv`. OtterRip no instala
paquetes Python en el entorno global del sistema. Algunas dependencias externas,
como `ffmpeg`, pueden requerir el gestor de paquetes de Linux.

## Backend y versiones

El archivo de distribución `packages/otterrip-backend-v7.2.0.tar.gz` contiene
`lib/` y `requirements.txt`. El script solo acepta el SHA-256 incluido para esa
versión y obtiene el paquete desde la etiqueta inmutable `v7.2.0`.

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

## Pruebas

```bash
PYTHONPATH=lib python3 -m unittest discover -s tests -v
bash -n Down-Linux-music_v7.sh
```
