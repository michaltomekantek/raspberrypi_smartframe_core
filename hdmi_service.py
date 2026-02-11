import os, socket, psutil
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Depends
from sqlalchemy.orm import Session
from PIL import Image, ImageDraw
# Pamiętaj: importujemy SessionLocal i ImageModel z Twojego nowego database.py
from database import SessionLocal, ImageModel

hdmi_router = APIRouter(tags=["HDMI Control"])

UPLOAD_DIR = "uploaded"
BASE_URL = "http://192.168.0.194/images/"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- STAN GLOBALNY HDMI ---
hdmi_interval = 10

try:
    import pygame
    pygame.init()
    # Próba otwarcia okna Pygame (HDMI)
    screen = pygame.display.set_mode((1024, 600), pygame.FULLSCREEN | pygame.NOFRAME)
    pygame.mouse.set_visible(False)
except:
    screen = None

# --- DEPENDENCY ---
# To pozwala każdemu zapytaniu o zdjęcia mieć własne połączenie z bazą
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- LOGIKA RENDEROWANIA ---
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
    try:
        img = Image.open(source) if isinstance(source, str) else source
        img = img.convert("RGB").resize((1024, 600))
        surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
        screen.blit(surf, (0, 0))
        pygame.display.update()
    except Exception as e:
        print(f"🔥 Błąd renderowania HDMI: {e}")

# --- ENDPOINTY HDMI ---

@hdmi_router.get("/show-stats")
def show_stats():
    data = get_sys_data()
    img = Image.new('RGB', (1024, 600), color=(18, 20, 24))
    draw = ImageDraw.Draw(img)
    draw.text((60, 50), f"STATS | CPU: {data['cpu']}% | TEMP: {data['temp']}C", fill=(255,255,255))
    render_hdmi(img)
    return {"status": "stats rendered"}

@hdmi_router.get("/images")
def get_images(db: Session = Depends(get_db)):
    # db jest teraz wstrzykiwane automatycznie przez FastAPI
    return db.query(ImageModel).all()

@hdmi_router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    # Tworzymy rekord w bazie danych
    new_img = ImageModel(filename="temp", url="temp")
    db.add(new_img)
    db.commit()
    db.refresh(new_img)

    ext = os.path.splitext(file.filename)[1].lower()
    fname = f"{new_img.id}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)

    # Zapisujemy fizyczny plik
    with open(path, "wb") as f:
        f.write(content)

    # Aktualizujemy nazwę pliku w bazie
    new_img.filename, new_img.url = fname, f"{BASE_URL}{fname}"
    db.commit()

    # Od razu wyświetlamy na HDMI
    render_hdmi(path)
    return new_img

@hdmi_router.get("/settings/interval")
def get_hdmi_interval():
    return {"interval": hdmi_interval}

@hdmi_router.post("/settings/interval")
def set_hdmi_interval(seconds: int):
    global hdmi_interval
    hdmi_interval = seconds
    return {"interval": hdmi_interval}

@hdmi_router.delete("/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if img:
        p = os.path.join(UPLOAD_DIR, img.filename)
        if os.path.exists(p):
            os.remove(p)
        db.delete(img)
        db.commit()
    return {"status": "deleted"}