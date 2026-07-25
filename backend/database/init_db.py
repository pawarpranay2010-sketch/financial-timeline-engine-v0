"""
Initialize Database

Creates all SQLAlchemy tables.

Run:
python backend/database/init_db.py
"""

from sqlalchemy import inspect

from backend.database.db import Base, engine
import backend.database.models


def initialize_database():
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    print("✅ Database initialized successfully.")
    print(f"Tables found ({len(tables)}):")

    for table in tables:
        print(f" - {table}")


if __name__ == "__main__":
    initialize_database()
