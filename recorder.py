import os
import re
import sys
import time
import shlex
import subprocess
from datetime import datetime
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



# ============================================================
# CONFIGURACIÓN
# ============================================================

STREAM_PAGE_URL = get_str_env("STREAM_PAGE_URL", required=True)

FILESTER_API_KEY = get_str_env("FILESTER_API_KEY", required=True)

FILESTER_API_KEY = get_str_env("FILESTER_FOLDER_ID", required=False)

# Duración de la grabación.
# 3600 = 1 hora.
RECORDING_DURATION = int(
    os.environ.get(
        "RECORDING_DURATION",
        "30"
    )
)

# Número de intentos para subir a Filester.
UPLOAD_RETRIES = int(
    os.environ.get(
        "UPLOAD_RETRIES",
        "5"
    )
)

# Segundos entre intentos.
RETRY_DELAY = int(
    os.environ.get(
        "RETRY_DELAY",
        "30"
    )
)

# URL de la API de Filester.
FILESTER_UPLOAD_URL = (
    "https://u1.filester.me/api/v1/upload"
)


# ============================================================
# CREAR NOMBRES
# ============================================================

def create_filenames():

    now = datetime.now()

    timestamp = now.strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    original = (
        f"grabacion_{timestamp}_720p.mp4"
    )

    final = (
        f"grabacion_{timestamp}_1080p.mp4"
    )

    return original, final


# ============================================================
# GRABAR STREAM HLS
# ============================================================

def record_stream(output_file):

    print()
    print("=" * 70)
    print("1. INICIANDO GRABACIÓN HLS")
    print("=" * 70)

    print(f"URL:")
    print(M3U8_URL)

    print()
    print(
        f"Duración: "
        f"{RECORDING_DURATION} segundos"
    )

    print(
        f"Archivo: "
        f"{output_file}"
    )

    command = [
        "ffmpeg",

        # Sobrescribir si existe
        "-y",

        # Reconexión HTTP
        "-reconnect",
        "1",

        "-reconnect_streamed",
        "1",

        "-reconnect_delay_max",
        "10",

        # HLS
        "-i",
        M3U8_URL,

        # Una hora
        "-t",
        str(RECORDING_DURATION),

        # Copiar sin recodificar
        "-c:v",
        "copy",

        "-c:a",
        "copy",

        # Convertir AAC ADTS a formato MP4
        "-bsf:a",
        "aac_adtstoasc",

        # Facilita reproducción/streaming posterior
        "-movflags",
        "+faststart",

        output_file
    ]

    print()
    print("Ejecutando FFmpeg...")
    print()

    try:

        result = subprocess.run(
            command,
            check=False
        )

    except FileNotFoundError:

        print(
            "ERROR: FFmpeg no está instalado."
        )

        return False

    if result.returncode != 0:

        print(
            "ERROR: FFmpeg terminó con "
            f"código {result.returncode}"
        )

        return False

    print()
    print(
        "Grabación terminada correctamente."
    )

    return True


# ============================================================
# VALIDAR ARCHIVO
# ============================================================

def validate_file(filename):

    path = Path(filename)

    if not path.exists():

        print(
            f"ERROR: no existe "
            f"{filename}"
        )

        return False

    size = path.stat().st_size

    if size <= 0:

        print(
            f"ERROR: {filename} "
            "está vacío."
        )

        return False

    size_mb = size / (
        1024 * 1024
    )

    size_gb = size / (
        1024 * 1024 * 1024
    )

    print()
    print(
        f"Archivo: {filename}"
    )

    print(
        f"Tamaño: {size_mb:.2f} MB "
        f"({size_gb:.2f} GB)"
    )

    return True


# ============================================================
# UPSCALE 720p → 1080p
# ============================================================

def upscale_to_1080p(
    input_file,
    output_file
):

    print()
    print("=" * 70)
    print("2. UPSCALE 720p → 1080p")
    print("=" * 70)

    print(
        f"Entrada: {input_file}"
    )

    print(
        f"Salida: {output_file}"
    )

    print()
    print(
        "Escalador: Lanczos"
    )

    print(
        "Codec: H.264"
    )

    print(
        "CRF: 17"
    )

    command = [
        "ffmpeg",

        "-y",

        "-i",
        input_file,

        # ----------------------------------------------------
        # ESCALADO
        # ----------------------------------------------------

        "-vf",
        "scale=1920:1080:flags=lanczos",

        # ----------------------------------------------------
        # VIDEO
        # ----------------------------------------------------

        "-c:v",
        "libx264",

        # Calidad
        "-crf",
        "17",

        # Mejor compresión.
        # slow = más tiempo, mejor compresión.
        "-preset",
        "slow",

        # ----------------------------------------------------
        # AUDIO
        # ----------------------------------------------------

        "-c:a",
        "copy",

        # ----------------------------------------------------
        # MP4
        # ----------------------------------------------------

        "-movflags",
        "+faststart",

        output_file
    ]

    print()
    print(
        "Procesando video..."
    )

    print(
        "Esto puede tardar bastante."
    )

    print()

    try:

        result = subprocess.run(
            command,
            check=False
        )

    except FileNotFoundError:

        print(
            "ERROR: FFmpeg no está instalado."
        )

        return False

    if result.returncode != 0:

        print(
            "ERROR: el upscale falló."
        )

        print(
            f"Código: {result.returncode}"
        )

        return False

    print()
    print(
        "Upscale terminado correctamente."
    )

    return True


# ============================================================
# SUBIR A FILESTER
# ============================================================

def upload_to_filester(
    filename
):

    print()
    print("=" * 70)
    print("3. SUBIENDO A FILESTER")
    print("=" * 70)

    headers = {
        "Authorization":
            f"Bearer {FILESTER_API_KEY}"
    }

    # Carpeta opcional
    if FILESTER_FOLDER_ID:

        headers[
            "X-Folder-ID"
        ] = FILESTER_FOLDER_ID

    for attempt in range(
        1,
        UPLOAD_RETRIES + 1
    ):

        print()
        print(
            f"Intento "
            f"{attempt}/"
            f"{UPLOAD_RETRIES}"
        )

        try:

            with open(
                filename,
                "rb"
            ) as file:

                files = {
                    "file": (
                        os.path.basename(
                            filename
                        ),
                        file,
                        "video/mp4"
                    )
                }

                response = requests.post(
                    FILESTER_UPLOAD_URL,
                    headers=headers,
                    files=files,

                    # Una hora de timeout.
                    timeout=3600
                )

            print(
                f"HTTP: "
                f"{response.status_code}"
            )

            # Intentar leer JSON
            try:

                data = response.json()

            except ValueError:

                data = None

            if response.ok:

                print()
                print(
                    "UPLOAD CORRECTO"
                )

                if data:

                    print()
                    print(
                        "Respuesta de Filester:"
                    )

                    print(data)

                    if data.get("url"):

                        print()
                        print(
                            "URL DE LA GRABACIÓN:"
                        )

                        print(
                            data["url"]
                        )

                return True

            print()
            print(
                "La subida falló."
            )

            if data:

                print(data)

            else:

                print(
                    response.text[:1000]
                )

        except requests.RequestException as error:

            print()
            print(
                "Error de conexión:"
            )

            print(error)

        # ----------------------------------------------------
        # REINTENTO
        # ----------------------------------------------------

        if attempt < UPLOAD_RETRIES:

            print()
            print(
                f"Esperando "
                f"{RETRY_DELAY} segundos..."
            )

            time.sleep(
                RETRY_DELAY
            )

    print()
    print(
        "ERROR: no se pudo subir "
        "el archivo."
    )

    return False


# ============================================================
# ELIMINAR ARCHIVO
# ============================================================

def delete_file(filename):

    try:

        os.remove(filename)

        print(
            f"Archivo eliminado: "
            f"{filename}"
        )

    except OSError as error:

        print(
            f"No se pudo eliminar "
            f"{filename}"
        )

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

    original_file, final_file = (
        create_filenames()
    )

    print()
    print(
        f"Original: {original_file}"
    )

    print(
        f"Final:    {final_file}"
    )

    # ========================================================
    # PASO 1
    # GRABAR 720P
    # ========================================================

    success = record_stream(
        original_file
    )

    if not success:

        print()
        print(
            "La grabación falló."
        )

        sys.exit(1)

    if not validate_file(
        original_file
    ):

        sys.exit(1)

    # ========================================================
    # PASO 2
    # UPSCALE
    # ========================================================

    success = upscale_to_1080p(
        original_file,
        final_file
    )

    if not success:

        print()
        print(
            "El procesamiento falló."
        )

        print(
            "Se conserva el archivo "
            f"original: {original_file}"
        )

        sys.exit(1)

    if not validate_file(
        final_file
    ):

        sys.exit(1)

    # ========================================================
    # PASO 3
    # SUBIR 1080P
    # ========================================================

    success = upload_to_filester(
        final_file
    )

    if not success:

        print()
        print(
            "La subida falló."
        )

        print(
            "Los archivos locales "
            "NO serán eliminados."
        )

        sys.exit(1)

    # ========================================================
    # PASO 4
    # LIMPIEZA
    # ========================================================

    print()
    print("=" * 70)
    print("4. LIMPIANDO ARCHIVOS")
    print("=" * 70)

    # El original 720p ya no es necesario
    delete_file(
        original_file
    )

    # El 1080p también puede eliminarse
    # después de confirmar el upload
    delete_file(
        final_file
    )

    print()
    print("=" * 70)
    print("PROCESO COMPLETADO")
    print("=" * 70)
    print()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
