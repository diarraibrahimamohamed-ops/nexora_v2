from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator

from app.database import get_db
from app.models.user import User, UserStatus, LoginAttempt
from app.services.auth_service import (
    hash_password,
    authenticate_user,
    create_access_token,
    get_current_user,
    is_admin_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Rate-limit : 5 échecs / 15 min par IP ou identifiant
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        import re

        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", v):
            raise ValueError("Email invalide")
        return v.lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    is_admin: bool = False


class LoginRequest(BaseModel):
    email: str
    password: str
    remember: bool = False


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    if request.client and request.client.host:
        return request.client.host[:45]
    return "unknown"


def _is_locked(db: Session, ip: str, identifier: str) -> bool:
    since = datetime.utcnow() - timedelta(minutes=LOCKOUT_MINUTES)
    q = (
        db.query(LoginAttempt)
        .filter(LoginAttempt.attempted_at >= since)
        .filter(
            (LoginAttempt.ip_address == ip) | (LoginAttempt.identifier == identifier)
        )
    )
    return q.count() >= MAX_FAILED_ATTEMPTS


def _record_failure(db: Session, ip: str, identifier: str, user_agent: Optional[str]):
    db.add(
        LoginAttempt(
            ip_address=ip or "unknown",
            identifier=(identifier or "")[:255],
            user_agent=(user_agent or "")[:2000] if user_agent else None,
        )
    )
    db.commit()


def _issue_token(user: User) -> TokenResponse:
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        is_admin=is_admin_user(user),
    )


@router.post("/register", status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email déjà utilisé")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "Nom d'utilisateur déjà utilisé")
    if len(body.password) < 8:
        raise HTTPException(400, "Mot de passe trop court (min 8 caractères)")
    user = User(
        username=body.username,
        email=body.email,
        password=hash_password(body.password),
        status=UserStatus.active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"success": True, "message": "Compte créé", "user_id": user.id}


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login JSON (frontend Nexora)."""
    ip = _client_ip(request)
    if _is_locked(db, ip, body.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives — réessayez dans {LOCKOUT_MINUTES} minutes",
        )
    user = authenticate_user(db, email=body.email, password=body.password)
    if not user:
        _record_failure(db, ip, body.email, request.headers.get("user-agent"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return _issue_token(user)


@router.post("/token", response_model=TokenResponse)
def login_form(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Login OAuth2 form-data (Swagger Authorize / OAuth2PasswordBearer).
    username = email Nexora.
    """
    ip = _client_ip(request)
    identifier = form_data.username
    if _is_locked(db, ip, identifier):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives — réessayez dans {LOCKOUT_MINUTES} minutes",
        )
    user = authenticate_user(db, email=identifier, password=form_data.password)
    if not user:
        _record_failure(db, ip, identifier, request.headers.get("user-agent"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return _issue_token(user)


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "status": current_user.status,
        "is_admin": is_admin_user(current_user),
    }
