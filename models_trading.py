# models_trading.py — trading DB models (SQLite + SQLAlchemy)
from sqlalchemy import create_engine, Column, String, Float, Integer, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import contextmanager
from typing import List
from config import DB_PATH

Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id         = Column(String, primary_key=True)
    pair       = Column(String, index=True, nullable=False)
    side       = Column(String, nullable=False)          # BUY / SELL
    price      = Column(Float, nullable=False)
    qty        = Column(Float, nullable=False)
    filled_qty = Column(Float, default=0.0, nullable=False)
    status     = Column(String, default="NEW", nullable=False)
    created    = Column(Integer, default=0, index=True, nullable=False)
    updated    = Column(Integer, default=0, index=True, nullable=False)
    paper      = Column(Boolean, default=True, nullable=False)
    reserved   = Column(Float, default=0.0, nullable=False)  # USD reserved for BUY
    mode       = Column(String, default="", nullable=False)  # "", "GRID", "ABOVE"

class Fill(Base):
    __tablename__ = "fills"
    id       = Column(String, primary_key=True)
    order_id = Column(String, index=True, nullable=False)
    pair     = Column(String, index=True, nullable=False)
    side     = Column(String, nullable=False)       # BUY / SELL
    price    = Column(Float, nullable=False)
    qty      = Column(Float, nullable=False)
    fee      = Column(Float, default=0.0, nullable=False)
    ts       = Column(Integer, index=True, nullable=False)
    mode     = Column(String, default="", nullable=False)

class Position(Base):
    __tablename__ = "position"
    pair    = Column(String, primary_key=True)
    qty     = Column(Float, default=0.0, nullable=False)
    avg     = Column(Float, default=0.0, nullable=False)
    updated = Column(Integer, default=0, nullable=False)

class Capital(Base):
    __tablename__ = "capital"
    pair         = Column(String, primary_key=True)
    limit_usd    = Column(Float, default=0.0, nullable=False)
    available_usd= Column(Float, default=0.0, nullable=False)
    realized_pnl = Column(Float, default=0.0, nullable=False)
    updated      = Column(Integer, default=0, nullable=False)

# --- engine / session ---
_engine = create_engine(
    f"sqlite:////opt/Ebot/{DB_PATH}" if "/" not in DB_PATH else f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionT = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

# --- helpers ---
def _colnames(conn, table: str) -> List[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]  # 1 = name

def _ensure_column(conn, table: str, col: str, ddl: str):
    if col not in _colnames(conn, table):
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def init_trading_db():
    # base tables
    Base.metadata.create_all(_engine)
    # pragmas for this connection
    conn = _engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        conn.commit()
    finally:
        conn.close()

    with _engine.begin() as conn2:
        # orders: reserved, mode, paper, updated
        _ensure_column(conn2, "orders", "reserved", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn2, "orders", "mode",     "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn2, "orders", "paper",    "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn2, "orders", "updated",  "INTEGER NOT NULL DEFAULT 0")
        # fills: fee, mode
        _ensure_column(conn2, "fills", "fee",  "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn2, "fills", "mode", "TEXT NOT NULL DEFAULT ''")
        # position: updated
        _ensure_column(conn2, "position", "updated", "INTEGER NOT NULL DEFAULT 0")
        # capital: realized_pnl, updated
        _ensure_column(conn2, "capital", "realized_pnl", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn2, "capital", "updated",      "INTEGER NOT NULL DEFAULT 0")

# optional: context manager if нужно быстро открыть/закрыть сессию
@contextmanager
def session_scope():
    s = SessionT()
    try:
        # per-session pragmas
        conn = s.connection()
        conn.exec_driver_sql("PRAGMA busy_timeout=5000;")
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        yield s
        s.commit()
    except:
        s.rollback()
        raise
    finally:
        s.close()
