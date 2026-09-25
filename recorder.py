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
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=30
        )
    except Exception as error:
        print(f"Telegram: {error}")


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip(" ._") or "grabacion"


def create_filename():
    name = sanitize_filename(CUSTOM_FILE_NAME) if CUSTOM_FILE_NAME else time.strftime("grabacion_%Y%m%d_%H%M%S")
    return name if name.lower().endswith(".mp4") else f"{name}.mp4"


def javascript_unescape(value):
    return value.replace(r"\/", "/").replace(r"\.", ".").replace(r"\?", "?").replace(r"\=", "=").replace(r"\&", "&").replace(r"\:", ":")


def is_valid_m3u8_url(url):
    if not url:
        return False
    url = javascript_unescape(url).strip()
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and parsed.path.lower().endswith(".m3u8")


def normalize_m3u8_url(url):
    return javascript_unescape(url).replace("\\", "").strip(" \"'`;,)")


def extract_m3u8_by_pattern(html):
    patterns = [
        r'https?://[^"\'<>\s\\]+\.m3u8(?:\?[^"\'<>\s\\]*)?',
        r'https?:\\?/\\?/[^"\'<>\s]+?\.m3u8(?:\?[^"\'<>\s]*)?',
        r'["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
        r'(?:source|sourceUrl|sourceURL)\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)'
    ]

    for pattern in patterns:
        for match in re.findall(pattern, html, re.I):
            if isinstance(match, tuple):
                match = next((x for x in match if x), "")
            url = normalize_m3u8_url(match)
            if is_valid_m3u8_url(url):
                print(f"M3U8 encontrada: {url}")
                return url

    return None


def decode_sw_array(html):
    match = re.search(r'\bSw\s*=\s*\[(.*?)\]\s*;\s*Sw\\?\.sort', html, re.S)
    if not match:
        return None

    pairs = re.findall(r'\[\s*(\d+)\s*,\s*["\']([^"\']+)["\']\s*\]', match.group(1))
    if not pairs:
        return None

    keys = re.findall(r'function\s+\w+\s*\(\s*\)\s*\{\s*return\s+(\d+)\s*;\s*\}', html)
    if len(keys) < 2:
        return None

    key = int(keys[0]) + int(keys[1])
    result = {}

    for index, value in pairs:
        try:
            decoded = base64.b64decode(value).decode("utf-8")
            number = re.sub(r"\D", "", decoded)
            if number:
                code = int(number) - key
                if 0 <= code <= 0x10FFFF:
                    result[int(index)] = chr(code)
        except Exception:
            continue

    if not result:
        return None

    url = normalize_m3u8_url("".join(result[i] for i in sorted(result)))
    return url if is_valid_m3u8_url(url) else None


def get_m3u8_url():
    print("\n" + "=" * 70 + "\n1. OBTENIENDO URL M3U8\n" + "=" * 70)

    if not STREAM_PAGE_URL:
        raise RuntimeError("Falta STREAM_PAGE_URL.")

    response = requests.get(
        STREAM_PAGE_URL,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    html = response.text
    print(f"Página descargada: {len(html):,} bytes")

    url = decode_sw_array(html)
    if url:
        print(f"M3U8 mediante ofuscación: {url}")
        return url

    url = extract_m3u8_by_pattern(html)
    if url:
        return url

    decoded_html = javascript_unescape(html)

    url = decode_sw_array(decoded_html)
    if url:
        print(f"M3U8 mediante ofuscación: {url}")
        return url

    url = extract_m3u8_by_pattern(decoded_html)
    if url:
        return url

    raise RuntimeError("No se encontró ninguna URL M3U8.")


def record_stream(m3u8_url, output_file):
    print("\n" + "=" * 70 + "\n2. GRABANDO STREAM\n" + "=" * 70)
    print(f"Archivo: {output_file}")
    print(f"Duración: {RECORDING_DURATION} segundos")

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-i", m3u8_url,
        "-t", str(RECORDING_DURATION),
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        output_file
    ]

    result = subprocess.run(command)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg terminó con código {result.returncode}.")

    print("Grabación finalizada.")


def validate_file(file_path):
    path = Path(file_path)

    if not path.exists():
        raise RuntimeError("El archivo grabado no existe.")

    size = path.stat().st_size

    if size < 1024:
        raise RuntimeError("El archivo grabado está vacío.")

    print(f"Archivo válido: {size / 1024 / 1024:.2f} MB")


def upload_to_filester(file_path):
    print("\n" + "=" * 70 + "\n3. SUBIENDO A FILESTER\n" + "=" * 70)

    if not FILESTER_API_KEY:
        raise RuntimeError("Falta FILESTER_API_KEY.")

    headers = {"Authorization": f"Bearer {FILESTER_API_KEY}"}
    data = {"folder_id": FILESTER_FOLDER_ID} if FILESTER_FOLDER_ID else {}
    last_error = None

    for attempt in range(1, UPLOAD_RETRIES + 1):
        print(f"Subida {attempt}/{UPLOAD_RETRIES}")

        try:
            with open(file_path, "rb") as file_handle:
                response = requests.post(
                    FILESTER_UPLOAD_URL,
                    headers=headers,
                    data=data,
                    files={"file": (Path(file_path).name, file_handle, "video/mp4")},
                    timeout=3600
                )

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
                print(f"Archivo subido: {file_url}")
                return file_url

            print(response.text[:1000])
            return None

        except Exception as error:
            last_error = error
            print(f"Error: {error}")

            if attempt < UPLOAD_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(f"No se pudo subir el archivo: {last_error}")


def delete_file(file_path):
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            print("Archivo local eliminado.")
    except Exception as error:
        print(f"No se pudo eliminar el archivo: {error}")


def main():
    print("=" * 70 + "\nRECORDER\n" + "=" * 70)
    output_file = create_filename()

    try:
        m3u8_url = get_m3u8_url()
        record_stream(m3u8_url, output_file)
        validate_file(output_file)
        file_url = upload_to_filester(output_file)

        if file_url:
            send_telegram(f"Grabación completada.\n\nArchivo: {output_file}\nURL: {file_url}")
        else:
            send_telegram(f"Grabación completada.\n\nArchivo: {output_file}\nFilester no devolvió URL.")

        delete_file(output_file)
        print("\nProceso completado.")

    except Exception as error:
        print(f"\nERROR:\n{error}")
        send_telegram(f"Error en recorder.py:\n\n{error}")
        raise


if __name__ == "__main__":
    main()
