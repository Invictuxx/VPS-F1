import os
import re
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path
import requests

# ============================================================
# CONFIGURACIÓN (Desde GitHub Actions)
# ============================================================
STREAM_PAGE_URL = os.environ.get("STREAM_PAGE_URL", "")
FILESTER_API_KEY = os.environ.get("FILESTER_API_KEY")
FILESTER_FOLDER_ID = os.environ.get("FILESTER_FOLDER_ID")
RECORDING_DURATION = int(os.environ.get("RECORDING_DURATION", "30"))
UPLOAD_RETRIES = int(os.environ.get("UPLOAD_RETRIES", "5"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "30"))
FILESTER_UPLOAD_URL = "https://u1.filester.me/api/v1/upload"


# ============================================================
# CREAR NOMBRE DE ARCHIVO
# ============================================================
def create_filename():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"grabacion_{timestamp}.mp4"


# ============================================================
# OBTENER M3U8 DESDE LA PÁGINA
# ============================================================
def javascript_unescape(value):
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    value = value.replace("\\x26", "&")
    return value


def get_m3u8_url():
    print("\n" + "=" * 70 + "\n0. OBTENIENDO ENLACE M3U8\n" + "=" * 70)

    if not STREAM_PAGE_URL:
        print("ERROR: La variable STREAM_PAGE_URL no está configurada.")
        sys.exit(1)

    print(f"Página: {STREAM_PAGE_URL}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    response = requests.get(STREAM_PAGE_URL, headers=headers, timeout=30)
    response.raise_for_status()
    html = response.text

    patterns = [
        r'playbackURL\s*=\s*"([^"]+)"',
        r"playbackURL\s*=\s*'([^']+)'",
        r'"playbackURL"\s*:\s*"([^"]+)"',
        r"'playbackURL'\s*:\s*'([^']+)'",
        r'https?:\\?/\\?/[^"\']+?\.m3u8[^"\']*',
    ]

    m3u8_url = None
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            m3u8_url = match.group(1) if len(match.groups()) > 0 else match.group(0)
            break

    if not m3u8_url:
        print("ERROR: No se encontró playbackURL ni una URL .m3u8 en el HTML.")
        sys.exit(1)

    m3u8_url = javascript_unescape(m3u8_url).strip()
    print(f"M3U8 Extraído: {m3u8_url}")
    return m3u8_url


# ============================================================
# GRABAR STREAM HLS
# ============================================================
def record_stream(m3u8_url, output_file):
    print("\n" + "=" * 70 + "\n1. INICIANDO GRABACIÓN HLS\n" + "=" * 70)
    print(f"Duración: {RECORDING_DURATION} segundos")
    print(f"Archivo: {output_file}")

    command = [
        "ffmpeg",
        "-hide_banner", "-y",
        "-fflags", "+genpts",          # Repara timestamps rotos
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-i", m3u8_url,
        "-t", str(RECORDING_DURATION),
        "-c:v", "copy",                 # Copia video sin recodificar (rápido, sin CPU)
        "-c:a", "copy",
        "-bsf:a", "aac_adtstoasc",      # Repara audio para contenedor MP4
        "-movflags", "+faststart",      # MP4 listo para reproducirse en la web
        output_file,
    ]

    print("\nEjecutando FFmpeg...\n")
    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError:
        print("ERROR: FFmpeg no está instalado.")
        return False

    if result.returncode != 0:
        print(f"ERROR: FFmpeg terminó con código {result.returncode}")
        return False

    print("\nGrabación terminada correctamente.")
    return True


# ============================================================
# VALIDAR ARCHIVO
# ============================================================
def validate_file(filename):
    path = Path(filename)

    if not path.exists():
        print(f"ERROR: no existe {filename}")
        return False

    size = path.stat().st_size
    if size <= 0:
        print(f"ERROR: {filename} está vacío.")
        return False

    size_mb = size / (1024 * 1024)
    size_gb = size / (1024 * 1024 * 1024)
    print(f"\nArchivo: {filename}")
    print(f"Tamaño: {size_mb:.2f} MB ({size_gb:.2f} GB)")
    return True


# ============================================================
# SUBIR A FILESTER
# ============================================================
def upload_to_filester(filename):
    print("\n" + "=" * 70 + "\n2. SUBIENDO A FILESTER\n" + "=" * 70)

    headers = {"Authorization": f"Bearer {FILESTER_API_KEY}"}
    if FILESTER_FOLDER_ID:
        headers["X-Folder-ID"] = FILESTER_FOLDER_ID

    for attempt in range(1, UPLOAD_RETRIES + 1):
        print(f"\nIntento {attempt}/{UPLOAD_RETRIES}")
        try:
            with open(filename, "rb") as file:
                files = {"file": (os.path.basename(filename), file, "video/mp4")}
                response = requests.post(
                    FILESTER_UPLOAD_URL, headers=headers, files=files, timeout=3600
                )

            print(f"HTTP: {response.status_code}")

            try:
                data = response.json()
            except ValueError:
                data = None

            if response.ok:
                print("\nUPLOAD CORRECTO")
                if data and data.get("url"):
                    print("\nURL DE LA GRABACIÓN:")
                    print(data["url"])
                return True

            print("\nLa subida falló.")
            print(data if data else response.text[:1000])

        except requests.RequestException as error:
            print("\nError de conexión:")
            print(error)

        if attempt < UPLOAD_RETRIES:
            print(f"\nEsperando {RETRY_DELAY} segundos...")
            time.sleep(RETRY_DELAY)

    print("\nERROR: no se pudo subir el archivo.")
    return False


# ============================================================
# ELIMINAR ARCHIVO
# ============================================================
def delete_file(filename):
    try:
        os.remove(filename)
        print(f"Archivo eliminado: {filename}")
    except OSError as error:
        print(f"No se pudo eliminar {filename}: {error}")


# ============================================================
# MAIN
# ============================================================
def main():
    print("\n" + "=" * 70 + "\nHLS RECORDER (SOLO GRABACIÓN DIRECTA)\n" + "=" * 70)

    video_file = create_filename()

    # PASO 0: Extraer URL m3u8
    m3u8_url = get_m3u8_url()

    # PASO 1: Grabar
    if not record_stream(m3u8_url, video_file):
        print("\nLa grabación falló.")
        sys.exit(1)

    if not validate_file(video_file):
        sys.exit(1)

    # PASO 2: Subir el archivo grabado (sin escalar)
    if not upload_to_filester(video_file):
        print("\nLa subida falló. El archivo local NO será eliminado.")
        sys.exit(1)

    # PASO 3: Limpieza
    print("\n" + "=" * 70 + "\n3. LIMPIANDO ARCHIVOS\n" + "=" * 70)
    delete_file(video_file)

    print("\n" + "=" * 70 + "\nPROCESO COMPLETADO\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
    
