"""
Initialize Database

Creates all SQLAlchemy tables.

Run

python backend/database/init_db.py
"""

from backend.database.db import Base
from backend.database.db import engine

import backend.database.models

def initialize_database():

    Base.metadata.create_all(bind=engine)

    print("Database initialized successfully.")


if __name__ == "__main__":
    initialize_database()
