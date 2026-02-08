import os
import threading
import io
import time
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from PIL import Image, ImageDraw, ImageFont
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
    print("✅ E-Paper hardware detected.")
except ImportError:
    EPAPER_AVAILABLE = False
    print("⚠️ E-Paper hardware NOT detected. Simulation mode ON.")

epaper_router = APIRouter(tags=["E-Paper Control"])

# Ścieżki
UPLOAD_EPAPER_DIR = os.path.join("uploaded", "epaper")
BASE_URL = "http://192.168.0.194/images/epaper/"

os.makedirs(UPLOAD_EPAPER_DIR, exist_ok=True)

# --- ZABEZPIECZENIA I STAN ---
HARDWARE_LOCK = threading.Lock()
last_refresh_time = 0
epaper_interval = 600.0  # 10 minut
slideshow_running = False  # DOMYŚLNIE WYŁĄCZONE
force_refresh_event = threading.Event()

def draw_image_task(img_data, is_manual: bool = False, is_path: bool = True):
    global last_refresh_time
    if not EPAPER_AVAILABLE:
        print("SYMULACJA: Wyświetlam obraz w konsoli (brak matrycy).")
        return True

    with HARDWARE_LOCK:
        now = time.time()
        time_since_last = now - last_refresh_time

        # E-papier V2 jest wytrzymały. 10s dla ręcznych zmian to bezpieczny margines.
        required_gap = 10 if is_manual else 60

        if time_since_last < required_gap:
            remaining = int(required_gap - time_since_last)
            print(f"❌ BLOKADA 429: Matryca musi odpocząć jeszcze {remaining}s")
            return False

        try:
            print(f"🔄 Odświeżanie matrycy (Manual: {is_manual})...")
            epd = epd7in5_V2.EPD()
            epd.init()

            if is_path:
                image = Image.open(img_data)
            else:
                image = img_data

            # Finalna konwersja do formatu e-ink (czarno-biały dithered)
            if image.mode != '1':
                image = image.convert('L').convert('1', dither=Image.FLOYDSTEINBERG)

            epd.display(epd.getbuffer(image))
            epd.sleep()
            last_refresh_time = time.time()
            print("✅ Odświeżanie zakończone sukcesem.")
            return True
        except Exception as e:
            print(f"🔥 Błąd krytyczny E-ink: {e}")
            return False

def epaper_slideshow_loop():
    global slideshow_running
    print("🚀 Wątek slideshow zainicjowany (oczekiwanie na start)...")
    while True:
        if slideshow_running:
            db = SessionLocal()
            active_images = db.query(EPaperImageModel).filter(EPaperImageModel.is_active == True).all()
            db.close()

            if active_images:
                for img_record in active_images:
                    # Sprawdź czy w międzyczasie ktoś nie wyłączył slideshow
                    if not slideshow_running:
                        break

                    file_path = os.path.join(UPLOAD_EPAPER_DIR, img_record.filename)
                    if os.path.exists(file_path):
                        # Próba wyświetlenia (slideshow używa rygoru 60s)
                        success = draw_image_task(file_path, is_manual=False)

                        # Czekamy zadany interwał LUB aż ktoś wymusi odświeżenie (force_refresh_event)
                        # Jeśli force_refresh_event zostanie wywołane, wait() zwróci True natychmiast
                        if force_refresh_event.wait(timeout=epaper_interval):
                            print("🔔 Slideshow przerwany przez manualną akcję, resetuję kolejkę.")
                            force_refresh_event.clear()
                            break # Wychodzi z pętli for, pobiera nową listę z bazy
            else:
                time.sleep(5)
        else:
            # Slideshow jest wyłączony, śpimy i sprawdzamy flagę co sekundę
            time.sleep(1)

# --- ENDPOINTY ---

@epaper_router.post("/epaper/show-text")
def show_text_on_epaper(text: str, title: str = "POWIADOMIENIE"):
    img = Image.new('L', (800, 480), 255)
    draw = ImageDraw.Draw(img)

    try:
        font_main = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except:
        font_main = ImageFont.load_default()
        font_title = ImageFont.load_default()

    draw.rectangle([10, 10, 790, 470], outline=0, width=3)
    draw.text((30, 30), title.upper(), font=font_title, fill=0)

    margin = 40
    offset = 120
    for line in [text[i:i+30] for i in range(0, len(text), 30)]:
        draw.text((margin, offset), line, font=font_main, fill=0)
        offset += 60

    success = draw_image_task(img, is_manual=True, is_path=False)

    if success:
        force_refresh_event.set() # Informujemy slideshow, że coś się zmieniło
        return {"status": "Tekst wyświetlony"}
    else:
        raise HTTPException(status_code=429, detail="Matryca odpoczywa. Spróbuj za chwilę.")

@epaper_router.post("/epaper/upload")
async def epaper_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # Tworzymy rekord w bazie
    new_img = EPaperImageModel(filename="temp", url="temp", is_active=True)
    db.add(new_img); db.commit(); db.refresh(new_img)

    content = await file.read()
    try:
        image = Image.open(io.BytesIO(content))
        # Skalowanie do rozdzielczości 7.5 cala Waveshare
        image = image.convert('L').resize((800, 480))

        filename = f"epd_{new_img.id}.png"
        file_path = os.path.join(UPLOAD_EPAPER_DIR, filename)
        image.save(file_path)

        new_img.filename = filename
        new_img.url = f"{BASE_URL}{filename}"
        db.commit()

        # AUTOMATYCZNE WYŚWIETLENIE PO UPLOADZIE
        # is_manual=True, żeby ominąć długi 60s gap slideshowu
        draw_image_task(file_path, is_manual=True)
        force_refresh_event.set()

        return new_img
    except Exception as e:
        db.delete(new_img); db.commit()
        raise HTTPException(status_code=400, detail=f"Błąd przetwarzania obrazu: {e}")

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
        raise HTTPException(status_code=429, detail="Zbyt częste odświeżanie.")

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
    print("▶️ Slideshow STARTED")
    return {"status": "Slideshow ON"}

@epaper_router.post("/epaper/control/stop")
def epaper_stop():
    global slideshow_running
    slideshow_running = False
    print("⏹️ Slideshow STOPPED")
    return {"status": "Slideshow OFF"}

@epaper_router.post("/epaper/settings/interval")
def set_epaper_interval(seconds: int):
    global epaper_interval
    if seconds < 60: raise HTTPException(status_code=400, detail="Minimum 60s")
    epaper_interval = float(seconds)
    return {"interval": epaper_interval}

def startup_epaper_display():
    # Uruchomienie wątku w tle, ale pętla wewnątrz czeka na slideshow_running = True
    threading.Thread(target=epaper_slideshow_loop, daemon=True).start()