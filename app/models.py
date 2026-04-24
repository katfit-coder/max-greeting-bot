from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class UserState(Base):
    __tablename__ = "user_states"
    user_id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, nullable=False)
    step = Column(String, default="idle")      # idle|choose_occasion|choose_style|preview|choose_channel|await_contact
    occasion = Column(String, default="")
    style = Column(String, default="")
    extra_wish = Column(Text, default="")
    recipient_info = Column(Text, default="")
    sender_name = Column(String, default="")
    recipient_name = Column(String, default="")
    custom_occasion = Column(Text, default="")
    channel = Column(String, default="")       # max|email
    generated_text = Column(Text, default="")
    generated_image = Column(LargeBinary, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostedImage(Base):
    __tablename__ = "hosted_images"
    id = Column(Integer, primary_key=True)
    content = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SentGreeting(Base):
    __tablename__ = "sent_greetings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    occasion = Column(String)
    style = Column(String)
    channel = Column(String)
    recipient_contact = Column(String)
    text = Column(Text)
    has_image = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # lightweight migration: add new columns if table already exists from previous deploys
    from sqlalchemy import text
    new_columns = [
        ("user_states", "recipient_info", "TEXT DEFAULT ''"),
        ("user_states", "custom_occasion", "TEXT DEFAULT ''"),
    ]
    with engine.begin() as conn:
        for table, col, ddl in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception:
                pass  # column already exists
