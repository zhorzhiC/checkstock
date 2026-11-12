import os

from alembic import command
from alembic.config import Config

from app.seed import seed

if __name__ == "__main__":
    command.upgrade(Config("alembic.ini"), "head")
    if os.environ.get("DEMO_SEED", "true").lower() == "true":
        seed()
    os.execvp(
        "uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    )
