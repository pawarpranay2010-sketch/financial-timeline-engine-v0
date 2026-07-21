"""
PostgreSQL Engine

Single database connection
used across the application.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = (
    "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/financial_engine"
)

engine = create_engine(

    DATABASE_URL,

    pool_size=10,

    max_overflow=20,

    pool_pre_ping=True,

    echo=False
)

SessionLocal = sessionmaker(

    autocommit=False,

    autoflush=False,

    bind=engine
)


def get_db():

    db = SessionLocal()

    try:

        yield db

    finally:

        db.close()
