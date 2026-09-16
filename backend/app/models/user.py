from sqlalchemy import Boolean, Column, Integer, String, DateTime, Enum as SAEnum, Text
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime
import enum


class UserStatus(str, enum.Enum):
    active   = "active"
    inactive = "inactive"
    banned   = "banned"


class User(Base):
    __tablename__ = "users"
    id         = Column(Integer, primary_key=True, index=True)
    username   = Column(String(50),  nullable=False, unique=True, index=True)
    email      = Column(String(255), nullable=False, unique=True, index=True)
    password   = Column(String(255), nullable=False)
    token      = Column(String(255), nullable=True)
    is_admin   = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    status     = Column(SAEnum(UserStatus), default=UserStatus.active, index=True)
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    analyses     = relationship("Analysis",   back_populates="user")
    docking_jobs = relationship("DockingJob", back_populates="user")
    hsa_jobs     = relationship("HSAJob",     back_populates="user")
    research_studies = relationship("ResearchStudy", back_populates="user", cascade="all, delete-orphan")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id           = Column(Integer, primary_key=True, index=True)
    ip_address   = Column(String(45),  nullable=False, index=True)
    identifier   = Column(String(255), nullable=False, index=True)
    user_agent   = Column(Text,        nullable=True)
    attempted_at = Column(DateTime,    default=datetime.utcnow, index=True)
