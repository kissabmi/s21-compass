# S21 Compass

Unofficial read-only School21 client: shared async Python API, Textual TUI,
loopback FastAPI dashboard. Never add credentials, private checkpoints or local paths.

- Contracts: `docs/interfaces.md`, `docs/spec.md`.
- API: `https://platform.21-school.ru/services/21-school/api/v1`; no double v1.
- No persisted passwords/tokens. Browser gets opaque HttpOnly session cookie.
- Demo must be explicitly labelled. Mock is not live-login evidence.
- Python >=3.11; `uv sync --extra dev`, `uv run pytest`, `uv run ruff check .`.
- Children write assigned files only, do not delegate, publish or write memory.
- Main owns packaging, CLI, docs, integration. UI has no CDN/build dependencies.
- Feature branch until independent review/QA; external delivery permission is separate.
