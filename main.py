import argparse
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from PIL import Image
import os
import subprocess

# Konfiguracja argumentów
parser = argparse.ArgumentParser()
parser.add_argument("mode", nargs="?", default="pi", choices=["pi", "mac"])
args = parser.parse_args()

app = FastAPI()

# Middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IS_MAC = (args.mode == "mac")

# Inicjalizacja ekranu
if not IS_MAC:
    import pygame
    pygame.init()

    # Wykrywanie natywnej rozdzielczości wyświetlacza
    info = pygame.display.Info()
    SCREEN_W = info.current_w
    SCREEN_H = info.current_h

    # Uruchomienie w trybie pełnoekranowym
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
    pygame.display.set_caption("SmartFrame RPi")
    pygame.mouse.set_visible(False)  # Ukrywa kursor myszy na ramce
else:
    SCREEN_W, SCREEN_H = 800, 480 # Domyślne dla Mac (podgląd)

def show_on_pi(path):
    # Otwieramy obraz i konwertujemy do formatu Pygame
    img = Image.open(path).convert("RGB")
    data = img.tobytes()
    surf = pygame.image.fromstring(data, img.size, "RGB")

    # Skalowanie do pełnych wymiarów wykrytego ekranu
    surf = pygame.transform.scale(surf, (SCREEN_W, SCREEN_H))

    screen.blit(surf, (0, 0))
    pygame.display.update()

def show_on_mac(path):
    print(f"🖼️ Opening image on macOS: {path}")
    # Na Macu otwieramy po prostu systemowy podgląd
    subprocess.run(["open", path])

@app.get("/test")
def test():
    return {
        "status": "ok",
        "mode": args.mode,
        "resolution": f"{SCREEN_W}x{SCREEN_H}"
    }

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs("uploaded", exist_ok=True)
    path = os.path.join("uploaded", file.filename)

    # Zapis pliku
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)

    # Wyświetlanie zależnie od trybu
    if IS_MAC:
        show_on_mac(path)
    else:
        show_on_pi(path)

    return {"status": "uploaded", "file": file.filename, "size": f"{SCREEN_W}x{SCREEN_H}"}

if __name__ == "__main__":
    # Host 0.0.0.0 pozwala na dostęp z innych urządzeń w sieci
    uvicorn.run(app, host="0.0.0.0", port=8000)