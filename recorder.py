import os
import re
import sys
import time
import json
import base64
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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

CUSTOM_FILE_NAME = os.environ.get("CUSTOM_FILE_NAME", "").strip()

CHECK_INTERVAL_SECONDS = 3600  # Aviso de estado cada 1 hora
POLL_INTERVAL_SECONDS = 30     # Cada cuánto se revisa si ffmpeg sigue vivo
SAFETY_MARGIN_SECONDS = 300    # Margen extra antes de matar ffmpeg por si tarda en cerrar


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(message):
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
    except requests.RequestException as error:
        # Si Telegram falla, no debe tumbar el proceso principal
        print(f"AVISO: no se pudo enviar mensaje a Telegram: {error}")


# ============================================================
# CREAR NOMBRE DE ARCHIVO
# ============================================================
def sanitize_filename(name):
    # Quita caracteres inválidos para nombres de archivo en Windows/Linux
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip().strip(".")
    return name

def create_filename():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if CUSTOM_FILE_NAME:
        clean_name = sanitize_filename(CUSTOM_FILE_NAME)
        if clean_name:
            # Se agrega el timestamp también para evitar sobreescribir archivos
            return f"{clean_name}_{timestamp}.mp4"

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
        raise RuntimeError("La variable STREAM_PAGE_URL no está configurada.")

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

    m3u8_url = None

    # --------------------------------------------------------
    # 1. INTENTO DE DESOFUSCACIÓN (Base64 + Resta matemática)
    # --------------------------------------------------------
    try:
        # Buscamos el bloque del array ofuscado evitando corchetes literales en el regex
        # Se usa \x5B para [ y \x5D para ] y así no se rompe la interfaz web.
        array_match = re.search(r'([a-zA-Z0-9_]+)\s*=\s*(\x5B\x5B\d+,\s*"[A-Za-z0-9+/=]+"\x5D[^;]+\x5D);', html)
        
        if array_match:
            array_name = array_match.group(1)
            array_data = json.loads(array_match.group(2))
            
            # Ordenar por el primer elemento (índice)
            array_data.sort(key=lambda x: x[0])
            
            # Buscar la definición de la llave (ej: var k=fXlED()+ugXDW();) 
            # Aseguramos que sea la llave correspondiente a nuestro array
            k_pattern = re.escape(array_name) + r'\.sort[^\;]+\;\s*var\s+[a-zA-Z0-9_]+\s*=\s*([a-zA-Z0-9_]+)\(\)\s*\+\s*([a-zA-Z0-9_]+)\(\)\s*;'
            k_match = re.search(k_pattern, html)
            
            if k_match:
                func1_name = k_match.group(1)
                func2_name = k_match.group(2)
                
                # Extraer los números que retornan ambas funciones
                func1_match = re.search(r'function\s+' + re.escape(func1_name) + r'\(\)\s*\{\s*return\s+(\d+)\s*;\s*\}', html)
                func2_match = re.search(r'function\s+' + re.escape(func2_name) + r'\(\)\s*\{\s*return\s+(\d+)\s*;\s*\}', html)
                
                if func1_match and func2_match:
                    # Sumamos los valores para obtener la llave 'k'
                    k_val = int(func1_match.group(1)) + int(func2_match.group(2))
                    
                    decoded_url = ""
                    for item in array_data:
                        encoded_val = item[1]
                        
                        # Decodificar Base64
                        decoded_bytes = base64.b64decode(encoded_val)
                        decoded_str = decoded_bytes.decode('utf-8', errors='ignore')
                        
                        # Extraer solo los dígitos (/\D/g en JS)
                        digits_only = re.sub(r'\D', '', decoded_str)
                        if digits_only:
                            # Restar la llave y convertir de código a carácter
                            char_code = int(digits_only) - k_val
                            decoded_url += chr(char_code)
                            
                    if ".m3u8" in decoded_url:
                        m3u8_url = decoded_url
                        print("¡URL desofuscada con éxito usando el nuevo método matemático!")
    except Exception as e:
        print(f"Advertencia: Falló el intento de desofuscación: {e}")

    # --------------------------------------------------------
    # 2. FALLBACK A LOS PATRONES ANTIGUOS DIRECTOS
    # --------------------------------------------------------
    if not m3u8_url:
        patterns = [
            r'playbackURL\s*=\s*"([^"]+)"',
            r"playbackURL\s*=\s*'([^']+)'",
            r'"playbackURL"\s*:\s*"([^"]+)"',
            r"'playbackURL'\s*:\s*'([^']+)'",
            r'https?:\\?/\\?/[^"\']+?\.m3u8[^"\']*',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                m3u8_url = match.group(1) if len(match.groups()) > 0 else match.group(0)
                break

    if not m3u8_url:
        raise RuntimeError("No se encontró la URL .m3u8 en el HTML (Ni ofuscada ni en texto plano).")

    m3u8_url = javascript_unescape(m3u8_url).strip()
    print(f"M3U8 Extraído: {m3u8_url}")
    return m3u8_url


# ============================================================
# GRABAR STREAM HLS
# ============================================================
def record_stream(m3u8_url, output_file):
    print("\n" + "=" * 70 + "\n1. INICIANDO GRABACIÓN HLS\n" + "=" * 70)
    print(f"Duración objetivo: {RECORDING_DURATION} segundos")
    print(f"Archivo: {output_file}")

    command = [
        "ffmpeg",
        "-hide_banner", "-y",
        "-fflags", "+genpts",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-i", m3u8_url,
        "-t", str(RECORDING_DURATION),
        "-c:v", "copy",
        "-c:a", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        output_file,
    ]

    print("\nEjecutando FFmpeg...\n")

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise RuntimeError("FFmpeg no está instalado.")

    send_telegram(
        f"🎬 **Grabación iniciada**\n"
        f"Archivo: {output_file}\n"
        f"Duración objetivo: {RECORDING_DURATION // 60} min"
    )

    start_time = time.time()
    last_check = start_time
    last_size = 0
    max_seconds = RECORDING_DURATION + SAFETY_MARGIN_SECONDS
    killed_for_timeout = False

    while True:
        ret = process.poll()
        if ret is not None:
            break

        now = time.time()
        elapsed = now - start_time

        # Si ffmpeg no cerró solo tras duración + margen, lo matamos
        if elapsed > max_seconds:
            process.kill()
            killed_for_timeout = True
            break

        # Aviso periódico de estado
        if now - last_check >= CHECK_INTERVAL_SECONDS:
            current_size = Path(output_file).stat().st_size if Path(output_file).exists() else 0
            growing = current_size > last_size
            size_mb = current_size / (1024 * 1024)
            elapsed_min = int(elapsed / 60)

            process_status = "✅ corriendo" if process.poll() is None else "❌ detenido"
            size_status = "✅ creciendo" if growing else "⚠️ NO está creciendo"

            send_telegram(
                f"🎥 **Grabación en curso** ({elapsed_min} min)\n"
                f"Proceso: {process_status}\n"
                f"Tamaño: {size_mb:.1f} MB — {size_status}"
            )

            last_size = current_size
            last_check = now

        time.sleep(POLL_INTERVAL_SECONDS)

    if killed_for_timeout:
        raise RuntimeError(
            f"FFmpeg no terminó tras {max_seconds} segundos y fue detenido manualmente."
        )

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg terminó con código {process.returncode}")

    print("\nGrabación terminada correctamente.")
    return True


# ============================================================
# VALIDAR ARCHIVO
# ============================================================
def validate_file(filename):
    path = Path(filename)

    if not path.exists():
        raise RuntimeError(f"No existe el archivo {filename}")

    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError(f"El archivo {filename} está vacío.")

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

    last_error = None

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
                url = data.get("url") if data else None
                if url:
                    print(f"\nURL DE LA GRABACIÓN:\n{url}")
                return True, url

            last_error = data if data else response.text[:1000]
            print("\nLa subida falló.")
            print(last_error)

        except requests.RequestException as error:
            last_error = str(error)
            print("\nError de conexión:")
            print(error)

        if attempt < UPLOAD_RETRIES:
            print(f"\nEsperando {RETRY_DELAY} segundos...")
            time.sleep(RETRY_DELAY)

    raise RuntimeError(f"No se pudo subir el archivo tras {UPLOAD_RETRIES} intentos. Último error: {last_error}")


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

    try:
        m3u8_url = get_m3u8_url()
        record_stream(m3u8_url, video_file)
        validate_file(video_file)
        success, url = upload_to_filester(video_file)

        print("\n" + "=" * 70 + "\n3. LIMPIANDO ARCHIVOS\n" + "=" * 70)
        delete_file(video_file)

        if url:
            send_telegram(f"✅ **Proceso completado**\n\nEnlace: {url}")
        else:
            send_telegram(
                "✅ **Proceso completado**\n\n"
                "Subida correcta, pero Filester no devolvió un enlace en la respuesta. "
                "Revisa el panel de Filester."
            )

        print("\n" + "=" * 70 + "\nPROCESO COMPLETADO\n" + "=" * 70 + "\n")

    except Exception as error:
        print(f"\nERROR FATAL: {error}")
        send_telegram(f"❌ **Error en la grabación**\n\n{error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
