import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine

# Importujemy routery z obu serwisów
from epaper_service import epaper_router
from hdmi_service import hdmi_router

# Inicjalizacja bazy danych (tabele)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="SmartFrame OS - Modular")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Podpinamy oba moduły pod jedną aplikację FastAPI
app.include_router(epaper_router)
app.include_router(hdmi_router)

@app.get("/")
def root():
    return {"message": "SmartFrame OS is running", "modules": ["HDMI", "E-Paper"]}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)