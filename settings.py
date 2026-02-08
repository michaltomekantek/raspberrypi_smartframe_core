import os
from fastapi import APIRouter, HTTPException

# Tworzymy router dla ustawień systemowych
settings_router = APIRouter(tags=["System Settings"])

@settings_router.post("/system/shutdown")
def shutdown_raspberry():
    """Bezpieczne wyłączanie Malinki"""
    try:
        # shutdown -h now wysyła sygnał halt (całkowite zatrzymanie)
        os.system("sudo shutdown -h now")
        return {"message": "Zamykanie systemu... Czekaj na zgaśnięcie zielonej diody."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@settings_router.post("/system/reboot")
def reboot_raspberry():
    """Restartowanie Malinki"""
    try:
        os.system("sudo reboot")
        return {"message": "Restartowanie..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@settings_router.get("/system/status")
def get_system_status():
    """Szybki check czy system żyje"""
    return {"status": "online", "device": "Raspberry Pi"}