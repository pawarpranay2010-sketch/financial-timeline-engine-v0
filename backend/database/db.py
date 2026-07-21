"""
Database Configuration

Creates:

- PostgreSQL Engine
- SQLAlchemy Session
- Declarative Base

Environment Variable Required:

DATABASE_URL

Example

postgresql+psycopg://user:password@localhost:5432/financial_db
"""

import os

from dotenv import load_dotenv

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

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
