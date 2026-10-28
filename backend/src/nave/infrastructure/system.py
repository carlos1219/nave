import secrets
import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class RandomIdGenerator:
    def new_uuid(self) -> uuid.UUID:
        return uuid.uuid4()


class RandomTokenSource:
    def new_token(self) -> str:
        return secrets.token_urlsafe(32)
