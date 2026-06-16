import time as _time
from datetime import datetime, timezone
from pathlib import Path

# Set ONCE when this module is first imported (= process start). The UI compares
# it to source-file mtimes to warn when code changed but the process is stale
# (Streamlit caches imported modules across reruns).
IMPORT_TIME = _time.time()

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR / 'app.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def _utcnow() -> datetime:
    """Timezone-aware UTC 'now' as a naive value (matches SQLite's naive storage).

    Replaces the deprecated datetime.utcnow() while keeping comparisons naive-vs-naive.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)
    upwork_email = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)

    tasks = relationship("Task", back_populates="company")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    strategy = Column(Text, nullable=True)
    is_active = Column(Integer, default=0)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)

    company = relationship("Company", back_populates="tasks")
    messages = relationship("TaskMessage", back_populates="task")


class TaskMessage(Base):
    __tablename__ = "task_messages"

    id = Column(Integer, primary_key=True, index=True)

    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)

    role = Column(String(50), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)

    created_at = Column(DateTime, default=_utcnow)

    task = relationship("Task", back_populates="messages")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)

    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    budget = Column(String(100), nullable=True)
    source_url = Column(Text, nullable=True, index=True)
    upwork_job_id = Column(String(100), nullable=True, index=True)

    status = Column(String(50), default="NEW")

    fit_score = Column(Integer, nullable=True)
    risk_score = Column(Integer, nullable=True)
    ai_decision = Column(String(50), nullable=True)
    ai_summary = Column(Text, nullable=True)
    skip_reason = Column(Text, nullable=True)
    ai_analysis_json = Column(Text, nullable=True)

    outcome = Column(String(50), nullable=True)  # REPLIED | INTERVIEW | WIN | LOST
    loss_reason = Column(Text, nullable=True)
    revenue = Column(Integer, nullable=True)  # USD booked on WIN

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)

    proposals = relationship("Proposal", back_populates="job")


class CaseStudy(Base):
    __tablename__ = "case_studies"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(255), nullable=False)
    niche = Column(String(255), nullable=True)
    stack = Column(Text, nullable=True)
    description = Column(Text, nullable=False)
    result = Column(Text, nullable=True)
    budget_range = Column(String(100), nullable=True)
    url = Column(Text, nullable=True)
    deleted = Column(Integer, default=0)
    # 1 = LLM-fabricated case tailored to a job (owner takes ToS responsibility).
    synthetic = Column(Integer, default=0)
    # Path to a generated attachment (PDF/PNG one-pager) for the Upwork proposal.
    artifact_path = Column(Text, nullable=True)
    # Job this synthetic case was generated for (NULL for manual base cases).
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class Proposal(Base):
    __tablename__ = "proposals"

    id = Column(Integer, primary_key=True, index=True)

    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)

    status = Column(String(50), default="DRAFT")
    content = Column(Text, nullable=False)
    estimate = Column(Text, nullable=True)
    selected_cases = Column(Text, nullable=True)
    connects_spent = Column(Integer, nullable=True)
    connects_cost = Column(Integer, nullable=True)  # per-proposal cost read from the form
    submitted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)

    job = relationship("Job", back_populates="proposals")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=True)
    platform = Column(String(100), default="Upwork")
    status = Column(String(50), default="NEW")
    job_id = Column(Integer, nullable=True)
    external_id = Column(String(255), nullable=True, index=True)  # Upwork roomId
    notes = Column(Text, nullable=True)
    ai_enabled = Column(Integer, default=1)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)

    messages = relationship("ClientMessage", back_populates="client")


class ClientMessage(Base):
    __tablename__ = "client_messages"

    id = Column(Integer, primary_key=True, index=True)

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)

    direction = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    ai_draft = Column(Text, nullable=True)
    external_id = Column(String(255), nullable=True, index=True)  # Upwork storyId

    created_at = Column(DateTime, default=_utcnow)

    client = relationship("Client", back_populates="messages")


class AgentChatMessage(Base):
    """Free-form operator↔agent chat (the «Чат с агентом» tab). Persistent history."""
    __tablename__ = "agent_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    role = Column(String(20), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class AgentQuestion(Base):
    __tablename__ = "agent_questions"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    job_id = Column(Integer, nullable=True)

    text = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    status = Column(String(50), default="open")  # open | answered

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id = Column(Integer, primary_key=True, index=True)
    day = Column(String(20), index=True)  # YYYY-MM-DD
    task_id = Column(Integer, nullable=True)
    metrics_json = Column(Text, nullable=True)
    text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow)


def _migrate_sqlite():
    """Lightweight in-place migrations for an existing SQLite file.

    SQLAlchemy's create_all() creates missing tables but never ALTERs existing
    ones, so new columns on a pre-existing table must be added manually.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "jobs" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("jobs")}
        with engine.begin() as conn:
            if "task_id" not in cols:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN task_id INTEGER"))
            if "upwork_job_id" not in cols:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN upwork_job_id VARCHAR(100)"))
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_jobs_upwork_job_id ON jobs (upwork_job_id)")
                )
            for col, ddl in (
                ("outcome", "ALTER TABLE jobs ADD COLUMN outcome VARCHAR(50)"),
                ("loss_reason", "ALTER TABLE jobs ADD COLUMN loss_reason TEXT"),
                ("revenue", "ALTER TABLE jobs ADD COLUMN revenue INTEGER"),
            ):
                if col not in cols:
                    conn.execute(text(ddl))

    if "case_studies" in inspector.get_table_names():
        ccols = {c["name"] for c in inspector.get_columns("case_studies")}
        with engine.begin() as conn:
            if "deleted" not in ccols:
                conn.execute(text("ALTER TABLE case_studies ADD COLUMN deleted INTEGER DEFAULT 0"))
            if "updated_at" not in ccols:
                conn.execute(text("ALTER TABLE case_studies ADD COLUMN updated_at DATETIME"))
            if "synthetic" not in ccols:
                conn.execute(text("ALTER TABLE case_studies ADD COLUMN synthetic INTEGER DEFAULT 0"))
            if "artifact_path" not in ccols:
                conn.execute(text("ALTER TABLE case_studies ADD COLUMN artifact_path TEXT"))
            if "job_id" not in ccols:
                conn.execute(text("ALTER TABLE case_studies ADD COLUMN job_id INTEGER"))

    if "proposals" in inspector.get_table_names():
        pcols = {c["name"] for c in inspector.get_columns("proposals")}
        with engine.begin() as conn:
            if "connects_spent" not in pcols:
                conn.execute(text("ALTER TABLE proposals ADD COLUMN connects_spent INTEGER"))
            if "connects_cost" not in pcols:
                conn.execute(text("ALTER TABLE proposals ADD COLUMN connects_cost INTEGER"))
            if "submitted_at" not in pcols:
                conn.execute(text("ALTER TABLE proposals ADD COLUMN submitted_at DATETIME"))

    if "clients" in inspector.get_table_names():
        clcols = {c["name"] for c in inspector.get_columns("clients")}
        with engine.begin() as conn:
            if "external_id" not in clcols:
                conn.execute(text("ALTER TABLE clients ADD COLUMN external_id VARCHAR(255)"))

    if "client_messages" in inspector.get_table_names():
        cmcols = {c["name"] for c in inspector.get_columns("client_messages")}
        with engine.begin() as conn:
            if "external_id" not in cmcols:
                conn.execute(text("ALTER TABLE client_messages ADD COLUMN external_id VARCHAR(255)"))


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()


def get_db_session():
    return SessionLocal()


if __name__ == "__main__":
    init_db()
    print(f"Database created: {DATA_DIR / 'app.db'}")