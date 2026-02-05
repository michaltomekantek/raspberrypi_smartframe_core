import argparse
import os
import subprocess
import socket
import platform
import time
import threading
from datetime import datetime

import uvicorn
import psutil
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont

# --- KONFIGURACJA ---
parser = argparse.ArgumentParser()
parser.add_argument("mode", nargs="?", default="pi", choices=["pi", "mac"])
args = parser.parse_args()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

IS_MAC = (args.mode == "mac")
SCREEN_W, SCREEN_H = (800, 480)

# FLAGI STERUJĄCE
dashboard_active = False # Domyślnie wyłączone, włącza endpoint /show-stats

if not IS_MAC:
    import pygame
    pygame.init()
    info = pygame.display.Info()
    SCREEN_W, SCREEN_H = info.current_w, info.current_h
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)

# --- LOGIKA SYSTEMOWA ---

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
        f_val = ImageFont.truetype("Arial.ttf", 35)
        f_lbl = ImageFont.truetype("Arial.ttf", 15)
    except:
        f_val = ImageFont.load_default(); f_lbl = ImageFont.load_default()
    draw.text((x+20, y+15), label, fill=(171, 178, 191), font=f_lbl)
    draw.text((x+20, y+40), f"{value}{unit}", fill=(255, 255, 255), font=f_val)

def create_dashboard_image():
    data = get_sys_data()
    img = Image.new('RGB', (SCREEN_W, SCREEN_H), color=(33, 37, 43))
    draw = ImageDraw.Draw(img)
    try:
        f_time = ImageFont.truetype("Arial.ttf", 80)
        f_date = ImageFont.truetype("Arial.ttf", 30)
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

    path = "current_ui.png"
    img.save(path)
    return path

# --- PĘTLA WYŚWIETLANIA (BACKGROUND THREAD) ---

def ui_loop():
    global dashboard_active
    while True:
        if dashboard_active:
            path = create_dashboard_image()
            if not IS_MAC:
                img = Image.open(path).convert("RGB")
                surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
                surf = pygame.transform.scale(surf, (SCREEN_W, SCREEN_H))
                screen.blit(surf, (0, 0))
                pygame.display.update()
            # Na macu w trybie loop nie otwieramy okna (tylko generujemy plik)
        time.sleep(1)

threading.Thread(target=ui_loop, daemon=True).start()

# --- ENDPOINTY ---

@app.get("/show-stats")
async def show_stats():
    global dashboard_active
    dashboard_active = True  # Włączamy odświeżanie w tle

    path = create_dashboard_image()
    if IS_MAC:
        # Na Macu wymuszamy otwarcie podglądu raz
        subprocess.run(["open", path])

    return {"status": "Dashboard mode activated (Live refresh ON)"}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    global dashboard_active
    dashboard_active = False  # WYŁĄCZAMY odświeżanie dashboardu

    os.makedirs("uploaded", exist_ok=True)
    path = os.path.join("uploaded", file.filename)
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)

    # Wyświetlamy wgrane zdjęcie
    if not IS_MAC:
        img = Image.open(path).convert("RGB")
        surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
        surf = pygame.transform.scale(surf, (SCREEN_W, SCREEN_H))
        screen.blit(surf, (0, 0))
        pygame.display.update()
    else:
        subprocess.run(["open", path])

    return {"status": "Image displayed, dashboard deactivated"}

@app.get("/system-info")
def get_info():
    return get_sys_data()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)