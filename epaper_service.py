import os
import threading
import io
import time
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image
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
except ImportError:
    EPAPER_AVAILABLE = False

epaper_router = APIRouter(tags=["E-Paper Control"])

# --- NOWE ŚCIEŻKI ZGODNE Z TWOIM POMYSŁEM ---
# Folder fizyczny to podfolder w 'uploaded'
UPLOAD_EPAPER_DIR = os.path.join("uploaded", "epaper")
# URL to podfolder w '/images/'
BASE_URL = "http://192.168.0.194/images/epaper/"

os.makedirs(UPLOAD_EPAPER_DIR, exist_ok=True)

# --- ZABEZPIECZENIA ---
HARDWARE_LOCK = threading.Lock()
last_refresh_time = 0
epaper_interval = 600.0
slideshow_running = False
force_refresh_event = threading.Event()

def draw_image_task(img_path: str, is_manual: bool = False):
    global last_refresh_time
    if not EPAPER_AVAILABLE: return False

    with HARDWARE_LOCK:
        now = time.time()
        time_since_last = now - last_refresh_time
        required_gap = 15 if is_manual else 60

        if time_since_last < required_gap:
            return False

        try:
            epd = epd7in5_V2.EPD()
            epd.init()
            image = Image.open(img_path)
            # Konwersja wymuszona (na wypadek gdyby plik został podmieniony)
            if image.mode != '1':
                image = image.convert('L').convert('1', dither=Image.FLOYDSTEINBERG)
            epd.display(epd.getbuffer(image))
            epd.sleep()
            last_refresh_time = time.time()
            return True
        except Exception as e:
            print(f"Błąd E-ink: {e}")
            return False

def epaper_slideshow_loop():
    global slideshow_running
    while True:
        if slideshow_running:
            db = SessionLocal()
            active_images = db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
            db.close()

            if active_images:
                for img_record in active_images:
                    if not slideshow_running: break
                    file_path = os.path.join(UPLOAD_EPAPER_DIR, img_record.filename)
                    if os.path.exists(file_path):
                        draw_image_task(file_path, is_manual=False)
                    if force_refresh_event.wait(timeout=epaper_interval):
                        force_refresh_event.clear()
                        break
            else:
                time.sleep(5)
        else:
            time.sleep(1)

# --- ENDPOINTY ---

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Wgrywa zdjęcie do podfolderu 'uploaded/epaper/'"""
    new_img = EPaperImageModel(filename="temp", url="temp", is_active=True)
    db.add(new_img); db.commit(); db.refresh(new_img)

    content = await file.read()
    try:
        image = Image.open(io.BytesIO(content))
        # Skalowanie i konwersja do stylu e-ink
        image = image.convert('L').resize((800, 480))
        image = image.convert('1', dither=Image.FLOYDSTEINBERG)

        filename = f"epd_{new_img.id}.png"
        file_path = os.path.join(UPLOAD_EPAPER_DIR, filename)
        image.save(file_path)
    except Exception as e:
        db.delete(new_img); db.commit()
        raise HTTPException(status_code=400, detail=f"Błąd: {e}")

    new_img.filename = filename
    new_img.url = f"{BASE_URL}{filename}"
    db.commit()
    return new_img

@epaper_router.get("/epaper/images")
def get_epaper_images(db: Session = Depends(get_db)):
    return db.query(EPaperImageModel).order_by(EPaperImageModel.added_at.desc()).all()

@epaper_router.post("/epaper/show/{image_id}")
def show_specific_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    file_path = os.path.join(UPLOAD_EPAPER_DIR, img.filename)
    success = draw_image_task(file_path, is_manual=True)
    if success:
        force_refresh_event.set()
        return {"status": "Wysłano do ekranu"}
    else:
        raise HTTPException(status_code=429, detail="Matryca musi odpocząć (min 15s).")

@epaper_router.patch("/epaper/images/{image_id}")
def toggle_epaper_image(image_id: int, is_active: bool, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    img.is_active = is_active
    db.commit()
    return img

@epaper_router.delete("/epaper/images/{image_id}")
def delete_epaper_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    file_path = os.path.join(UPLOAD_EPAPER_DIR, img.filename)
    if os.path.exists(file_path): os.remove(file_path)
    db.delete(img); db.commit()
    return {"status": "deleted"}

@epaper_router.post("/epaper/control/start")
def epaper_start():
    global slideshow_running
    slideshow_running = True
    return {"status": "Slideshow ON"}

@epaper_router.post("/epaper/control/stop")
def epaper_stop():
    global slideshow_running
    slideshow_running = False
    return {"status": "Slideshow OFF"}

@epaper_router.post("/epaper/settings/interval")
def set_epaper_interval(seconds: int):
    global epaper_interval
    if seconds < 60: raise HTTPException(status_code=400, detail="Minimum 60s")
    epaper_interval = float(seconds)
    return {"interval": epaper_interval}

def startup_epaper_display():
    threading.Thread(target=epaper_slideshow_loop, daemon=True).start()