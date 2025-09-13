from sqlalchemy import Column, String, Float, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from config import DB_PATH

Base = declarative_base()

class MinMax(Base):
    __tablename__ = "minmax"
    pair = Column(String, primary_key=True)
    time = Column(Integer, primary_key=True)  # UNIX-время (сек) начала минутной свечи
    min = Column(Float)
    max = Column(Float)
    mid = Column(Float)
    open = Column(Float)
    close = Column(Float)

class Range24h(Base):
    __tablename__ = "ranges"
    pair = Column(String, primary_key=True)
    ts = Column(Integer, primary_key=True)     # метка минуты (сек, UTC), на которую рассчитан диапазон
    med_min = Column(Float, nullable=False)
    med_max = Column(Float, nullable=False)
    n_rows = Column(Integer, nullable=False)   # сколько свечей вошло в расчёт (до 1440)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
