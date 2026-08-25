"""
Sets up the SQLAlchemy engine (the database connection) and a session
factory. Everything else in the app imports get_db() to talk to the DB.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Load the secret values from the .env file into the environment.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and paste your "
        "Neon connection string."
    )

# The engine is the core connection pool to PostgreSQL.
# pool_pre_ping=True checks a connection is alive before using it, which
# prevents errors on Neon's free tier when it sleeps after inactivity.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# SessionLocal() creates a short-lived conversation with the database.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency: hands out a DB session and closes it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()