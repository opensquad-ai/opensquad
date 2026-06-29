"""
Database connection and session management
"""
import json
import logging
import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models import Base


from opensquad.system_config import syscfg

_log = logging.getLogger("database")


def _write_init_log(msg: str):
    """Directly append to init log file before logging handlers are attached."""
    try:
        log_dir = syscfg.workspace_logs_dir("gateway")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "database_init.log")
        with open(log_path, "a", encoding="utf-8") as f:
            import datetime
            f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass  # Do not let log write failures block startup


def load_config():
    """Load from unified configuration system"""
    _write_init_log("[DB] load_config() called")
    _write_init_log(f"[DB] _WORKSPACE_ROOT BEFORE raw(): {getattr(syscfg, '_WORKSPACE_ROOT', '(unknown)')}")

    # ── Trigger workspace detection first to ensure _WORKSPACE_ROOT is updated ──
    raw_cfg = syscfg.raw()
    _write_init_log(f"[DB] _WORKSPACE_ROOT AFTER  raw(): {getattr(syscfg, '_WORKSPACE_ROOT', '(unknown)')}")

    # ── Now compute db_path with the correct workspace path ──
    db_path = syscfg.workspace_db_path("chat.db")
    _write_init_log(f"[DB] final db_path = {db_path}")

    return {
        "frontend": {
            "port": syscfg.port("frontend"),
            "host": syscfg.host("frontend")
        },
        "backend": {
            "host": syscfg.host("gateway"),
            "port": syscfg.port("gateway"),
            "database": {
                "url": f"sqlite+aiosqlite:///{db_path}",
                "echo": False
            }
        }
    }


# Load configuration
config = load_config()
db_config = config.get("backend", {}).get("database", {})
DATABASE_URL = db_config.get("url", f"sqlite+aiosqlite:///{syscfg.workspace_db_path('chat.db')}")
DATABASE_ECHO = db_config.get("echo", False)

_write_init_log(f"[DB] ENGINE DATABASE_URL = {DATABASE_URL}")

# Ensure database directory exists (workspace may be a new directory with no subdirs yet)
_db_file = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
os.makedirs(os.path.dirname(_db_file), exist_ok=True)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=DATABASE_ECHO,
    poolclass=NullPool,
    connect_args={"check_same_thread": False}
)

# Enable WAL mode: allows concurrent reads and writes, significantly reducing write contention
@event.listens_for(engine.sync_engine, "connect")
def _set_wal_mode(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
    dbapi_connection.execute("PRAGMA cache_size=-16000")   # 16MB page cache
    dbapi_connection.execute("PRAGMA temp_store=MEMORY")

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


async def get_db():
    """Dependency function for getting a database session"""
    async with AsyncSessionLocal() as session:
        # async with auto-closes on exit; do NOT close again in finally (avoids double-close → CancelledError)
        yield session


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_indexes()


async def ensure_indexes():
    """Add missing performance indexes and columns to existing databases (idempotent, safe to re-run)"""
    ddls = [
        "CREATE INDEX IF NOT EXISTS ix_messages_group_id   ON messages (group_id)",
        "CREATE INDEX IF NOT EXISTS ix_messages_timestamp  ON messages (timestamp)",
        "CREATE INDEX IF NOT EXISTS ix_messages_group_ts   ON messages (group_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS ix_ugs_group_id        ON user_group_settings (group_id)",
    ]
    async with engine.begin() as conn:
        for ddl in ddls:
            await conn.execute(text(ddl))
        # Add duration column to attachments table if missing (for voice messages)
        try:
            await conn.execute(text("ALTER TABLE attachments ADD COLUMN duration INTEGER"))
        except Exception:
            pass  # Column already exists
