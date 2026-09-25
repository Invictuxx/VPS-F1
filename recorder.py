```python
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
            timeout=20,
        )
    except Exception:
        pass


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    return name.strip(" .") or "recording"


def create_filename():
    if CUSTOM_FILE_NAME:
        name = CUSTOM_FILE_NAME
        if not Path(name).suffix:
            name += ".mp4"
        return sanitize_filename(name)

    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    return f"recording_{timestamp}.mp4"


def javascript_unescape(value):
    return (
        value.replace(r"\/", "/")
        .replace(r"\.", ".")
        .replace(r"\?", "?")
        .replace(r"\=", "=")
        .replace(r"\&", "&")
        .replace(r"\:", ":")
    )


def normalize_m3u8_url(url):
    return javascript_unescape(url).replace("\\", "").strip(" \"'`;,)")


def is_valid_m3u8_url(url):
    if not url:
        return False

    url = normalize_m3u8_url(url)
    parsed = urlparse(url)

    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
        and parsed.path.lower().endswith(".m3u8")
    )


def extract_m3u8_by_pattern(html):
    patterns = [
        r'https?://[^"\'<>\s\\]+\.m3u8(?:\?[^"\'<>\s\\]*)?',
        r'https?:\\?/\\?/[^"\'<>\s]+?\.m3u8(?:\?[^"\'<>\s]*)?',
        r'["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
        r'(?:source|sourceUrl|sourceURL)\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, html, re.I):
            url = match.group(1) if match.lastindex else match.group(0)
            url = normalize_m3u8_url(url)
            if is_valid_m3u8_url(url):
                return url

    return None


def decode_sw_array(html):
    match = re.search(
        r'\bSw\s*=\s*\[(.*?)\]\s*\]\s*;\s*Sw(?:\\\.)?sort',
        html,
        re.S,
    )

    if not match:
        return None

    pairs = re.findall(
        r'\[\s*(\d+)\s*,\s*["\']([^"\']+)["\']\s*\]',
        match.group(1),
    )

    if not pairs:
        return None

    key_match = re.search(
        r'var\s+k\s*=\s*(\w+)\s*\(\s*\)\s*\+\s*(\w+)\s*\(\s*\)',
        html,
    )

    if not key_match:
        return None

    values = []

    for name in key_match.groups():
        function_match = re.search(
            rf'function\s+{re.escape(name)}\s*\(\s*\)\s*\{{\s*return\s+(\d+)\s*;\s*\}}',
            html,
        )

        if not function_match:
            return None

        values.append(int(function_match.group(1)))

    key = sum(values)
    result = {}

    for index, value in pairs:
        try:
            decoded = base64.b64decode(value).decode("utf-8")
            number = re.sub(r"\D", "", decoded)

            if not number:
                continue

            code = int(number) - key

            if 0 <= code <= 0x10FFFF:
                result[int(index)] = chr(code)

        except Exception:
            continue

    if not result:
        return None

    url = normalize_m3u8_url(
        "".join(result[i] for i in sorted(result))
    )

    return url if is_valid_m3u8_url(url) else None


def get_m3u8_url():
    if not STREAM_PAGE_URL:
        raise RuntimeError("STREAM_PAGE_URL no está configurada.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        )
    }

    response = requests.get(
        STREAM_PAGE_URL,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()
    html = response.text

    print(f"Página descargada: {len(html):,} bytes")

    url = decode_sw_array(html)

    if url:
        print("M3U8 encontrada mediante Sw.")
        return url

    url = extract_m3u8_by_pattern(html)

    if url:
        print("M3U8 encontrada mediante pattern.")
        return url

    decoded_html = javascript_unescape(html)

    url = decode_sw_array(decoded_html)

    if url:
        print("M3U8 encontrada después de desescapar JavaScript.")
        return url

    url = extract_m3u8_by_pattern(decoded_html)

    if url:
        print("M3U8 encontrada después de desescapar JavaScript.")
        return url

    raise RuntimeError("No se encontró ninguna URL M3U8.")


def record_stream(m3u8_url, output_file):
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
        output_file,
    ]

    print("Iniciando grabación...")

    result = subprocess.run(command)

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg terminó con código {result.returncode}."
        )


def validate_file(file_path):
    path = Path(file_path)

    if not path.exists():
        raise RuntimeError("El archivo grabado no existe.")

    if path.stat().st_size < 1024:
        raise RuntimeError("El archivo grabado está vacío o incompleto.")

    return True


def upload_to_filester(file_path):
    if not FILESTER_API_KEY:
        raise RuntimeError("FILESTER_API_KEY no está configurada.")

    headers = {
        "Authorization": f"Bearer {FILESTER_API_KEY}",
    }

    data = {}

    if FILESTER_FOLDER_ID:
        data["folder_id"] = FILESTER_FOLDER_ID

    last_error = None

    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            print(f"Subiendo a Filester. Intento {attempt}/{UPLOAD_RETRIES}.")

            with open(file_path, "rb") as file:
                response = requests.post(
                    FILESTER_UPLOAD_URL,
                    headers=headers,
                    data=data,
                    files={
                        "file": (
                            Path(file_path).name,
                            file,
                            "video/mp4",
                        )
                    },
                    timeout=300,
                )

            response.raise_for_status()

            try:
                result = response.json()
            except ValueError:
                result = {"response": response.text}

            print("Subida a Filester completada.")
            return result

        except Exception as exc:
            last_error = exc

            if attempt < UPLOAD_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"Falló la subida a Filester: {last_error}"
    )


def delete_file(file_path):
    try:
        Path(file_path).unlink(missing_ok=True)
    except Exception:
        pass


def main():
    print("=" * 70)
    print("RECORDER")
    print("=" * 70)

    output_file = create_filename()

    try:
        print("\n" + "=" * 70)
        print("1. OBTENIENDO URL M3U8")
        print("=" * 70)

        m3u8_url = get_m3u8_url()

        parsed = urlparse(m3u8_url)
        print(
            f"M3U8 encontrada: "
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        )

        print("\n" + "=" * 70)
        print("2. GRABANDO STREAM")
        print("=" * 70)

        record_stream(m3u8_url, output_file)
        validate_file(output_file)

        print(f"Archivo creado: {output_file}")

        print("\n" + "=" * 70)
        print("3. SUBIENDO A FILESTER")
        print("=" * 70)

        result = upload_to_filester(output_file)

        send_telegram(
            f"Grabación completada.\n"
            f"Archivo: {output_file}\n"
            f"Filester: {result}"
        )

        delete_file(output_file)
        print("Archivo local eliminado.")

    except Exception as exc:
        print(f"ERROR: {exc}")
        send_telegram(f"Error en recorder: {exc}")
        raise


if __name__ == "__main__":
    main()
```
