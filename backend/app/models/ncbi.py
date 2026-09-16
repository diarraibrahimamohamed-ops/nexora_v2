from sqlalchemy import Column, Integer, String, Text, DateTime
from app.database import Base
from datetime import datetime


class NcbiSequence(Base):
    __tablename__ = "ncbi_sequences"
    id            = Column(Integer,    primary_key=True, index=True)
    accession     = Column(String(100), nullable=False, unique=True, index=True)
    database_name = Column(String(50),  nullable=False)
    organism      = Column(String(255), nullable=True, index=True)
    title         = Column(Text,        nullable=True)
    sequence      = Column(Text,        nullable=False)
    length        = Column(Integer,     nullable=False)
    cached_at     = Column(DateTime, default=datetime.utcnow, index=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
