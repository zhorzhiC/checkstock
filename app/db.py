import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://checkstock:checkstock-local-only@localhost:5432/checkstock",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=10)
Session = sessionmaker(engine, expire_on_commit=False)
