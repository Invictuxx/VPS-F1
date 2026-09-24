import os,re,sys,time,json,base64,subprocess
from datetime import datetime
from pathlib import Path
import requests

STREAM_PAGE_URL=os.environ.get("STREAM_PAGE_URL","")
UPLOAD_SERVER=os.environ.get("UPLOAD_SERVER","filester").lower().strip()
FILESTER_API_KEY=os.environ.get("FILESTER_API_KEY")
FILESTER_FOLDER_ID=os.environ.get("FILESTER_FOLDER_ID")
VIKINGFILE_USER=os.environ.get("VIKINGFILE_USER","")
VIKINGFILE_PATH=os.environ.get("VIKINGFILE_PATH","")
RECORDING_DURATION=int(os.environ.get("RECORDING_DURATION","30"))
UPLOAD_RETRIES=int(os.environ.get("UPLOAD_RETRIES","5"))
RETRY_DELAY=int(os.environ.get("RETRY_DELAY","30"))
CUSTOM_FILE_NAME=os.environ.get("CUSTOM_FILE_NAME","").strip()
TELEGRAM_BOT_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID=os.environ.get("TELEGRAM_CHAT_ID","")
TELEGRAM_ENABLED=bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

FILESTER_UPLOAD_URL="https://u1.filester.me/api/v1/upload"
VIKINGFILE_GET_URL="https://vikingfile.com/api/get-upload-url"
VIKINGFILE_COMPLETE_URL="https://vikingfile.com/api/complete-upload"

CHECK_INTERVAL_SECONDS=3600
POLL_INTERVAL_SECONDS=30
SAFETY_MARGIN_SECONDS=300


def send_telegram(message):
    if not TELEGRAM_ENABLED:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id":TELEGRAM_CHAT_ID,
                "text":message,
                "parse_mode":"HTML",
                "disable_web_page_preview":False
            },
            timeout=15
        )
    except requests.RequestException as error:
        print(f"AVISO: Telegram: {error}")


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]',"",name).strip().strip(".")


def create_filename():
    timestamp=datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if CUSTOM_FILE_NAME:
        name=sanitize_filename(CUSTOM_FILE_NAME)
        if name:
            return f"{name}_{timestamp}.mp4"
    return f"grabacion_{timestamp}.mp4"


def javascript_unescape(value):
    return value.replace("\\/","/").replace("\\u0026","&").replace("\\x26","&")


def get_m3u8_url():
    print("\n"+"="*70+"\n0. OBTENIENDO ENLACE M3U8\n"+"="*70)

    if not STREAM_PAGE_URL:
        raise RuntimeError("Falta STREAM_PAGE_URL.")

    headers={
        "User-Agent":(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    response=requests.get(
        STREAM_PAGE_URL,
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    html=response.text
    m3u8_url=None

    try:
        array_match=re.search(
            r'([a-zA-Z0-9_]+)\s*=\s*(\x5B\x5B\d+,\s*"[A-Za-z0-9+/=]+"\x5D[^;]+\x5D);',
            html
        )

        if array_match:
            array_name=array_match.group(1)
            array_data=json.loads(array_match.group(2))
            array_data.sort(key=lambda x:x[0])

            k_pattern=(
                re.escape(array_name)+
                r'\.sort[^\;]+\;\s*var\s+[a-zA-Z0-9_]+\s*=\s*'
                r'([a-zA-Z0-9_]+)\(\)\s*\+\s*([a-zA-Z0-9_]+)\(\)\s*;'
            )

            k_match=re.search(k_pattern,html)

            if k_match:
                f1=k_match.group(1)
                f2=k_match.group(2)

                m1=re.search(
                    r'function\s+'+re.escape(f1)+
                    r'\(\)\s*\{\s*return\s+(\d+)\s*;\s*\}',
                    html
                )

                m2=re.search(
                    r'function\s+'+re.escape(f2)+
                    r'\(\)\s*\{\s*return\s+(\d+)\s*;\s*\}',
                    html
                )

                if m1 and m2:
                    k=int(m1.group(1))+int(m2.group(1))
                    decoded_url=""

                    for item in array_data:
                        raw=base64.b64decode(item[1])
                        text=raw.decode("utf-8",errors="ignore")
                        digits=re.sub(r"\D","",text)

                        if digits:
                            decoded_url+=chr(int(digits)-k)

                    if ".m3u8" in decoded_url:
                        m3u8_url=decoded_url
                        print("URL desofuscada correctamente.")

    except Exception as error:
        print(f"Advertencia: falló la desofuscación: {error}")

    if not m3u8_url:
        patterns=[
            r'playbackURL\s*=\s*"([^"]+)"',
            r"playbackURL\s*=\s*'([^']+)'",
            r'"playbackURL"\s*:\s*"([^"]+)"',
            r"'playbackURL'\s*:\s*'([^']+)'",
            r'https?:\\?/\\?/[^"\']+?\.m3u8[^"\']*'
        ]

        for pattern in patterns:
            match=re.search(pattern,html,re.IGNORECASE)
            if match:
                m3u8_url=match.group(1) if match.groups() else match.group(0)
                break

    if not m3u8_url:
        raise RuntimeError("No se encontró la URL .m3u8.")

    m3u8_url=javascript_unescape(m3u8_url).strip()
    print(f"M3U8 Extraído: {m3u8_url}")
    return m3u8_url


def record_stream(m3u8_url,output_file):
    print("\n"+"="*70+"\n1. INICIANDO GRABACIÓN HLS\n"+"="*70)
    print(f"Duración: {RECORDING_DURATION} segundos")
    print(f"Archivo: {output_file}")

    command=[
        "ffmpeg","-hide_banner","-y",
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

    try:
        process=subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        raise RuntimeError("FFmpeg no está instalado.")

    send_telegram(
        "🎬 <b>Grabación iniciada</b>\n"
        f"Archivo: {output_file}\n"
        f"Duración: {RECORDING_DURATION//60} min"
    )

    start=time.time()
    last_check=start
    last_size=0
    max_seconds=RECORDING_DURATION+SAFETY_MARGIN_SECONDS

    while process.poll() is None:
        now=time.time()
        elapsed=now-start

        if elapsed>max_seconds:
            process.kill()
            raise RuntimeError(
                f"FFmpeg no terminó tras {max_seconds} segundos."
            )

        if now-last_check>=CHECK_INTERVAL_SECONDS:
            size=Path(output_file).stat().st_size if Path(output_file).exists() else 0
            growing=size>last_size
            size_mb=size/(1024*1024)

            send_telegram(
                "🎥 <b>Grabación en curso</b>\n"
                f"Tiempo: {int(elapsed/60)} min\n"
                f"Tamaño: {size_mb:.1f} MB\n"
                f"Estado: {'✅ creciendo' if growing else '⚠️ NO está creciendo'}"
            )

            last_size=size
            last_check=now

        time.sleep(POLL_INTERVAL_SECONDS)

    if process.returncode!=0:
        raise RuntimeError(
            f"FFmpeg terminó con código {process.returncode}"
        )

    print("\nGrabación terminada correctamente.")


def validate_file(filename):
    path=Path(filename)

    if not path.exists():
        raise RuntimeError(f"No existe el archivo {filename}.")

    size=path.stat().st_size

    if size<=0:
        raise RuntimeError(f"El archivo {filename} está vacío.")

    print(
        f"\nArchivo: {filename}\n"
        f"Tamaño: {size/(1024*1024):.2f} MB "
        f"({size/(1024*1024*1024):.2f} GB)"
    )


def upload_to_filester(filename):
    print("\n"+"="*70+"\n2. SUBIENDO A FILESTER\n"+"="*70)

    headers={"Authorization":f"Bearer {FILESTER_API_KEY}"}

    if FILESTER_FOLDER_ID:
        headers["X-Folder-ID"]=FILESTER_FOLDER_ID

    last_error=None

    for attempt in range(1,UPLOAD_RETRIES+1):
        print(f"\nIntento {attempt}/{UPLOAD_RETRIES}")

        try:
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
                    files=files,
                    timeout=3600
                )

            print(f"HTTP: {response.status_code}")

            try:
                data=response.json()
            except ValueError:
                data=None

            if response.ok:
                url=data.get("url") if data else None
                print("\nUPLOAD CORRECTO")

                if url:
                    print(f"\nURL:\n{url}")

                return True,url

            last_error=data if data else response.text[:1000]
            print(f"La subida falló:\n{last_error}")

        except requests.RequestException as error:
            last_error=str(error)
            print(f"Error de conexión:\n{error}")

        if attempt<UPLOAD_RETRIES:
            time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"Filester falló tras {UPLOAD_RETRIES} intentos. "
        f"Último error: {last_error}"
    )


def upload_to_vikingfile(filename):
    print("\n"+"="*70+"\n2. SUBIENDO A VIKINGFILE\n"+"="*70)

    file_size=os.path.getsize(filename)
    last_error=None

    for attempt in range(1,UPLOAD_RETRIES+1):
        print(f"\nIntento {attempt}/{UPLOAD_RETRIES}")

        try:
            response=requests.post(
                VIKINGFILE_GET_URL,
                data={"size":file_size},
                timeout=60
            )

            print(f"Get upload URL HTTP: {response.status_code}")
            response.raise_for_status()

            upload_data=response.json()
            upload_id=upload_data["uploadId"]
            key=upload_data["key"]
            urls=upload_data["urls"]
            part_size=upload_data.get("partSize")

            if not part_size:
                raise RuntimeError(
                    "VikingFile no devolvió partSize."
                )

            parts=[]

            with open(filename,"rb") as file:
                for index,upload_url in enumerate(urls):
                    part_number=index+1
                    print(
                        f"Subiendo parte "
                        f"{part_number}/{len(urls)}..."
                    )

                    data=file.read(part_size)

                    if not data:
                        break

                    upload_response=requests.put(
                        upload_url,
                        data=data,
                        timeout=3600
                    )

                    upload_response.raise_for_status()

                    etag=(
                        upload_response.headers.get("ETag")
                        or upload_response.headers.get("etag")
                    )

                    if not etag:
                        raise RuntimeError(
                            f"Falta ETag en la parte {part_number}."
                        )

                    parts.append({
                        "PartNumber":part_number,
                        "ETag":etag.strip('"')
                    })

            complete_data={
                "key":key,
                "uploadId":upload_id,
                "name":os.path.basename(filename),
                "parts":json.dumps(parts)
            }

            if VIKINGFILE_USER:
                complete_data["user"]=VIKINGFILE_USER

            if VIKINGFILE_PATH:
                complete_data["path"]=VIKINGFILE_PATH

            response=requests.post(
                VIKINGFILE_COMPLETE_URL,
                data=complete_data,
                timeout=300
            )

            print(
                f"Complete upload HTTP: "
                f"{response.status_code}"
            )

            response.raise_for_status()

            result=response.json()

            url=(
                result.get("url")
                or result.get("downloadUrl")
                or result.get("download_url")
            )

            print("\nUPLOAD CORRECTO")

            if url:
                print(f"\nURL:\n{url}")

            return True,url

        except (
            requests.RequestException,
            KeyError,
            ValueError
        ) as error:
            last_error=str(error)
            print(f"La subida falló:\n{error}")

        except Exception as error:
            last_error=str(error)
            print(f"Error durante la subida:\n{error}")

        if attempt<UPLOAD_RETRIES:
            time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"VikingFile falló tras {UPLOAD_RETRIES} intentos. "
        f"Último error: {last_error}"
    )


def upload_file(filename):
    if UPLOAD_SERVER=="filester":
        return upload_to_filester(filename)

    if UPLOAD_SERVER=="vikingfile":
        return upload_to_vikingfile(filename)

    raise RuntimeError(
        f"Servidor no válido: {UPLOAD_SERVER}"
    )


def delete_file(filename):
    try:
        os.remove(filename)
        print(f"Archivo eliminado: {filename}")
    except OSError as error:
        print(f"No se pudo eliminar {filename}: {error}")


def main():
    print("\n"+"="*70)
    print("HLS RECORDER")
    print("="*70)
    print(f"Servidor: {UPLOAD_SERVER}")

    video_file=create_filename()

    try:
        m3u8_url=get_m3u8_url()
        record_stream(m3u8_url,video_file)
        validate_file(video_file)

        success,url=upload_file(video_file)

        print("\n"+"="*70)
        print("3. LIMPIANDO ARCHIVOS")
        print("="*70)

        delete_file(video_file)

        if url:
            send_telegram(
                "✅ <b>Proceso completado</b>\n\n"
                f"Servidor: {UPLOAD_SERVER}\n"
                f"Enlace: {url}"
            )
        else:
            send_telegram(
                "✅ <b>Proceso completado</b>\n\n"
                f"Servidor: {UPLOAD_SERVER}\n"
                "La subida terminó sin enlace."
            )

        print("\n"+"="*70)
        print("PROCESO COMPLETADO")
        print("="*70)

    except Exception as error:
        print(f"\nERROR FATAL: {error}")

        send_telegram(
            "❌ <b>Error en la grabación</b>\n\n"
            f"{error}"
        )

        sys.exit(1)


if __name__=="__main__":
    main()
