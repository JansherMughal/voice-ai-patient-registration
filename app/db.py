"""SQLAlchemy engine/session setup, shared by every router via FastAPI's Depends()."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DATABASE_URL

# Railway hands out "postgresql://" (and some providers still say "postgres://"),
# both of which SQLAlchemy resolves to psycopg2 — a driver that dynamically links
# the system libpq the deploy image doesn't ship. psycopg3's wheels bundle libpq,
# so force that dialect instead of depending on the host having it.
url = DATABASE_URL
for prefix in ("postgresql://", "postgres://"):
    if url.startswith(prefix):
        url = "postgresql+psycopg://" + url[len(prefix):]
        break

# SQLite needs check_same_thread=False because FastAPI can hit the connection
# from different threads within one request lifecycle; Postgres ignores it if passed,
# so we only set it conditionally.
connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
engine = create_engine(url, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields one DB session per request, always closed after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
