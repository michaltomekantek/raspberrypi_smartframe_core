import os
import threading
import socket
import io
from datetime import datetime
from fastapi import APIRouter, UploadFile, File
from PIL import Image, ImageDraw, ImageFont

# Import sterownika
try:
    from lib.waveshare_epd import epd7in5_V2
    EPAPER_AVAILABLE = True
except ImportError:
    print("BŁĄD: Nie znaleziono folderu lib w projekcie!")
    EPAPER_AVAILABLE = False

epaper_router = APIRouter(tags=["E-Paper Control"])

def draw_text_task(text_to_display: str):
    """Funkcja do rysowania tekstu i statusu"""
    if not EPAPER_AVAILABLE: return
    try:
        epd = epd7in5_V2.EPD()
        epd.init()
        image = Image.new('1', (epd.width, epd.height), 255)
        draw = ImageDraw.Draw(image)

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        try:
            f_big = ImageFont.truetype(font_path, 40)
            f_sm = ImageFont.truetype(font_path, 20)
        except:
            f_big = ImageFont.load_default(); f_sm = ImageFont.load_default()

        draw.text((50, 40), "SMARTFRAME E-INK MODULE", font=f_sm, fill=0)
        draw.rectangle((50, 75, 750, 77), fill=0)
        draw.text((50, 150), text_to_display, font=f_big, fill=0)

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ip = socket.gethostbyname(socket.gethostname())
        draw.text((50, 400), f"Ostatnia aktualizacja: {now}", font=f_sm, fill=0)
        draw.text((50, 430), f"IP Malinki: {ip}", font=f_sm, fill=0)

        epd.display(epd.getbuffer(image))
        epd.sleep()
    except Exception as e:
        print(f"Błąd sprzętowy e-papieru: {e}")

def draw_image_task(img_bytes: bytes):
    """Funkcja do przetwarzania i wyświetlania zdjęć"""
    if not EPAPER_AVAILABLE: return
    try:
        epd = epd7in5_V2.EPD()
        image = Image.open(io.BytesIO(img_bytes))

        # Przetwarzanie pod e-papier
        image = image.convert('L')
        image = image.resize((epd.width, epd.height))
        image = image.convert('1', dither=Image.FLOYDSTEINBERG)

        epd.init()
        epd.display(epd.getbuffer(image))
        epd.sleep()
        print("Grafika wyświetlona poprawnie i ekran uśpiony.")
    except Exception as e:
        print(f"Błąd podczas renderowania obrazu: {e}")

# --- ENDPOINTY ---

@epaper_router.get("/epaper-refresh")
def refresh_epaper(msg: str = "SYSTEM ONLINE"):
    threading.Thread(target=draw_text_task, args=(msg,), daemon=True).start()
    return {"status": "Zlecenie wysłane", "wiadomosc": msg}

@epaper_router.post("/epaper-upload-image")
async def epaper_upload_image(file: UploadFile = File(...)):
    contents = await file.read()
    threading.Thread(target=draw_image_task, args=(contents,), daemon=True).start()
    return {"status": "Zdjęcie odebrane, trwa odświeżanie ekranu..."}

@epaper_router.get("/epaper-clear")
def clear_epaper():
    def clear_task():
        if not EPAPER_AVAILABLE: return
        epd = epd7in5_V2.EPD()
        epd.init()
        epd.Clear()
        epd.sleep()
    threading.Thread(target=clear_task, daemon=True).start()
    return {"status": "Czyszczenie ekranu..."}

# Funkcja uruchamiana przy starcie main.py - teraz tylko loguje start
def startup_epaper_display():
    if EPAPER_AVAILABLE:
        print("Moduł e-papieru gotowy. Oczekiwanie na wgranie zdjęcia lub komendę...")
        # Usunęliśmy stąd wywołanie draw_text_task, więc ekran nic nie zrobi przy starcie.