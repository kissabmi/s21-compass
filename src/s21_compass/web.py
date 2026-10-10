"""Loopback-only dashboard backend. OAuth secrets never leave server memory."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .client import School21Client, School21Error, validate_login, validate_resource
from .demo import DemoClient

COOKIE_NAME = "s21_compass_session"
SESSION_IDLE_SECONDS = 3600
SESSION_MAX_SECONDS = 8 * 3600
MAX_SESSIONS = 256
CLIENT_CLOSE_SECONDS = 5
STATIC_DIR = Path(__file__).with_name("static")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@dataclass
class BrowserSession:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    client: object | None = field(default=None, repr=False)
    created_at: float = field(default_factory=time.monotonic)
    touched_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def expired(self) -> bool:
        now = time.monotonic()
        return (now - self.touched_at >= SESSION_IDLE_SECONDS
                or now - self.created_at >= SESSION_MAX_SECONDS)

    def public(self) -> dict:
        authenticated = self.client is not None and self.client.authenticated
        return {"authenticated": bool(authenticated),
                "login": self.client.login_name if authenticated else None,
                "demo": bool(authenticated and self.client.demo), "csrf": self.csrf}


def _check_loopback(request: Request) -> None:
    """Use ASGI's bound server port, not forwarding headers or the supplied Host."""
    server = request.scope.get("server")
    scheme = request.scope["scheme"]
    if not server or server[0] not in {"localhost", "127.0.0.1", "::1"} or scheme not in {"http", "https"}:
        raise School21Error(403, "Кабинет доступен только через loopback.")
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        raise School21Error(403, "Недопустимый Host.")
    host = hosts[0]
    try:
        parsed = urlsplit(f"{scheme}://{host}")
        port = parsed.port or (443 if scheme == "https" else 80)
        if (parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or port != server[1]
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or host != host.strip() or any(c.isspace() for c in host)):
            raise ValueError
        hostname = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
        canonical = hostname + (f":{port}" if port != (443 if scheme == "https" else 80) else "")
        if host not in {canonical, f"{hostname}:{port}"}:
            raise ValueError
    except ValueError:
        raise School21Error(403, "Недопустимый Host или порт.") from None
    origins = request.headers.getlist("origin")
    if len(origins) > 1 or (request.method not in {"GET", "HEAD"} and not origins):
        raise School21Error(403, "Требуется same-origin запрос.")
    if origins and origins[0] != f"{scheme}://{canonical}":
        raise School21Error(403, "Недопустимый Origin.")


def create_app(*, client_factory=School21Client) -> FastAPI:
    sessions: dict[str, BrowserSession] = {}

    async def dispose(session: BrowserSession) -> None:
        if session.client is not None:
            client, session.client = session.client, None
            await close_client(client)

    async def close_client(client) -> None:
        # A faulty embedding client must not stall cleanup or keep other clients alive.
        with suppress(Exception):
            await asyncio.wait_for(client.close(), timeout=CLIENT_CLOSE_SECONDS)

    async def cleanup() -> None:
        expired = []
        for identifier, session in list(sessions.items()):
            if session.expired() and not session.lock.locked():
                async with session.lock:
                    if sessions.get(identifier) is session and session.expired():
                        sessions.pop(identifier)
                        expired.append(session)
        await asyncio.gather(*(dispose(session) for session in expired))

    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(60)
            await cleanup()

    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            remaining = list(sessions.values())
            sessions.clear()
            await asyncio.gather(*(dispose(session) for session in remaining))

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions

    @app.exception_handler(School21Error)
    async def safe_error(request: Request, error: School21Error):
        return JSONResponse({"error": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse({"error": "Некорректный запрос."}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse({"error": "Ресурс не найден." if error.status_code == 404
                             else "Запрос не поддерживается."}, status_code=error.status_code)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        try:
            _check_loopback(request)
            await cleanup()
            response = await call_next(request)
        except School21Error as error:
            response = JSONResponse({"error": error.message}, status_code=error.status)
        except Exception:  # noqa: BLE001 -- no exception text or traceback may echo credentials
            response = JSONResponse({"error": "Не удалось обработать запрос. Повторите попытку."},
                                    status_code=500)
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        return response

    def current(request: Request, *, mutation: bool = False) -> tuple[str, BrowserSession]:
        identifier = request.cookies.get(COOKIE_NAME, "")
        session = sessions.get(identifier)
        if session is None or session.expired():
            raise School21Error(403 if mutation else 401, "Сессия истекла. Обновите страницу.")
        if mutation:
            tokens = request.headers.getlist("x-csrf-token")
            if len(tokens) != 1 or not secrets.compare_digest(
                tokens[0].encode("utf-8"), session.csrf.encode("ascii")
            ):
                raise School21Error(403, "Недопустимый CSRF token. Обновите страницу.")
        session.touched_at = time.monotonic()
        return identifier, session

    def still_current(identifier: str, session: BrowserSession) -> None:
        if sessions.get(identifier) is not session or session.expired():
            raise School21Error(401, "Сессия изменилась. Обновите страницу.")

    def cookie(response: JSONResponse, identifier: str, request: Request) -> JSONResponse:
        response.set_cookie(COOKIE_NAME, identifier, httponly=True, samesite="strict",
                            secure=request.scope["scheme"] == "https", path="/",
                            max_age=SESSION_MAX_SECONDS)
        return response

    def rotate(identifier: str, session: BrowserSession, client, request: Request) -> JSONResponse:
        sessions.pop(identifier, None)
        replacement = BrowserSession(client=client)
        new_identifier = secrets.token_urlsafe(32)
        sessions[new_identifier] = replacement
        return cookie(JSONResponse(replacement.public()), new_identifier, request)

    @app.get("/api/session")
    async def session_info(request: Request):
        identifier = request.cookies.get(COOKIE_NAME, "")
        session = sessions.get(identifier)
        if session is None:
            if len(sessions) >= MAX_SESSIONS:
                raise School21Error(429, "Слишком много локальных сессий. Подождите и повторите.")
            identifier = secrets.token_urlsafe(32)
            session = BrowserSession()
            sessions[identifier] = session
        async with session.lock:
            still_current(identifier, session)
            session.touched_at = time.monotonic()
            return cookie(JSONResponse(session.public()), identifier, request)

    @app.post("/api/login")
    async def login(request: Request):
        identifier, session = current(request, mutation=True)
        # Bounded body parsing avoids storing credentials and validation-error echoes.
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise School21Error(400, "Вход требует JSON.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise School21Error(400, "Слишком большой запрос входа.")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise School21Error(400, "Некорректный JSON.") from None
        if not isinstance(payload, dict) or payload.keys() - {"username", "password", "login"}:
            raise School21Error(400, "Некорректные поля входа.")
        username, password = payload.get("username"), payload.get("password")
        nickname = payload.get("login")
        if (not isinstance(username, str) or not 1 <= len(username) <= 512
                or not isinstance(password, str) or not 1 <= len(password) <= 4096):
            raise School21Error(400, "Введите имя пользователя и пароль.")
        validate_login(nickname if nickname is not None else username.split("@", 1)[0])
        async with session.lock:
            still_current(identifier, session)
            client = client_factory()
            transferred = False
            try:
                await client.login(username, password, nickname)
                await dispose(session)
                still_current(identifier, session)
                response = rotate(identifier, session, client, request)
                transferred = True
                return response
            finally:
                if not transferred:
                    await close_client(client)

    @app.post("/api/demo")
    async def demo(request: Request):
        identifier, session = current(request, mutation=True)
        async with session.lock:
            still_current(identifier, session)
            await dispose(session)
            still_current(identifier, session)
            return rotate(identifier, session, DemoClient(), request)

    @app.post("/api/logout")
    async def logout(request: Request):
        identifier, session = current(request, mutation=True)
        async with session.lock:
            still_current(identifier, session)
            await dispose(session)
            still_current(identifier, session)
            return rotate(identifier, session, None, request)

    def authenticated(session: BrowserSession):
        if session.client is None or not session.client.authenticated:
            raise School21Error(401, "Войдите в аккаунт или откройте демо.")
        return session.client

    @app.get("/api/overview/{login}")
    async def overview(login: str, request: Request):
        identifier, session = current(request)
        async with session.lock:
            still_current(identifier, session)
            client = authenticated(session)
            result = await client.overview(validate_login(login))
            still_current(identifier, session)
            if not client.authenticated:
                raise School21Error(401, "Сессия истекла. Войдите снова.")
            return result

    @app.get("/api/data/{path:path}")
    async def data(path: str, request: Request):
        identifier, session = current(request)
        items = list(request.query_params.multi_items())
        if len({key for key, _ in items}) != len(items):
            raise School21Error(400, "Query-параметры не должны повторяться.")
        params = dict(items)
        # Validate here as well, even if a custom factory is used by embedding code.
        validate_resource(path, params)
        async with session.lock:
            still_current(identifier, session)
            result = await authenticated(session).get(path, params)
            still_current(identifier, session)
            return result

    @app.get("/")
    async def index():
        if not (STATIC_DIR / "index.html").is_file():
            raise School21Error(503, "Веб-интерфейс ещё не установлен.")
        return FileResponse(STATIC_DIR / "index.html")

    # Frontend may be written in parallel; importing the backend needs no assets.
    app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return app
