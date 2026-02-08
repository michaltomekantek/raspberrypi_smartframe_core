import os
import threading
import io
import time
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import Session

# Import bazy danych z Twojego pliku database.py
from database import Base, SessionLocal, get_db

# --- MODEL TABELI DLA E-PAPIERU ---
class EPaperImageModel(Base):
    __tablename__ = "epaper_images"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    url = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

# --- INICJALIZACJA STEROWNIKA ---
try:
    from lib.waveshare_epd import epd7in5_V2
    EPAPER_AVAILABLE = True
except ImportError:
    print("OSTRZEŻENIE: Sterownik Waveshare nieodnaleziony. Tryb symulacji.")
    EPAPER_AVAILABLE = False

epaper_router = APIRouter(tags=["E-Paper Control"])
UPLOAD_EPAPER_DIR = "uploaded_epaper"
BASE_URL = "http://192.168.0.194/epaper-images/" # Zmień na IP swojej Malinki
os.makedirs(UPLOAD_EPAPER_DIR, exist_ok=True)

# --- GLOBALNE ZABEZPIECZENIA I KONTROLA ---
HARDWARE_LOCK = threading.Lock()  # Zapobiega konfliktom wątków przy dostępie do GPIO
last_refresh_time = 0             # Czas ostatniego fizycznego odświeżenia
epaper_interval = 600.0           # Domyślnie 10 minut
slideshow_running = False         # Stan pętli
force_refresh_event = threading.Event()

def draw_image_task(img_path: str, is_manual: bool = False):
    """
    Główna funkcja wysyłająca obraz do matrycy z blokadami bezpieczeństwa.
    """
    global last_refresh_time
    if not EPAPER_AVAILABLE:
        print(f"SYMULACJA: Wyświetlam {img_path}")
        return True

    # HARDWARE SAFETY LAYER
    with HARDWARE_LOCK:
        now = time.time()
        time_since_last = now - last_refresh_time

        # Bezwzględne limity: 15s dla ręcznego kliknięcia, 60s dla pętli
        required_gap = 15 if is_manual else 60

        if time_since_last < required_gap:
            print(f"BLOKADA: Matryca stygnie. Pozostało {int(required_gap - time_since_last)}s")
            return False

        try:
            epd = epd7in5_V2.EPD()
            print("E-Paper: Inicjalizacja...")
            epd.init()

            image = Image.open(img_path)
            # Upewniamy się, że obraz jest w trybie 1-bitowym
            if image.mode != '1':
                image = image.convert('L').convert('1', dither=Image.FLOYDSTEINBERG)

            epd.display(epd.getbuffer(image))

            print("E-Paper: Zasypianie (Deep Sleep)...")
            epd.sleep() # Kluczowe dla żywotności matrycy

            last_refresh_time = time.time()
            return True
        except Exception as e:
            print(f"BŁĄD SPRZĘTU: {e}")
            return False

def epaper_slideshow_loop():
    """Wątek pętli działający w tle"""
    global slideshow_running
    print("E-Paper Slideshow: Wątek aktywny.")
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

                    # Czekaj na interwał LUB na sygnał 'force_refresh_event' (przerwanie pętli)
                    if force_refresh_event.wait(timeout=epaper_interval):
                        force_refresh_event.clear()
                        break # Wychodzimy z 'for', żeby zacząć od nowa (np. po wymuszeniu zdjęcia)
            else:
                time.sleep(5)
        else:
            time.sleep(1)

# --- API ENDPOINTS ---

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Wgrywa i konwertuje zdjęcie do 1-bit PNG. Nie odświeża ekranu."""
    new_img = EPaperImageModel(filename="temp", url="temp", is_active=True)
    db.add(new_img); db.commit(); db.refresh(new_img)

    content = await file.read()
    try:
        image = Image.open(io.BytesIO(content))
        # Konwersja do specyfikacji e-papieru (800x480, Floyd-Steinberg)
        image = image.convert('L').resize((800, 480))
        image = image.convert('1', dither=Image.FLOYDSTEINBERG)

        filename = f"epd_{new_img.id}.png"
        file_path = os.path.join(UPLOAD_EPAPER_DIR, filename)
        image.save(file_path)
    except Exception as e:
        db.delete(new_img); db.commit()
        raise HTTPException(status_code=400, detail=f"Błąd przetwarzania: {e}")

    new_img.filename = filename
    new_img.url = f"{BASE_URL}{filename}"
    db.commit()
    return new_img

@epaper_router.get("/epaper/images")
def get_epaper_images(db: Session = Depends(get_db)):
    """Pobiera galerię zdjęć e-papieru"""
    return db.query(EPaperImageModel).order_by(EPaperImageModel.added_at.desc()).all()

@epaper_router.post("/epaper/show/{image_id}")
def show_specific_image(image_id: int, db: Session = Depends(get_db)):
    """Wymusza natychmiastowe wyświetlenie zdjęcia po ID"""
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404, detail="Nie ma takiego zdjęcia.")

    file_path = os.path.join(UPLOAD_EPAPER_DIR, img.filename)

    # Wywołanie z priorytetem (is_manual=True)
    success = draw_image_task(file_path, is_manual=True)

    if success:
        force_refresh_event.set() # Informujemy pętlę, żeby zresetowała swój licznik
        return {"status": "Wysłano do ekranu", "id": image_id}
    else:
        raise HTTPException(status_code=429, detail="Ochrona matrycy: Odczekaj chwilę przed kolejną zmianą.")

@epaper_router.patch("/epaper/images/{image_id}")
def toggle_epaper_image(image_id: int, is_active: bool, db: Session = Depends(get_db)):
    """Ustawia czy zdjęcie ma brać udział w pokazie slajdów"""
    img = db.query(EPaperImageModel).filter(EPaperImageModel.id == image_id).first()
    if not img: raise HTTPException(status_code=404)
    img.is_active = is_active
    db.commit()
    return img

@epaper_router.delete("/epaper/images/{image_id}")
def delete_epaper_image(image_id: int, db: Session = Depends(get_db)):
    """Usuwa zdjęcie całkowicie"""
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
    if seconds < 60: raise HTTPException(status_code=400, detail="Minimum 60 sekund")
    epaper_interval = float(seconds)
    return {"interval": epaper_interval}

@epaper_router.get("/epaper/settings/interval")
def get_epaper_interval():
    return {"interval": epaper_interval}

def startup_epaper_display():
    """Uruchamiane przy starcie main.py"""
    threading.Thread(target=epaper_slideshow_loop, daemon=True).start()