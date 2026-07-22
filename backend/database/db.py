"""
Database Configuration

Creates:
- PostgreSQL Engine
- SQLAlchemy Session
- Declarative Base

Environment Variable Required:
DATABASE_URL
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Load .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Stop immediately if DATABASE_URL is missing
if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL is missing. Check your Railway Variables or .env file."
    )

# Railway compatibility
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg2://",
        1,
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
