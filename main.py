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
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from PIL import Image, ImageDraw, ImageFont

# --- KONFIGURACJA BAZY ---
DATABASE_URL = "sqlite:///./smartframe.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ImageModel(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

# --- KONFIGURACJA APKI ---
app = FastAPI(title="SmartFrame OS", version="3.4.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

parser = argparse.ArgumentParser()
parser.add_argument("mode", nargs="?", default="pi", choices=["pi", "mac"])
args = parser.parse_args()

IS_MAC = (args.mode == "mac")
UPLOAD_DIR = "uploaded"

# Jeśli Twoje IP się zmieniło, podstaw aktualne.
BASE_URL = "http://192.168.0.194/images/"

os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- ZMIENNE GLOBALNE ---
dashboard_active = False
slideshow_running = False
global_interval = 10
SCREEN_W, SCREEN_H = (800, 480)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# --- LOGIKA SYSTEMOWA I RYSOWANIE ---

def get_sys_data():
    ram = psutil.virtual_memory()
    temp = None
    if not IS_MAC:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = round(float(f.read()) / 1000.0, 1)
        except: pass
    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%d.%m.%Y"),
        "cpu": psutil.cpu_percent(),
        "temp": temp or "--",
        "ram": ram.percent,
        "storage": psutil.disk_usage('/').percent,
        "ip": socket.gethostbyname(socket.gethostname()) if not IS_MAC else "localhost"
    }

def draw_card(draw, x, y, w, h, label, value, unit, color):
    draw.rounded_rectangle([x, y, x+w, y+h], radius=15, fill=(40, 44, 52))
    draw.rectangle([x, y+10, x+5, y+h-10], fill=color)
    try:
        font_path = "/Library/Fonts/Arial.ttf" if IS_MAC else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f_val = ImageFont.truetype(font_path, 35)
        f_lbl = ImageFont.truetype(font_path, 15)
    except:
        f_val = ImageFont.load_default(); f_lbl = ImageFont.load_default()
    draw.text((x+20, y+15), label, fill=(171, 178, 191), font=f_lbl)
    draw.text((x+20, y+40), f"{value}{unit}", fill=(255, 255, 255), font=f_val)

def create_dashboard_image():
    data = get_sys_data()
    img = Image.new('RGB', (SCREEN_W, SCREEN_H), color=(33, 37, 43))
    draw = ImageDraw.Draw(img)
    try:
        font_path = "/Library/Fonts/Arial.ttf" if IS_MAC else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f_time = ImageFont.truetype(font_path, 80); f_date = ImageFont.truetype(font_path, 30)
    except:
        f_time = ImageFont.load_default(); f_date = ImageFont.load_default()

    draw.text((40, 30), data["time"], fill=(97, 175, 239), font=f_time)
    draw.text((40, 120), data["date"], fill=(152, 195, 121), font=f_date)

    card_w, card_h = 220, 110
    draw_card(draw, 40,  200, card_w, card_h, "CPU USAGE", data["cpu"], "%", (224, 108, 117))
    draw_card(draw, 280, 200, card_w, card_h, "CPU TEMP", data["temp"], "°C", (209, 154, 102))
    draw_card(draw, 520, 200, card_w, card_h, "RAM LOAD", data["ram"], "%", (198, 120, 221))
    draw_card(draw, 40,  330, card_w, card_h, "DISK USED", data["storage"], "%", (86, 182, 194))
    draw.text((520, 440), f"IP: {data['ip']}", fill=(92, 99, 112))

    path = os.path.abspath("current_ui.png")
    img.save(path)
    return path

# --- POPRAWIONA FUNKCJA RENDEROWANIA (BEZ ROZCIĄGANIA) ---

def render_to_pygame(path, screen_obj):
    import pygame
    try:
        # Ładowanie PILa do konwersji
        img_pil = Image.open(path).convert("RGB")
        img_w, img_h = img_pil.size
        sw, sh = screen_obj.get_size()

        # Obliczanie proporcji Aspect Fit
        ratio = min(sw / img_w, sh / img_h)
        new_w = int(img_w * ratio)
        new_h = int(img_h * ratio)

        # Konwersja na powierzchnię Pygame
        surf = pygame.image.fromstring(img_pil.tobytes(), img_pil.size, "RGB")

        # Skalowanie płynne (nie rozciąga!)
        scaled_surf = pygame.transform.smoothscale(surf, (new_w, new_h))

        # Centrowanie na czarnym tle
        screen_obj.fill((0, 0, 0))
        offset_x = (sw - new_w) // 2
        offset_y = (sh - new_h) // 2

        screen_obj.blit(scaled_surf, (offset_x, offset_y))
        pygame.display.update()
    except Exception as e:
        print(f"Błąd renderowania obrazu {path}: {e}")

# --- GŁÓWNA PĘTLA WYŚWIETLANIA ---

def global_display_loop():
    global dashboard_active, slideshow_running, global_interval
    local_screen = None
    if not IS_MAC:
        try:
            import pygame
            pygame.init()
            # Pobieramy info o ekranie dla pełnego wymiaru
            info = pygame.display.Info()
            local_screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        except Exception as e:
            print(f"Blad Pygame: {e}")

    while True:
        if dashboard_active:
            path = create_dashboard_image()
            if IS_MAC:
                subprocess.run(["open", "-g", "-a", "Preview", path])
            elif local_screen:
                render_to_pygame(path, local_screen)
            time.sleep(2 if IS_MAC else 1)
        elif slideshow_running:
            db = SessionLocal()
            active_images = db.query(ImageModel).filter(ImageModel.is_active == True).all()
            db.close()
            if active_images:
                for img in active_images:
                    if not slideshow_running or dashboard_active: break
                    path = os.path.abspath(os.path.join(UPLOAD_DIR, img.filename))
                    if os.path.exists(path):
                        if IS_MAC:
                            subprocess.run(["open", "-g", "-a", "Preview", path])
                        elif local_screen:
                            render_to_pygame(path, local_screen)
                        time.sleep(global_interval)
            else:
                time.sleep(2)
        else:
            time.sleep(1)

threading.Thread(target=global_display_loop, daemon=True).start()

# --- ENDPOINTY ---

@app.post("/upload", tags=["Library"])
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ext = os.path.splitext(file.filename)[1].lower()
    new_img = ImageModel(filename="temp", url="temp")
    db.add(new_img)
    db.commit()
    db.refresh(new_img)
    new_filename = f"{new_img.id}{ext}"
    final_path = os.path.join(UPLOAD_DIR, new_filename)

    content = await file.read()
    with open(final_path, "wb") as f:
        f.write(content)

    os.chmod(final_path, 0o644)

    new_img.filename = new_filename
    new_img.url = f"{BASE_URL}{new_filename}"
    db.commit()
    db.refresh(new_img)
    return new_img

@app.get("/show-stats", tags=["Display Control"])
def show_stats():
    global dashboard_active, slideshow_running
    slideshow_running = False; dashboard_active = True
    return {"status": "Stats mode active"}

@app.get("/start-slideshow", tags=["Display Control"])
def start_slideshow():
    global dashboard_active, slideshow_running
    dashboard_active = False; slideshow_running = True
    return {"status": "Slideshow started"}

@app.get("/stop-all", tags=["Display Control"])
def stop_all():
    global dashboard_active, slideshow_running
    dashboard_active = False; slideshow_running = False
    return {"status": "Display stopped"}

@app.post("/settings/interval", tags=["Settings"])
def set_interval(seconds: int):
    global global_interval
    global_interval = seconds
    return {"global_interval": global_interval}

@app.get("/images", tags=["Library"])
def get_images(db: Session = Depends(get_db)):
    return db.query(ImageModel).all()

@app.patch("/images/{image_id}", tags=["Library"])
def toggle_image(image_id: int, is_active: bool, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    img.is_active = is_active
    db.commit()
    return img

@app.delete("/images/{image_id}", tags=["Library"])
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    file_path = os.path.join(UPLOAD_DIR, img.filename)
    if os.path.exists(file_path): os.remove(file_path)
    db.delete(img)
    db.commit()
    return {"status": "deleted", "id": image_id}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)