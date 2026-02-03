import argparse
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from PIL import Image
import os
import subprocess
import platform

parser = argparse.ArgumentParser()
parser.add_argument("mode", nargs="?", default="pi", choices=["pi", "mac"])
args = parser.parse_args()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IS_MAC = (args.mode == "mac")

if not IS_MAC:
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("SmartFrame RPi")

def show_on_pi(path):
    img = Image.open(path).convert("RGB")
    data = img.tobytes()
    surf = pygame.image.fromstring(data, img.size, "RGB")
    surf = pygame.transform.scale(surf, (800, 480))
    screen.blit(surf, (0, 0))
    pygame.display.update()

def show_on_mac(path):
    print(f"🖼️ Opening image on macOS: {path}")
    subprocess.run(["open", path])  # systemowe Preview


@app.get("/test")
def test():
    return {"status": "ok", "mode": args.mode}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs("uploaded", exist_ok=True)
    path = f"uploaded/{file.filename}"

    with open(path, "wb") as f:
        f.write(await file.read())

    if IS_MAC:
        show_on_mac(path)
    else:
        show_on_pi(path)

    return {"status": "uploaded", "file": file.filename}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
