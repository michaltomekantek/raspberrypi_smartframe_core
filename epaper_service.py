import os, io, random, threading, time
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from database import Base, SessionLocal

# --- MODEL ---
class EPaperImageModel(Base):
    __tablename__ = "epaper_images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True) # To odpowiada za udział w pokazie

# --- HARDWARE ---
try:
    from lib.waveshare_epd import epd7in5_V2
    epd = epd7in5_V2.EPD()
    EPAPER_AVAILABLE = True
except:
    EPAPER_AVAILABLE = False
    epd = None

epaper_router = APIRouter(tags=["E-Paper Control"])
UPLOAD_EPAPER_DIR = os.path.join("uploaded", "epaper")
BASE_URL = "http://192.168.0.194/images/epaper/"
os.makedirs(UPLOAD_EPAPER_DIR, exist_ok=True)

# --- STAN GLOBALNY ---
db = SessionLocal()
epaper_interval = 120
slideshow_active = False
slideshow_thread = None
current_image_info = None
next_image_info = None
last_refresh_time = 0

def draw_on_hardware(img_source):
    if not EPAPER_AVAILABLE:
        print("Hardware E-Ink niedostępny.")
        return
    try:
        epd.init()
        image = Image.open(img_source) if isinstance(img_source, str) else img_source
        if image.mode != '1':
            image = image.convert('L').convert('1', dither=Image.FLOYDSTEINBERG)
        epd.display(epd.getbuffer(image))
        epd.sleep()
    except Exception as e:
        print(f"🔥 Błąd matrycy: {e}")

def slideshow_worker():
    global slideshow_active, current_image_info, next_image_info, last_refresh_time
    while slideshow_active:
        # Pobieramy tylko aktywne zdjęcia
        imgs = db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
        if imgs:
            if len(imgs) > 1:
                # Losujemy, dbając by nie było to samo co teraz (jeśli możliwe)
                pool = [img for img in imgs if current_image_info and img.id != current_image_info.get('id')]
                selected = random.choice(pool if pool else imgs)
            else:
                selected = imgs[0]

            current_image_info = {"id": selected.id, "filename": selected.filename}
            last_refresh_time = time.time()

            # Przygotuj info o następnym (podgląd dla statusu)
            next_img = random.choice(imgs)
            next_image_info = {"id": next_img.id, "filename": next_img.filename}

            draw_on_hardware(os.path.join(UPLOAD_EPAPER_DIR, selected.filename))

        # Inteligentne czekanie (sprawdza co sekundę czy nie wyłączono pokazu)
        for _ in range(int(epaper_interval)):
            if not slideshow_active: break
            time.sleep(1)

# --- ENDPOINTY ZARZĄDZANIA ---

@epaper_router.get("/epaper/images")
def get_epaper_images():
    return db.query(EPaperImageModel).all()

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...)):
    content = await file.read()
    new_img = EPaperImageModel(filename="temp", url="temp", is_active=True)
    db.add(new_img); db.commit(); db.refresh(new_img)

    img = Image.open(io.BytesIO(content)).convert('L').resize((800, 480))
    fname = f"epd_{new_img.id}.png"
    fpath = os.path.join(UPLOAD_EPAPER_DIR, fname)
    img.save(fpath)

    new_img.filename, new_img.url = fname, f"{BASE_URL}{fname}"
    db.commit()
    draw_on_hardware(fpath)
    return new_img

@epaper_router.patch("/epaper/images/{image_id}/toggle")
def toggle_image_active(image_id: int):
    """Włącza/wyłącza zdjęcie z pokazu slajdów"""
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    img.is_active = not img.is_active
    db.commit()
    return {"id": img.id, "is_active": img.is_active}

@epaper_router.post("/epaper/show/{image_id}")
def show_specific_image(image_id: int):
    """Ustawia wybrane zdjęcie na ramce w tej chwili"""
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    draw_on_hardware(os.path.join(UPLOAD_EPAPER_DIR, img.filename))
    return {"status": "displayed", "id": image_id}

# --- ENDPOINTY KONTROLNE ---

@epaper_router.get("/epaper/status")
def get_epaper_status():
    """Zwraca pełny status pokazu slajdów"""
    remaining = 0
    if slideshow_active and last_refresh_time > 0:
        elapsed = time.time() - last_refresh_time
        remaining = max(0, int(epaper_interval - elapsed))

    return {
        "slideshow_active": slideshow_active,
        "current_image": current_image_info,
        "next_refresh_in": remaining,
        "interval": epaper_interval,
        "epaper_available": EPAPER_AVAILABLE
    }

@epaper_router.post("/epaper/settings/interval")
def set_epaper_interval(seconds: int):
    global epaper_interval
    epaper_interval = max(10, seconds)
    return {"interval": epaper_interval}

@epaper_router.post("/epaper/control/start")
def start_epaper_slideshow():
    global slideshow_active, slideshow_thread
    if not slideshow_active:
        slideshow_active = True
        slideshow_thread = threading.Thread(target=slideshow_worker, daemon=True)
        slideshow_thread.start()
    return {"status": "started"}

@epaper_router.post("/epaper/control/stop")
def stop_epaper_slideshow():
    global slideshow_active
    slideshow_active = False
    return {"status": "stopped"}

@epaper_router.delete("/epaper/images/{image_id}")
def delete_epaper_image(image_id: int):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if img:
        p = os.path.join(UPLOAD_EPAPER_DIR, img.filename)
        if os.path.exists(p): os.remove(p)
        db.delete(img); db.commit()
    return {"status": "deleted"}