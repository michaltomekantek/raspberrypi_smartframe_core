import os, socket, psutil, uvicorn, threading, time
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from PIL import Image, ImageDraw, ImageFont
from database import Base, engine, SessionLocal

db = SessionLocal()

class ImageModel(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String); url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="SmartFrame OS - FULL")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

from epaper_service import epaper_router
app.include_router(epaper_router)

UPLOAD_DIR = "uploaded"
BASE_URL = "http://192.168.0.194/images/"
os.makedirs(UPLOAD_DIR, exist_ok=True)

hdmi_interval = 10
hdmi_slideshow_running = False

try:
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((1024, 600), pygame.FULLSCREEN | pygame.NOFRAME)
    pygame.mouse.set_visible(False)
except: screen = None

def get_sys_data():
    temp = "--"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = round(float(f.read()) / 1000.0, 1)
    except: pass
    return {
        "time": datetime.now().strftime("%H:%M"),
        "cpu": psutil.cpu_percent(), "temp": temp,
        "ram": psutil.virtual_memory().percent,
        "storage": psutil.disk_usage('/').percent,
        "ip": socket.gethostbyname(socket.gethostname())
    }

def render_hdmi(source):
    if not screen: return
    img = Image.open(source) if isinstance(source, str) else source
    img = img.convert("RGB").resize((1024, 600))
    surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
    screen.blit(surf, (0, 0))
    pygame.display.update()

# --- ENDPOINTY HDMI ---

@app.get("/show-stats")
def show_stats():
    data = get_sys_data()
    img = Image.new('RGB', (1024, 600), color=(18, 20, 24))
    draw = ImageDraw.Draw(img)
    draw.text((60, 50), f"STATS | CPU: {data['cpu']}% | TEMP: {data['temp']}C", fill=(255,255,255))
    render_hdmi(img)
    return {"status": "stats rendered"}

@app.get("/images")
def get_images():
    return db.query(ImageModel).all()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    new_img = ImageModel(filename="temp", url="temp")
    db.add(new_img); db.commit(); db.refresh(new_img)
    ext = os.path.splitext(file.filename)[1].lower()
    fname = f"{new_img.id}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f: f.write(content)
    new_img.filename, new_img.url = fname, f"{BASE_URL}{fname}"
    db.commit()
    render_hdmi(path)
    return new_img

@app.get("/settings/interval")
def get_hdmi_interval():
    return {"interval": hdmi_interval}

@app.post("/settings/interval")
def set_hdmi_interval(seconds: int):
    global hdmi_interval
    hdmi_interval = seconds
    return {"interval": hdmi_interval}

@app.delete("/images/{image_id}")
def delete_image(image_id: int):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if img:
        p = os.path.join(UPLOAD_DIR, img.filename)
        if os.path.exists(p): os.remove(p)
        db.delete(img); db.commit()
    return {"status": "deleted"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)