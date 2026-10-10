# Shared implementation contract

Package: `src/s21_compass`. Python >=3.11, async httpx, payload dict/list preserve
upstream names. Resource paths omit `/v1`.
The mandatory shared resource/coverage contract is `docs/resources.md`.

## Core owner: client.py, demo.py, web.py; tests/test_client.py, tests/test_web.py

`School21Error(Exception)` has `status: int`, `message: str`; str(error) is safe
message, never upstream credential/token response body.

`School21Client(*, transport=None)`:
- public `login_name: str | None`, `authenticated: bool`, `demo: bool = False`
- `async login(username: str, password: str, participant_login: str | None = None) -> None`:
  username submitted unchanged; login_name = explicit participant_login if supplied,
  otherwise username before @ is only a suggestion. Both UI expose editable nickname.
- `async get(path: str, params: dict | None = None) -> dict | list | float`:
  whitelist resources, base `/api/v1`, optional leading slash; encoded segments.
- `async overview(login: str | None = None) -> dict`: keys `participant`, `points`,
  `logtime`, `feedback`, `skills`, `badges`, `coalition`, `workstation`, `errors`
  (mapping section->safe string). Independent errors collected.
- `async logout() -> None`: clear local tokens/login.
- `async close() -> None`: clear auth and close HTTP.

`DemoClient()` same interface, starts authenticated, login `demo-peer`, demo=True.
All routes including filters/pages/details, no network, events relative to today.

`create_app(*, client_factory=School21Client)` in web.py, import never starts server.
- GET `/api/session`: anonymous session if needed; JSON `{authenticated, login,
  demo, csrf}`.
- POST `/api/login`: `{username,password,login?}`, X-CSRF-Token; session JSON on success,
  rotate opaque ID. POST `/api/demo`, `/api/logout` require same CSRF header.
- GET `/api/overview/{login}`: overview, authenticated required.
- GET `/api/data/{path:path}`: whitelisted resource+query via client.get.
- errors JSON `{error: safe message}`, meaningful status.
- GET `/` index, `/static/*` packaged relative assets. No arbitrary proxy.

## TUI owner: tui.py; tests/test_tui.py

`CompassApp(*, client=None, demo: bool=False)`: provided client for tests; else
DemoClient if demo, School21Client otherwise. Own client lifecycle; main CLI calls
`.run()`. Use get/overview above. No extra mandatory core methods.

## Frontend owner: static/index.html, static/app.js, static/style.css

No framework/build/CDN dependencies. Initialize csrf from session. Same-origin
fetch, mutation X-CSRF-Token. 401 returns login; 429 actionable message. Abort or
ignore stale navigation requests. Encode segments, textContent for upstream data.
Accessible labels/focus, desktop+mobile, working detail modal, persistent demo badge.

Design: cool daylight campus workspace. Ink #162d32, mist #eff4f4, paper #fff,
teal #086c65, sky #4a70b0, amber #a66510. System sans; mono for codes/IDs only.
Compact persistent nav; dominant active-project workspace and contextual progress
rail, no marketing hero. Left aligned, quiet borders, 8px rhythm; calm two-column
login. Mobile nav adapts, wide tables scroll inside panel, not entire viewport.

## Main owner

__init__.py, cli.py, packaging, README/LICENSE/CI, docs, integrations/public preflight.

## Upstream reference

Pinned community OpenAPI: https://raw.githubusercontent.com/ikaelfess/school21_api_sdk/57bab5d98984aa5a147624de68a5bae734bd1f4a/openapi.yaml
Local ignored copy: `.autopilot/school21-client-20261010/reference/openapi.yaml`.
Wrappers: projects/courses/skills/badges/events/campuses/clusters/coalitions/sales;
XP wrapper `expHistory`, seats `clusterMap`, participant lists `participants` strings.
Participant: login, expValue, level, expToNextLevel, campus{id,shortName}, status.
Points: peerReviewPoints/codeReviewPoints/coins. Logtime is JSON number of hours.
Participant project/course IDs `id`; catalog details `projectId`/`courseId`.
Events require UTC `from` and `to`; dates startDateTime/endDateTime. XP entries
expValue/accrualDateTime. Graph nodes{id,label,items:[id,code,handles,entityType,
entityId]}, edges{id,source,target,sourceHandle,targetHandle}. No coordinates.
Cluster{id,name,capacity,availableCapacity,floor}; seats row,number,login optional.
Sales type PRP/CRP, status NON_ACTIVE/ACTIVE/PLANNED, startDateTime/progressPercentage
optional. Full DTO contracts are in pinned reference. No total/hasNext from API.
