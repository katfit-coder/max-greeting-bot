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
    step = Column(String, default="idle")
    occasion = Column(String, default="")
    style = Column(String, default="")
    extra_wish = Column(Text, default="")
    recipient_info = Column(Text, default="")
    sender_name = Column(String, default="")
    recipient_name = Column(String, default="")
    custom_occasion = Column(Text, default="")
    channel = Column(String, default="")
    generated_text = Column(Text, default="")
    generated_image = Column(LargeBinary, nullable=True)
    schedule_mode = Column(Integer, default=0)
    scheduled_at = Column(DateTime, nullable=True)
    display_name = Column(String, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScheduledGreeting(Base):
    __tablename__ = "scheduled_greetings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    chat_id = Column(Integer, nullable=False)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    channel = Column(String, nullable=False)
    recipient_contact = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    image_id = Column(Integer, nullable=True)
    occasion = Column(String, default="")
    custom_occasion = Column(String, default="")
    style = Column(String, default="")
    recipient_info = Column(Text, default="")
    status = Column(String, default="pending", index=True)   # pending | sent | failed
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)


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
    custom_occasion = Column(String, default="")
    style = Column(String)
    channel = Column(String)
    recipient_contact = Column(String)
    recipient_info = Column(Text, default="")
    extra_wish = Column(Text, default="")
    text = Column(Text)
    has_image = Column(Integer, default=0)
    image_id = Column(Integer, nullable=True)
    sender_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # индекс для процессора очереди — ускоряет SELECT pending+due
    from sqlalchemy import text as _t
    try:
        with engine.begin() as conn:
            conn.execute(_t("CREATE INDEX IF NOT EXISTS ix_scheduled_status_at ON scheduled_greetings(status, scheduled_at)"))
    except Exception:
        pass
    # lightweight migration: add new columns if table already exists from previous deploys
    from sqlalchemy import text
    new_columns = [
        ("user_states", "recipient_info", "TEXT DEFAULT ''"),
        ("user_states", "custom_occasion", "TEXT DEFAULT ''"),
        ("user_states", "schedule_mode", "INTEGER DEFAULT 0"),
        ("user_states", "scheduled_at", "DATETIME"),
        ("user_states", "display_name", "TEXT DEFAULT ''"),
        ("sent_greetings", "custom_occasion", "TEXT DEFAULT ''"),
        ("sent_greetings", "recipient_info", "TEXT DEFAULT ''"),
        ("sent_greetings", "extra_wish", "TEXT DEFAULT ''"),
        ("sent_greetings", "image_id", "INTEGER"),
        ("sent_greetings", "sender_user_id", "INTEGER"),
    ]
    with engine.begin() as conn:
        for table, col, ddl in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception:
                pass  # column already exists
