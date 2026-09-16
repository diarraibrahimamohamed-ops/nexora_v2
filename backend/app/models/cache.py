from sqlalchemy import Column, Integer, String, Text, DateTime
from app.database import Base
from datetime import datetime


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    id          = Column(Integer,    primary_key=True, index=True)
    cache_key   = Column(String(255), nullable=False, unique=True, index=True)
    cache_value = Column(Text,        nullable=False)
    expires_at  = Column(DateTime,    nullable=False, index=True)
    created_at  = Column(DateTime,    default=datetime.utcnow)
