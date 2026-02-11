import argparse
import os
import subprocess
import socket
import time
import threading
from datetime import datetime
from typing import List, Optional

import uvicorn
import psutil
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import Session
from PIL import Image, ImageDraw, ImageFont

# --- IMPORTY Z NASZYCH MODUŁÓW ---
from database import Base, engine, SessionLocal, get_db

# Moduł E-papieru
try:
    from epaper_service import epaper_router, startup_epaper_display
except ImportError:
    print("BŁĄD: Nie można zaimportować epaper_service.py!")
    epaper_router = None
    startup_epaper_display = None

# Moduł Ustawień Systemowych (Shutdown/Reboot)
try:
    from settings import settings_router
except ImportError:
    print("OSTRZEŻENIE: Nie odnaleziono pliku settings.py!")
    settings_router = None

# --- MODEL TABELI DLA HDMI ---
class ImageModel(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

# --- INICJALIZACJA BAZY ---
Base.metadata.create_all(bind=engine)

# --- KONFIGURACJA APKI ---
app = FastAPI(title="SmartFrame OS", version="4.5.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# PODPIĘCIE ROUTERÓW
if epaper_router:
    app.include_router(epaper_router)
if settings_router:
    app.include_router(settings_router)

# --- ARGUMENTY STARTOWE ---
parser = argparse.ArgumentParser()
parser.add_argument("mode", nargs="?", default="pi", choices=["pi", "mac"])
args = parser.parse_args()

IS_MAC = (args.mode == "mac")
UPLOAD_DIR = "uploaded"
BASE_URL = "http://192.168.0.194/images/" # Adres dla HDMI

os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- ZMIENNE GLOBALNE I KONTROLA WĄTKÓW ---
dashboard_active = False
slideshow_running = False
global_interval = 10
SCREEN_W, SCREEN_H = (1024, 600)
skip_requested = False
hdmi_event = threading.Event() # Mechanizm blokady (0% CPU gdy nic nie wyświetlamy)

# --- LOGIKA SYSTEMOWA (HDMI DASHBOARD) ---

def get_sys_data():
    ram = psutil.virtual_memory()
    temp = None
    if not IS_MAC:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = round(float(f.read()) / 1000.0, 1)
        except: pass
    return {
        "time": datetime.now().strftime("%H:%M"),
        "seconds": datetime.now().strftime(":%S"),
        "date": datetime.now().strftime("%A, %d %B %Y").upper(),
        "cpu": psutil.cpu_percent(),
        "temp": temp or "--",
        "ram": ram.percent,
        "storage": psutil.disk_usage('/').percent,
        "ip": socket.gethostbyname(socket.gethostname()) if not IS_MAC else "localhost"
    }

def draw_card(draw, x, y, w, h, label, value, unit, color):
    draw.rounded_rectangle([x, y, x+w, y+h], radius=15, fill=(33, 37, 43))
    draw.rounded_rectangle([x, y, x+12, y+h], radius=5, fill=color)
    font_path = "/Library/Fonts/Arial.ttf" if IS_MAC else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        f_val = ImageFont.truetype(font_path, 50)
        f_lbl = ImageFont.truetype(font_path, 20)
    except:
        f_val = ImageFont.load_default(); f_lbl = ImageFont.load_default()
    draw.text((x+35, y+25), label, fill=(155, 160, 170), font=f_lbl)
    draw.text((x+35, y+55), f"{value}", fill=(255, 255, 255), font=f_val)
    val_w = draw.textlength(f"{value}", font=f_val)
    draw.text((x+35+val_w+5, y+75), unit, fill=color, font=f_lbl)

def create_dashboard_image():
    data = get_sys_data()
    img = Image.new('RGB', (SCREEN_W, SCREEN_H), color=(18, 20, 24))
    draw = ImageDraw.Draw(img)
    y_offset = -20
    font_path = "/Library/Fonts/Arial.ttf" if IS_MAC else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        f_time = ImageFont.truetype(font_path, 130)
        f_sec = ImageFont.truetype(font_path, 60)
        f_date = ImageFont.truetype(font_path, 32)
        f_ip = ImageFont.truetype(font_path, 18)
    except:
        f_time = ImageFont.load_default(); f_sec = ImageFont.load_default(); f_date = ImageFont.load_default(); f_ip = ImageFont.load_default()
    time_w = draw.textlength(data["time"], font=f_time)
    draw.text((60, 50 + y_offset), data["time"], fill=(255, 255, 255), font=f_time)
    draw.text((60 + time_w + 5, 105 + y_offset), data["seconds"], fill=(97, 175, 239), font=f_sec)
    draw.text((65, 185 + y_offset), data["date"], fill=(152, 195, 121), font=f_date)

    card_w, card_h = 285, 140
    row1_y = 270 + y_offset
    row2_y = 430 + y_offset
    draw_card(draw, 60,  row1_y, card_w, card_h, "PROCESOR", data["cpu"], "%", (224, 108, 117))
    draw_card(draw, 370, row1_y, card_w, card_h, "TERMAL", data["temp"], "°C", (209, 154, 102))
    draw_card(draw, 680, row1_y, card_w, card_h, "PAMIĘĆ RAM", data["ram"], "%", (198, 120, 221))
    draw_card(draw, 60,  row2_y, card_w, card_h, "DYSK SYSTEM", data["storage"], "%", (86, 182, 194))
    draw.text((680, 570 + y_offset), f"NETWORK ADDRESS: {data['ip']}", fill=(92, 99, 112), font=f_ip)

    path = os.path.abspath("current_ui.png")
    img.save(path)
    return path

def render_to_pygame(path, screen_obj):
    import pygame
    try:
        img_pil = Image.open(path).convert("RGB")
        surf = pygame.image.fromstring(img_pil.tobytes(), img_pil.size, "RGB")
        screen_obj.blit(surf, (0, 0))
        pygame.display.update()
    except Exception as e:
        print(f"Błąd renderowania HDMI: {e}")

# --- PĘTLA WYŚWIETLANIA (HDMI) ---

def global_display_loop():
    global dashboard_active, slideshow_running, global_interval, skip_requested
    local_screen = None
    if not IS_MAC:
        try:
            import pygame
            pygame.init()
            local_screen = pygame.display.set_mode((1024, 600), pygame.FULLSCREEN | pygame.NOFRAME)
            pygame.mouse.set_visible(False)
        except Exception as e: print(f"Błąd Pygame: {e}")

    while True:
        # KLUCZ: Jeśli nic nie robimy na HDMI, wątek zasypia (nie bierze CPU)
        if not dashboard_active and not slideshow_running:
            hdmi_event.wait(timeout=2)
            if not dashboard_active and not slideshow_running: continue

        if local_screen:
            import pygame
            for event in pygame.event.get():
                if event.type in [pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN]:
                    skip_requested = True

        if dashboard_active:
            path = create_dashboard_image()
            if not IS_MAC and local_screen:
                render_to_pygame(path, local_screen)
            time.sleep(1)

        elif slideshow_running:
            db = SessionLocal()
            try:
                active_images = db.query(ImageModel).filter(ImageModel.is_active == True).all()
            finally:
                db.close()

            if active_images:
                for img in active_images:
                    if not slideshow_running or dashboard_active: break
                    path = os.path.abspath(os.path.join(UPLOAD_DIR, img.filename))
                    if os.path.exists(path):
                        if not IS_MAC and local_screen: render_to_pygame(path, local_screen)
                        start_wait = time.time()
                        while time.time() - start_wait < global_interval:
                            if skip_requested or not slideshow_running or dashboard_active: break
                            if not IS_MAC:
                                for event in pygame.event.get():
                                    if event.type in [pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN]:
                                        skip_requested = True
                            time.sleep(0.5)
                        skip_requested = False
            else: time.sleep(2)
        else: time.sleep(1)

# --- START WĄTKÓW ---
threading.Thread(target=global_display_loop, daemon=True).start()
if startup_epaper_display:
    startup_epaper_display()

# --- API ENDPOINTS (HDMI / LIBRARY) ---

@app.post("/upload", tags=["HDMI Library"])
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ext = os.path.splitext(file.filename)[1].lower()
    new_img = ImageModel(filename="temp", url="temp")
    db.add(new_img); db.commit(); db.refresh(new_img)
    new_filename = f"{new_img.id}{ext}"
    final_path = os.path.join(UPLOAD_DIR, new_filename)
    content = await file.read()
    with open(final_path, "wb") as f: f.write(content)
    new_img.filename = new_filename
    new_img.url = f"{BASE_URL}{new_filename}"
    db.commit()
    return new_img

@app.get("/show-stats", tags=["Display Control"])
def show_stats():
    global dashboard_active, slideshow_running
    slideshow_running = False; dashboard_active = True
    hdmi_event.set() # Budzimy HDMI
    return {"status": "Stats mode active"}

@app.get("/start-slideshow", tags=["Display Control"])
def start_slideshow():
    global dashboard_active, slideshow_running
    dashboard_active = False; slideshow_running = True
    hdmi_event.set() # Budzimy HDMI
    return {"status": "Slideshow started"}

@app.get("/images", tags=["HDMI Library"])
def get_images(db: Session = Depends(get_db)):
    return db.query(ImageModel).all()

@app.delete("/images/{image_id}", tags=["HDMI Library"])
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    file_path = os.path.join(UPLOAD_DIR, img.filename)
    if os.path.exists(file_path): os.remove(file_path)
    db.delete(img); db.commit()
    return {"status": "deleted", "id": image_id}

# Dodaj brakujące endpointy konfiguracji, jeśli jakieś miałeś
@app.get("/settings/interval", tags=["Display Control"])
def get_hdmi_interval():
    return {"interval": global_interval}

@app.post("/settings/interval", tags=["Display Control"])
def set_hdmi_interval(seconds: int):
    global global_interval
    global_interval = seconds
    return {"status": "Interval updated", "interval": global_interval}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)