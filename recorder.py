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

# Ahora lee la página web desde los secrets de Actions
STREAM_PAGE_URL = os.environ.get("STREAM_PAGE_URL", "")

# Credenciales de Filester
FILESTER_API_KEY = os.environ.get("FILESTER_API_KEY", "TWQwwjc2Jp1lDyVkd4AViqE3h4Nxnfbx")
FILESTER_FOLDER_ID = os.environ.get("FILESTER_FOLDER_ID", "e0bccad1b2ca55ff")

# Duración de la grabación.
RECORDING_DURATION = int(os.environ.get("RECORDING_DURATION", "30"))

# Número de intentos para subir a Filester.
UPLOAD_RETRIES = int(os.environ.get("UPLOAD_RETRIES", "5"))

# Segundos entre intentos.
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "30"))

# URL de la API de Filester.
FILESTER_UPLOAD_URL = "https://u1.filester.me/api/v1/upload"


# ============================================================
# CREAR NOMBRES
# ============================================================

def create_filenames():
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")

    # IMPORTANTE: Grabamos en .ts para evitar corrupción y pixelación
    original = f"grabacion_{timestamp}_720p.ts"
    final = f"grabacion_{timestamp}_1080p.mp4"

    return original, final


# ============================================================
# OBTENER M3U8 DESDE LA PÁGINA
# ============================================================

def javascript_unescape(value):
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    value = value.replace("\\x26", "&")
    value = value.replace("&", "&")
    return value

def get_m3u8_url():
    print()
    print("=" * 70)
    print("0. OBTENIENDO ENLACE M3U8")
    print("=" * 70)

    if not STREAM_PAGE_URL:
        print("ERROR: La variable STREAM_PAGE_URL no está configurada.")
        sys.exit(1)

    print(f"Página: {STREAM_PAGE_URL}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    response = requests.get(STREAM_PAGE_URL, headers=headers, timeout=30)
    response.raise_for_status()
    html = response.text

    patterns = [
        r'playbackURL\s*=\s*"([^"]+)"',
        r"playbackURL\s*=\s*'([^']+)'",
        r'"playbackURL"\s*:\s*"([^"]+)"',
        r"'playbackURL'\s*:\s*'([^']+)'",
        r'https?:\\?/\\?/[^"\']+?\.m3u8[^"\']*'
    ]

    m3u8_url = None
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            # Si hay grupos, tomamos el 1, si no, el coincidencia completa (0)
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
    print()
    print("=" * 70)
    print("1. INICIANDO GRABACIÓN HLS")
    print("=" * 70)

    print(f"Duración: {RECORDING_DURATION} segundos")
    print(f"Archivo temporal: {output_file}")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-fflags", "+genpts",      # Repara timestamps rotos para evitar píxeles corruptos
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-i", m3u8_url,
        "-t", str(RECORDING_DURATION),
        "-c:v", "copy",
        "-c:a", "copy",
        output_file                # Se guarda en .ts
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
# UPSCALE 720p → 1080p
# ============================================================

def upscale_to_1080p(input_file, output_file):
    print()
    print("=" * 70)
    print("2. UPSCALE 720p → 1080p")
    print("=" * 70)

    print(f"Entrada: {input_file}")
    print(f"Salida: {output_file}")
    print("\nOptimizaciones activadas: Bicubic, Veryfast, CRF 23")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i", input_file,
        
        # Escalado más rápido
        "-vf", "scale=1920:1080:flags=bicubic:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        
        "-c:v", "libx264",
        "-preset", "veryfast",   # Más rápido para no agotar el timeout de GitHub
        "-crf", "23",            # Calidad balanceada
        "-pix_fmt", "yuv420p",
        
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_file
    ]

    print("\nProcesando video... Esto puede tardar bastante.\n")

    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError:
        print("ERROR: FFmpeg no está instalado.")
        return False

    if result.returncode != 0:
        print("ERROR: el upscale falló.")
        print(f"Código: {result.returncode}")
        return False

    print("\nUpscale terminado correctamente.")
    return True


# ============================================================
# SUBIR A FILESTER
# ============================================================

def upload_to_filester(filename):
    print()
    print("=" * 70)
    print("3. SUBIENDO A FILESTER")
    print("=" * 70)

    headers = {
        "Authorization": f"Bearer {FILESTER_API_KEY}"
    }

    if FILESTER_FOLDER_ID:
        headers["X-Folder-ID"] = FILESTER_FOLDER_ID

    for attempt in range(1, UPLOAD_RETRIES + 1):
        print(f"\nIntento {attempt}/{UPLOAD_RETRIES}")

        try:
            with open(filename, "rb") as file:
                files = {
                    "file": (os.path.basename(filename), file, "video/mp4")
                }

                response = requests.post(
                    FILESTER_UPLOAD_URL,
                    headers=headers,
                    files=files,
                    timeout=3600
                )

            print(f"HTTP: {response.status_code}")

            try:
                data = response.json()
            except ValueError:
                data = None

            if response.ok:
                print("\nUPLOAD CORRECTO")
                if data:
                    if data.get("url"):
                        print("\nURL DE LA GRABACIÓN:")
                        print(data["url"])
                return True

            print("\nLa subida falló.")
            if data:
                print(data)
            else:
                print(response.text[:1000])

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
        print(f"No se pudo eliminar {filename}")
        print(error)


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 70)
    print("HLS RECORDER + 1080P UPSCALE")
    print("=" * 70)

    # --------------------------------------------------------
    # Nombres
    # --------------------------------------------------------
    original_file, final_file = create_filenames()

    # ========================================================
    # PASO 0: EXTRAER URL
    # ========================================================
    m3u8_url = get_m3u8_url()

    # ========================================================
    # PASO 1: GRABAR HLS
    # ========================================================
    success = record_stream(m3u8_url, original_file)

    if not success:
        print("\nLa grabación falló.")
        sys.exit(1)

    if not validate_file(original_file):
        sys.exit(1)

    # ========================================================
    # PASO 2: UPSCALE
    # ========================================================
    success = upscale_to_1080p(original_file, final_file)

    if not success:
        print("\nEl procesamiento falló.")
        print(f"Se conserva el archivo original: {original_file}")
        sys.exit(1)

    if not validate_file(final_file):
        sys.exit(1)

    # ========================================================
    # PASO 3: SUBIR 1080P
    # ========================================================
    success = upload_to_filester(final_file)

    if not success:
        print("\nLa subida falló. Los archivos locales NO serán eliminados.")
        sys.exit(1)

    # ========================================================
    # PASO 4: LIMPIEZA
    # ========================================================
    print()
    print("=" * 70)
    print("4. LIMPIANDO ARCHIVOS")
    print("=" * 70)

    delete_file(original_file)
    delete_file(final_file)

    print("\n" + "=" * 70)
    print("PROCESO COMPLETADO")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
