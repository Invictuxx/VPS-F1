import os
import re
import sys
import time
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import requests


# ============================================================================
# UTILIDADES DE CONFIGURACIÓN
# ============================================================================

def get_str_env(name, default="", required=False):
    value = os.environ.get(name, default).strip()

    if required and not value:
        print(f"ERROR DE CONFIGURACIÓN: falta la variable {name}.", flush=True)
        sys.exit(1)

    return value


def get_int_env(name, default):
    raw = os.environ.get(name, str(default)).strip()

    if not raw:
        raw = str(default)

    try:
        return int(raw)
    except ValueError:
        print(
            f"ERROR DE CONFIGURACIÓN: {name}='{raw}' no es un entero válido.",
            flush=True,
        )
        sys.exit(1)


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

STREAM_PAGE_URL = get_str_env("STREAM_PAGE_URL", required=True)
FILESTER_API_KEY = get_str_env("FILESTER_API_KEY", required=True)

# Opcional: identificador de carpeta de Filester donde subir el archivo.
# Si se deja vacío, se sube a la raíz de la cuenta.
# Ver: https://filester.me/api-docs (header X-Folder-ID)
FILESTER_FOLDER_ID = get_str_env("FILESTER_FOLDER_ID", default="")

RECORDING_DURATION = get_int_env("RECORDING_DURATION", 30)

# Márgenes de seguridad para no colgar el job entero si algo se queda
# esperando indefinidamente (red caída, stream que nunca corta, etc.).
FFMPEG_TIMEOUT = RECORDING_DURATION + 300  # 5 min de margen sobre la duración

WORK_DIR = Path("work")

INPUT_VIDEO = WORK_DIR / "recorded_720p.ts"
FINAL_VIDEO = WORK_DIR / "final_1080p.mp4"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131 Safari/537.36"
)


# ============================================================================
# UTILIDADES
# ============================================================================

def log(message=""):
    print(message, flush=True)


def redact_url(url):
    """
    Oculta todo el path y la query string, dejando solo el dominio.
    Muchos M3U8 llevan tokens de autorización en el PATH (no solo en la
    query), así que no basta con cortar en el '?'.
    """
    if not url:
        return url

    try:
        parts = urlsplit(url)
        if not parts.netloc:
            return "[REDACTED]"
        return f"{parts.scheme}://{parts.netloc}/[REDACTED]"
    except Exception:
        return "[REDACTED]"


def redact_command(command):
    """
    Convierte el comando en texto y oculta cualquier URL http(s).
    """
    result = []

    for item in command:
        item = str(item)

        if re.match(r"^https?://", item, re.IGNORECASE):
            item = redact_url(item)

        result.append(item)

    return " ".join(shlex.quote(x) for x in result)


def run_command(command, description=None, timeout=None):
    """
    Ejecuta un comando mostrando stdout/stderr en tiempo real.
    Si se supera `timeout` segundos, se mata el proceso para no colgar
    el job entero hasta el límite del workflow.
    """

    if description:
        log()
        log("=" * 70)
        log(description)
        log("=" * 70)

    log()
    log("Comando:")
    log(redact_command(command))
    log()

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output = []
    start = time.time()

    try:
        for line in process.stdout:
            print(line, end="", flush=True)
            output.append(line)

            if timeout and (time.time() - start) > timeout:
                process.kill()
                process.wait()
                raise RuntimeError(
                    f"El comando superó el timeout de {timeout}s "
                    f"y fue cancelado."
                )

        process.wait()

    except RuntimeError:
        raise

    except Exception:
        process.kill()
        raise

    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            command,
            output="".join(output),
        )

    return "".join(output)


# ============================================================================
# COMPROBAR ENTORNO
# ============================================================================

def check_environment():
    log("=" * 70)
    log("COMPROBANDO ENTORNO")
    log("=" * 70)

    for program in ("ffmpeg", "ffprobe"):
        from shutil import which

        if which(program) is None:
            raise RuntimeError(f"{program} no está instalado o no está en PATH.")

    log("✓ STREAM_PAGE_URL configurado")
    log("✓ FILESTER_API_KEY configurado")

    if FILESTER_FOLDER_ID:
        log(f"✓ FILESTER_FOLDER_ID configurado: {FILESTER_FOLDER_ID}")
    else:
        log("✓ FILESTER_FOLDER_ID no configurado (se sube a la raíz)")

    log("✓ ffmpeg / ffprobe disponibles")

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    log(f"✓ Directorio de trabajo: {WORK_DIR.resolve()}")


# ============================================================================
# OBTENER M3U8 DESDE LA PÁGINA
# ============================================================================

def javascript_unescape(value):
    """
    Desescapa las secuencias habituales que aparecen en JavaScript:

        https:\\/\\/servidor\\/archivo.m3u8
        \\u0026
        \\x26

    """

    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    value = value.replace("\\x26", "&")
    value = value.replace("&amp;", "&")

    return value


def get_m3u8_url():
    log()
    log("=" * 70)
    log("OBTENIENDO PLAYBACK URL")
    log("=" * 70)

    log("Página: " + redact_url(STREAM_PAGE_URL))

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
    }

    response = requests.get(STREAM_PAGE_URL, headers=headers, timeout=30)
    response.raise_for_status()

    html = response.text

    log(f"Página obtenida correctamente: {len(html)} bytes")

    patterns = [
        r'playbackURL\s*=\s*"([^"]+)"',
        r"playbackURL\s*=\s*'([^']+)'",
        r'"playbackURL"\s*:\s*"([^"]+)"',
        r"'playbackURL'\s*:\s*'([^']+)'",
    ]

    m3u8_url = None

    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)

        if match:
            m3u8_url = match.group(1)
            break

    if not m3u8_url:
        direct_pattern = r'https?:\\?/\\?/[^"\']+?\.m3u8[^"\']*'
        match = re.search(direct_pattern, html, re.IGNORECASE)

        if match:
            m3u8_url = match.group(0)

    if not m3u8_url:
        raise RuntimeError(
            "No se encontró playbackURL ni una URL .m3u8 en el HTML."
        )

    m3u8_url = javascript_unescape(m3u8_url).strip()

    if not re.match(r"^https?://", m3u8_url, re.IGNORECASE):
        raise RuntimeError("La URL M3U8 encontrada no es HTTP/HTTPS.")

    if ".m3u8" not in m3u8_url.lower():
        raise RuntimeError("La URL encontrada no parece ser una URL M3U8.")

    log("M3U8 encontrada: " + redact_url(m3u8_url))

    return m3u8_url


# ============================================================================
# GRABAR STREAM
# ============================================================================

def record_stream(m3u8_url):
    log()
    log("=" * 70)
    log("GRABANDO STREAM")
    log("=" * 70)

    log(f"Duración: {RECORDING_DURATION} segundos")

    if INPUT_VIDEO.exists():
        INPUT_VIDEO.unlink()

    command = [
        "ffmpeg",
        "-hide_banner",
        "-fflags", "+genpts",            # <-- NUEVO: Repara timestamps rotos del stream
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-user_agent", USER_AGENT,
        "-i", m3u8_url,
        "-t", str(RECORDING_DURATION),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "copy",
        "-c:a", "copy",
        "-y", str(INPUT_VIDEO),          # <-- Guarda como .ts (el formato más seguro)
    ]

    run_command(command, "FFMPEG - GRABACIÓN 720P", timeout=FFMPEG_TIMEOUT)

    if not INPUT_VIDEO.exists():
        raise RuntimeError("FFmpeg terminó pero no creó el archivo.")

    size = INPUT_VIDEO.stat().st_size

    if size <= 0:
        raise RuntimeError("El archivo grabado está vacío.")

    log()
    log(f"✓ Grabación creada: {INPUT_VIDEO}")
    log(f"✓ Tamaño: {size / 1024 / 1024:.2f} MB")


# ============================================================================
# INFORMACIÓN DEL VIDEO
# ============================================================================

def video_info(video):
    """
    Informativo únicamente: si ffprobe falla, se registra el motivo
    (incluyendo stderr) pero NO se aborta todo el proceso por esto.
    """

    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate",
        "-of", "default=noprint_wrappers=1",
        str(video),
    ]

    log()
    log("=" * 70)
    log(f"INFORMACIÓN DE VIDEO: {video.name}")
    log("=" * 70)

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        log(f"⚠ No se pudo obtener información de {video.name}: {exc}")
        if exc.stderr:
            log("ffprobe stderr:")
            log(exc.stderr.strip())
        return

    log(result.stdout.strip())


# ============================================================================
# ESCALADO A 1080P (FFMPEG, LANCZOS)
# ============================================================================

def scale_to_1080p():
    """
    Reescala directamente a 1920x1080 por interpolación (lanczos), sin IA.
    No requiere GPU y es prácticamente instantáneo incluso para
    grabaciones largas, a diferencia de un pipeline con super-resolución.
    """

    log()
    log("=" * 70)
    log("FFMPEG - REESCALADO A 1080P (LANCZOS)")
    log("=" * 70)

    if FINAL_VIDEO.exists():
        FINAL_VIDEO.unlink()

    command = [
        "ffmpeg",
        "-hide_banner",
        "-i", str(INPUT_VIDEO),
        "-vf", (
            "scale=1920:1080:flags=lanczos:"
            "force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
        ),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", str(FINAL_VIDEO),
    ]

    run_command(command, "FFMPEG - ESCALADO LANCZOS", timeout=FFMPEG_TIMEOUT)

    if not FINAL_VIDEO.exists():
        raise RuntimeError("FFmpeg terminó pero no creó el archivo final.")

    size = FINAL_VIDEO.stat().st_size

    if size <= 0:
        raise RuntimeError("El archivo final está vacío.")

    log()
    log("✓ Video final creado.")
    log(f"✓ Archivo: {FINAL_VIDEO}")
    log(f"✓ Tamaño: {size / 1024 / 1024:.2f} MB")


# ============================================================================
# SUBIR A FILESTER
# ============================================================================

def upload_filester():
    log()
    log("=" * 70)
    log("SUBIENDO A FILESTER")
    log("=" * 70)

    if not FINAL_VIDEO.exists():
        raise RuntimeError("No existe el video final para subir.")

    url = "https://u1.filester.me/api/v1/upload"
    headers = {"Authorization": f"Bearer {FILESTER_API_KEY}"}

    if FILESTER_FOLDER_ID:
        headers["X-Folder-ID"] = FILESTER_FOLDER_ID

    log(f"Archivo: {FINAL_VIDEO.name}")
    log(f"Tamaño: {FINAL_VIDEO.stat().st_size / 1024 / 1024:.2f} MB")

    if FILESTER_FOLDER_ID:
        log(f"Carpeta destino: {FILESTER_FOLDER_ID}")
    else:
        log("Carpeta destino: raíz de la cuenta")

    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
        log()
        log(f"Intento de subida {attempt}/{max_attempts}")

        try:
            with open(FINAL_VIDEO, "rb") as file_handle:
                files = {
                    "file": (FINAL_VIDEO.name, file_handle, "video/mp4")
                }

                response = requests.post(
                    url, headers=headers, files=files, timeout=1800
                )

            log(f"HTTP status: {response.status_code}")

            # No reintentamos errores de cliente no recuperables
            # (credenciales inválidas, carpeta inexistente, etc.),
            # salvo 429 (rate limit), donde sí tiene sentido esperar.
            if 400 <= response.status_code < 500 and response.status_code != 429:
                response.raise_for_status()

            response.raise_for_status()

            data = response.json()

            if not data.get("success", False):
                raise RuntimeError(
                    "Filester respondió success=false: "
                    + data.get("message", "sin detalle")
                )

            log()
            log("=" * 70)
            log("✓ SUBIDA COMPLETADA")
            log("=" * 70)

            if data.get("url"):
                log(f"URL: {data['url']}")
            if data.get("slug"):
                log(f"Slug: {data['slug']}")
            if data.get("file_id"):
                log(f"File ID: {data['file_id']}")
            if data.get("thumbnail_url"):
                log(f"Thumbnail: {data['thumbnail_url']}")

            return data

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None

            if status is not None and 400 <= status < 500 and status != 429:
                detail = ""
                try:
                    detail = exc.response.json().get("message", "")
                except Exception:
                    pass

                log(
                    f"Error HTTP {status} no recuperable, no se reintenta: "
                    f"{detail or exc}"
                )
                raise

            last_error = str(exc)
            log(f"Error en subida: {last_error}")

        except Exception as exc:
            last_error = str(exc)
            log(f"Error en subida: {last_error}")

        if attempt < max_attempts:
            wait = attempt * 10
            log(f"Reintentando en {wait} segundos...")
            time.sleep(wait)
        else:
            raise RuntimeError(
                f"Filester no aceptó la subida tras {max_attempts} intentos. "
                f"Último error: {last_error}"
            )


# ============================================================================
# LIMPIEZA
# ============================================================================

def clean_work():
    log()
    log("=" * 70)
    log("LIMPIANDO ARCHIVOS TEMPORALES")
    log("=" * 70)

    for file_path in [INPUT_VIDEO, FINAL_VIDEO]:
        try:
            if file_path.exists():
                file_path.unlink()
                log(f"Eliminado: {file_path}")
        except Exception as exc:
            log(f"No se pudo eliminar {file_path}: {exc}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    start_time = time.time()

    try:
        log()
        log("=" * 70)
        log("RECORDER - INICIO")
        log("=" * 70)
        log(f"RECORDING_DURATION = {RECORDING_DURATION}s")

        check_environment()

        m3u8_url = get_m3u8_url()

        record_stream(m3u8_url)
        video_info(INPUT_VIDEO)

        scale_to_1080p()
        video_info(FINAL_VIDEO)

        upload_filester()

        clean_work()

        elapsed = time.time() - start_time

        log()
        log("=" * 70)
        log("✓ PROCESO COMPLETADO")
        log("=" * 70)
        log(f"Tiempo total: {elapsed / 60:.1f} minutos")

    except KeyboardInterrupt:
        log()
        log("Proceso cancelado.")
        sys.exit(130)

    except subprocess.CalledProcessError as exc:
        log()
        log("=" * 70)
        log("✗ ERROR (comando externo)")
        log("=" * 70)
        log(f"Código de salida: {exc.returncode}")
        if exc.output:
            log("Últimas líneas de salida:")
            log("".join(exc.output.splitlines(keepends=True)[-30:]))
        log()
        log("Los archivos de trabajo quedan en work/ dentro del runner.")
        log("Si necesitas inspeccionarlos, sube 'work/' como artifact en el workflow.")
        sys.exit(1)

    except Exception as exc:
        log()
        log("=" * 70)
        log("✗ ERROR")
        log("=" * 70)
        log(f"{type(exc).__name__}: {exc}")
        log()
        log("Los archivos de trabajo quedan en work/ dentro del runner.")
        log("Si necesitas inspeccionarlos, sube 'work/' como artifact en el workflow.")
        sys.exit(1)


if __name__ == "__main__":
    main()
