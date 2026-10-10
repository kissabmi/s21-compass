"""Local ASGI + mock upstream evidence; these tests never authenticate against School21."""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from s21_compass.client import School21Client, School21Error
from s21_compass.web import COOKIE_NAME, create_app

ORIGIN = "http://127.0.0.1:8765"


def browser(app, *, origin=ORIGIN):
    async def bound_app(scope, receive, send):
        # ASGITransport uses None for implicit ports; real ASGI servers supply the bound port.
        if scope["server"][1] is None:
            scope = {**scope, "server": (scope["server"][0],
                                        443 if scope["scheme"] == "https" else 80)}
        await app(scope, receive, send)

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=bound_app), base_url=origin)


async def csrf_headers(browser):
    session = (await browser.get("/api/session")).json()
    return {"Origin": str(browser.base_url).rstrip("/"), "X-CSRF-Token": session["csrf"]}


async def enter_demo(browser):
    response = await browser.post("/api/demo", headers=await csrf_headers(browser))
    assert response.status_code == 200
    return response


@pytest.fixture
async def mock_web():
    clients, requests = [], []

    def factory():
        identity = {}

        def handle(request):
            requests.append(request)
            if request.method == "POST":
                form = parse_qs(request.content.decode())
                if form["username"] == ["rejected"]:
                    return httpx.Response(400, json={"error": "invalid_grant",
                                                    "error_description": "private-marker"})
                identity["viewer"] = form["username"][0]
                return httpx.Response(200, json={"access_token": "private-access-marker",
                                                "refresh_token": "private-refresh-marker",
                                                "expires_in": 300})
            if request.url.path.endswith("/points"):
                return httpx.Response(403, text="private-marker")
            return httpx.Response(200, json={"viewer": identity["viewer"]})

        client = School21Client(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    app = create_app(client_factory=factory)
    async with app.router.lifespan_context(app):
        yield app, clients, requests
    assert all(client._http.is_closed and not client.authenticated for client in clients)


async def test_session_cookie_rotation_client_isolation_and_logout(mock_web, caplog):
    app, clients, requests = mock_web
    async with browser(app) as first, browser(app) as second:
        headers = await csrf_headers(first)
        first_id = first.cookies[COOKIE_NAME]
        anonymous = (await first.get("/api/session")).json()
        assert set(anonymous) == {"authenticated", "login", "demo", "csrf"}
        assert anonymous["authenticated"] is False and anonymous["login"] is None
        assert not anonymous["demo"] and len(anonymous["csrf"]) >= 32
        assert first.cookies[COOKIE_NAME] == first_id
        second_headers = await csrf_headers(second)
        assert second.cookies[COOKIE_NAME] != first_id
        response = await first.post("/api/login", headers=headers, json={
            "username": "first@school.test", "password": "private-marker", "login": "peer+owl"
        })
        assert response.status_code == 200
        assert response.json()["login"] == "peer+owl"
        assert response.json()["authenticated"] and not response.json()["demo"]
        assert response.json()["csrf"] != headers["X-CSRF-Token"]
        current_id = first.cookies[COOKIE_NAME]
        assert current_id != first_id and first_id not in app.state.sessions
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
        assert "private-marker" not in response.text and "private-access-marker" not in cookie
        assert (await second.get("/api/data/sales")).status_code == 401
        response = await second.post("/api/login", headers=second_headers, json={
            "username": "second", "password": "private-marker"
        })
        assert response.status_code == 200
        assert len(clients) == 2 and clients[0] is not clients[1]
        assert (await first.get("/api/data/sales")).json() == {"viewer": "first@school.test"}
        assert (await second.get("/api/data/sales")).json() == {"viewer": "second"}
        assert (await first.get("/api/data/sales", headers={
            "Cookie": f"{COOKIE_NAME}={first_id}"
        })).status_code == 401
        assert (await first.post("/api/logout", headers=headers)).status_code == 403
        result = await first.post("/api/logout", headers=await csrf_headers(first))
        assert result.status_code == 200 and not result.json()["authenticated"]
        assert first.cookies[COOKIE_NAME] != current_id
        assert clients[0]._http.is_closed and not clients[0].authenticated
        assert clients[1].authenticated
        assert (await first.get("/api/data/sales")).status_code == 401
    assert len([request for request in requests if request.method == "POST"]) == 2
    assert "private-marker" not in caplog.text and "private-access-marker" not in caplog.text


async def test_login_failure_is_safe_and_preserves_existing_client(mock_web):
    app, clients, _ = mock_web
    async with browser(app) as web:
        headers = await csrf_headers(web)
        initial = web.cookies[COOKIE_NAME]
        bad = await web.post("/api/login", headers=headers, json={
            "username": "rejected", "password": "private-marker"
        })
        assert bad.status_code == 401 and set(bad.json()) == {"error"}
        assert "private-marker" not in bad.text
        assert web.cookies[COOKIE_NAME] == initial and clients[0]._http.is_closed
        assert (await web.get("/api/session")).json()["authenticated"] is False
        assert (await web.post("/api/login", headers=headers, json={
            "username": "first", "password": "private-marker"
        })).status_code == 200
        good_id = web.cookies[COOKIE_NAME]
        bad = await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "rejected", "password": "private-marker"
        })
        assert bad.status_code == 401 and web.cookies[COOKIE_NAME] == good_id
        assert clients[1].authenticated and clients[2]._http.is_closed
        assert (await web.get("/api/data/sales")).json() == {"viewer": "first"}


async def test_auth_block_page_returns_safe_login_diagnostic_to_browser():
    def factory():
        transport = httpx.MockTransport(lambda request: httpx.Response(
            403, headers={"Content-Type": "text/html"},
            text="<h1>Forbidden</h1><p>If you are not a bot</p><p>private-marker</p>",
        ))
        return School21Client(transport=transport)

    app = create_app(client_factory=factory)
    async with browser(app) as web:
        response = await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "fixture-account", "password": "fixture-only", "login": "fixture-peer",
        })
        assert response.status_code == 403
        assert "Защита сайта" in response.json()["error"]
        assert "авторизации" in response.json()["error"]
        assert "private-marker" not in response.text and "fixture-only" not in response.text
        assert (await web.get("/api/session")).json()["authenticated"] is False


@pytest.mark.parametrize("endpoint", ["/api/login", "/api/demo", "/api/logout"])
@pytest.mark.parametrize("variant", ["missing", "wrong", "duplicate", "unicode", "other-session"])
async def test_csrf_required_for_every_mutation(endpoint, variant):
    app = create_app()
    async with browser(app) as web, browser(app) as other:
        headers = await csrf_headers(web)
        original = web.cookies[COOKIE_NAME]
        if variant == "missing":
            headers.pop("X-CSRF-Token")
        elif variant == "wrong":
            headers["X-CSRF-Token"] = "wrong"
        elif variant == "duplicate":
            headers = list(headers.items()) + [("X-CSRF-Token", headers["X-CSRF-Token"])]
        elif variant == "unicode":
            headers = [(b"Origin", ORIGIN.encode()), (b"X-CSRF-Token", b"\xff")]
        else:
            headers["X-CSRF-Token"] = (await csrf_headers(other))["X-CSRF-Token"]
        response = await web.post(endpoint, headers=headers, json={
            "username": "owner", "password": "private-marker"
        })
        assert response.status_code == 403 and set(response.json()) == {"error"}
        assert web.cookies[COOKIE_NAME] == original
        assert (await web.get("/api/session")).json()["authenticated"] is False


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.test"}, {"Origin": "null"}, {"Origin": "http://127.0.0.1:8766"},
    {"Origin": "https://127.0.0.1:8765"}, {"Origin": "http://localhost:8765"},
    {"Host": "evil.test:8765", "Origin": ORIGIN},
    {"Host": "127.0.0.1:8766", "Origin": ORIGIN},
    {"Host": "127.0.0.1", "Origin": ORIGIN},
    {"Host": "user@127.0.0.1:8765", "Origin": ORIGIN},
    {"Host": "127.0.0.1:8765/path", "Origin": ORIGIN},
    {"Host": "127.0.0.1:8765#fragment", "Origin": ORIGIN},
    {"Host": "127.0.0.1:08765", "Origin": ORIGIN},
    {"Host": "127.0.0.1:8765 ", "Origin": ORIGIN},
    {"Origin": ORIGIN + "/"}, {},
])
async def test_host_origin_scheme_and_port_checks(headers):
    app = create_app()
    async with browser(app) as web:
        csrf = await csrf_headers(web)
        response = await web.post("/api/demo", headers={
            "X-CSRF-Token": csrf["X-CSRF-Token"], **headers
        })
        assert response.status_code == 403 and set(response.json()) == {"error"}


async def test_duplicate_host_origin_and_forwarding_headers_are_not_trusted():
    app = create_app()
    async with browser(app) as web:
        headers = list((await csrf_headers(web)).items())
        for extras in ([("Host", "127.0.0.1:8765"), ("Host", "127.0.0.1:8765")],
                       [("Origin", ORIGIN)]):
            assert (await web.post("/api/demo", headers=headers + extras)).status_code == 403
        assert (await web.get("/api/session", headers={
            "Host": "evil.test:8765", "X-Forwarded-Host": "127.0.0.1:8765",
            "X-Forwarded-Proto": "http", "Forwarded": "host=127.0.0.1:8765"
        })).status_code == 403
    async with browser(app, origin="http://evil.test:8765") as web:
        assert (await web.get("/api/session", headers={"Host": "127.0.0.1:8765"})).status_code == 403


@pytest.mark.parametrize("origin", ["http://localhost:8765", "http://127.0.0.1",
                                   "http://[::1]:8765", "https://127.0.0.1:8765"])
async def test_loopback_variants_and_secure_cookie(origin):
    app = create_app()
    async with app.router.lifespan_context(app), browser(app, origin=origin) as web:
        response = await enter_demo(web)
        assert ("; secure" in response.headers["set-cookie"].lower()) == origin.startswith("https:")
        assert (await web.get("/api/data/sales")).status_code == 200


@pytest.mark.parametrize("payload", [None, [], {"username": "owner", "password": "private-marker",
                                               "access_token": "private-marker"},
                                    {"username": "owner", "password": "private-marker", "login": "../x"},
                                    {"username": "owner", "password": None},
                                    {"username": 42, "password": "private-marker"},
                                    {"username": "owner", "password": "private-marker", "login": 42},
                                    {"username": "x" * 513, "password": "private-marker"}])
async def test_invalid_login_body_is_safe_without_creating_client(mock_web, payload):
    app, clients, requests = mock_web
    async with browser(app) as web:
        headers = await csrf_headers(web)
        response = await web.post("/api/login", headers=headers, json=payload)
        assert response.status_code == 400 and set(response.json()) == {"error"}
        assert "private-marker" not in response.text and not clients and not requests


async def test_login_parsing_and_size_limits_without_echo(mock_web):
    app, clients, _ = mock_web
    async with browser(app) as web:
        headers = await csrf_headers(web)
        for content, content_type in ((b"{private-marker", "application/json"),
                                      (b"\xffprivate-marker", "application/json"),
                                      (b"password=private-marker", "application/x-www-form-urlencoded"),
                                      (b"x" * 16385, "application/json")):
            response = await web.post("/api/login", headers={
                **headers, "Content-Type": content_type
            }, content=content)
            assert response.status_code == 400 and "private-marker" not in response.text
        assert clients == []


async def test_demo_routes_query_validation_and_no_arbitrary_proxy():
    app = create_app(client_factory=lambda: pytest.fail("Demo must not create an upstream client"))
    async with app.router.lifespan_context(app), browser(app) as web:
        result = await enter_demo(web)
        assert result.json()["demo"] and result.json()["login"] == "demo-peer"
        first = await web.get("/api/data/participants/demo-peer/projects?limit=2&offset=0&status=ACCEPTED")
        assert first.status_code == 200
        assert len(first.json()["projects"]) == 2
        assert all(item["status"] == "ACCEPTED" for item in first.json()["projects"])
        assert (await web.get("/api/data/participants/demo-peer/projects?offset=1000")).json() == {"projects": []}
        assert (await web.get("/api/data/projects/101")).json()["projectId"] == 101
        overview = (await web.get("/api/overview/demo-peer")).json()
        assert overview["participant"]["login"] == "demo-peer" and overview["errors"] == {}
        assert (await web.get("/api/data/events")).status_code == 400
        assert (await web.get("/api/data/participants/demo-peer/projects?limit=2&limit=3")).status_code == 400
        for path in ("sales?url=https://evil.test", "https://evil.test/sales", "v1/sales",
                     "participants/a%2Fb", "participants/a%252Fb", "projects/not-a-number",
                     "sales?password=private-marker"):
            response = await web.get("/api/data/" + path)
            assert response.status_code == 400 and "private-marker" not in response.text
        assert (await web.post("/api/data/sales", headers=await csrf_headers(web))).status_code == 405


async def test_partial_overview_and_safe_expired_upstream_session(mock_web):
    app, clients, _ = mock_web
    async with browser(app) as web:
        assert (await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "first", "password": "private-marker"
        })).status_code == 200
        result = await web.get("/api/overview/other-peer")
        assert result.status_code == 200
        assert result.json()["points"] is None and set(result.json()["errors"]) == {"points"}
        assert "private-marker" not in result.text
        await clients[0].logout()
        assert (await web.get("/api/data/sales")).status_code == 401
        assert (await web.get("/api/overview/first")).status_code == 401
        assert (await web.get("/api/session")).json()["authenticated"] is False


async def test_security_headers_assets_and_safe_generic_errors():
    class BrokenClient:
        async def login(self, *args):
            raise RuntimeError("private-marker")

        async def close(self):
            pass

    app = create_app(client_factory=BrokenClient)
    async with app.router.lifespan_context(app), browser(app) as web:
        headers = await csrf_headers(web)
        responses = [await web.get("/"), await web.get("/static/app.js"),
                     await web.get("/static/style.css"), await web.get("/missing"),
                     await web.get("/api/data/sales"), await web.post("/api/login", headers=headers,
                         json={"username": "owner", "password": "private-marker"})]
        assert [r.status_code for r in responses] == [200, 200, 200, 404, 401, 500]
        assert "private-marker" not in responses[-1].text
        for response in responses:
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert "unsafe-inline" not in response.headers["content-security-policy"]
        traversal = await web.get("/static/%2e%2e/client.py")
        assert traversal.status_code == 404
        assert "School21Client" not in traversal.text


@pytest.mark.parametrize("expiry", ["idle", "absolute"])
async def test_expired_sessions_close_clients_and_replace_cookies(mock_web, expiry):
    app, clients, _ = mock_web
    async with browser(app) as web:
        assert (await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "first", "password": "private-marker"
        })).status_code == 200
        previous = web.cookies[COOKIE_NAME]
        session = app.state.sessions[previous]
        if expiry == "idle":
            session.touched_at -= 3601
        else:
            session.created_at -= 8 * 3600 + 1
        assert (await web.get("/api/data/sales")).status_code == 401
        assert previous not in app.state.sessions and clients[0]._http.is_closed
        response = await web.get("/api/session")
        assert response.status_code == 200 and not response.json()["authenticated"]
        assert web.cookies[COOKIE_NAME] != previous


async def test_session_capacity_and_app_state_are_bounded_and_isolated(monkeypatch):
    monkeypatch.setattr("s21_compass.web.MAX_SESSIONS", 2)
    app, other = create_app(), create_app()
    async with browser(app) as first, browser(app) as second, browser(app) as third, browser(other) as isolated:
        await csrf_headers(first)
        await csrf_headers(second)
        assert len(app.state.sessions) == 2
        response = await third.get("/api/session")
        assert response.status_code == 429 and COOKIE_NAME not in third.cookies
        assert (await isolated.get("/api/session")).status_code == 200
        assert len(other.state.sessions) == 1
        app.state.sessions[first.cookies[COOKIE_NAME]].touched_at -= 3601
        assert (await third.get("/api/session")).status_code == 200
        assert len(app.state.sessions) == 2


async def test_concurrent_duplicate_login_uses_one_client_and_rejects_stale_mutation():
    started, release = asyncio.Event(), asyncio.Event()
    instances = []

    class SlowClient:
        demo = False
        authenticated = False
        login_name = None

        def __init__(self):
            instances.append(self)

        async def login(self, username, password, nickname):
            started.set()
            await release.wait()
            self.authenticated, self.login_name = True, username

        async def close(self):
            self.authenticated = False

    app = create_app(client_factory=SlowClient)
    async with app.router.lifespan_context(app), browser(app) as web:
        headers = await csrf_headers(web)
        body = {"username": "owner", "password": "private-marker"}
        first = asyncio.create_task(web.post("/api/login", headers=headers, json=body))
        await asyncio.wait_for(started.wait(), 1)
        duplicate = asyncio.create_task(web.post("/api/login", headers=headers, json=body))
        await asyncio.sleep(0.01)
        release.set()
        responses = await asyncio.wait_for(asyncio.gather(first, duplicate), 1)
        assert [r.status_code for r in responses] == [200, 401]
        assert len(instances) == 1 and len(app.state.sessions) == 1


async def test_cancelled_login_closes_untransferred_client():
    started, closed = asyncio.Event(), asyncio.Event()

    class CancelledClient:
        async def login(self, *args):
            started.set()
            await asyncio.Event().wait()

        async def close(self):
            closed.set()

    app = create_app(client_factory=CancelledClient)
    async with app.router.lifespan_context(app), browser(app) as web:
        headers = await csrf_headers(web)
        task = asyncio.create_task(web.post("/api/login", headers=headers, json={
            "username": "owner", "password": "private-marker"
        }))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        assert (await web.get("/api/session")).json()["authenticated"] is False


async def test_cleanup_does_not_block_other_browser_on_busy_expired_session():
    app = create_app()
    async with app.router.lifespan_context(app), browser(app) as busy, browser(app) as other:
        await enter_demo(busy)
        session = app.state.sessions[busy.cookies[COOKIE_NAME]]
        await session.lock.acquire()
        session.touched_at -= 3601
        try:
            assert (await asyncio.wait_for(other.get("/api/session"), 0.2)).status_code == 200
        finally:
            session.lock.release()
        assert (await other.get("/api/session")).status_code == 200
        assert session.client is None


async def test_cleanup_close_failures_are_bounded_and_do_not_leak_clients(monkeypatch):
    monkeypatch.setattr("s21_compass.web.CLIENT_CLOSE_SECONDS", 0.01)
    closed = []

    class CloseClient:
        demo = False
        authenticated = False
        login_name = None

        def __init__(self):
            self.number = len(closed)
            closed.append(False)

        async def login(self, username, *args):
            self.authenticated, self.login_name = True, username

        async def close(self):
            self.authenticated = False
            closed[self.number] = True
            if self.number == 0:
                await asyncio.Event().wait()
            raise RuntimeError("private-marker")

    app = create_app(client_factory=CloseClient)
    async with app.router.lifespan_context(app), browser(app) as first, browser(app) as second:
        for web in (first, second):
            assert (await web.post("/api/login", headers=await csrf_headers(web), json={
                "username": "owner", "password": "private-marker"
            })).status_code == 200
        for session in app.state.sessions.values():
            session.touched_at -= 3601
        response = await asyncio.wait_for(first.get("/api/session"), 0.5)
        assert response.status_code == 200 and closed == [True, True]


async def test_demo_switch_closes_previous_live_client(mock_web):
    app, clients, _ = mock_web
    async with browser(app) as web:
        await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "owner", "password": "private-marker"
        })
        previous = web.cookies[COOKIE_NAME]
        response = await enter_demo(web)
        assert response.json()["demo"] and web.cookies[COOKIE_NAME] != previous
        assert clients[0]._http.is_closed and not clients[0].authenticated


async def test_safe_upstream_error_status_reaches_browser():
    class RateLimitedClient:
        authenticated = True
        demo = False
        login_name = "owner"

        async def login(self, *args):
            pass

        async def get(self, *args):
            raise School21Error(429, "Слишком много запросов. Подождите.")

        async def close(self):
            self.authenticated = False

    app = create_app(client_factory=RateLimitedClient)
    async with app.router.lifespan_context(app), browser(app) as web:
        await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "owner", "password": "private-marker"
        })
        response = await web.get("/api/data/sales")
        assert response.status_code == 429 and "Подождите" in response.json()["error"]


@pytest.mark.parametrize("route", ["/api/data/sales", "/api/overview/owner"])
async def test_expiry_during_request_rejects_late_data(route):
    started, release = asyncio.Event(), asyncio.Event()
    closed = []

    class SlowDataClient:
        authenticated = True
        demo = False
        login_name = "owner"

        async def login(self, *args):
            pass

        async def get(self, *args):
            started.set()
            await release.wait()
            return {"sensitive-result-marker": True}

        overview = get

        async def close(self):
            closed.append(True)
            self.authenticated = False

    app = create_app(client_factory=SlowDataClient)
    async with app.router.lifespan_context(app), browser(app) as web:
        await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "owner", "password": "private-marker"
        })
        identifier = web.cookies[COOKIE_NAME]
        task = asyncio.create_task(web.get(route))
        await asyncio.wait_for(started.wait(), 1)
        app.state.sessions[identifier].created_at -= 8 * 3600 + 1
        release.set()
        response = await task
        assert response.status_code == 401 and "sensitive-result-marker" not in response.text
        assert (await web.get("/api/session")).status_code == 200
        assert closed == [True] and identifier not in app.state.sessions


async def test_shutdown_closes_busy_sessions_without_waiting_for_get_lock():
    app = create_app()
    async with browser(app) as web:
        async with app.router.lifespan_context(app):
            await enter_demo(web)
            session = app.state.sessions[web.cookies[COOKIE_NAME]]
            client = session.client
            await session.lock.acquire()
        assert not client.authenticated and session.client is None and not app.state.sessions
        session.lock.release()


@pytest.mark.parametrize("endpoint", ["/api/demo", "/api/logout"])
async def test_expiry_during_client_close_cannot_rotate_session(endpoint):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowCloseClient:
        authenticated = True
        demo = False
        login_name = "owner"

        async def login(self, *args):
            pass

        async def close(self):
            self.authenticated = False
            started.set()
            await release.wait()

    app = create_app(client_factory=SlowCloseClient)
    async with app.router.lifespan_context(app), browser(app) as web:
        assert (await web.post("/api/login", headers=await csrf_headers(web), json={
            "username": "owner", "password": "private-marker"
        })).status_code == 200
        headers = await csrf_headers(web)
        identifier = web.cookies[COOKIE_NAME]
        session = app.state.sessions[identifier]
        task = asyncio.create_task(web.post(endpoint, headers=headers))
        await asyncio.wait_for(started.wait(), 1)
        session.created_at -= 8 * 3600 + 1
        release.set()
        response = await asyncio.wait_for(task, 1)
        assert response.status_code == 401
        assert "set-cookie" not in response.headers and web.cookies[COOKIE_NAME] == identifier
        assert session.client is None and list(app.state.sessions) == [identifier]
        renewed = await web.get("/api/session")
        assert renewed.status_code == 200 and not renewed.json()["authenticated"]
        assert web.cookies[COOKIE_NAME] != identifier and identifier not in app.state.sessions
