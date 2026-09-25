import os
import re
import time
import base64
import subprocess
from pathlib import Path
import requests

STREAM_PAGE_URL=os.getenv("STREAM_PAGE_URL","")
FILESTER_API_KEY=os.getenv("FILESTER_API_KEY","")
FILESTER_FOLDER_ID=os.getenv("FILESTER_FOLDER_ID","")
RECORDING_DURATION=int(os.getenv("RECORDING_DURATION","30"))
UPLOAD_RETRIES=int(os.getenv("UPLOAD_RETRIES","5"))
RETRY_DELAY=int(os.getenv("RETRY_DELAY","30"))
CUSTOM_FILE_NAME=os.getenv("CUSTOM_FILE_NAME","")
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","")

FILESTER_UPLOAD_URL="https://u1.filester.me/api/v1/upload"

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id":TELEGRAM_CHAT_ID,"text":message},
            timeout=30
        )
    except Exception as error:
        print(f"Telegram falló: {error}")

def sanitize_filename(name):
    name=re.sub(r'[<>:"/\\|?*]','_',name or "")
    return re.sub(r'\s+',' ',name).strip()

def create_filename():
    name=sanitize_filename(CUSTOM_FILE_NAME)
    if not name:
        name=f"grabacion_{time.strftime('%Y%m%d_%H%M%S')}"
    return f"{name}.mp4"

def javascript_unescape(value):
    try:
        return bytes(value,"utf-8").decode("unicode_escape")
    except Exception:
        return value

def get_m3u8_url():
    print("\n"+"="*70+"\n1. OBTENIENDO URL M3U8\n"+"="*70)

    response=requests.get(STREAM_PAGE_URL,timeout=60)
    response.raise_for_status()
    html=javascript_unescape(response.text)

    patterns=[
        r'https?[^"\']+\.m3u8[^"\']*',
        r'https?:\\/\\/[^"\']+\.m3u8[^"\']*',
        r'"file"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"'
    ]

    for pattern in patterns:
        match=re.search(pattern,html,re.I)
        if match:
            url=match.group(1) if match.lastindex else match.group(0)
            url=url.replace("\\/","/")
            print(f"M3U8 encontrada: {url}")
            return url

    encoded=re.findall(r'([A-Za-z0-9+/]{40,}={0,2})',html)

    for value in encoded:
        try:
            decoded=base64.b64decode(value).decode("utf-8")
            numbers=re.findall(r'\d+',decoded)

            if numbers:
                result=""
                for number in numbers:
                    try:
                        result+=chr(int(number)-3)
                    except Exception:
                        pass

                match=re.search(r'https?[^"\']+\.m3u8[^"\']*',result)

                if match:
                    url=match.group(0)
                    print(f"M3U8 encontrada: {url}")
                    return url
        except Exception:
            continue

    raise RuntimeError("No se encontró ninguna URL M3U8.")

def record_stream(m3u8_url,output_file):
    print("\n"+"="*70+"\n2. GRABANDO STREAM\n"+"="*70)
    print(f"Duración: {RECORDING_DURATION} segundos")
    print(f"Archivo: {output_file}")

    command=[
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-fflags","+genpts",
        "-reconnect","1",
        "-reconnect_streamed","1",
        "-reconnect_delay_max","10",
        "-i",m3u8_url,
        "-t",str(RECORDING_DURATION),
        "-c:v","copy",
        "-c:a","copy",
        "-bsf:a","aac_adtstoasc",
        "-movflags","+faststart",
        output_file
    ]

    result=subprocess.run(command)
    if result.returncode!=0:
        raise RuntimeError("FFmpeg terminó con error.")

def validate_file(filename):
    path=Path(filename)

    if not path.exists():
        raise RuntimeError("El archivo no existe.")

    size=path.stat().st_size

    if size<1024:
        raise RuntimeError("El archivo es demasiado pequeño.")

    print(f"Archivo válido: {size/1024/1024:.2f} MB")

def upload_to_filester(filename):
    print("\n"+"="*70+"\n3. SUBIENDO A FILESTER\n"+"="*70)

    if not FILESTER_API_KEY:
        raise RuntimeError("Falta FILESTER_API_KEY.")

    last_error=None

    for attempt in range(1,UPLOAD_RETRIES+1):
        print(f"\nIntento {attempt}/{UPLOAD_RETRIES}")

        try:
            headers={
                "Authorization":f"Bearer {FILESTER_API_KEY}"
            }

            data={}

            if FILESTER_FOLDER_ID:
                data["folder_id"]=FILESTER_FOLDER_ID

            with open(filename,"rb") as file:
                files={
                    "file":(
                        os.path.basename(filename),
                        file,
                        "video/mp4"
                    )
                }

                response=requests.post(
                    FILESTER_UPLOAD_URL,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=3600
                )

            print(f"HTTP: {response.status_code}")
            response.raise_for_status()

            result=response.json()
            print(f"Respuesta Filester: {result}")

            url=(
                result.get("url")
                or result.get("download_url")
                or result.get("file_url")
                or result.get("link")
            )

            if not url:
                raise RuntimeError(
                    f"Filester no devolvió una URL: {result}"
                )

            print("\nUPLOAD CORRECTO")
            print(f"URL: {url}")

            return True,url

        except Exception as error:
            last_error=str(error)
            print(f"La subida falló:\n{error}")

        if attempt<UPLOAD_RETRIES:
            time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"Filester falló tras {UPLOAD_RETRIES} intentos. "
        f"Último error: {last_error}"
    )

def delete_file(filename):
    try:
        if os.path.exists(filename):
            os.remove(filename)
            print(f"Archivo eliminado: {filename}")
    except Exception as error:
        print(f"No se pudo eliminar el archivo: {error}")

def main():
    filename=create_filename()

    try:
        if not STREAM_PAGE_URL:
            raise RuntimeError("Falta STREAM_PAGE_URL.")

        if not FILESTER_API_KEY:
            raise RuntimeError("Falta FILESTER_API_KEY.")

        send_telegram(
            f"Grabación iniciada.\nArchivo: {filename}\n"
            f"Duración: {RECORDING_DURATION} segundos."
        )

        m3u8_url=get_m3u8_url()
        record_stream(m3u8_url,filename)
        validate_file(filename)

        _,url=upload_to_filester(filename)

        send_telegram(
            f"Grabación completada.\n"
            f"Archivo: {filename}\n"
            f"Filester:\n{url}"
        )

        print("\n"+"="*70)
        print("PROCESO COMPLETADO")
        print("="*70)

    except Exception as error:
        print("\n"+"="*70)
        print("ERROR")
        print("="*70)
        print(error)

        send_telegram(
            f"Error en la grabación:\n{error}"
        )

        raise

    finally:
        delete_file(filename)

if __name__=="__main__":
    main()
