"""Process-wide services shared by the API, the worker and the admin CLI."""

from __future__ import annotations

from dataclasses import dataclass, field

from fsweb.db import Database
from fsweb.engine import Engine, make_engine
from fsweb.security.crypto import FieldCipher, LocalKMS
from fsweb.security.ratelimit import RateLimiter, make_rate_limiter
from fsweb.settings import Settings
from fsweb.storage import Storage, make_storage


@dataclass
class AppState:
    settings: Settings
    db: Database
    storage: Storage
    engine: Engine
    cipher: FieldCipher
    limiter: RateLimiter
    component: str = "api"  # api | worker | ai-gateway | cli
    extras: dict[str, object] = field(default_factory=dict)


def build_state(settings: Settings, *, engine: Engine | None = None, storage: Storage | None = None, component: str = "api") -> AppState:
    db = Database(settings.database_url, settings.app_database_url)
    st = storage or make_storage(
        settings.storage,
        endpoint=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
    return AppState(
        settings=settings,
        db=db,
        storage=st,
        engine=engine or make_engine(settings.engine),
        cipher=FieldCipher(LocalKMS(settings.master_keys())),
        limiter=make_rate_limiter(settings.redis_url),
        component=component,
    )
