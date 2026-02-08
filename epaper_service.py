import os
import threading
import socket
from datetime import datetime
from fastapi import APIRouter
from PIL import Image, ImageDraw, ImageFont

# Import sterownika z Twojego folderu lib
try:
    from lib import epd7in5_V2
    EPAPER_AVAILABLE = True
except ImportError:
    print("BŁĄD: Nie znaleziono folderu lib w projekcie!")
    EPAPER_AVAILABLE = False

# Tworzymy router, który main.py sobie "pobierze"
epaper_router = APIRouter(tags=["E-Paper Control"])

def draw_task(text_to_display: str):
    """Funkcja wykonująca ciężką pracę rysowania na e-papierze"""
    if not EPAPER_AVAILABLE:
        print("E-papier niedostępny, przerywam rysowanie.")
        return

    try:
        print(f"Rozpoczynam odświeżanie e-papieru: {text_to_display}")
        epd = epd7in5_V2.EPD()
        epd.init()

        # Tworzymy obraz (800x480 dla Waveshare 7.5 V2)
        image = Image.new('1', (epd.width, epd.height), 255)
        draw = ImageDraw.Draw(image)

        # Ścieżka do czcionki systemowej
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        try:
            f_big = ImageFont.truetype(font_path, 40)
            f_sm = ImageFont.truetype(font_path, 20)
        except:
            f_big = ImageFont.load_default()
            f_sm = ImageFont.load_default()

        # Rysowanie interfejsu e-papieru
        draw.text((50, 40), "SMARTFRAME E-INK MODULE", font=f_sm, fill=0)
        draw.rectangle((50, 75, 750, 77), fill=0) # Linia oddzielająca

        draw.text((50, 150), text_to_display, font=f_big, fill=0)

        # Stopka z informacjami
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ip = socket.gethostbyname(socket.gethostname())
        draw.text((50, 400), f"Ostatnia aktualizacja: {now}", font=f_sm, fill=0)
        draw.text((50, 430), f"IP Malinki: {ip}", font=f_sm, fill=0)

        # Wysyłka do bufora i uśpienie panelu
        epd.display(epd.getbuffer(image))
        epd.sleep()
        print("E-papier zaktualizowany pomyślnie.")
    except Exception as e:
        print(f"Błąd sprzętowy e-papieru: {e}")

# --- ENDPOINTY DLA TWOJEGO API ---

@epaper_router.get("/epaper-refresh")
def refresh_epaper(msg: str = "SYSTEM ONLINE"):
    """Zmienia tekst na e-papierze przez URL: /epaper-refresh?msg=Twoj+Tekst"""
    threading.Thread(target=draw_task, args=(msg,), daemon=True).start()
    return {"status": "Zlecenie wysłane do e-papieru", "wiadomosc": msg}

# Funkcja uruchamiana raz przy starcie main.py
def startup_epaper_display():
    if EPAPER_AVAILABLE:
        threading.Thread(target=draw_task, args=("START SYSTEMU...",), daemon=True).start()