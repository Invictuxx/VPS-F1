import os
import re
import time
import base64
import subprocess
from pathlib import Path
from urllib.parse import urlparse
import requests

STREAM_PAGE_URL = os.getenv("STREAM_PAGE_URL", "").strip()
FILESTER_API_KEY = os.getenv("FILESTER_API_KEY", "").strip()
FILESTER_FOLDER_ID = os.getenv("FILESTER_FOLDER_ID", "").strip()
RECORDING_DURATION = int(os.getenv("RECORDING_DURATION", "10800"))
UPLOAD_RETRIES = int(os.getenv("UPLOAD_RETRIES", "3"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "10"))
CUSTOM_FILE_NAME = os.getenv("CUSTOM_FILE_NAME", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
FILESTER_UPLOAD_URL = "https://u1.filester.me/api/v1/upload"

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )
    try:
        requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=30
        )
    except Exception as error:
        print(f"Telegram no disponible: {error}")

def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip(" ._") or "grabacion"

def create_filename():
    if CUSTOM_FILE_NAME:
        name = sanitize_filename(CUSTOM_FILE_NAME)
    else:
        name = time.strftime("grabacion_%Y%m%d_%H%M%S")

    if not name.lower().endswith(".mp4"):
        name += ".mp4"

    return name

def javascript_unescape(value):
    value = value.replace(r"\/", "/")
    value = value.replace(r"\.", ".")
    value = value.replace(r"\?", "?")
    value = value.replace(r"\=", "=")
    value = value.replace(r"\&", "&")
    value = value.replace(r"\:", ":")
    value = value.replace(r"\_", "_")
    return value


def is_valid_m3u8_url(url):
    if not url:
        return False
    url = javascript_unescape(url).strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return parsed.path.lower().endswith(".m3u8")

def normalize_m3u8_url(url):
    url = javascript_unescape(url)
    url = url.replace("\\", "")
    url = url.strip(" \"'`;,)")
    return url


def extract_m3u8_by_pattern(html):
    print("\nBuscando URL M3U8 mediante pattern...")
    patterns = [
        r'https?://[^"\'<>\s\\]+\.m3u8(?:\?[^"\'<>\s\\]*)?',
        r'https?:\\?/\\?/[^"\'<>\s]+?\.m3u8(?:\?[^"\'<>\s]*)?',
        r'["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
        r'source(?:Url|URL|url)?\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)',
        r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)'
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html, re.I)
        for match in matches:
            if isinstance(match, tuple):
                match = next(
                    (item for item in match if item), "")
            url = normalize_m3u8_url(match)
            if is_valid_m3u8_url(url):
                print(f"M3U8 encontrada por pattern:\n{url}")
                return url
    return None

def decode_sw_array(html):
    print("\nBuscando ofuscación mediante arreglo Sw...")
    match = re.search(
        r'var\s+Sw\s*=\s*\[(.*?)\]\s*;\s*Sw\\?\.sort',
        html,
        re.S)
    if not match:
        return None
    pairs = re.findall(
        r'\[(\d+)\s*,\s*["\']([^"\']+)["\']\]',
        match.group(1))
    if not pairs:
        print("Se encontró Sw, pero no contiene elementos válidos.")
        return None
    key = 67043 + 828580
    decoded_parts = {}
    for index, value in pairs:
        try:
            raw = base64.b64decode(value).decode("utf-8")
            number_text = re.sub(r"\D", "", raw)
            if not number_text:
                continue
            number = int(number_text)
            character_code = number - key
            if character_code < 0 or character_code > 0x10FFFF:
                continue
            decoded_parts[int(index)] = chr(character_code)
        except Exception as error:
            print(
                f"No se pudo decodificar índice "
                f"{index}: {error}")
    if not decoded_parts:
        print("No se pudo resolver el arreglo Sw.")
        return None
    playback_url = "".join(
        decoded_parts[index]
        for index in sorted(decoded_parts))
    playback_url = normalize_m3u8_url(playback_url)
    if not is_valid_m3u8_url(playback_url):
        print("Sw fue procesado, pero no produjo una M3U8 válida.")
        return None
    print(f"M3U8 encontrada mediante ofuscación:\n{playback_url}")
    return playback_url

def get_m3u8_url():
    print("\n" + "=" * 70)
    print("1. OBTENIENDO URL M3U8")
    print("=" * 70)
    if not STREAM_PAGE_URL:
        raise RuntimeError("Falta STREAM_PAGE_URL.")
    response = requests.get(
        STREAM_PAGE_URL,
        timeout=60,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            )
        }
    )

    response.raise_for_status()
    html = response.text
    print(f"Página descargada: {len(html):,} bytes")
    # ---------------------------------------------------------------
    # MÉTODO 1: OFUSCACIÓN ESPECÍFICA DE LA PÁGINA
    # ---------------------------------------------------------------
    m3u8_url = decode_sw_array(html)
    if m3u8_url:
        return m3u8_url
    print("No se detectó una ofuscación Sw válida.")
    # ---------------------------------------------------------------
    # MÉTODO 2: BÚSQUEDA GENERAL POR PATTERN M3U8
    # ---------------------------------------------------------------
    m3u8_url = extract_m3u8_by_pattern(html)
    if m3u8_url:
        return m3u8_url
    # ---------------------------------------------------------------
    # MÉTODO 3: BUSCAR M3U8 DESPUÉS DE DESESCAPAR JAVASCRIPT
    # ---------------------------------------------------------------
    print("Probando nuevamente después de desescapar JavaScript...")
    decoded_html = javascript_unescape(html)
    m3u8_url = extract_m3u8_by_pattern(decoded_html)
    if m3u8_url:
        return m3u8_url
    raise RuntimeError("No se encontró ninguna URL M3U8 en la página.")

def record_stream(m3u8_url, output_file):
    print("\n" + "=" * 70)
    print("2. GRABANDO STREAM")
    print("=" * 70)
    print(f"Archivo: {output_file}")
    print(f"Duración: {RECORDING_DURATION} segundos")
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        m3u8_url,
        "-t",
        str(RECORDING_DURATION),
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        output_file
    ]
    print("Ejecutando FFmpeg...")
    result = subprocess.run(
        command,
        stdout=None,
        stderr=None)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg terminó con código {result.returncode}.")
    print("Grabación finalizada.")

def validate_file(file_path):
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError("El archivo grabado no existe.")
    size = path.stat().st_size
    if size < 1024:
        raise RuntimeError("El archivo grabado está vacío o es demasiado pequeño.")
    print(f"Archivo válido: {size / 1024 / 1024:.2f} MB")
    return True

def upload_to_filester(file_path):
    print("\n" + "=" * 70)
    print("3. SUBIENDO A FILESTER")
    print("=" * 70)
    if not FILESTER_API_KEY:
        raise RuntimeError("Falta FILESTER_API_KEY.")
    headers = {
        "Authorization": f"Bearer {FILESTER_API_KEY}"
    }
    data = {}
    if FILESTER_FOLDER_ID:
        data["folder_id"] = FILESTER_FOLDER_ID
    last_error = None
    for attempt in range(1, UPLOAD_RETRIES + 1):
        print(f"Intento de subida {attempt}/{UPLOAD_RETRIES}...")
        try:
            with open(file_path, "rb") as file_handle:
                response = requests.post(
                    FILESTER_UPLOAD_URL,
                    headers=headers,
                    data=data,
                    files={
                        "file": (
                            Path(file_path).name,
                            file_handle,
                            "video/mp4"
                        )
                    },
                    timeout=3600
                )
            print(f"Respuesta Filester: HTTP {response.status_code}")
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError:
                result = {}
            file_url = (
                result.get("url")
                or result.get("download_url")
                or result.get("file_url")
                or result.get("link")
            )
            if file_url:
                print(f"Archivo subido:\n{file_url}")
                return file_url
            print("Filester respondió correctamente, pero no devolvió una URL.")
            print(response.text[:1000])
            return None
        except Exception as error:
            last_error = error
            print(f"Error de subida: {error}")
            if attempt < UPLOAD_RETRIES:
                print(f"Esperando {RETRY_DELAY} segundos...")
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"No se pudo subir el archivo: {last_error}")

def delete_file(file_path):
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            print(f"Archivo local eliminado: {path}")
    except Exception as error:
        print(f"No se pudo eliminar el archivo local: {error}")

def main():
    print("=" * 70)
    print("RECORDER")
    print("=" * 70)
    output_file = create_filename()
    try:
        m3u8_url = get_m3u8_url()
        print("\nURL M3U8 lista para FFmpeg.")
        record_stream(
            m3u8_url,
            output_file
        )
        validate_file(output_file)
        file_url = upload_to_filester(
            output_file
        )
        if file_url:
            send_telegram(
                "Grabación completada.\n\n"
                f"Archivo: {output_file}\n"
                f"URL: {file_url}"
            )
        else:
            send_telegram(
                "Grabación completada.\n\n"
                f"Archivo: {output_file}\n"
                "Filester no devolvió URL."
            )
        delete_file(output_file)
        print("\nProceso completado.")
    except Exception as error:
        print(f"\nERROR:{error}")
        send_telegram(
            "Error en recorder.py:\n\n"
            f"{error}"
        )
        raise
if __name__ == "__main__":
    main()
