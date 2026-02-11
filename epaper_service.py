import os
import threading
import io
import time
import random
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import Session

# Import bazy danych (upewnij się, że plik database.py istnieje w tym samym folderze)
from database import Base, SessionLocal, get_db

class EPaperImageModel(Base):
    __tablename__ = "epaper_images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

# Próba inicjalizacji hardware'u
try:
    from lib.waveshare_epd import epd7in5_V2
    EPAPER_AVAILABLE = True
    print("✅ Hardware e-papieru wykryty.")
except ImportError:
    EPAPER_AVAILABLE = False
    print("⚠️ Hardware NIE wykryty. Tryb symulacji.")

epaper_router = APIRouter(tags=["E-Paper Control"])

# Konfiguracja ścieżek
UPLOAD_EPAPER_DIR = os.path.join("uploaded", "epaper")
BASE_URL = "http://192.168.0.194/images/epaper/"
os.makedirs(UPLOAD_EPAPER_DIR, exist_ok=True)

# --- MECHANIZM OPTYMALIZACJI (EVENTS) ---
HARDWARE_LOCK = threading.Lock()
slideshow_event = threading.Event()    # Blokuje wątek, gdy slideshow jest OFF
force_refresh_event = threading.Event() # Przerywa czekanie (np. zmiana interwału)

last_refresh_time = 0
next_refresh_time = 0
epaper_interval = 120.0
slideshow_running = False  # Startuje jako wyłączony, żeby nie mielić CPU na starcie
current_image = None
next_image = None

def draw_image_task(img_data, is_manual: bool = False, is_path: bool = True):
    global last_refresh_time
    if not EPAPER_AVAILABLE:
        print("SYMULACJA: Wyświetlam obraz.")
        return True

    with HARDWARE_LOCK:
        now = time.time()
        time_since_last = now - last_refresh_time
        required_gap = 10 if is_manual else 60

        if time_since_last < required_gap:
            if not is_manual:
                time.sleep(required_gap - time_since_last)
            else:
                return False

        try:
            epd = epd7in5_V2.EPD()
            epd.init()
            image = Image.open(img_data) if is_path else img_data
            if image.mode != '1':
                image = image.convert('L').convert('1', dither=Image.FLOYDSTEINBERG)
            epd.display(epd.getbuffer(image))
            epd.sleep()
            last_refresh_time = time.time()
            return True
        except Exception as e:
            print(f"🔥 Błąd matrycy: {e}")
            return False

def epaper_slideshow_loop():
    global current_image, next_image, next_refresh_time, slideshow_running
    print("🚀 Wątek e-papieru zainicjowany (oczekiwanie na sygnał startu).")

    while True:
        # Jeśli slideshow jest wyłączony, wątek TU ZAMARZA (0% CPU)
        if not slideshow_running:
            slideshow_event.wait()

        db = SessionLocal()
        try:
            active_images = db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
        finally:
            db.close()

        if active_images and slideshow_running:
            random.shuffle(active_images)
            for i in range(len(active_images)):
                if not slideshow_running:
                    break

                current_image = active_images[i]
                next_image = active_images[(i + 1) % len(active_images)]

                file_path = os.path.join(UPLOAD_EPAPER_DIR, current_image.filename)
                if os.path.exists(file_path):
                    draw_image_task(file_path, is_manual=False)

                    # Obliczamy kiedy następna zmiana
                    next_refresh_time = time.time() + epaper_interval

                    # Wątek zasypia na 'epaper_interval' LUB do czasu force_refresh_event.set()
                    interrupted = force_refresh_event.wait(timeout=epaper_interval)
                    force_refresh_event.clear()
                    if interrupted:
                        print("🔔 Odświeżanie wymuszone przed czasem.")
                        break
        else:
            # Jeśli brak zdjęć, poczekaj chwilę przed ponownym sprawdzeniem bazy
            time.sleep(10)

# --- ENDPOINTY ---

@epaper_router.get("/epaper/settings/status")
def get_slideshow_status():
    remaining = 0
    if slideshow_running and next_refresh_time > 0:
        remaining = max(0, int(next_refresh_time - time.time()))
    return {
        "slideshow_running": slideshow_running,
        "remaining_seconds": remaining,
        "interval": epaper_interval,
        "current_image": current_image,
        "next_image": next_image
    }

@epaper_router.post("/epaper/settings/interval")
def set_epaper_interval(seconds: int):
    global epaper_interval
    if seconds < 30:
        raise HTTPException(status_code=400, detail="Minimum 30s")
    epaper_interval = float(seconds)
    force_refresh_event.set() # Natychmiastowe zastosowanie nowego interwału
    return {"interval": epaper_interval}

@epaper_router.post("/epaper/control/start")
def epaper_start():
    global slideshow_running
    slideshow_running = True
    slideshow_event.set() # Budzimy wątek ze snu
    force_refresh_event.set() # Wymuszamy start pierwszego zdjęcia
    return {"status": "Slideshow ON"}

@epaper_router.post("/epaper/control/stop")
def epaper_stop():
    global slideshow_running
    slideshow_running = False
    slideshow_event.clear() # Wątek zasypia przy najbliższej okazji
    return {"status": "Slideshow OFF"}

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    new_img = EPaperImageModel(filename="temp", url="temp")
    db.add(new_img)
    db.commit()
    db.refresh(new_img)
    try:
        image = Image.open(io.BytesIO(content)).convert('L').resize((800, 480))
        filename = f"epd_{new_img.id}.png"
        save_path = os.path.join(UPLOAD_EPAPER_DIR, filename)
        image.save(save_path)
        new_img.filename, new_img.url = filename, f"{BASE_URL}{filename}"
        db.commit()
        draw_image_task(save_path, is_manual=True)
        force_refresh_event.set()
        return new_img
    except Exception as e:
        db.delete(new_img); db.commit()
        raise HTTPException(status_code=400, detail=str(e))

def startup_epaper_display():
    t = threading.Thread(target=epaper_slideshow_loop, daemon=True)
    t.start()