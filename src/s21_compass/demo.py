"""Fictional, schema-shaped School21 fixtures. This client never opens a socket."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from .client import School21Error, collect_overview, utc_datetime, validate_login, validate_resource

DEMO_CAMPUS_ID = "11111111-1111-4111-8111-111111111111"
SECOND_CAMPUS_ID = "22222222-2222-4222-8222-222222222222"


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


class DemoClient:
    demo = True

    def __init__(self) -> None:
        self.login_name: str | None = "demo-peer"
        self.authenticated = True
        self._closed = False
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        self._peers = ["demo-peer", "demo-owl", "demo-fox"] + [f"demo-peer-{i:02}" for i in range(61)]
        self._campuses = [
            {"id": DEMO_CAMPUS_ID, "shortName": "Demo North", "fullName": "Вымышленный кампус Север"},
            {"id": SECOND_CAMPUS_ID, "shortName": "Demo South", "fullName": "Вымышленный кампус Юг"},
        ]
        statuses = ["IN_PROGRESS", "IN_REVIEWS", "ACCEPTED", "REGISTERED", "ASSIGNED", "FAILED"]
        titles = ["C2 SimpleBashUtils", "C3 String+", "C4 Math", "C5 Decimal", "C6 Matrix", "C7 SmartCalc"]
        self._projects = [
            {"id": 101 + i, "title": titles[i % 6] + (f" · {i // 6 + 1}" if i >= 6 else ""),
             "type": "GROUP" if i % 4 == 1 else "INDIVIDUAL", "status": statuses[i % 6],
             "finalPercentage": 115 if i % 6 == 2 else (45 if i % 6 == 5 else None),
             "completionDateTime": _iso(today - timedelta(days=i + 1)) if i % 6 in {2, 5} else None,
             "teamMembers": [{"login": "demo-peer", "isTeamlead": True},
                             {"login": "demo-owl", "isTeamlead": False}] if i % 4 == 1 else None,
             "courseId": 201 + i % 3}
            for i in range(18)
        ]
        course_statuses = ["IN_PROGRESS", "REGISTERED", "ACCEPTED", "ASSIGNED", "FAILED"]
        self._courses = [
            {"id": 201 + i, "title": ["Основы C", "SQL Bootcamp", "Python", "DevOps", "Алгоритмы"][i % 5]
             + (f" · {i // 5 + 1}" if i >= 5 else ""), "status": course_statuses[i % 5],
             "finalPercentage": 100 if i % 5 == 2 else None,
             "completionDateTime": _iso(today - timedelta(days=14 + i)) if i % 5 == 2 else None}
            for i in range(12)
        ]
        self._history = [{"expValue": 100 + i * 5, "accrualDateTime": _iso(today - timedelta(days=i))}
                         for i in range(64)]
        self._events = [
            {"id": 301 + i, "type": ["ACTIVITY", "EXAM", "TEST"][i % 3],
             "name": ["Демо: peer-to-peer workshop", "Демо: экзамен C", "Демо: SQL test"][i % 3],
             "description": "Вымышленное событие для знакомства с интерфейсом.",
             "location": "Demo North · Ocean", "startDateTime": _iso(today + timedelta(days=i, hours=10)),
             "endDateTime": _iso(today + timedelta(days=i, hours=12)),
             "capacity": 24, "registerCount": 8 + i % 12, "organizers": ["demo-owl"]}
            for i in range(64)
        ]
        self._seats = [{"row": chr(97 + i // 16), "number": i % 16 + 1,
                        **({"login": self._peers[i]} if i % 3 else {})} for i in range(64)]
        self._coalitions = [{"coalitionId": 401, "name": "Demo Capybaras"},
                            {"coalitionId": 402, "name": "Demo Owls"}]
        self._clusters = [{"id": 501, "name": "Ocean", "capacity": 64,
                           "availableCapacity": 22, "floor": 2},
                          {"id": 502, "name": "Forest", "capacity": 64,
                           "availableCapacity": 22, "floor": 3}]
        self._graph = {
            "nodes": [
                {"id": "c", "label": "Основы C", "items": [
                    {"id": "c-101", "code": "C2", "handles": ["c-101-out"],
                     "entityType": "PROJECT", "entityId": 101},
                    {"id": "c-102", "code": "C3", "handles": ["c-102-in", "c-102-out"],
                     "entityType": "PROJECT", "entityId": 102}]},
                {"id": "sql", "label": "SQL", "items": [
                    {"id": "sql-202", "code": "SQL", "handles": ["sql-202-in"],
                     "entityType": "COURSE", "entityId": 202}]},
            ],
            "edges": [{"id": "c-sql", "source": "c", "target": "sql",
                       "sourceHandle": "c-102-out", "targetHandle": "sql-202-in"}],
        }
        self._sales = [{"type": "PRP", "status": "ACTIVE", "startDateTime": _iso(today),
                        "progressPercentage": 35},
                       {"type": "CRP", "status": "PLANNED",
                        "startDateTime": _iso(today + timedelta(days=2)), "progressPercentage": None}]
        self._badge_date = _iso(today - timedelta(days=7))

    async def login(self, username: str, password: str, participant_login: str | None = None) -> None:
        if self._closed:
            raise School21Error(401, "Клиент закрыт.")
        self.login_name = validate_login(participant_login if participant_login is not None else "demo-peer")
        self.authenticated = True

    def _page(self, items: list, query: dict, default: int = 50) -> list:
        offset = query.get("offset", 0)
        return items[offset:offset + query.get("limit", default)]

    def _detail(self, items: list, identifier: int) -> dict:
        for item in items:
            if item["id"] == identifier:
                return item
        raise School21Error(404, "Демо-данные не найдены.")

    async def get(self, path: str, params: dict | None = None) -> dict | list | float:
        resource, _, variables, query = validate_resource(path, params)
        if not self.authenticated:
            raise School21Error(401, "Сессия истекла. Войдите снова.")
        template = resource.template
        login = variables.get("login")
        if login is not None and login not in self._peers:
            raise School21Error(404, "Участник не найден в вымышленном демо-кампусе.")
        if "campusId" in variables and variables["campusId"] not in {DEMO_CAMPUS_ID, SECOND_CAMPUS_ID}:
            raise School21Error(404, "Демо-кампус не найден.")
        if "clusterId" in variables and variables["clusterId"] not in {501, 502}:
            raise School21Error(404, "Демо-кластер не найден.")
        if "coalitionId" in variables and variables["coalitionId"] not in {401, 402}:
            raise School21Error(404, "Демо-коалиция не найдена.")
        if template == "participants/{login}":
            payload = {"login": login, "expValue": 4250, "level": 7, "expToNextLevel": 750,
                       "campus": {"id": DEMO_CAMPUS_ID, "shortName": "Demo North"}, "status": "ACTIVE",
                       "className": "DEMO", "parallelName": "Вымышленная основная программа"}
        elif template == "participants/{login}/points":
            payload = {"peerReviewPoints": 12, "codeReviewPoints": 5, "coins": 24}
        elif template == "participants/{login}/logtime":
            payload = 26.5
        elif template == "participants/{login}/feedback":
            payload = {"averageVerifierPunctuality": 4.7, "averageVerifierInterest": 4.5,
                       "averageVerifierThoroughness": None, "averageVerifierFriendliness": 4.9}
        elif template == "participants/{login}/skills":
            payload = {"skills": [{"name": "C", "points": 72}, {"name": "SQL", "points": 38},
                                  {"name": "Teamwork", "points": 54}]}
        elif template == "participants/{login}/badges":
             payload = {"badges": [{"name": "Демо: первый проект", "receiptDateTime": self._badge_date,
                                  "iconUrl": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
                                  "viewBox='0 0 24 24'%3E%3Cpath fill='%23086c65' "
                                  "d='m12 2 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z'/%3E%3C/svg%3E"}]}
        elif template == "participants/{login}/coalition":
            payload = {**self._coalitions[0], "rank": 12}
        elif template == "participants/{login}/workstation":
            payload = {"clusterId": 501, "clusterName": "Ocean", "row": "b", "number": 8}
        elif template == "participants/{login}/experience-history":
            payload = {"expHistory": self._page(self._history, query)}
        elif template in {"participants/{login}/projects", "participants/{login}/courses"}:
            key = template.rsplit("/", 1)[1]
            items = self._projects if key == "projects" else self._courses
            if "status" in query:
                items = [item for item in items if item["status"] == query["status"]]
            payload = {key: self._page(items, query, resource.default_limit)}
        elif template == "participants/{login}/projects/{projectId}":
            payload = self._detail(self._projects, variables["projectId"])
        elif template == "participants/{login}/courses/{courseId}":
            payload = self._detail(self._courses, variables["courseId"])
        elif template == "projects/{projectId}":
            item = self._detail(self._projects, variables["projectId"])
            payload = {"projectId": item["id"], "title": item["title"], "type": item["type"],
                       "description": "Демо-проект: практика, тестирование и peer review.",
                       "durationHours": 40, "xp": 500, "courseId": item["courseId"],
                       "startConditions": None}
        elif template == "courses/{courseId}":
            item = self._detail(self._courses, variables["courseId"])
            payload = {"courseId": item["id"], "title": item["title"],
                       "description": "Вымышленный учебный курс для демонстрации.",
                       "durationHours": 120, "xp": 1500, "startConditions": []}
        elif template == "events":
            start, end = utc_datetime(query["from"], "from"), utc_datetime(query["to"], "to")
            items = [item for item in self._events
                     if start <= utc_datetime(item["startDateTime"], "startDateTime") <= end
                     and ("type" not in query or item["type"] == query["type"])]
            payload = {"events": self._page(items, query)}
        elif template == "campuses":
            payload = {"campuses": self._campuses}
        elif template == "campuses/{campusId}/clusters":
            payload = {"clusters": self._clusters if variables["campusId"] == DEMO_CAMPUS_ID else []}
        elif template == "campuses/{campusId}/coalitions":
            items = self._coalitions if variables["campusId"] == DEMO_CAMPUS_ID else []
            payload = {"coalitions": self._page(items, query)}
        elif template in {"campuses/{campusId}/participants", "coalitions/{coalitionId}/participants",
                          "projects/{projectId}/participants"}:
            items = self._peers
            if template == "campuses/{campusId}/participants" and variables["campusId"] != DEMO_CAMPUS_ID:
                items = []
            if template == "projects/{projectId}/participants":
                self._detail(self._projects, variables["projectId"])
                if "status" in query:
                    items = [peer for i, peer in enumerate(items)
                             if self._projects[i % len(self._projects)]["status"] == query["status"]]
                if "campusId" in query and query["campusId"] != DEMO_CAMPUS_ID:
                    items = []
            payload = {"participants": self._page(items, query)}
        elif template == "clusters/{clusterId}/map":
            items = self._seats
            if "occupied" in query:
                items = [item for item in items if bool(item.get("login")) == (query["occupied"] == "true")]
            payload = {"clusterMap": self._page(items, query)}
        elif template == "graph":
            payload = self._graph
        elif template == "sales":
            payload = {"sales": self._sales}
        else:
            raise School21Error(400, "Ресурс не входит в демо API.")
        return deepcopy(payload)

    async def overview(self, login: str | None = None) -> dict:
        if not self.authenticated:
            raise School21Error(401, "Сессия истекла. Войдите снова.")
        return await collect_overview(self, login)

    async def logout(self) -> None:
        self.authenticated = False
        self.login_name = None

    async def close(self) -> None:
        await self.logout()
        self._closed = True
