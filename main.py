import argparse
import os
import socket
import time
import threading
import psutil
import uvicorn
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import Session
from PIL import Image, ImageDraw, ImageFont

from database import Base, engine, SessionLocal, get_db

# Próba importów modułów
try:
    from epaper_service import epaper_router, startup_epaper_display
except ImportError:
    epaper_router = None; startup_epaper_display = None

# HDMI Image Model
class ImageModel(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="SmartFrame OS")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

if epaper_router:
    app.include_router(epaper_router)

# --- STAN HDMI ---
dashboard_active = False
hdmi_slideshow_running = False
global_interval = 10
skip_requested = False
hdmi_event = threading.Event()

def get_sys_data():
    ram = psutil.virtual_memory()
    temp = "--"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = round(float(f.read()) / 1000.0, 1)
    except: pass
    return {
        "time": datetime.now().strftime("%H:%M"),
        "seconds": datetime.now().strftime(":%S"),
        "date": datetime.now().strftime("%A, %d %B %Y").upper(),
        "cpu": psutil.cpu_percent(),
        "temp": temp,
        "ram": ram.percent,
        "storage": psutil.disk_usage('/').percent,
        "ip": socket.gethostbyname(socket.gethostname())
    }

def create_dashboard_image():
    data = get_sys_data()
    img = Image.new('RGB', (1024, 600), color=(18, 20, 24))
    # ... (tutaj Twoja pełna logika rysowania draw_card, f_time itp.) ...
    # Dla skrócenia przykładu, rysujemy prosty tekst (zachowaj swoją funkcję draw_card!)
    draw = ImageDraw.Draw(img)
    draw.text((60, 50), f"{data['time']}{data['seconds']}", fill=(255,255,255))
    path = os.path.abspath("current_ui.png")
    img.save(path)
    return path

def render_to_pygame(path, screen_obj):
    import pygame
    img_pil = Image.open(path).convert("RGB")
    surf = pygame.image.fromstring(img_pil.tobytes(), img_pil.size, "RGB")
    screen_obj.blit(surf, (0, 0))
    pygame.display.update()

def global_display_loop():
    global dashboard_active, hdmi_slideshow_running, skip_requested
    local_screen = None
    try:
        import pygame
        pygame.init()
        local_screen = pygame.display.set_mode((1024, 600), pygame.FULLSCREEN | pygame.NOFRAME)
        pygame.mouse.set_visible(False)
    except: print("Pygame nie zainicjowany.")

    while True:
        # Blokada: Jeśli nic nie robimy na HDMI, wątek zasypia na Event.wait()
        if not dashboard_active and not hdmi_slideshow_running:
            hdmi_event.wait(timeout=2)
            if not dashboard_active and not hdmi_slideshow_running: continue

        if dashboard_active:
            path = create_dashboard_image()
            if local_screen: render_to_pygame(path, local_screen)
            time.sleep(1)

        elif hdmi_slideshow_running:
            db = SessionLocal()
            try:
                imgs = db.query(ImageModel).filter(ImageModel.is_active == True).all()
            finally:
                db.close()

            if imgs:
                for img in imgs:
                    if not hdmi_slideshow_running or dashboard_active: break
                    p = os.path.abspath(os.path.join("uploaded", img.filename))
                    if os.path.exists(p) and local_screen:
                        render_to_pygame(p, local_screen)
                        start_w = time.time()
                        while time.time() - start_w < global_interval:
                            if skip_requested or not hdmi_slideshow_running or dashboard_active: break
                            # Obsługa dotyku co 0.5s
                            for event in pygame.event.get():
                                if event.type in [pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN]:
                                    skip_requested = True
                            time.sleep(0.5)
                        skip_requested = False
            else: time.sleep(5)

@app.get("/show-stats")
def show_stats():
    global dashboard_active, hdmi_slideshow_running
    hdmi_slideshow_running = False; dashboard_active = True
    hdmi_event.set()
    return {"status": "Stats mode active"}

@app.get("/start-slideshow")
def start_hdmi_slideshow():
    global dashboard_active, hdmi_slideshow_running
    dashboard_active = False; hdmi_slideshow_running = True
    hdmi_event.set()
    return {"status": "HDMI Slideshow started"}

# Start wątków
threading.Thread(target=global_display_loop, daemon=True).start()
if startup_epaper_display:
    startup_epaper_display()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)