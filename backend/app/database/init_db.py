"""
Run this ONCE to create all tables in your Neon database.
Usage (from the backend/ folder, with venv active):
    python -m app.database.init_db
"""

from app.database.models import Base
from app.database.session import engine


def init_database():
    print("Creating APEX-Commerce tables in Neon PostgreSQL...")
    Base.metadata.create_all(bind=engine)
    print("Done. Tables created:")
    for table_name in Base.metadata.tables.keys():
        print(f"   - {table_name}")


if __name__ == "__main__":
    init_database()