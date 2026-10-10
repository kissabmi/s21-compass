"""Async, read-only Textual workspace for the shared School21 client."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, ClassVar
from urllib.parse import quote

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Select, Static, Tree

SECTIONS = [
    ("Обзор", "overview"), ("Проекты", "projects"), ("Курсы", "courses"),
    ("Прогресс", "progress"), ("События", "events"), ("Кампусы", "campuses"),
    ("Дерево", "graph"), ("Распродажи", "sales"),
]
STATUSES = ["ASSIGNED", "REGISTERED", "IN_PROGRESS", "IN_REVIEWS", "ACCEPTED", "FAILED"]
LABELS = {
    "participant": "Профиль", "login": "Никнейм", "level": "Уровень",
    "expValue": "XP", "expToNextLevel": "XP до следующего уровня", "campus": "Кампус",
    "status": "Статус", "className": "Волна", "parallelName": "Программа",
    "points": "Баланс", "peerReviewPoints": "PRP", "codeReviewPoints": "CRP",
    "coins": "Монеты", "logtime": "Средние часы в неделю", "feedback": "Отзывы",
    "averageVerifierPunctuality": "Пунктуальность",
    "averageVerifierInterest": "Заинтересованность",
    "averageVerifierThoroughness": "Тщательность",
    "averageVerifierFriendliness": "Дружелюбие", "coalition": "Коалиция",
    "coalitionId": "ID коалиции", "rank": "Ранг", "workstation": "Рабочее место",
    "clusterId": "ID кластера", "clusterName": "Кластер", "row": "Ряд",
    "number": "Место", "name": "Название", "title": "Название",
    "shortName": "Короткое название", "fullName": "Полное название",
    "description": "Описание", "durationHours": "Длительность, ч",
    "xp": "XP за завершение", "type": "Тип", "finalPercentage": "Результат, %",
    "completionDateTime": "Дата завершения (UTC)", "teamMembers": "Команда",
    "isTeamlead": "Лидер", "startConditions": "Условия старта",
    "courseId": "ID курса", "projectId": "ID проекта", "id": "ID",
    "startDateTime": "Начало (UTC)", "endDateTime": "Конец (UTC)",
    "location": "Место проведения", "organizers": "Организаторы",
    "capacity": "Вместимость", "registerCount": "Зарегистрировано",
    "availableCapacity": "Свободно", "floor": "Этаж", "receiptDateTime": "Получен (UTC)",
    "accrualDateTime": "Начислено (UTC)", "iconUrl": "Иконка", "skills": "Навыки",
    "badges": "Значки", "progressPercentage": "Прогресс, %", "errors": "Ошибки разделов",
    "catalog": "Каталог", "personal": "Прогресс участника", "dependencies": "Зависимости",
    "source": "Откуда", "target": "Куда", "sourceHandle": "Исходный порт",
    "targetHandle": "Целевой порт", "code": "Код", "entityType": "Тип сущности",
    "entityId": "ID сущности", "handles": "Порты", "pointsValue": "Баллы",
    "logicalOperator": "Связка", "rulesInGroup": "Условия группы",
    "fieldName": "Параметр", "fieldId": "ID параметра", "operator": "Сравнение",
    "subFieldValue": "Уточнение", "subFieldKey": "Ключ уточнения", "value": "Значение",
    "key": "Ключ", "error": "Ошибка", "progress": "Прогресс",
}


def display(value: Any) -> str:
    """Plain text only: upstream brackets never become Rich markup."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def fields(value: Any, depth: int = 0) -> str:
    """Readable recursive detail inspector, including complex start conditions."""
    indent = "  " * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = LABELS.get(key, key)
            if isinstance(item, (dict, list)) and item:
                lines.extend([f"{indent}{label}:", fields(item, depth + 1)])
            else:
                lines.append(f"{indent}{label}: {display(item) if item != [] else 'нет данных'}")
        return "\n".join(lines) or f"{indent}Нет данных"
    if isinstance(value, list):
        return "\n".join(f"{indent}• {fields(item, depth + 1).lstrip()}" for item in value)
    return indent + display(value)


def error_message(error: Exception) -> str:
    status = getattr(error, "status", None)
    if status == 401:
        return "Сессия истекла. Войдите снова."
    if status == 403:
        return "Нет разрешения на просмотр этого раздела."
    if status == 429:
        return "Лимит запросов: подождите и повторите (Ctrl+R)."
    # Unknown exceptions may include request/credential bodies: don't echo them.
    if hasattr(error, "message"):
        return str(error.message)
    return "Не удалось загрузить данные. Проверьте соединение и повторите (Ctrl+R)."


class DetailScreen(ModalScreen):
    BINDINGS: ClassVar = [Binding("escape", "dismiss", "Закрыть", priority=True)]
    DEFAULT_CSS = """
    DetailScreen { align: center middle; background: $background 70%; }
    #detail-box { width: 90%; height: 90%; border: round $accent; padding: 1; }
    #detail-title { height: 2; text-style: bold; }
    #detail-scroll { height: 1fr; }
    #detail-actions { height: 3; }
    #detail-actions Button { width: 1fr; min-width: 10; }
    """

    def __init__(self, title: str, data: Any, actions: list[tuple[str, str]] | None = None):
        super().__init__()
        self.detail_title, self.data, self.actions = title, data, actions or []

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-box"):
            yield Static(self.detail_title, id="detail-title", markup=False)
            with VerticalScroll(id="detail-scroll"):
                yield Static(fields(self.data), id="detail-content", markup=False)
            with Horizontal(id="detail-actions"):
                for label, key in self.actions:
                    yield Button(label, id=key)
                yield Button("Закрыть · Esc", id="detail-close")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id if event.button.id != "detail-close" else None)


class CompassApp(App):
    """Owns one client; all upstream work runs in cancellable async workers."""

    TITLE = "S21 Compass"
    CSS = """
    Screen { background: $background; }
    #brand { height: 1; background: $primary; color: $text; padding: 0 1; }
    #login-panel { width: 60; max-width: 100%; height: 1fr; margin: 0 1; }
    #login-panel Label { height: 1; }
    #login-panel Input { height: 3; }
    #login-actions { height: 3; }
    #login-actions Button { width: 1fr; min-width: 10; }
    #login-status { height: auto; max-height: 3; }
    #workspace { height: 1fr; }
    #sidebar { width: 16; border-right: solid $primary; }
    #sidebar Button { width: 15; min-width: 0; height: 2; border: none; }
    #sidebar .selected { background: $accent; }
    #main { width: 1fr; padding: 0 1; }
    #peer-bar, #filters, #date-bar, #context-bar, #pager { height: 3; }
    #peer-bar Input { width: 1fr; }
    #peer-bar Button { width: 9; min-width: 9; }
    #filters Select { width: 24; }
    #filters Input { width: 1fr; }
    #filters Button { width: 10; min-width: 10; }
    #date-bar Input { width: 1fr; }
    #date-bar Label { width: 5; padding-top: 1; }
    #date-bar Button { width: 11; min-width: 11; }
    #context-bar Button { width: 1fr; min-width: 10; }
    #heading { height: 1; text-style: bold; }
    #status { height: auto; max-height: 3; color: $text-muted; }
    #content { height: 1fr; }
    #summary-scroll { height: 1fr; }
    #summary { height: auto; }
    #table, #graph-tree { height: 1fr; }
    #pager Button { width: 10; min-width: 10; }
    #page-info { width: 1fr; padding-top: 1; }
    #help { height: 1; color: $text-muted; }
    Footer { height: 1; }
    """
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Выход", priority=True),
        Binding("ctrl+r", "refresh", "Повторить", priority=True),
        Binding("ctrl+l", "peer_focus", "Пир", priority=True),
        Binding("ctrl+n", "next_page", "След. стр.", show=False, priority=True),
        Binding("ctrl+p", "previous_page", "Пред. стр.", show=False, priority=True),
        Binding("ctrl+b", "back", "Назад", show=False, priority=True),
        Binding("ctrl+o", "logout", "Выйти", priority=True),
        Binding("escape", "cancel_load", "Отмена", show=False),
        *[Binding(f"ctrl+{index}", f"section('{key}')", label, show=False, priority=True)
          for index, (label, key) in enumerate(SECTIONS, 1)],
    ]

    def __init__(self, *, client=None, demo: bool = False):
        super().__init__()
        if client is None:
            if demo:
                from .demo import DemoClient
                client = DemoClient()
            else:
                from .client import School21Client
                client = School21Client()
        self.client = client
        self.peer = client.login_name or ""
        self.section = "overview"
        self.offset = 0
        self.limit = 10
        self.filter_value = ""
        self.search = ""
        self.context: dict = {}
        self.rows: list = []
        self.overview_data: dict = {}
        self.graph_data: dict = {}
        self.generation = 0
        self.pending = False
        self.auth_pending = False
        self._configuring = False
        self._closing = False

    def compose(self) -> ComposeResult:
        yield Static("S21 Compass · неофициальный read-only клиент", id="brand", markup=False)
        with VerticalScroll(id="login-panel"):
            yield Static("Вход в Школу 21", markup=False)
            yield Label("Аккаунт (username / email)")
            yield Input(placeholder="Аккаунт", id="username")
            yield Label("Пароль — только в памяти на время входа")
            yield Input(password=True, placeholder="Пароль", id="password")
            yield Label("Никнейм участника (явно, может отличаться от аккаунта)")
            yield Input(placeholder="participant nickname", id="participant-login")
            with Horizontal(id="login-actions"):
                yield Button("Войти", id="login", variant="primary")
                yield Button("Демо", id="demo")
                yield Button("Отмена", id="cancel-auth")
            yield Static("Демо: вымышленные данные, без сети.", id="login-status", markup=False)
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                for label, section in SECTIONS:
                    yield Button(label, id=f"nav-{section}")
                yield Button("Обновить", id="refresh")
                yield Button("Выйти", id="logout")
            with Vertical(id="main"):
                with Horizontal(id="peer-bar"):
                    yield Input(self.peer, placeholder="Точный никнейм пира", id="peer")
                    yield Button("Найти", id="lookup")
                yield Static("Обзор", id="heading", markup=False)
                with Horizontal(id="filters"):
                    yield Select([("Все", "")], value="", allow_blank=False, id="filter")
                    yield Input(placeholder="campus UUID (необязательно)", id="campus-filter")
                    yield Input(placeholder="Поиск на странице / в дереве", id="search")
                    yield Button("Искать", id="search-go")
                with Horizontal(id="date-bar"):
                    yield Label("С / дата")
                    yield Input(date.today().isoformat(), id="date-from")
                    yield Label("По")
                    yield Input((date.today() + timedelta(days=30)).isoformat(), id="date-to")
                    yield Button("Применить", id="dates-go")
                with Horizontal(id="context-bar"):
                    yield Button("Назад", id="back")
                    yield Button("Участники", id="members")
                    yield Button("Коалиции", id="coalitions")
                    yield Button("Кластеры", id="clusters")
                    yield Button("Место", id="workstation")
                    yield Button("Кампус", id="my-campus")
                yield Static("", id="status", markup=False)
                with Vertical(id="content"):
                    with VerticalScroll(id="summary-scroll"):
                        yield Static("", id="summary", markup=False)
                    yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
                    yield Tree(Text("Учебное дерево"), id="graph-tree")
                with Horizontal(id="pager"):
                    yield Button("← Пред.", id="previous")
                    yield Static("Страница 1", id="page-info", markup=False)
                    yield Button("След. →", id="next")
                yield Static("Ctrl+1–8: раздел · Enter: детали · Ctrl+B: назад · Esc: отмена", id="help")
        yield Footer()

    def on_mount(self) -> None:
        self.show_session()

    async def on_unmount(self) -> None:
        self._closing = True
        self.generation += 1
        self.workers.cancel_all()
        await self.client.close()

    def show_session(self) -> None:
        authenticated = self.client.authenticated
        self.query_one("#login-panel").display = not authenticated
        self.query_one("#workspace").display = authenticated
        marker = "ДЕМО · вымышленные данные" if self.client.demo else "read-only"
        self.query_one("#brand", Static).update(f"S21 Compass · {marker}")
        if authenticated:
            self.peer = self.client.login_name or self.peer
            self.query_one("#peer", Input).value = self.peer
            self.navigate("overview")
        else:
            self.query_one("#username", Input).focus()

    def invalidate(self) -> int:
        self.generation += 1
        self.workers.cancel_group(self, "data")
        self.pending = False
        return self.generation

    def current(self, token: int) -> bool:
        return token == self.generation and not self._closing

    def navigate(self, section: str, context: dict | None = None) -> None:
        self.invalidate()
        self.section, self.context = section, context or {}
        self.offset, self.filter_value, self.search = 0, "", ""
        self.configure()
        self.load()

    def configure(self) -> None:
        self._configuring = True
        section = self.section
        for _, key in SECTIONS:
            self.query_one(f"#nav-{key}").set_class(section == key, "selected")
        title = {key: label for label, key in SECTIONS}.get(section, {
            "clusters": "Кластеры", "seats": "Места · Enter: профиль занятого места",
            "peers": "Участники · Enter: профиль", "coalitions": "Коалиции",
        }.get(section, section))
        self.query_one("#heading", Static).update(f"{title} · {self.peer}")
        options = [("Все", "")]
        if section in ("projects", "courses") or self.context.get("projectId"):
            options += [(value, value) for value in STATUSES
                        if section != "courses" or value != "IN_REVIEWS"]
        elif section == "events":
            options += [(value, value) for value in ("ACTIVITY", "EXAM", "TEST")]
        elif section == "seats":
            options += [("Занятые", "true"), ("Свободные", "false")]
        self.query_one("#filter", Select).set_options(options)
        self.query_one("#filter", Select).value = ""
        self.query_one("#search", Input).value = ""
        self.query_one("#campus-filter", Input).value = ""
        self.query_one("#campus-filter").display = section == "peers" and bool(
            self.context.get("projectId")
        )
        self.query_one("#filters").display = section != "overview"
        self.query_one("#date-bar").display = section in ("events", "overview")
        self.query_one("#date-to").display = section == "events"
        self.query_one("#date-bar Label", Label).update("Дата" if section == "overview" else "С")
        self.query_one("#context-bar").display = bool(self.context) or section == "overview"
        campus = bool(self.context.get("campusId"))
        self.query_one("#members").display = campus or section == "overview"
        self.query_one("#coalitions").display = campus
        self.query_one("#clusters").display = campus
        self.query_one("#workstation").display = section == "overview"
        self.query_one("#my-campus").display = section == "overview"
        self.query_one("#back").display = bool(self.context)
        self.query_one("#pager").display = section in (
            "projects", "courses", "progress", "events", "seats", "peers", "coalitions"
        )
        self.query_one("#summary-scroll").display = section == "overview"
        self.query_one("#summary-scroll").styles.height = "1fr"
        self.query_one("#graph-tree").display = section == "graph"
        self.query_one("#table").display = section not in ("overview", "graph")
        self.query_one("#previous", Button).disabled = True
        self.query_one("#next", Button).disabled = True
        self.call_after_refresh(self._configured)

    def _configured(self) -> None:
        self._configuring = False

    def participant_path(self) -> str:
        return "participants/" + quote(self.peer, safe="")

    def request(self) -> tuple[str, dict, str]:
        section = self.section
        params: dict = {"limit": self.limit, "offset": self.offset}
        base = self.participant_path()
        if section in ("projects", "courses"):
            if self.filter_value:
                params["status"] = self.filter_value
            return f"{base}/{section}", params, section
        if section == "progress":
            return f"{base}/experience-history", params, "expHistory"
        if section == "events":
            start = date.fromisoformat(self.query_one("#date-from", Input).value)
            end = date.fromisoformat(self.query_one("#date-to", Input).value)
            if end < start:
                raise ValueError("end before start")
            params.update({"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"})
            if self.filter_value:
                params["type"] = self.filter_value
            return "events", params, "events"
        if section == "seats":
            if self.filter_value:
                params["occupied"] = self.filter_value
            return f"clusters/{self.context['clusterId']}/map", params, "clusterMap"
        if section == "peers":
            if self.context.get("projectId"):
                path = f"projects/{self.context['projectId']}/participants"
                if self.filter_value:
                    params["status"] = self.filter_value
                campus = self.query_one("#campus-filter", Input).value.strip()
                if campus:
                    params["campusId"] = campus
            elif self.context.get("coalitionId"):
                path = f"coalitions/{self.context['coalitionId']}/participants"
            else:
                path = f"campuses/{self.context['campusId']}/participants"
            return path, params, "participants"
        if section in ("clusters", "coalitions"):
            path = f"campuses/{self.context['campusId']}/{section}"
            return path, params if section == "coalitions" else {}, section
        return section, {}, section

    def load(self) -> None:
        if not self.client.authenticated:
            return
        token = self.invalidate()
        self.pending = True
        self.rows = []
        if self.section == "overview":
            self.overview_data = {}
        self.query_one("#table", DataTable).clear(columns=True)
        self.query_one("#summary", Static).update("")
        self.query_one("#graph-tree", Tree).clear()
        self.query_one("#status", Static).update("Загрузка… · Esc: отменить")
        self.query_one("#next", Button).disabled = True
        self.fetch(token)

    @work(group="data", exclusive=True, exit_on_error=False)
    async def fetch(self, token: int) -> None:
        try:
            if self.section == "overview":
                data = await self.client.overview(self.peer)
                if self.current(token):
                    if not self.client.authenticated:
                        await self.session_expired()
                        return
                    self.overview_data = data
                    self.query_one("#summary", Static).update(fields(data))
                    self.query_one("#status", Static).update(
                        "Часть данных недоступна · Ctrl+R: повторить" if data.get("errors") else ""
                    )
            else:
                path, params, key = self.request()
                extras = {}
                try:
                    data = await self.client.get(path, params or None)
                except Exception as error:
                    if self.section != "progress" or getattr(error, "status", None) == 401:
                        raise
                    data = {key: []}
                    extras["expHistory"] = {"error": error_message(error)}
                if not self.current(token):
                    return
                if self.section == "progress":
                    for resource in ("skills", "badges"):
                        try:
                            extras[resource] = await self.client.get(
                                f"{self.participant_path()}/{resource}"
                            )
                        except Exception as error:
                            if getattr(error, "status", None) == 401:
                                raise
                            extras[resource] = {"error": error_message(error)}
                        if not self.current(token):
                            return
                if not self.current(token):
                    return
                if self.section == "graph":
                    self.graph_data = data
                    self.render_graph()
                else:
                    self.rows = data.get(key, []) if isinstance(data, dict) else data
                    self.render_rows(extras)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - isolate failures of injected async clients
            if self.current(token):
                if getattr(error, "status", None) == 401:
                    await self.session_expired()
                else:
                    message = ("Введите даты YYYY-MM-DD; конец не раньше начала."
                               if isinstance(error, ValueError) else error_message(error))
                    self.query_one("#status", Static).update(message)
        finally:
            if token == self.generation:
                self.pending = False

    def render_rows(self, extras: dict | None = None) -> None:
        table = self.query_one("#table", DataTable)
        section = self.section
        columns = {
            "projects": ["title", "status", "finalPercentage"],
            "courses": ["title", "status", "finalPercentage"],
            "events": ["name", "type", "startDateTime", "registerCount", "capacity"],
            "campuses": ["shortName", "fullName"],
            "clusters": ["name", "floor", "availableCapacity", "capacity"],
            "seats": ["row", "number", "login"],
            "coalitions": ["name", "coalitionId"],
            "sales": ["type", "status", "startDateTime", "progressPercentage"],
            "progress": ["accrualDateTime", "expValue"],
            "peers": ["login"],
        }[section]
        table.clear(columns=True)
        table.add_columns(*(Text(LABELS.get(key, key)) for key in columns))
        for index, row in enumerate(self.rows):
            record = {"login": row} if isinstance(row, str) else row
            if self.search and self.search.casefold() not in fields(record).casefold():
                continue
            table.add_row(*(Text(display(record.get(key))) for key in columns), key=str(index))
        count = len(self.rows)
        self.query_one("#page-info", Static).update(
            f"Стр. {self.offset // self.limit + 1} · {count} записей"
        )
        self.query_one("#previous", Button).disabled = self.offset == 0
        self.query_one("#next", Button).disabled = count != self.limit
        self.query_one("#status", Static).update(
            "Нет данных на этой странице." if not count else
            "Нет совпадений на этой странице." if not table.row_count else "Enter: открыть детали"
        )
        if extras:
            # Skills and badges remain readable alongside paginated XP, independently of its rows.
            self.query_one("#summary-scroll").display = True
            self.query_one("#summary-scroll").styles.height = 5
            self.query_one("#summary", Static).update(fields(extras))
        else:
            self.query_one("#summary-scroll").styles.height = "1fr"

    def render_graph(self) -> None:
        tree = self.query_one("#graph-tree", Tree)
        tree.clear()
        matches = 0
        for node in self.graph_data.get("nodes", []):
            items = [item for item in node.get("items", []) if not self.search or
                     self.search.casefold() in (display(node.get("label")) + fields(item)).casefold()]
            if not items:
                continue
            group = tree.root.add(Text(display(node.get("label"))), expand=True)
            for item in items:
                group.add_leaf(Text(f"{display(item.get('code'))} · {item.get('entityType', '—')}"),
                               data={**item, "nodeId": node.get("id")})
                matches += 1
        tree.root.expand()
        self.query_one("#status", Static).update(
            f"{matches} элементов · Enter: каталог и зависимости" if matches else "Нет совпадений."
        )

    @on(Button.Pressed)
    async def button_pressed(self, event: Button.Pressed) -> None:
        key = event.button.id or ""
        if key.startswith("nav-"):
            self.navigate(key[4:])
        elif key == "login":
            self.start_login()
        elif key == "demo":
            if not self.auth_pending:
                self.swap_demo()
        elif key == "cancel-auth":
            self.cancel_auth()
        elif key in ("refresh", "search-go", "dates-go"):
            if key == "search-go":
                self.search = self.query_one("#search", Input).value.strip()
            self.offset = 0 if key != "refresh" else self.offset
            self.load_logtime() if key == "dates-go" and self.section == "overview" else self.load()
        elif key == "lookup":
            self.lookup()
        elif key == "logout":
            await self.action_logout()
        elif key == "previous":
            self.action_previous_page()
        elif key == "next":
            self.action_next_page()
        elif key == "back":
            self.action_back()
        elif key in ("members", "coalitions", "clusters", "workstation", "my-campus"):
            self.context_action(key)

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        key = event.input.id
        if key in ("username", "password", "participant-login"):
            self.start_login()
        elif key == "peer":
            self.lookup()
        elif key == "search":
            self.search = event.value.strip()
            if self.section == "graph":
                self.render_graph()
            else:
                self.load()
        elif key == "campus-filter":
            self.offset = 0
            self.load()
        elif key in ("date-from", "date-to"):
            self.offset = 0
            self.load_logtime() if self.section == "overview" else self.load()

    @on(Select.Changed, "#filter")
    def filter_changed(self, event: Select.Changed) -> None:
        if self._configuring or event.value is Select.BLANK or event.value == self.filter_value:
            return
        self.filter_value = str(event.value)
        self.offset = 0
        self.load()

    def lookup(self) -> None:
        value = self.query_one("#peer", Input).value.strip()
        if not value or any(character in value for character in "/\\?#"):
            self.query_one("#status", Static).update("Введите точный никнейм, без /, ?, #.")
            return
        self.peer = value
        self.navigate("overview")

    def start_login(self) -> None:
        if self.auth_pending:
            return
        username = self.query_one("#username", Input).value
        password_input = self.query_one("#password", Input)
        password = password_input.value
        password_input.value = ""
        nickname = self.query_one("#participant-login", Input).value.strip()
        if not username or not password or not nickname:
            self.query_one("#login-status", Static).update("Заполните аккаунт, пароль и никнейм.")
            return
        self.auth_pending = True
        self.query_one("#login", Button).disabled = True
        self.query_one("#demo", Button).disabled = True
        self.query_one("#login-status", Static).update("Вход… · Отмена: прервать")
        self.generation += 1
        self.authenticate(self.generation, username, password, nickname)

    @work(group="auth", exclusive=True, exit_on_error=False)
    async def authenticate(self, token: int, username: str, password: str, nickname: str) -> None:
        try:
            if self.client.demo:
                from .client import School21Client

                await self.client.close()
                if token != self.generation:
                    return
                self.client = School21Client()
            await self.client.login(username, password, participant_login=nickname)
            if token == self.generation:
                self.auth_pending = False
                self.query_one("#login", Button).disabled = False
                self.query_one("#demo", Button).disabled = False
                self.peer = nickname
                self.show_session()
            else:
                await self.client.logout()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - auth errors must stay on login screen
            if token == self.generation:
                self.query_one("#login-status", Static).update(
                    str(error.message) if getattr(error, "status", None) == 401 and
                    hasattr(error, "message") else error_message(error)
                )
        finally:
            password = ""
            if token == self.generation or not self.auth_pending:
                self.auth_pending = False
                self.query_one("#login", Button).disabled = False
                self.query_one("#demo", Button).disabled = False

    def cancel_auth(self) -> None:
        self.generation += 1
        self.workers.cancel_group(self, "auth")
        self.auth_pending = False
        self.query_one("#password", Input).value = ""
        self.query_one("#login", Button).disabled = False
        self.query_one("#demo", Button).disabled = False
        self.query_one("#login-status", Static).update("Вход отменён. Можно повторить.")

    @work(group="auth", exclusive=True, exit_on_error=False)
    async def swap_demo(self) -> None:
        from .demo import DemoClient
        self.generation += 1
        self.auth_pending = True
        self.query_one("#demo", Button).disabled = True
        self.query_one("#login", Button).disabled = True
        self.query_one("#password", Input).value = ""
        try:
            await self.client.close()
            self.client = DemoClient()
            self.show_session()
        finally:
            self.auth_pending = False
            self.query_one("#demo", Button).disabled = False
            self.query_one("#login", Button).disabled = False

    async def session_expired(self) -> None:
        self.invalidate()
        await self.client.logout()
        self.show_session()
        self.query_one("#login-status", Static).update("Сессия истекла. Войдите снова.")

    async def action_logout(self) -> None:
        while isinstance(self.screen, DetailScreen):
            await self.pop_screen()
        self.invalidate()
        self.cancel_auth()
        await self.client.logout()
        self.peer, self.rows, self.overview_data, self.graph_data = "", [], {}, {}
        self.query_one("#peer", Input).value = ""
        self.query_one("#summary", Static).update("")
        self.query_one("#table", DataTable).clear(columns=True)
        self.query_one("#graph-tree", Tree).clear()
        self.show_session()
        self.query_one("#login-status", Static).update("Вы вышли. Пароль не сохранён.")

    def action_refresh(self) -> None:
        if isinstance(self.screen, DetailScreen):
            self.screen.dismiss("detail-retry")
            return
        if self.client.authenticated:
            self.load()

    def action_peer_focus(self) -> None:
        if isinstance(self.screen, DetailScreen):
            return
        if self.client.authenticated:
            self.query_one("#peer", Input).focus()

    def action_section(self, section: str) -> None:
        if self.client.authenticated:
            if isinstance(self.screen, DetailScreen):
                self.screen.dismiss()
            self.navigate(section)

    def action_next_page(self) -> None:
        if isinstance(self.screen, DetailScreen):
            return
        if self.client.authenticated and not self.pending and not self.query_one("#next", Button).disabled:
            self.offset += self.limit
            self.load()

    def action_previous_page(self) -> None:
        if isinstance(self.screen, DetailScreen):
            return
        if self.client.authenticated and not self.pending and self.offset:
            self.offset = max(0, self.offset - self.limit)
            self.load()

    def action_cancel_load(self) -> None:
        if self.auth_pending:
            self.cancel_auth()
        elif self.pending:
            self.invalidate()
            self.query_one("#status", Static).update("Загрузка отменена · Ctrl+R: повторить")

    def action_back(self) -> None:
        if isinstance(self.screen, DetailScreen):
            self.screen.dismiss()
            return
        context = self.context
        if self.section == "seats" and context.get("campusId"):
            self.navigate("clusters", {"campusId": context["campusId"]})
        elif context.get("projectId"):
            self.navigate("projects")
        elif context.get("campusId"):
            self.navigate("campuses")
        else:
            self.navigate("overview")

    def context_action(self, key: str) -> None:
        if self.section == "overview":
            participant = self.overview_data.get("participant") or {}
            campus = participant.get("campus") or {}
            if key == "my-campus":
                if campus.get("id"):
                    self.navigate("clusters", {"campusId": campus["id"]})
                else:
                    self.query_one("#status", Static).update("Кампус недоступен.")
                return
            if key == "workstation":
                station = self.overview_data.get("workstation") or {}
                if station.get("clusterId") is not None:
                    context = {"clusterId": station["clusterId"]}
                    if campus.get("id"):
                        context["campusId"] = campus["id"]
                    self.navigate("seats", context)
                else:
                    self.query_one("#status", Static).update("Рабочее место недоступно.")
                return
            coalition = self.overview_data.get("coalition") or {}
            if coalition.get("coalitionId") is not None:
                self.navigate("peers", {"coalitionId": coalition["coalitionId"]})
            else:
                self.query_one("#status", Static).update("Коалиция недоступна.")
        elif self.context.get("campusId"):
            self.navigate("peers" if key == "members" else key,
                          {"campusId": self.context["campusId"]})

    @on(DataTable.RowSelected, "#table")
    def selected_row(self, event: DataTable.RowSelected) -> None:
        if self.pending:
            return
        row = self.rows[int(event.row_key.value)]
        section = self.section
        if section == "peers" or (section == "seats" and row.get("login")):
            self.peer = row if isinstance(row, str) else row["login"]
            self.query_one("#peer", Input).value = self.peer
            self.navigate("overview")
        elif section == "campuses":
            self.navigate("clusters", {"campusId": row["id"]})
        elif section == "clusters":
            self.navigate("seats", {**self.context, "clusterId": row["id"]})
        elif section == "coalitions":
            self.navigate("peers", {**self.context, "coalitionId": row["coalitionId"]})
        elif section in ("projects", "courses"):
            self.catalog_detail(section, row["id"], personal=True)
        else:
            self.push_screen(DetailScreen("Подробности", row))

    @on(Tree.NodeSelected, "#graph-tree")
    def selected_node(self, event: Tree.NodeSelected) -> None:
        item = event.node.data
        if item and not self.pending:
            kind = "courses" if item.get("entityType") == "COURSE" else "projects"
            self.catalog_detail(kind, item["entityId"], graph_item=item)

    @work(group="data", exclusive=True, exit_on_error=False)
    async def catalog_detail(self, kind: str, entity_id: int, *, personal: bool = False,
                             graph_item: dict | None = None) -> None:
        if isinstance(self.screen, DetailScreen):
            return
        token = self.generation
        self.pending = True
        self.query_one("#status", Static).update("Загрузка подробностей…")
        data, actions = {}, [("Повторить", "detail-retry")]
        try:
            for label, path in [("catalog", f"{kind}/{entity_id}")] + (
                [("personal", f"{self.participant_path()}/{kind}/{entity_id}")] if personal else []
            ):
                try:
                    data[label] = await self.client.get(path)
                except Exception as error:
                    if getattr(error, "status", None) == 401:
                        raise
                    data[label] = {"error": error_message(error)}
            if not self.current(token):
                return
            if graph_item:
                labels = {node["id"]: node["label"] for node in self.graph_data.get("nodes", [])}
                data["dependencies"] = [
                    {**edge, "source": labels.get(edge["source"], edge["source"]),
                     "target": labels.get(edge["target"], edge["target"])}
                    for edge in self.graph_data.get("edges", [])
                    if (edge.get("source") == graph_item["nodeId"] and
                        edge.get("sourceHandle") in graph_item.get("handles", [])) or
                    (edge.get("target") == graph_item["nodeId"] and
                     edge.get("targetHandle") in graph_item.get("handles", []))
                ]
            if kind == "projects":
                actions.append(("Участники", "detail-peers"))
            self.query_one("#status", Static).update("Enter: детали · Ctrl+R: повторить")

            def drilldown(choice: str | None) -> None:
                if choice == "detail-retry" and self.current(token):
                    self.catalog_detail(kind, entity_id, personal=personal, graph_item=graph_item)
                elif choice == "detail-peers" and self.current(token):
                    self.navigate("peers", {"projectId": entity_id})

            self.push_screen(DetailScreen("Проект" if kind == "projects" else "Курс", data, actions),
                             drilldown)
        except Exception as error:  # noqa: BLE001 - keep detail failures recoverable
            if self.current(token):
                if getattr(error, "status", None) == 401:
                    await self.session_expired()
                else:
                    self.query_one("#status", Static).update(error_message(error))
        finally:
            if token == self.generation:
                self.pending = False

    @work(group="data", exclusive=True, exit_on_error=False)
    async def load_logtime(self) -> None:
        self.generation += 1
        token = self.generation
        self.pending = True
        try:
            value = self.query_one("#date-from", Input).value
            date.fromisoformat(value)
            self.query_one("#status", Static).update("Загрузка logtime…")
            result = await self.client.get(f"{self.participant_path()}/logtime", {"date": value})
            if self.current(token):
                self.overview_data["logtime"] = result
                self.query_one("#summary", Static).update(fields(self.overview_data))
                self.query_one("#status", Static).update(f"Logtime за неделю с датой {value}")
        except Exception as error:  # noqa: BLE001 - isolate optional section errors
            if self.current(token):
                if getattr(error, "status", None) == 401:
                    await self.session_expired()
                else:
                    self.query_one("#status", Static).update(
                        "Дата: YYYY-MM-DD" if isinstance(error, ValueError) else error_message(error)
                    )
        finally:
            if token == self.generation:
                self.pending = False
