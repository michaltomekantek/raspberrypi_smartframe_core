import os, io, random, threading, time
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

# Importujemy SessionLocal oraz model EPaperImageModel z centralnej bazy danych
from database import SessionLocal, EPaperImageModel

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

# --- DEPENDENCY ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- STAN GLOBALNY ---
epaper_interval = 120
slideshow_active = False
slideshow_thread = None
current_image_info = None
next_image_info = None
last_refresh_time = 0

def image_to_dict(img_model):
    """Pomocnicza funkcja do zamiany modelu SQLAlchemy na słownik"""
    if not img_model: return None
    return {
        "id": img_model.id,
        "filename": img_model.filename,
        "url": img_model.url,
        "is_active": img_model.is_active,
        "added_at": img_model.added_at.isoformat() if img_model.added_at else None
    }

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
        worker_db = SessionLocal()
        try:
            imgs = worker_db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
            if imgs:
                if len(imgs) > 1:
                    pool = [img for img in imgs if current_image_info and img.id != current_image_info.get('id')]
                    selected = random.choice(pool if pool else imgs)
                else:
                    selected = imgs[0]

                next_selected = random.choice(imgs)

                current_image_info = image_to_dict(selected)
                next_image_info = image_to_dict(next_selected)
                last_refresh_time = time.time()

                draw_on_hardware(os.path.join(UPLOAD_EPAPER_DIR, selected.filename))
        finally:
            worker_db.close()

        for _ in range(int(epaper_interval)):
            if not slideshow_active: break
            time.sleep(1)

# --- ENDPOINTY ZARZĄDZANIA ---

@epaper_router.get("/epaper/images")
def get_epaper_images(db: Session = Depends(get_db)):
    return db.query(EPaperImageModel).all()

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
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

@epaper_router.patch("/epaper/images/{image_id}")
def set_image_active(image_id: int, is_active: bool, db: Session = Depends(get_db)):
    """Ustawia konkretny stan (is_active) dla zdjęcia (zamiast toggle)"""
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    img.is_active = is_active
    db.commit()
    db.refresh(img)
    return image_to_dict(img)

@epaper_router.post("/epaper/show/{image_id}")
def show_specific_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    draw_on_hardware(os.path.join(UPLOAD_EPAPER_DIR, img.filename))
    return {"status": "displayed", "id": image_id}

# --- ENDPOINTY KONTROLNE ---

@epaper_router.get("/epaper/settings/status")
def get_epaper_status():
    remaining = 0
    if slideshow_active and last_refresh_time > 0:
        elapsed = time.time() - last_refresh_time
        remaining = max(0, int(epaper_interval - elapsed))

    last_refresh_iso = None
    if last_refresh_time > 0:
        last_refresh_iso = datetime.fromtimestamp(last_refresh_time).isoformat()

    return {
        "slideshow_running": slideshow_active,
        "remaining_seconds": remaining,
        "interval": epaper_interval,
        "current_image": current_image_info,
        "next_image": next_image_info,
        "last_refresh": last_refresh_iso
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
def delete_epaper_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if img:
        p = os.path.join(UPLOAD_EPAPER_DIR, img.filename)
        if os.path.exists(p): os.remove(p)
        db.delete(img); db.commit()
    return {"status": "deleted"}

@epaper_router.get("/epaper/test-performance")
async def test_performance():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "message": "FastAPI is alive!"
    }