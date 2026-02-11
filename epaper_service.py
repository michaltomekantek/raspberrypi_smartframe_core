import os, io, random, threading, time
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from database import Base, SessionLocal

class EPaperImageModel(Base):
    __tablename__ = "epaper_images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

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

def draw_on_hardware(img_source):
    if not EPAPER_AVAILABLE: return
    try:
        epd.init()
        image = Image.open(img_source) if isinstance(img_source, str) else img_source
        if image.mode != '1': image = image.convert('L').convert('1')
        epd.display(epd.getbuffer(image))
        epd.sleep()
    except Exception as e: print(f"E-ink Error: {e}")

def slideshow_worker():
    """Wątek, który 'śpi' przez większość czasu (0% CPU)"""
    global slideshow_active
    while slideshow_active:
        imgs = db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
        if imgs:
            selected = random.choice(imgs)
            draw_on_hardware(os.path.join(UPLOAD_EPAPER_DIR, selected.filename))

        # Klucz do wydajności: śpimy długo, nie sprawdzając niczego w pętli
        for _ in range(int(epaper_interval)):
            if not slideshow_active: break
            time.sleep(1)

# --- ENDPOINTY ---

@epaper_router.get("/epaper/images")
def get_epaper_images():
    return db.query(EPaperImageModel).all()

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...)):
    content = await file.read()
    new_img = EPaperImageModel(filename="temp", url="temp")
    db.add(new_img); db.commit(); db.refresh(new_img)
    img = Image.open(io.BytesIO(content)).convert('L').resize((800, 480))
    fname = f"epd_{new_img.id}.png"
    fpath = os.path.join(UPLOAD_EPAPER_DIR, fname)
    img.save(fpath)
    new_img.filename, new_img.url = fname, f"{BASE_URL}{fname}"
    db.commit()
    draw_on_hardware(fpath)
    return new_img

@epaper_router.get("/epaper/settings/interval")
def get_epaper_interval():
    return {"interval": epaper_interval}

@epaper_router.post("/epaper/settings/interval")
def set_epaper_interval(seconds: int):
    global epaper_interval
    epaper_interval = seconds
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

@epaper_router.post("/epaper/control/clear")
def clear_epaper():
    if EPAPER_AVAILABLE:
        epd.init(); epd.Clear(); epd.sleep()
    return {"status": "cleared"}

@epaper_router.delete("/epaper/images/{image_id}")
def delete_epaper_image(image_id: int):
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if img:
        p = os.path.join(UPLOAD_EPAPER_DIR, img.filename)
        if os.path.exists(p): os.remove(p)
        db.delete(img); db.commit()
    return {"status": "deleted"}