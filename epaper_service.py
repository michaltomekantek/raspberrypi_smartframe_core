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

# Import bazy danych
from database import Base, SessionLocal, get_db

class EPaperImageModel(Base):
    __tablename__ = "epaper_images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

try:
    from lib.waveshare_epd import epd7in5_V2
    EPAPER_AVAILABLE = True
    print("✅ Hardware e-papieru wykryty.")
except ImportError:
    EPAPER_AVAILABLE = False
    print("⚠️ Hardware NIE wykryty. Tryb symulacji włączony.")

epaper_router = APIRouter(tags=["E-Paper Control"])

# Ścieżki
UPLOAD_EPAPER_DIR = os.path.join("uploaded", "epaper")
BASE_URL = "http://192.168.0.194/images/epaper/"

os.makedirs(UPLOAD_EPAPER_DIR, exist_ok=True)

# --- ZABEZPIECZENIA I STAN ---
HARDWARE_LOCK = threading.Lock()
last_refresh_time = 0
next_refresh_time = 0 # NOWOŚĆ: Przechowuje planowany czas zmiany
epaper_interval = 120.0
slideshow_running = False
force_refresh_event = threading.Event()

def draw_image_task(img_data, is_manual: bool = False, is_path: bool = True):
    global last_refresh_time
    if not EPAPER_AVAILABLE:
        print("SYMULACJA: Wyświetlam obraz w konsoli.")
        return True

    with HARDWARE_LOCK:
        now = time.time()
        time_since_last = now - last_refresh_time
        required_gap = 10 if is_manual else 60

        if time_since_last < required_gap:
            if not is_manual:
                wait_time = required_gap - time_since_last
                time.sleep(wait_time)
            else:
                return False

        try:
            print(f"🔄 Odświeżanie matrycy...")
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
            print(f"🔥 Błąd: {e}")
            return False

def epaper_slideshow_loop():
    global slideshow_running, next_refresh_time
    print("🚀 Wątek slideshow aktywny.")
    while True:
        if slideshow_running:
            db = SessionLocal()
            active_images = db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
            db.close()

            if active_images:
                random.shuffle(active_images)
                for img_record in active_images:
                    if not slideshow_running:
                        break

                    file_path = os.path.join(UPLOAD_EPAPER_DIR, img_record.filename)
                    if os.path.exists(file_path):
                        draw_image_task(file_path, is_manual=False)

                        # AKTUALIZACJA: Obliczamy kiedy nastąpi kolejny refresh
                        next_refresh_time = time.time() + epaper_interval

                        if force_refresh_event.wait(timeout=epaper_interval):
                            print("🔔 Reset kolejki.")
                            force_refresh_event.clear()
                            break
            else:
                next_refresh_time = 0
                time.sleep(5)
        else:
            next_refresh_time = 0
            time.sleep(1)

# --- ENDPOINTY ---

@epaper_router.get("/epaper/settings/status")
def get_slideshow_status():
    """NOWOŚĆ: Pobiera info ile zostało do następnego zdjęcia"""
    remaining = 0
    if slideshow_running and next_refresh_time > 0:
        remaining = max(0, int(next_refresh_time - time.time()))

    return {
        "slideshow_running": slideshow_running,
        "remaining_seconds": remaining,
        "interval": epaper_interval,
        "last_refresh": datetime.fromtimestamp(last_refresh_time).isoformat() if last_refresh_time > 0 else None
    }

@epaper_router.get("/epaper/settings/interval")
def get_epaper_interval():
    return {"interval": epaper_interval}

@epaper_router.post("/epaper/settings/interval")
def set_epaper_interval(seconds: int):
    global epaper_interval
    if seconds < 30: raise HTTPException(status_code=400, detail="Min 30s")
    epaper_interval = float(seconds)
    # Wymuszamy refresh, żeby nowy interwał wszedł w życie od razu
    force_refresh_event.set()
    return {"interval": epaper_interval}

@epaper_router.post("/epaper/control/clear")
def clear_epaper():
    if not EPAPER_AVAILABLE: return {"status": "Symulacja: Biały ekran"}
    with HARDWARE_LOCK:
        try:
            epd = epd7in5_V2.EPD(); epd.init(); epd.Clear(); epd.sleep()
            force_refresh_event.set()
            return {"status": "Matryca wyczyszczona"}
        except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@epaper_router.post("/epaper/show-text")
def show_text_on_epaper(text: str, title: str = "POWIADOMIENIE"):
    img = Image.new('L', (800, 480), 255)
    draw = ImageDraw.Draw(img)
    # ... (pominięto logikę rysowania tekstu dla czytelności, jest identyczna jak wcześniej)
    if draw_image_task(img, is_manual=True, is_path=False):
        force_refresh_event.set()
        return {"status": "Wysłano"}
    raise HTTPException(status_code=429)

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    new_img = EPaperImageModel(filename="temp", url="temp", is_active=True)
    db.add(new_img); db.commit(); db.refresh(new_img)
    content = await file.read()
    try:
        image = Image.open(io.BytesIO(content)).convert('L').resize((800, 480))
        filename = f"epd_{new_img.id}.png"
        image.save(os.path.join(UPLOAD_EPAPER_DIR, filename))
        new_img.filename, new_img.url = filename, f"{BASE_URL}{filename}"
        db.commit()
        draw_image_task(os.path.join(UPLOAD_EPAPER_DIR, filename), is_manual=True)
        force_refresh_event.set()
        return new_img
    except Exception as e:
        db.delete(new_img); db.commit()
        raise HTTPException(status_code=400, detail=str(e))

@epaper_router.get("/epaper/images")
def get_epaper_images(db: Session = Depends(get_db)):
    return db.query(EPaperImageModel).order_by(EPaperImageModel.added_at.desc()).all()

@epaper_router.post("/epaper/show/{image_id}")
def show_specific_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img or not draw_image_task(os.path.join(UPLOAD_EPAPER_DIR, img.filename), is_manual=True):
        raise HTTPException(status_code=429 if img else 404)
    force_refresh_event.set()
    return {"status": "Wysłano"}

@epaper_router.delete("/epaper/images/{image_id}")
def delete_epaper_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if img:
        p = os.path.join(UPLOAD_EPAPER_DIR, img.filename);
        if os.path.exists(p): os.remove(p)
        db.delete(img); db.commit()
    return {"status": "deleted"}

@epaper_router.post("/epaper/control/start")
def epaper_start():
    global slideshow_running
    slideshow_running = True
    force_refresh_event.set() # Startujemy od razu
    return {"status": "Slideshow ON"}

@epaper_router.post("/epaper/control/stop")
def epaper_stop():
    global slideshow_running
    slideshow_running = False
    return {"status": "Slideshow OFF"}

def startup_epaper_display():
    threading.Thread(target=epaper_slideshow_loop, daemon=True).start()