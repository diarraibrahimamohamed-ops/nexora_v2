from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from passlib.context import CryptContext
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.user import User, UserStatus

# passlib 1.7.4 lit bcrypt.__about__, retiré depuis bcrypt 4.1.
try:
    import bcrypt as _bcrypt
    if not hasattr(_bcrypt, "__about__"):
        class _About:
            __version__ = getattr(_bcrypt, "__version__", "4.1.2")
        _bcrypt.__about__ = _About()
except Exception:
    pass

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

def hash_password(p: str) -> str:
    # Never silently truncate a password: registration must reject >72 UTF-8 bytes.
    if len(p.encode("utf-8")) > 72:
        raise ValueError("Mot de passe trop long pour bcrypt (maximum 72 octets UTF-8)")
    return pwd_context.hash(p)

def verify_password(plain: str, hashed: str) -> bool:
    try:
        if len(plain.encode("utf-8")) > 72:
            return False
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError, AttributeError):
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token invalide ou expiré",
                            headers={"WWW-Authenticate": "Bearer"})

def get_current_user(token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> User:
    payload = decode_token(token)
    uid = payload.get("sub")
    if not isinstance(uid, str) or not uid.isdecimal() or len(uid) > 20:
        raise HTTPException(401, "Token invalide")
    user = db.query(User).filter(User.id == int(uid)).first()
    if not user or user.status != UserStatus.active:
        raise HTTPException(401, "Utilisateur inactif ou introuvable")
    return user


def is_admin_user(user: User) -> bool:
    bootstrap = (settings.ADMIN_EMAIL or "").strip().lower()
    return bool(user.is_admin) or (bool(bootstrap) and user.email.lower() == bootstrap)


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require database admin flag or the explicitly configured bootstrap email."""
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Droits administrateur requis")
    return current_user

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    ident = (email or "").strip()
    if not ident:
        return None
    user = (
        db.query(User)
        .filter(or_(func.lower(User.email) == ident.lower(), User.username == ident))
        .first()
    )
    if not user or user.status != UserStatus.active:
        return None
    return user if verify_password(password, user.password) else None
