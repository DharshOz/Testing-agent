import os
from sqlmodel import SQLModel, create_engine, Session

DATABASE_FILE = "tickets.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

# Connect args for SQLite to allow multi-thread access
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
