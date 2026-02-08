from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Wspólna baza danych dla wszystkich modułów
DATABASE_URL = "sqlite:///./smartframe.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Funkcja pomocnicza do sesji bazy
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()