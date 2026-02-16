from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import bcrypt as _bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer()

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


@dataclass
class ReviewUser:
    id: int
    username: str


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hashed.encode())


def create_review_token(user_id: int, username: str) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.review_jwt_secret, algorithm=ALGORITHM)


async def verify_review_token(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> ReviewUser:
    settings = get_settings()
    try:
        payload = jwt.decode(
            creds.credentials, settings.review_jwt_secret, algorithms=[ALGORITHM]
        )
        return ReviewUser(id=int(payload["sub"]), username=payload["username"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid token")
