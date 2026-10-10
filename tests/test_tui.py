"""Headless interaction checks, deliberately using only the public client contract."""

import asyncio
from datetime import date

import pytest
from textual.widgets import Button, DataTable, Input, Select, Static, Tree

from s21_compass.tui import CompassApp, DetailScreen


class FakeError(Exception):
    def __init__(self, status, message="Ошибка"):
        self.status, self.message = status, message


class FakeClient:
    demo = False

    def __init__(self, authenticated=True):
        self.authenticated = authenticated
        self.login_name = "test-peer" if authenticated else None
        self.calls = []
        self.closed = False
        self.fail = None
        self.gate = None
        self.auth_gate = None
        self.ignore_cancel = False

    async def login(self, username, password, participant_login=None):
        self.calls.append(("login", username, password, participant_login))
        if self.auth_gate:
            await self.auth_gate.wait()
        self.authenticated, self.login_name = True, participant_login

    async def logout(self):
        self.authenticated, self.login_name = False, None

    async def close(self):
        self.closed = True
        await self.logout()

    async def overview(self, login=None):
        self.calls.append(("overview", login))
        return {
            "participant": {"login": login, "level": 4, "expValue": 1234,
                            "expToNextLevel": 456, "campus": {"id": "campus-a", "shortName": "A"}},
            "points": {"peerReviewPoints": 0, "codeReviewPoints": 2, "coins": None},
            "logtime": 3.25, "feedback": {"averageVerifierInterest": None},
            "coalition": {"coalitionId": 7, "name": "Otters"},
            "workstation": {"clusterId": 3, "row": "a", "number": 2},
            "errors": {"badges": "Временно недоступны"},
        }

    async def get(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/projects") and self.gate:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancel:
                    raise
                await self.gate.wait()
        if self.fail:
            raise self.fail
        if path == "graph":
            return {"nodes": [{"id": "a", "label": "Core [red]", "items": [
                {"id": "item", "code": "C1", "entityId": 1, "entityType": "PROJECT",
                 "handles": ["out"]}]}, {"id": "b", "label": "Next", "items": []}],
                    "edges": [{"id": "e", "source": "a", "target": "b",
                               "sourceHandle": "out", "targetHandle": "in"}]}
        if path == "campuses":
            return {"campuses": [{"id": "campus-a", "shortName": "A", "fullName": "Campus A"}]}
        if path.endswith("/clusters"):
            return {"clusters": [{"id": 3, "name": "Ocean", "floor": 2,
                                  "capacity": 10, "availableCapacity": 9}]}
        if path.endswith("/map"):
            return {"clusterMap": [{"row": "a", "number": 2, "login": "other-peer"},
                                   {"row": "b", "number": 1, "login": None}]}
        if path.endswith("/participants"):
            return {"participants": ["other-peer"]}
        if path.endswith("/coalitions"):
            return {"coalitions": [{"coalitionId": 7, "name": "Otters"}]}
        if path.endswith("/skills"):
            return {"skills": [{"name": "C", "points": 99}]}
        if path.endswith("/badges"):
            return {"badges": [{"name": "Explorer", "receiptDateTime": "2026-01-01"}]}
        if path.endswith("/experience-history"):
            return {"expHistory": [{"expValue": 100, "accrualDateTime": "2026-01-01"}]}
        if path.endswith("/logtime"):
            return 4.5
        if path == "events":
            return {"events": [{"id": 8, "name": "Meetup", "type": "ACTIVITY",
                                "startDateTime": "2026-10-10T12:00:00Z", "capacity": 20,
                                "registerCount": 2, "description": "[bold]literal[/bold]"}]}
        if path == "sales":
            return {"sales": [{"type": "PRP", "status": "PLANNED", "startDateTime": None}]}
        if path.endswith(("/projects", "/courses")):
            kind = path.split("/")[-1]
            records = [{"id": i + 1, "title": f"Task [red] {i}", "status": "ACCEPTED",
                        "finalPercentage": 110} for i in range(10)]
            return {kind: records if not params.get("offset") else []}
        if path.startswith(("projects/", "courses/")):
            return {"title": "Catalog title", "description": "[red]Not markup[/red]",
                    "durationHours": 20, "xp": None, "startConditions": [{"rulesInGroup": []}]}
        if "/projects/" in path or "/courses/" in path:
            return {"title": "Personal title", "status": "ACCEPTED", "finalPercentage": 110}
        raise AssertionError(path)


async def settled(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def text(app, selector):
    return str(app.query_one(selector, Static).content)


async def open_row(app, pilot):
    app.query_one("#table", DataTable).focus()
    await pilot.press("enter")
    await settled(app, pilot)


@pytest.mark.asyncio
async def test_login_mask_explicit_nickname_clear_password_and_close():
    client = FakeClient(False)
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.query_one("#password", Input).password
        app.query_one("#username", Input).value = "account@example.org"
        app.query_one("#password", Input).value = "local-fixture"
        app.query_one("#participant-login", Input).value = "distinct-peer"
        await pilot.click("#login")
        await settled(app, pilot)
        assert app.query_one("#password", Input).value == ""
        assert client.calls[0] == ("login", "account@example.org", "local-fixture", "distinct-peer")
        assert app.peer == "distinct-peer"
        assert not app.auth_pending
        assert app.query_one("#workspace").display
        await pilot.press("ctrl+o")
        assert not client.authenticated
        assert app.query_one("#login-panel").display
    assert client.closed


@pytest.mark.asyncio
async def test_demo_logout_then_normal_login_uses_live_client_factory(monkeypatch):
    client = FakeClient(False)
    monkeypatch.setattr("s21_compass.client.School21Client", lambda: client)
    app = CompassApp(demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        demo = app.client
        await pilot.press("ctrl+o")
        await settled(app, pilot)
        for key, value in (("username", "fixture-account"), ("password", "fixture-only"),
                           ("participant-login", "real-peer")):
            app.query_one(f"#{key}", Input).value = value
        await pilot.click("#login")
        await settled(app, pilot)
        assert app.client is client and not app.client.demo
        assert client.calls[0] == ("login", "fixture-account", "fixture-only", "real-peer")
        assert not demo.authenticated
        assert "ДЕМО" not in text(app, "#brand")


@pytest.mark.asyncio
async def test_failed_peer_lookup_clears_previous_profile_context():
    class MissingPeer(FakeClient):
        async def overview(self, login=None):
            if login == "missing-peer":
                raise FakeError(404, "Участник не найден")
            return await super().overview(login)

    client = MissingPeer()
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        app.query_one("#peer", Input).value = "missing-peer"
        await pilot.click("#lookup")
        await settled(app, pilot)
        assert "не найден" in text(app, "#status")
        assert not app.overview_data
        for action in ("workstation", "my-campus", "members"):
            await pilot.click(f"#{action}")
            await settled(app, pilot)
            assert app.section == "overview"
        assert not any(call[0] == "clusters/3/map" for call in client.calls)


@pytest.mark.asyncio
async def test_pending_peer_lookup_cannot_open_old_workstation():
    gate, entered = asyncio.Event(), asyncio.Event()

    class WaitingPeer(FakeClient):
        async def overview(self, login=None):
            if login == "new-peer":
                entered.set()
                await gate.wait()
            return await super().overview(login)

    client = WaitingPeer()
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        app.query_one("#peer", Input).value = "new-peer"
        await pilot.click("#lookup")
        await entered.wait()
        await pilot.click("#workstation")
        assert app.section == "overview" and not app.overview_data
        gate.set()
        await settled(app, pilot)
        assert app.overview_data["participant"]["login"] == "new-peer"
        assert not any(call[0] == "clusters/3/map" for call in client.calls)


@pytest.mark.asyncio
async def test_missing_campus_does_not_fall_through_to_coalition():
    class MissingCampus(FakeClient):
        async def overview(self, login=None):
            data = await super().overview(login)
            data["participant"] = None
            data["errors"]["participant"] = "Профиль временно недоступен"
            return data

    client = MissingCampus()
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#my-campus")
        await settled(app, pilot)
        assert app.section == "overview"
        assert "Кампус недоступен" in text(app, "#status")
        assert not any(call[0] == "coalitions/7/participants" for call in client.calls)
    assert client.closed


@pytest.mark.asyncio
async def test_pending_auth_cannot_double_submit_and_cancel():
    client = FakeClient(False)
    client.auth_gate = asyncio.Event()
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        for key, value in (("username", "account"), ("password", "fixture"),
                           ("participant-login", "peer")):
            app.query_one(f"#{key}", Input).value = value
        app.start_login()
        await pilot.pause()
        assert app.auth_pending
        assert app.query_one("#login", Button).disabled
        app.start_login()
        assert len(client.calls) == 1
        await pilot.click("#cancel-auth")
        await pilot.pause()
        assert not app.auth_pending
        assert not client.authenticated
        assert app.query_one("#password", Input).value == ""


@pytest.mark.asyncio
async def test_overview_partial_missing_values_and_dated_logtime():
    app = CompassApp(client=FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        summary = text(app, "#summary")
        assert "XP: 1234" in summary and "Монеты: —" in summary
        assert "PRP: 0" in summary and "Заинтересованность: —" in summary
        assert "Часть данных" in text(app, "#status")
        app.query_one("#date-from", Input).value = "2026-10-01"
        await pilot.click("#dates-go")
        await settled(app, pilot)
        assert ("participants/test-peer/logtime", {"date": "2026-10-01"}) in app.client.calls
        assert "4.5" in text(app, "#summary")
        await pilot.click("#workstation")
        await settled(app, pilot)
        assert app.section == "seats"


@pytest.mark.asyncio
async def test_projects_filter_pages_merged_details_peers_and_plain_text():
    app = CompassApp(client=FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#nav-projects")
        await settled(app, pilot)
        assert app.query_one("#next", Button).region.bottom <= 23
        await pilot.press("ctrl+n")
        await settled(app, pilot)
        assert app.offset == 10 and "Нет данных" in text(app, "#status")
        app.query_one("#filter", Select).value = "ACCEPTED"
        await settled(app, pilot)
        assert app.offset == 0
        assert app.client.calls[-1][1]["status"] == "ACCEPTED"
        await open_row(app, pilot)
        assert isinstance(app.screen, DetailScreen)
        content = str(app.screen.query_one("#detail-content", Static).content)
        assert "Catalog title" in content and "Personal title" in content
        assert "Результат, %: 110" in content and "[red]Not markup[/red]" in content
        assert "[red]Not markup[/red]" in str(
            app.screen.query_one("#detail-content", Static).render()
        )
        await pilot.click("#detail-peers")
        await settled(app, pilot)
        assert app.section == "peers"
        await open_row(app, pilot)
        assert app.peer == "other-peer" and app.section == "overview"


@pytest.mark.asyncio
async def test_events_utc_type_validation_and_detail():
    app = CompassApp(client=FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#nav-events")
        await settled(app, pilot)
        app.query_one("#filter", Select).value = "EXAM"
        await settled(app, pilot)
        params = app.client.calls[-1][1]
        assert params["from"].endswith("T00:00:00Z") and params["to"].endswith("T23:59:59Z")
        assert params["type"] == "EXAM"
        await open_row(app, pilot)
        assert "Место" not in str(app.screen.query_one("#detail-content", Static).content)
        assert "Зарегистрировано: 2" in str(app.screen.query_one("#detail-content", Static).content)
        await pilot.press("escape")
        app.query_one("#date-to", Input).value = "bad-date"
        await pilot.click("#dates-go")
        await settled(app, pilot)
        assert "YYYY-MM-DD" in text(app, "#status")


@pytest.mark.asyncio
async def test_campus_cluster_seat_filter_coalitions_and_peer_drilldown():
    app = CompassApp(client=FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#nav-campuses")
        await settled(app, pilot)
        await open_row(app, pilot)
        assert app.section == "clusters"
        await open_row(app, pilot)
        assert app.section == "seats"
        app.query_one("#filter", Select).value = "false"
        await settled(app, pilot)
        assert app.client.calls[-1] == ("clusters/3/map", {"limit": 10, "offset": 0,
                                                        "occupied": "false"})
        await pilot.click("#coalitions")
        await settled(app, pilot)
        await open_row(app, pilot)
        assert app.section == "peers"
        assert app.client.calls[-1][0] == "coalitions/7/participants"
        await open_row(app, pilot)
        assert app.peer == "other-peer"


@pytest.mark.asyncio
async def test_progress_graph_search_dependencies_and_sales():
    app = CompassApp(client=FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#nav-progress")
        await settled(app, pilot)
        assert "Навыки" in text(app, "#summary") and "Explorer" in text(app, "#summary")
        assert app.query_one("#table", DataTable).row_count == 1
        await pilot.click("#nav-graph")
        await settled(app, pilot)
        app.query_one("#search", Input).value = "C1"
        app.query_one("#search", Input).focus()
        await pilot.press("enter")
        tree = app.query_one("#graph-tree", Tree)
        tree.select_node(tree.root.children[0].children[0])
        tree.focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert isinstance(app.screen, DetailScreen)
        assert "Зависимости" in str(app.screen.query_one("#detail-content", Static).content)
        assert "Next" in str(app.screen.query_one("#detail-content", Static).content)
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        await pilot.click("#nav-sales")
        await settled(app, pilot)
        assert app.query_one("#table", DataTable).row_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(403, "разрешения"), (429, "подождите"),
                                             (503, "Ошибка")])
async def test_error_retry_and_success(status, expected):
    client = FakeClient()
    client.fail = FakeError(status)
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#nav-sales")
        await settled(app, pilot)
        assert expected in text(app, "#status")
        client.fail = None
        await pilot.press("ctrl+r")
        await settled(app, pilot)
        assert app.query_one("#table", DataTable).row_count == 1


@pytest.mark.asyncio
async def test_expired_session_returns_login():
    class ExpiredClient(FakeClient):
        async def get(self, path, params=None):
            if self.fail:
                self.authenticated = False
            return await super().get(path, params)

    client = ExpiredClient()
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        client.fail = FakeError(401)
        await pilot.click("#nav-sales")
        await settled(app, pilot)
        assert not client.authenticated
        assert "Сессия истекла" in text(app, "#login-status")


@pytest.mark.asyncio
async def test_loading_cancel_and_late_result_cannot_replace_new_section():
    client = FakeClient()
    client.gate = asyncio.Event()
    client.ignore_cancel = True
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#nav-projects")
        await pilot.pause()
        assert app.pending and "Загрузка" in text(app, "#status")
        await pilot.click("#nav-sales")
        await pilot.pause()
        assert app.section == "sales"
        client.gate.set()
        await settled(app, pilot)
        assert app.section == "sales" and app.query_one("#table", DataTable).row_count == 1
        assert app.rows[0]["type"] == "PRP"
        client.gate.clear()
        client.ignore_cancel = False
        await pilot.click("#nav-projects")
        await pilot.press("escape")
        await pilot.pause()
        assert not app.pending and "отменена" in text(app, "#status")


@pytest.mark.asyncio
async def test_input_typing_does_not_trigger_navigation_and_exact_peer():
    app = CompassApp(client=FakeClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+l")
        app.query_one("#peer", Input).value = ""
        await pilot.press("p", "r", "o", "j", "e", "c", "t", "s")
        assert app.section == "overview" and app.query_one("#peer", Input).value == "projects"
        await pilot.press("enter")
        await settled(app, pilot)
        assert app.peer == "projects" and ("overview", "projects") in app.client.calls


@pytest.mark.asyncio
async def test_demo_client_integration_all_primary_sections():
    from s21_compass.demo import DemoClient

    app = CompassApp(client=DemoClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert "ДЕМО" in text(app, "#brand") and "вымышленные" in text(app, "#brand")
        for section in ("projects", "courses", "progress", "events", "campuses", "graph", "sales"):
            await pilot.click(f"#nav-{section}")
            await settled(app, pilot)
            assert app.section == section
            assert "Не удалось" not in text(app, "#status")
            if section not in ("graph",):
                assert app.query_one("#table", DataTable).row_count > 0
        await pilot.press("ctrl+o")
        await pilot.click("#demo")
        await settled(app, pilot)
        assert app.client.demo and app.client.authenticated
        assert date.today().isoformat() == app.query_one("#date-from", Input).value


@pytest.mark.asyncio
async def test_demo_contextual_resources_and_course_detail():
    from s21_compass.demo import DEMO_CAMPUS_ID, DemoClient

    app = CompassApp(client=DemoClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+3")
        await settled(app, pilot)
        assert app.section == "courses"
        await open_row(app, pilot)
        assert "Каталог" in str(app.screen.query_one("#detail-content", Static).render())
        assert "Прогресс участника" in str(app.screen.query_one("#detail-content", Static).render())
        await pilot.press("escape")
        await pilot.press("ctrl+2")
        await settled(app, pilot)
        await open_row(app, pilot)
        await pilot.click("#detail-peers")
        await settled(app, pilot)
        app.query_one("#campus-filter", Input).value = DEMO_CAMPUS_ID
        app.query_one("#campus-filter", Input).focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert app.rows
        await pilot.press("ctrl+n")
        await settled(app, pilot)
        assert app.offset == 10
        await pilot.press("ctrl+6")
        await settled(app, pilot)
        await open_row(app, pilot)
        await pilot.click("#members")
        await settled(app, pilot)
        assert app.section == "peers" and app.rows
        await pilot.click("#coalitions")
        await settled(app, pilot)
        await open_row(app, pilot)
        assert app.context.get("coalitionId") and app.rows
        await pilot.click("#clusters")
        await settled(app, pilot)
        await open_row(app, pilot)
        assert app.section == "seats"
        app.query_one("#filter", Select).value = "false"
        await settled(app, pilot)
        assert app.rows and all(not row.get("login") for row in app.rows)
        assert not app.pending


@pytest.mark.asyncio
async def test_progress_xp_failure_keeps_skills_and_badges():
    class PartialClient(FakeClient):
        async def get(self, path, params=None):
            if path.endswith("/experience-history"):
                raise FakeError(503, "XP временно недоступен")
            return await super().get(path, params)

    app = CompassApp(client=PartialClient())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+4")
        await settled(app, pilot)
        assert "XP временно недоступен" in text(app, "#summary")
        assert "Explorer" in text(app, "#summary")
        assert "99" in text(app, "#summary")


@pytest.mark.asyncio
async def test_late_peer_overview_cannot_replace_new_peer():
    gate = asyncio.Event()

    class SlowPeer(FakeClient):
        async def overview(self, login=None):
            if login == "slow-peer":
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    await gate.wait()
            return await super().overview(login)

    app = CompassApp(client=SlowPeer())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        app.query_one("#peer", Input).value = "slow-peer"
        app.lookup()
        await pilot.pause()
        app.query_one("#peer", Input).value = "new-peer"
        app.lookup()
        await pilot.pause()
        gate.set()
        await settled(app, pilot)
        assert app.peer == "new-peer"
        assert "Никнейм: new-peer" in text(app, "#summary")
        assert "slow-peer" not in text(app, "#summary")


@pytest.mark.asyncio
async def test_auth_error_has_actionable_message_and_clears_password():
    class DeniedClient(FakeClient):
        async def login(self, username, password, participant_login=None):
            raise FakeError(401, "Проверьте учётные данные")

    app = CompassApp(client=DeniedClient(False))
    async with app.run_test(size=(80, 24)) as pilot:
        for key, value in (("username", "account"), ("password", "fixture"),
                           ("participant-login", "peer")):
            app.query_one(f"#{key}", Input).value = value
        await pilot.click("#login")
        await settled(app, pilot)
        assert "Проверьте учётные данные" in text(app, "#login-status")
        assert app.query_one("#password", Input).value == ""
        assert not app.query_one("#login", Button).disabled


@pytest.mark.asyncio
async def test_overview_collecting_expired_sections_returns_login():
    class ExpiredOverview(FakeClient):
        async def overview(self, login=None):
            self.authenticated = False
            return {"participant": None, "errors": {"participant": "Сессия истекла"}}

    app = CompassApp(client=ExpiredOverview())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert app.query_one("#login-panel").display
        assert "Сессия истекла" in text(app, "#login-status")


@pytest.mark.asyncio
async def test_detail_logout_and_unknown_error_does_not_leak_exception_body():
    client = FakeClient()
    app = CompassApp(client=client)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        client.fail = TimeoutError("sensitive request body")
        await pilot.press("ctrl+8")
        await settled(app, pilot)
        assert "соединение" in text(app, "#status")
        assert "sensitive" not in text(app, "#status")
        client.fail = None
        await pilot.press("ctrl+r")
        await settled(app, pilot)
        await open_row(app, pilot)
        await pilot.press("ctrl+o")
        await settled(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.query_one("#login-panel").display
        assert not client.authenticated
