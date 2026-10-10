"""Read-only School21 API, with in-memory OAuth and a closed resource whitelist."""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote, unquote
from uuid import UUID

import httpx

API_URL = "https://platform.21-school.ru/services/21-school/api/v1"
TOKEN_URL = "https://auth.21-school.ru/auth/realms/EduPowerKeycloak/protocol/openid-connect/token"
CLIENT_ID = "s21-open-api"
PROJECT_STATUSES = frozenset(
    {"ASSIGNED", "REGISTERED", "IN_PROGRESS", "IN_REVIEWS", "ACCEPTED", "FAILED"}
)
COURSE_STATUSES = PROJECT_STATUSES - {"IN_REVIEWS"}


class School21Error(Exception):
    """An intentionally safe error; upstream bodies are never used as messages."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Resource:
    template: str
    queries: tuple[str, ...] = ()
    default_limit: int = 50
    max_offset: int = 2**63 - 1


RESOURCES = (
    Resource("participants/{login}"),
    Resource("participants/{login}/points"),
    Resource("participants/{login}/logtime", ("date",)),
    Resource("participants/{login}/feedback"),
    Resource("participants/{login}/skills"),
    Resource("participants/{login}/badges"),
    Resource("participants/{login}/coalition"),
    Resource("participants/{login}/workstation"),
    Resource("participants/{login}/experience-history", ("limit", "offset")),
    Resource("participants/{login}/projects", ("limit", "offset", "status"), 10),
    Resource("participants/{login}/projects/{projectId}"),
    Resource("projects/{projectId}"),
    Resource("projects/{projectId}/participants", ("limit", "offset", "status", "campusId")),
    Resource("participants/{login}/courses", ("limit", "offset", "status"), 10),
    Resource("participants/{login}/courses/{courseId}"),
    Resource("courses/{courseId}"),
    Resource("events", ("from", "to", "type", "limit", "offset")),
    Resource("campuses"),
    Resource("campuses/{campusId}/clusters"),
    Resource("campuses/{campusId}/participants", ("limit", "offset")),
    Resource("campuses/{campusId}/coalitions", ("limit", "offset"), max_offset=2**31 - 1),
    Resource("coalitions/{coalitionId}/participants", ("limit", "offset"), max_offset=2**31 - 1),
    Resource("clusters/{clusterId}/map", ("limit", "offset", "occupied"), max_offset=2**31 - 1),
    Resource("graph"),
    Resource("sales"),
)


def validate_login(login: str) -> str:
    """Validate one decoded segment; do not confuse OAuth usernames with nicknames."""
    if (
        not isinstance(login, str)
        or not 1 <= len(login) <= 128
        or login in {".", ".."}
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in login)
        or any(c in login for c in "/\\%?#")
    ):
        raise School21Error(400, "Некорректный ник участника.")
    try:
        login.encode("utf-8")
    except UnicodeError:
        raise School21Error(400, "Некорректное кодирование ника участника.") from None
    return login


def _integer(value: object, name: str, minimum: int, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise School21Error(400, f"Параметр {name} должен быть целым числом.")
    if isinstance(value, str) and not re.fullmatch(r"[0-9]{1,19}", value):
        raise School21Error(400, f"Параметр {name} должен быть целым числом.")
    result = int(value)
    if not minimum <= result <= maximum:
        raise School21Error(400, f"Параметр {name} вне допустимого диапазона.")
    return result


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise School21Error(400, "campusId должен быть UUID.")
    try:
        result = UUID(value)
    except ValueError:
        raise School21Error(400, "campusId должен быть UUID.") from None
    if str(result) != value.lower():
        raise School21Error(400, "campusId должен быть UUID.")
    return str(result)


def utc_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value
    ):
        raise School21Error(400, f"Параметр {name} требует дату и время UTC (ISO 8601).")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise School21Error(400, f"Некорректная дата в параметре {name}.") from None
    return result


def validate_resource(path: str, params: dict | None = None) -> tuple[Resource, str, dict, dict]:
    """Return resource, canonical encoded path, path variables and validated query."""
    if not isinstance(path, str) or len(path) > 1024:
        raise School21Error(400, "Некорректный путь ресурса.")
    parts = path.removeprefix("/").split("/")
    if any(not part for part in parts):
        raise School21Error(400, "Некорректный путь ресурса.")
    for resource in RESOURCES:
        pattern = resource.template.split("/")
        if len(pattern) != len(parts):
            continue
        variables = {}
        encoded = []
        for expected, actual in zip(pattern, parts, strict=True):
            if not expected.startswith("{"):
                if actual != expected:
                    break
                encoded.append(expected)
                continue
            name = expected[1:-1]
            try:
                value = unquote(actual, errors="strict")
            except UnicodeError:
                raise School21Error(400, "Некорректное кодирование пути.") from None
            if name == "login":
                value = validate_login(value)
            elif name == "campusId":
                value = _uuid(value)
            else:
                value = _integer(value, name, 0)
            variables[name] = value
            encoded.append(quote(str(value), safe=""))
        else:
            query = _validate_query(resource, params)
            return resource, "/".join(encoded), variables, query
    raise School21Error(400, "Ресурс не входит в разрешённый API.")


def _validate_query(resource: Resource, params: dict | None) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict) or any(key not in resource.queries for key in params):
        raise School21Error(400, "Недопустимый query-параметр для этого ресурса.")
    query = {}
    for name, value in params.items():
        if name in {"limit", "offset"}:
            value = _integer(value, name, 1 if name == "limit" else 0,
                              1000 if name == "limit" else resource.max_offset)
        elif name == "campusId":
            value = _uuid(value)
        elif name == "occupied":
            if isinstance(value, bool):
                value = "true" if value else "false"
            if value not in ("true", "false"):
                raise School21Error(400, "occupied должен быть true или false.")
        elif name == "status":
            allowed = COURSE_STATUSES if "/courses" in resource.template else PROJECT_STATUSES
            if not isinstance(value, str) or value not in allowed:
                raise School21Error(400, "Неизвестный статус.")
        elif name == "type":
            if value not in ("ACTIVITY", "EXAM", "TEST"):
                raise School21Error(400, "Неизвестный тип события.")
        elif name == "date":
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                raise School21Error(400, "date должен быть датой YYYY-MM-DD.") from None
        elif name in {"from", "to"}:
            utc_datetime(value, name)
        query[name] = value
    if resource.template == "events":
        if "from" not in query or "to" not in query:
            raise School21Error(400, "Для событий обязательны from и to в UTC.")
        if utc_datetime(query["from"], "from") > utc_datetime(query["to"], "to"):
            raise School21Error(400, "Дата from должна быть не позже to.")
    return query


def _response_error(status: int, *, auth: bool = False) -> School21Error:
    messages = {
        400: "Некорректный запрос к School21.",
        401: "Сессия истекла. Войдите снова.",
        403: "Нет разрешения на чтение этих данных.",
        404: "Данные не найдены.",
        429: "Слишком много запросов. Подождите и повторите попытку.",
    }
    if auth and status in {400, 401}:
        return School21Error(401, "Не удалось войти. Проверьте учётные данные.")
    return School21Error(status if 400 <= status <= 599 else 502,
                         messages.get(status, "School21 временно недоступна. Повторите попытку."))


async def collect_overview(client, login: str | None = None) -> dict:
    nickname = validate_login(login if login is not None else client.login_name)
    prefix = f"participants/{quote(nickname, safe='')}"
    sections = ("participant", "points", "logtime", "feedback", "skills", "badges",
                "coalition", "workstation")
    paths = [prefix if section == "participant" else f"{prefix}/{section}" for section in sections]
    results = await asyncio.gather(*(client.get(path) for path in paths), return_exceptions=True)
    overview = {"errors": {}}
    for section, result in zip(sections, results, strict=True):
        if isinstance(result, School21Error):
            overview[section] = None
            overview["errors"][section] = result.message
        elif isinstance(result, BaseException):
            # Unexpected programming errors must not leak exception text to the browser.
            if not isinstance(result, Exception):
                raise result
            overview[section] = None
            overview["errors"][section] = "Не удалось загрузить раздел. Повторите попытку."
        else:
            overview[section] = result
    return overview


class School21Client:
    demo = False

    def __init__(self, *, transport=None) -> None:
        self.login_name: str | None = None
        self._http = httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False,
                                      trust_env=False)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._refresh_expires_at: float | None = None
        self._revision = 0
        # A token rotation retains the identity; login/logout invalidate in-flight reads.
        self._generation = 0
        self._auth_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._closed = False

    @property
    def authenticated(self) -> bool:
        return self._access_token is not None and not self._closed

    def _clear_auth(self) -> None:
        self._access_token = self._refresh_token = self.login_name = None
        self._expires_at = 0.0
        self._refresh_expires_at = None
        self._revision += 1
        self._generation += 1

    def _require_generation(self, generation: int) -> None:
        if generation != self._generation or self._closed:
            raise _response_error(401)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise School21Error(504, "School21 не ответила вовремя. Повторите попытку.") from None
        except httpx.RequestError:
            raise School21Error(502, "Не удалось связаться со School21. Проверьте подключение.") from None

    async def _tokens(self, form: dict, generation: int, *, refresh: bool = False) -> None:
        response = await self._request("POST", TOKEN_URL, data=form)
        # A delayed OAuth response must never resurrect a logged-out client.
        self._require_generation(generation)
        if response.status_code != 200:
            invalid_grant = False
            try:
                body = response.json()
                invalid_grant = isinstance(body, dict) and body.get("error") == "invalid_grant"
            except ValueError:
                pass
            if refresh and (invalid_grant or response.status_code in {400, 401}):
                self._clear_auth()
                raise _response_error(401)
            raise _response_error(response.status_code, auth=True)
        try:
            body = response.json()
            access = body["access_token"]
            refresh_token = body["refresh_token"]
            lifetime = body["expires_in"]
            refresh_lifetime = body.get("refresh_expires_in")
            if not all(isinstance(token, str) and token and token.isascii()
                       and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token)
                       for token in (access, refresh_token)):
                raise ValueError
            for duration in (lifetime, refresh_lifetime):
                if duration is not None and (isinstance(duration, bool)
                        or not isinstance(duration, (int, float))
                        or not math.isfinite(duration) or duration < 0):
                    raise ValueError
            if lifetime is None:
                raise ValueError
        except (ValueError, KeyError, TypeError, OverflowError):
            self._clear_auth()
            raise School21Error(502, "School21 вернула некорректный ответ авторизации.") from None
        now = time.monotonic()
        self._access_token, self._refresh_token = access, refresh_token
        # Refresh slightly before expiry, while preserving very short-lived tokens.
        self._expires_at = now + lifetime - min(15, lifetime * 0.1)
        self._refresh_expires_at = now + refresh_lifetime if refresh_lifetime is not None else None
        self._revision += 1

    async def login(self, username: str, password: str,
                    participant_login: str | None = None) -> None:
        if (not isinstance(username, str) or not username or len(username) > 512
                or not isinstance(password, str) or not password or len(password) > 4096):
            raise School21Error(400, "Введите имя пользователя и пароль.")
        try:
            username.encode("utf-8")
            password.encode("utf-8")
        except UnicodeError:
            raise School21Error(400, "Некорректное кодирование учётных данных.") from None
        nickname = validate_login(participant_login if participant_login is not None
                                  else username.split("@", 1)[0])
        generation = self._generation
        async with self._auth_lock:
            self._require_generation(generation)
            self._clear_auth()
            generation = self._generation
            try:
                await self._tokens({"client_id": CLIENT_ID, "grant_type": "password",
                                    "username": username, "password": password}, generation)
            except asyncio.CancelledError:
                if generation == self._generation:
                    self._clear_auth()
                raise
            self.login_name = nickname

    async def _do_refresh(self, revision: int) -> None:
        async with self._auth_lock:
            if revision != self._revision:
                return
            if (not self.authenticated or not self._refresh_token
                    or (self._refresh_expires_at is not None
                        and time.monotonic() >= self._refresh_expires_at)):
                self._clear_auth()
                raise _response_error(401)
            await self._tokens({"client_id": CLIENT_ID, "grant_type": "refresh_token",
                                 "refresh_token": self._refresh_token}, self._generation, refresh=True)

    @staticmethod
    def _observe_refresh(task: asyncio.Task) -> None:
        # All callers may have been cancelled while the shielded refresh was pending.
        if not task.cancelled():
            task.exception()

    async def _refresh(self, revision: int) -> None:
        if revision != self._revision:
            return
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._do_refresh(revision))
            self._refresh_task.add_done_callback(self._observe_refresh)
        # One cancelled section must not cancel the refresh needed by other sections.
        try:
            await asyncio.shield(self._refresh_task)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # Logout cancelled the worker, rather than this requesting task.
            raise _response_error(401) from None

    async def get(self, path: str, params: dict | None = None) -> dict | list | float:
        _, encoded, _, query = validate_resource(path, params)
        if not self.authenticated:
            raise _response_error(401)
        generation = self._generation
        if time.monotonic() >= self._expires_at:
            await self._refresh(self._revision)
        for attempt in range(2):
            self._require_generation(generation)
            revision = self._revision
            if not self.authenticated:
                raise _response_error(401)
            response = await self._request("GET", f"{API_URL}/{encoded}", params=query,
                                            headers={"Authorization": f"Bearer {self._access_token}"})
            self._require_generation(generation)
            if response.status_code == 401 and attempt == 0:
                await self._refresh(revision)
                continue
            if response.status_code != 200:
                if response.status_code == 401:
                    async with self._auth_lock:
                        if revision == self._revision:
                            self._clear_auth()
                raise _response_error(response.status_code)
            try:
                payload = response.json()
                if isinstance(payload, bool) or not isinstance(payload, (dict, list, int, float)):
                    raise TypeError
                if isinstance(payload, float) and not math.isfinite(payload):
                    raise ValueError
                return payload
            except (ValueError, TypeError):
                raise School21Error(502, "School21 вернула некорректные данные.") from None
        raise _response_error(401)

    async def overview(self, login: str | None = None) -> dict:
        if not self.authenticated:
            raise _response_error(401)
        return await collect_overview(self, login)

    async def logout(self) -> None:
        # Invalidate first: logout must not wait for a slow token endpoint or GET.
        self._clear_auth()
        task, self._refresh_task = self._refresh_task, None
        if task is not None and not task.done():
            task.cancel()

    async def close(self) -> None:
        self._closed = True
        await self.logout()
        await self._http.aclose()
