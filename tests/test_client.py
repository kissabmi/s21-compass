import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from s21_compass.client import API_URL, CLIENT_ID, TOKEN_URL, School21Client, School21Error
from s21_compass.demo import DEMO_CAMPUS_ID, DemoClient


def tokens(access="access-one", refresh="refresh-one", **extra):
    return httpx.Response(200, json={"access_token": access, "refresh_token": refresh,
                                    "expires_in": 300, "refresh_expires_in": 1800, **extra})


async def test_oauth_form_url_and_distinct_nickname():
    requests = []

    def handle(request):
        requests.append(request)
        return tokens() if request.method == "POST" else httpx.Response(200, json={"login": "peer+owl"})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner+21@school.test", "test & + пароль", "peer+owl")
    assert client.login_name == "peer+owl" and client.authenticated and not client.demo
    request = requests[0]
    assert str(request.url) == TOKEN_URL
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {
        "client_id": [CLIENT_ID], "grant_type": ["password"],
        "username": ["owner+21@school.test"], "password": ["test & + пароль"],
    }
    await client.get("/participants/peer%2Bowl")
    assert str(requests[1].url) == API_URL + "/participants/peer%2Bowl"
    assert requests[1].headers["authorization"] == "Bearer access-one"
    assert "password" not in requests[1].headers and not requests[1].content
    await client.login("suggestion@school.test", "test-password")
    assert client.login_name == "suggestion"
    await client.close()
    assert not client.authenticated and client.login_name is None
    assert client._access_token is None and client._refresh_token is None


async def test_refresh_expiry_rotation_and_logout(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("s21_compass.client.time.monotonic", lambda: clock[0])
    forms, authorization = [], []

    def handle(request):
        if request.method == "POST":
            forms.append(parse_qs(request.content.decode()))
            n = len(forms)
            return tokens(f"access-{n}", f"refresh-{n}", expires_in=10)
        authorization.append(request.headers["authorization"])
        return httpx.Response(200, json={"sales": []})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    for instant in (110, 120):
        clock[0] = instant
        await client.get("sales")
    assert forms[1] == {"client_id": [CLIENT_ID], "grant_type": ["refresh_token"],
                        "refresh_token": ["refresh-1"]}
    assert forms[2]["refresh_token"] == ["refresh-2"]
    assert authorization == ["Bearer access-2", "Bearer access-3"]
    await client.logout()
    with pytest.raises(School21Error, match="Сессия истекла"):
        await client.get("sales")
    assert client._refresh_token is None
    await client.close()


async def test_concurrent_401_is_singleflight_and_rotates_refresh():
    old_ready = asyncio.Event()
    old_count = refresh_count = 0
    get_count = 0

    async def handle(request):
        nonlocal old_count, refresh_count, get_count
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["password"]:
                return tokens()
            refresh_count += 1
            assert form == {"client_id": [CLIENT_ID], "grant_type": ["refresh_token"],
                            "refresh_token": ["refresh-one"]}
            await asyncio.sleep(0.01)
            return tokens("access-two", "refresh-two")
        get_count += 1
        if request.headers["authorization"] == "Bearer access-one":
            old_count += 1
            if old_count == 8:
                old_ready.set()
            await old_ready.wait()
            return httpx.Response(401)
        assert request.headers["authorization"] == "Bearer access-two"
        return httpx.Response(200, json={"sales": []})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    results = await asyncio.wait_for(asyncio.gather(*(client.get("sales") for _ in range(8))), 2)
    assert results == [{"sales": []}] * 8
    assert refresh_count == 1 and get_count == 16
    assert client._refresh_token == "refresh-two"
    await client.close()


@pytest.mark.parametrize("failure", ["invalid_grant", "second_401"])
async def test_refresh_failure_is_bounded_and_clears_auth(failure):
    get_count = refresh_count = 0

    def handle(request):
        nonlocal get_count, refresh_count
        if request.method == "GET":
            get_count += 1
            return httpx.Response(401, json={"password": "private-marker"})
        if parse_qs(request.content.decode())["grant_type"] == ["password"]:
            return tokens()
        refresh_count += 1
        if failure == "invalid_grant":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "private-marker"})
        return tokens("access-two", "refresh-two")

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == 401 and "private-marker" not in str(error.value)
    assert get_count == (1 if failure == "invalid_grant" else 2) and refresh_count == 1
    assert not client.authenticated and client.login_name is None
    await client.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 302])
async def test_safe_upstream_errors(status):
    def handle(request):
        if request.method == "POST":
            return tokens()
        return httpx.Response(status, text="private-marker access_token=password")

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == (502 if status == 302 else status)
    assert "private-marker" not in str(error.value)
    if status == 429:
        assert "Подождите" in str(error.value)
    await client.close()


async def test_requests_identify_compass_consistently_for_auth_refresh_and_api():
    from s21_compass import __version__

    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["user-agent"] == f"S21-Compass/{__version__}"
        assert request.headers["accept"] == "application/json"
        if request.method == "POST":
            return tokens()
        if len([r for r in seen if r.method == "GET"]) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"sales": []})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("fixture-account", "fixture-only", "fixture-peer")
    assert await client.get("sales") == {"sales": []}
    assert [r.method for r in seen] == ["POST", "GET", "POST", "GET"]
    await client.close()


@pytest.mark.parametrize("blocked_page", [True, False])
async def test_auth_forbidden_identifies_login_stage_without_echoing_response(blocked_page):
    def handle(request):
        if blocked_page:
            return httpx.Response(403, headers={"Content-Type": "text/html; charset=UTF-8"},
                                  text="<h1>Forbidden</h1><p>If you are not a bot, please "
                                  "contact support.</p><p>private-marker</p>")
        return httpx.Response(403, json={"error": "access_denied",
                                        "error_description": "private-marker"})

    client = School21Client(transport=httpx.MockTransport(handle))
    with pytest.raises(School21Error) as error:
        await client.login("fixture-account", "private-password", "fixture-peer")
    message = str(error.value)
    assert error.value.status == 403 and not client.authenticated
    assert "авторизации" in message and "HTTP 403" in message
    assert "private-marker" not in message and "private-password" not in message
    if blocked_page:
        assert "Защита сайта" in message and "до проверки учётных данных" in message
    else:
        assert "Защита сайта" not in message
    await client.close()


@pytest.mark.parametrize("kind,expected", [("timeout", 504), ("network", 502), ("json", 502)])
async def test_network_timeout_and_malformed_json(kind, expected):
    def handle(request):
        if request.method == "POST":
            return tokens()
        if kind == "timeout":
            raise httpx.ReadTimeout("private-marker", request=request)
        if kind == "network":
            raise httpx.ConnectError("private-marker", request=request)
        return httpx.Response(200, text="private-marker")

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == expected and "private-marker" not in str(error.value)
    await client.close()


async def test_overview_keeps_independent_sections():
    def handle(request):
        if request.method == "POST":
            return tokens()
        if request.url.path.endswith("/points"):
            return httpx.Response(403, text="private-marker")
        if request.url.path.endswith("/logtime"):
            return httpx.Response(200, json=26.5)
        return httpx.Response(200, json={"login": "other-peer"})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password", "nickname")
    overview = await client.overview("other-peer")
    assert set(overview) == {"participant", "points", "logtime", "feedback", "skills", "badges",
                             "coalition", "workstation", "errors"}
    assert overview["participant"]["login"] == "other-peer"
    assert overview["points"] is None and set(overview["errors"]) == {"points"}
    assert overview["logtime"] == 26.5 and "private-marker" not in str(overview)
    await client.close()


# Independent cases from docs/resources.md and the pinned OpenAPI; not generated
# from the implementation's resource table.
ROUTE_CASES = [
    ("participants/demo-peer", {}, None, {"login", "level", "expValue", "expToNextLevel", "campus", "status"}),
    ("participants/demo-peer/points", {}, None, {"peerReviewPoints", "codeReviewPoints", "coins"}),
    ("participants/demo-peer/logtime", {"date": "2026-10-10"}, None, set()),
    ("participants/demo-peer/feedback", {}, None, set()),
    ("participants/demo-peer/skills", {}, "skills", {"name", "points"}),
    ("participants/demo-peer/badges", {}, "badges", {"name", "receiptDateTime", "iconUrl"}),
    ("participants/demo-peer/coalition", {}, None, {"coalitionId", "name"}),
    ("participants/demo-peer/workstation", {}, None, {"clusterId", "clusterName", "row", "number"}),
    ("participants/demo-peer/experience-history", {"limit": 2, "offset": 1}, "expHistory", {"expValue", "accrualDateTime"}),
    ("participants/demo-peer/projects", {"limit": 2, "offset": 0, "status": "IN_PROGRESS"}, "projects", {"id", "title", "type", "status"}),
    ("participants/demo-peer/projects/101", {}, None, {"id", "title", "type", "status"}),
    ("projects/101", {}, None, {"projectId", "title", "description", "durationHours", "type"}),
    ("projects/101/participants", {"limit": 2, "offset": 0, "status": "ACCEPTED", "campusId": DEMO_CAMPUS_ID}, "participants", set()),
    ("participants/demo-peer/courses", {"limit": 2, "offset": 0, "status": "IN_PROGRESS"}, "courses", {"id", "title", "status"}),
    ("participants/demo-peer/courses/201", {}, None, {"id", "title", "status"}),
    ("courses/201", {}, None, {"courseId", "title", "description", "durationHours", "xp"}),
    ("events", {"from": "2020-01-01T00:00:00Z", "to": "2099-01-01T00:00:00Z", "type": "ACTIVITY", "limit": 2, "offset": 0}, "events", {"id", "type", "name", "location", "startDateTime", "endDateTime", "capacity", "registerCount"}),
    ("campuses", {}, "campuses", {"id", "shortName", "fullName"}),
    (f"campuses/{DEMO_CAMPUS_ID}/clusters", {}, "clusters", {"id", "name", "capacity", "availableCapacity", "floor"}),
    (f"campuses/{DEMO_CAMPUS_ID}/participants", {"limit": 2, "offset": 1}, "participants", set()),
    (f"campuses/{DEMO_CAMPUS_ID}/coalitions", {"limit": 2, "offset": 0}, "coalitions", {"coalitionId", "name"}),
    ("coalitions/401/participants", {"limit": 2, "offset": 1}, "participants", set()),
    ("clusters/501/map", {"limit": 2, "offset": 0, "occupied": False}, "clusterMap", {"row", "number"}),
    ("graph", {}, None, {"nodes", "edges"}),
    ("sales", {}, "sales", {"type", "status"}),
]


@pytest.mark.parametrize("path,params,wrapper,required", ROUTE_CASES)
async def test_all_routes_exact_upstream_url_and_query(path, params, wrapper, required):
    requests = []

    def handle(request):
        requests.append(request)
        return tokens() if request.method == "POST" else httpx.Response(200, json={"ok": True})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    assert await client.get(path, params) == {"ok": True}
    request = requests[-1]
    assert str(request.url).split("?", 1)[0] == API_URL + "/" + path
    expected = {key: ("false" if value is False else str(value)) for key, value in params.items()}
    assert dict(request.url.params) == expected
    assert len(ROUTE_CASES) == 25 and sum(len(case[1]) for case in ROUTE_CASES) == 27
    await client.close()


@pytest.mark.parametrize("path,params,wrapper,required", ROUTE_CASES)
async def test_demo_all_routes_are_schema_shaped_without_network(monkeypatch, path, params, wrapper, required):
    def no_network(*args, **kwargs):
        pytest.fail("Demo must not initialize an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    client = DemoClient()
    payload = await client.get(path, params)
    if path.endswith("/logtime"):
        assert isinstance(payload, float)
    elif wrapper:
        assert set(payload) == {wrapper}
        assert isinstance(payload[wrapper], list) and payload[wrapper]
        for item in payload[wrapper]:
            if wrapper == "participants":
                assert isinstance(item, str)
            else:
                assert required <= item.keys()
        if "limit" in params:
            assert len(payload[wrapper]) <= params["limit"]
    else:
        assert required <= payload.keys()
    if path == "graph":
        for node in payload["nodes"]:
            assert {"id", "label", "items"} <= node.keys()
            assert "position" not in node
            for item in node["items"]:
                assert {"id", "code", "handles", "entityType", "entityId"} <= item.keys()
        for edge in payload["edges"]:
            assert {"id", "source", "target", "sourceHandle", "targetHandle"} <= edge.keys()
    await client.close()


@pytest.mark.parametrize("path,params", [
    ("https://evil.test/sales", {}), ("//sales", {}), ("v1/sales", {}),
    ("participants/../points", {}), ("participants/a%2Fb", {}),
    ("participants/a%252Fb", {}), ("participants/a%00b", {}),
    ("participants/a b", {}), ("participants/a%3Fb", {}),
    ("sales?limit=1", {}), ("projects/not-an-id", {}), ("projects/-1", {}),
    ("campuses/not-a-uuid/clusters", {}), ("sales", {"login": "x"}),
    ("sales", {"limit": 1}), ("participants/demo-peer/projects", {"limit": 0}),
    ("participants/demo-peer/projects", {"limit": 1001}),
    ("participants/demo-peer/projects", {"limit": True}),
    ("participants/demo-peer/projects", {"offset": -1}),
    ("participants/demo-peer/projects", {"limit": 1.5}),
    ("participants/demo-peer/projects", {"status": "UNKNOWN"}),
    ("participants/demo-peer/courses", {"status": "IN_REVIEWS"}),
    ("projects/101/participants", {"campusId": "bad"}),
    ("participants/demo-peer/logtime", {"date": "2026-02-30"}),
    ("clusters/501/map", {"occupied": "0"}), ("events", {}),
    ("events", {"from": "2026-10-10", "to": "2026-10-11"}),
    ("events", {"from": "2026-10-10T00:00:00+03:00", "to": "2026-10-11T00:00:00Z"}),
    ("events", {"from": "2026-10-12T00:00:00Z", "to": "2026-10-11T00:00:00Z"}),
])
async def test_invalid_resources_and_queries_rejected_before_network(path, params):
    def no_network(request):
        pytest.fail("Invalid resources must never reach the network")

    live = School21Client(transport=httpx.MockTransport(no_network))
    for client in (live, DemoClient()):
        with pytest.raises(School21Error) as error:
            await client.get(path, params)
        assert error.value.status == 400
        await client.close()


async def test_demo_pages_filters_details_and_defensive_copies():
    client = DemoClient()
    first = await client.get("participants/demo-peer/projects", {"limit": 2})
    second = await client.get("participants/demo-peer/projects", {"limit": 2, "offset": 2})
    assert {p["id"] for p in first["projects"]}.isdisjoint(p["id"] for p in second["projects"])
    assert await client.get("participants/demo-peer/projects", {"offset": 1000}) == {"projects": []}
    accepted = await client.get("participants/demo-peer/projects", {"status": "ACCEPTED"})
    assert all(p["status"] == "ACCEPTED" for p in accepted["projects"])
    assert accepted["projects"][0]["finalPercentage"] > 100
    first["projects"][0]["title"] = "changed"
    detail = await client.get("participants/demo-peer/projects/101")
    assert detail["title"] != "changed"
    assert (await client.get("projects/101"))["projectId"] == detail["id"]
    free = await client.get("clusters/501/map", {"occupied": "false", "limit": 1000})
    occupied = await client.get("clusters/501/map", {"occupied": True, "limit": 1000})
    assert len(free["clusterMap"]) == 22 and len(occupied["clusterMap"]) == 42
    assert all(not seat.get("login") for seat in free["clusterMap"])
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    events = await client.get("events", {"from": start.isoformat(),
                                         "to": (start + timedelta(days=5)).isoformat(), "type": "EXAM"})
    assert events["events"] and all(e["type"] == "EXAM" for e in events["events"])
    for path in ("projects/999", "courses/999", "participants/unknown-peer"):
        with pytest.raises(School21Error) as error:
            await client.get(path)
        assert error.value.status == 404
    overview = await client.overview()
    assert overview["errors"] == {} and overview["participant"]["login"] == "demo-peer"
    await client.logout()
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == 401


@pytest.mark.parametrize("payload", [None, [], {"access_token": "private-marker"},
                                     {"access_token": "a", "refresh_token": "b", "expires_in": -1},
                                     {"access_token": "a", "refresh_token": "b", "expires_in": 10**400},
                                     {"access_token": "токен", "refresh_token": "b", "expires_in": 300}])
async def test_malformed_auth_response_is_safe(payload):
    client = School21Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    with pytest.raises(School21Error) as error:
        await client.login("owner", "test-password")
    assert error.value.status == 502 and "private-marker" not in str(error.value)
    assert not client.authenticated
    await client.close()


async def test_invalid_password_grant_does_not_echo_credentials():
    client = School21Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "private-marker"})))
    with pytest.raises(School21Error) as error:
        await client.login("owner", "private-marker")
    assert error.value.status == 401 and "private-marker" not in str(error.value)
    assert not client.authenticated
    await client.close()


async def test_cancelled_reader_does_not_cancel_shared_refresh():
    started, release = asyncio.Event(), asyncio.Event()
    refresh_count = 0

    async def handle(request):
        nonlocal refresh_count
        if request.method == "GET":
            return httpx.Response(200, json={"sales": []})
        if parse_qs(request.content.decode())["grant_type"] == ["password"]:
            return tokens(expires_in=0)
        refresh_count += 1
        started.set()
        await release.wait()
        return tokens("access-two", "refresh-two")

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    cancelled = asyncio.create_task(client.get("sales"))
    await asyncio.wait_for(started.wait(), 1)
    survivor = asyncio.create_task(client.get("sales"))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    assert await asyncio.wait_for(survivor, 1) == {"sales": []}
    assert client.authenticated and refresh_count == 1
    await client.close()


@pytest.mark.parametrize("pending", ["login", "refresh", "get"])
async def test_logout_invalidates_inflight_auth_and_reads(pending):
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        form = parse_qs(request.content.decode()) if request.method == "POST" else {}
        kind = "get" if request.method == "GET" else (
            "login" if form["grant_type"] == ["password"] else "refresh"
        )
        if kind == pending:
            started.set()
            await release.wait()
        return tokens(expires_in=0 if pending == "refresh" else 300) if form else httpx.Response(
            200, json={"sales": []}
        )

    client = School21Client(transport=httpx.MockTransport(handle))
    if pending != "login":
        await client.login("owner", "test-password")
    task = asyncio.create_task(client.login("owner", "test-password") if pending == "login"
                               else client.get("sales"))
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.wait_for(client.logout(), 0.2)
    assert not client.authenticated and client.login_name is None
    release.set()
    with pytest.raises(School21Error) as error:
        await asyncio.wait_for(task, 1)
    assert error.value.status == 401
    assert client._access_token is None and client._refresh_token is None
    await client.close()


async def test_old_get_cannot_return_data_or_replay_under_new_login():
    started, release = asyncio.Event(), asyncio.Event()
    gets = 0

    async def handle(request):
        nonlocal gets
        if request.method == "POST":
            name = parse_qs(request.content.decode())["username"][0]
            return tokens("access-" + name, "refresh-" + name)
        gets += 1
        started.set()
        await release.wait()
        return httpx.Response(401)

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("first", "test-password")
    previous = asyncio.create_task(client.get("sales"))
    await asyncio.wait_for(started.wait(), 1)
    await client.login("second", "test-password")
    release.set()
    with pytest.raises(School21Error) as error:
        await previous
    assert error.value.status == 401 and gets == 1
    assert client.authenticated and client.login_name == "second"
    await client.close()


@pytest.mark.parametrize("operation", ["logout", "close"])
async def test_cancelled_refresh_cannot_resurrect_client(operation):
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        if parse_qs(request.content.decode())["grant_type"] == ["password"]:
            return tokens(expires_in=0)
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Model an already-completing transport that still returns a late response.
            await release.wait()
        return tokens("late-access", "late-refresh")

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    reading = asyncio.create_task(client.get("sales"))
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.wait_for(getattr(client, operation)(), 0.2)
    release.set()
    with pytest.raises(School21Error) as error:
        await asyncio.wait_for(reading, 1)
    assert error.value.status == 401 and not client.authenticated
    assert client._access_token is None and client._refresh_token is None
    await client.close()


async def test_cancelled_password_grant_leaves_client_unauthenticated():
    started = asyncio.Event()

    async def handle(request):
        started.set()
        await asyncio.Event().wait()

    client = School21Client(transport=httpx.MockTransport(handle))
    task = asyncio.create_task(client.login("owner", "test-password"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.authenticated and client.login_name is None
    await client.close()


@pytest.mark.parametrize("kind,expected", [("429", 429), ("503", 503), ("network", 502),
                                          ("timeout", 504)])
async def test_transient_refresh_failure_keeps_rotatable_tokens_for_retry(kind, expected):
    refresh_count = 0

    def handle(request):
        nonlocal refresh_count
        if request.method == "GET":
            return httpx.Response(200, json={"sales": []})
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["password"]:
            return tokens(expires_in=0)
        refresh_count += 1
        assert form["refresh_token"] == ["refresh-one"]
        if refresh_count == 1:
            if kind == "network":
                raise httpx.ConnectError("private-marker", request=request)
            if kind == "timeout":
                raise httpx.ReadTimeout("private-marker", request=request)
            return httpx.Response(int(kind), text="private-marker")
        return tokens("access-two", "refresh-two")

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == expected and "private-marker" not in str(error.value)
    assert client.authenticated and client._refresh_token == "refresh-one"
    assert await client.get("sales") == {"sales": []}
    assert client._refresh_token == "refresh-two" and refresh_count == 2
    await client.close()


async def test_zero_refresh_lifetime_is_expired_without_network_retry():
    requests = []

    def handle(request):
        requests.append(request)
        return tokens(expires_in=0, refresh_expires_in=0)

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == 401 and len(requests) == 1
    assert not client.authenticated
    await client.close()


@pytest.mark.parametrize("payload", [True, "text", None, float("inf"), float("nan")])
async def test_invalid_success_payload_is_safe(payload):
    def handle(request):
        if request.method == "POST":
            return tokens()
        return httpx.Response(200, text=json.dumps(payload))

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("owner", "test-password")
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == 502
    await client.close()


async def test_empty_live_workstation_is_absent_without_hiding_other_empty_responses():
    def handle(request):
        if request.method == "POST":
            return tokens()
        if request.url.path.endswith("/workstation"):
            return httpx.Response(200, content=b"")
        if request.url.path.endswith("/sales"):
            return httpx.Response(200, content=b"")
        if request.url.path.endswith("/logtime"):
            return httpx.Response(200, json=0.0)
        return httpx.Response(200, json={})

    client = School21Client(transport=httpx.MockTransport(handle))
    await client.login("fixture-account", "fixture-only", "fixture-peer")
    assert await client.get("participants/fixture-peer/workstation") == {}
    overview = await client.overview()
    assert overview["workstation"] == {} and "workstation" not in overview["errors"]
    with pytest.raises(School21Error) as error:
        await client.get("sales")
    assert error.value.status == 502
    await client.close()


async def test_int64_offsets_and_int32_resource_bounds():
    from s21_compass.client import validate_resource

    assert validate_resource("participants/demo-peer/projects", {"offset": 2**40})[3] == {
        "offset": 2**40
    }
    for path in ("clusters/501/map", "coalitions/401/participants",
                 f"campuses/{DEMO_CAMPUS_ID}/coalitions"):
        with pytest.raises(School21Error) as error:
            validate_resource(path, {"offset": 2**31})
        assert error.value.status == 400


async def test_demo_default_pages_and_empty_campus_are_consistent():
    from s21_compass.demo import SECOND_CAMPUS_ID

    client = DemoClient()
    for path, key in (("participants/demo-peer/projects", "projects"),
                      ("participants/demo-peer/courses", "courses")):
        assert len((await client.get(path))[key]) == 10
    for path, key in (("participants/demo-peer/experience-history", "expHistory"),
                      (f"campuses/{DEMO_CAMPUS_ID}/participants", "participants"),
                      ("clusters/501/map", "clusterMap")):
        first = (await client.get(path))[key]
        second = (await client.get(path, {"offset": 50}))[key]
        assert len(first) == 50 and len(second) == 14
        assert await client.get(path, {"offset": 100}) == {key: []}
    for key in ("participants", "clusters", "coalitions"):
        assert await client.get(f"campuses/{SECOND_CAMPUS_ID}/{key}") == {key: []}
    badge = (await client.get("participants/demo-peer/badges"))["badges"][0]
    assert badge["iconUrl"].startswith("data:image/svg+xml,")
    await client.close()


@pytest.mark.parametrize("path,wrapper,params", [
    ("participants/demo-peer/courses", "courses", {"status": "FAILED"}),
    ("projects/101/participants", "participants", {"status": "ACCEPTED", "campusId": DEMO_CAMPUS_ID}),
    (f"campuses/{DEMO_CAMPUS_ID}/coalitions", "coalitions", {}),
    ("coalitions/401/participants", "participants", {}),
    ("events", "events", {"from": "2020-01-01T00:00:00Z", "to": "2099-01-01T00:00:00Z", "type": "TEST"}),
])
async def test_demo_filters_apply_before_pagination_for_all_lists(path, wrapper, params):
    client = DemoClient()
    whole = (await client.get(path, {**params, "limit": 1000}))[wrapper]
    first = (await client.get(path, {**params, "limit": 1}))[wrapper]
    second = (await client.get(path, {**params, "limit": 1, "offset": 1}))[wrapper]
    assert first == whole[:1] and second == whole[1:2] and whole
    if wrapper == "courses":
        assert all(item["status"] == "FAILED" for item in whole)
    if wrapper == "events":
        assert all(item["type"] == "TEST" for item in whole)
    await client.close()


async def test_demo_event_inclusive_boundaries_and_schema_links():
    from s21_compass.demo import SECOND_CAMPUS_ID

    client = DemoClient()
    event = (await client.get("events", {"from": "2020-01-01T00:00:00Z",
                                         "to": "2099-01-01T00:00:00Z", "limit": 1}))["events"][0]
    instant = event["startDateTime"]
    assert await client.get("events", {"from": instant, "to": instant}) == {"events": [event]}
    assert await client.get("projects/101/participants", {"campusId": SECOND_CAMPUS_ID}) == {
        "participants": []
    }
    graph = await client.get("graph")
    nodes = {node["id"]: node for node in graph["nodes"]}
    for node in nodes.values():
        for item in node["items"]:
            catalog = "projects" if item["entityType"] == "PROJECT" else "courses"
            assert await client.get(f"{catalog}/{item['entityId']}")
    for edge in graph["edges"]:
        for side in ("source", "target"):
            handles = {handle for item in nodes[edge[side]]["items"] for handle in item["handles"]}
            assert edge[side + "Handle"] in handles
    for cluster in (await client.get(f"campuses/{DEMO_CAMPUS_ID}/clusters"))["clusters"]:
        places = (await client.get(f"clusters/{cluster['id']}/map", {"limit": 1000}))["clusterMap"]
        assert len(places) == cluster["capacity"]
        assert sum("login" not in place for place in places) == cluster["availableCapacity"]
    await client.close()


async def test_orphaned_refresh_failure_is_observed_without_asyncio_warning():
    started, release = asyncio.Event(), asyncio.Event()
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))

    async def handle(request):
        if parse_qs(request.content.decode())["grant_type"] == ["password"]:
            return tokens(expires_in=0)
        started.set()
        await release.wait()
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = School21Client(transport=httpx.MockTransport(handle))
    try:
        await client.login("owner", "test-password")
        task = asyncio.create_task(client.get("sales"))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.sleep(0.01)
        assert not client.authenticated
        await client.close()
        assert errors == []
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.parametrize("username,password,nickname", [
    ("private-marker\ud800", "password", "peer"),
    ("owner", "private-marker\ud800", "peer"),
    ("owner", "password", "private-marker\ud800"),
])
async def test_invalid_unicode_credentials_are_safe_before_network(username, password, nickname):
    client = School21Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("Invalid Unicode must not reach HTTP encoding")
    ))
    with pytest.raises(School21Error) as error:
        await client.login(username, password, nickname)
    assert error.value.status == 400 and "private-marker" not in str(error.value)
    await client.close()
