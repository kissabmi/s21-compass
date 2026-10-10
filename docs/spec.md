# S21 Compass · спецификация

Отдельный неофициальный клиент Школы 21 (Сбер): вход своим аккаунтом, чтение данных
в интерактивном терминале и самостоятельном локальном веб-кабинете. Проверенные
s21toolkit/s21cli (CLI GraphQL), rafikov/watchman (web), Amazomic/s21_helper_front
(React с отдельно требуемым proxy/backend) не закрывают оба интерфейса на общем
официальном API. Их исходники не копируются.

## Требования и критерии

- R-LOGIN: официальный Keycloak, password grant `s21-open-api`, form-urlencoded;
  auto refresh, один повтор GET после 401, выход без сохранения пароля.
  Явное редактируемое поле nickname участника: API не предоставляет `/me`,
  auth username не считается доказанным идентификатором participant.
- R-TUI: Textual, маскированный пароль, постоянная навигация/подсказки клавиш,
  таблицы/карточки/детали, обновление и поиск пира. Async, терминал от 80×24.
- R-WEB: адаптивный самостоятельный интерфейс: вход/демо, обзор, проекты, курсы,
  XP/навыки/значки, события, кампусы/кластеры/места, дерево, распродажи, поиск пира.
  Приоритет — прогресс и активные проекты. У основных разделов читаемый UI.
- R-DATA: все группы GET переданного OpenAPI, pagination, status filters, даты
  событий, детали проекта/курса. Независимые ошибки разделов не ломают остальные.
  Обоим UI обязательна матрица `docs/resources.md`, включая loading/empty/error,
  retry, logout, refresh и защиту от запоздалого ответа при переключении.
- R-PROJECT: независимый пакет, CLI `s21-compass tui` и `s21-compass web`, README,
  MIT, воспроизводимая установка, тесты/CI, подготовка для публичного GitHub.
  Закрывается только публичным URL согласованного репозитория и совпавшим remote SHA.

## Сессии и демонстрация

Loopback по умолчанию; отдельный клиент на browser session. Случайный HttpOnly
SameSite=Strict cookie, токены только в RAM. Пароли/токены не попадают в browser
storage, cookie, файл или журналы. CSRF token и Origin/Host checks для mutations;
session expiry/cleanup, CSP same-origin, без CDN и произвольного upstream proxy.

Demo fixtures вымышлены, обозначены на обоих UI, schema-shaped, без сети.
Meaningful tests: auth form/URL, refresh rotation/401/429/сеть, browser isolation и
CSRF, params/pagination, TUI navigation/detail/logout, browser+mobile flow.

OIDC discovery доступен. Успешный live login и реальные защищённые ответы до
локального ввода учётных данных владельцем остаются not tested.

## Источники

- https://platform.21-school.ru/docs
- https://auth.21-school.ru/auth/realms/EduPowerKeycloak/.well-known/openid-configuration
- https://github.com/ikaelfess/school21_api_sdk/blob/main/openapi.yaml (community mirror)

## Публикация

10.10.2026 владелец явно разрешил создать публичный `kissabmi/s21-compass` и
опубликовать проверенные исходники под MIT. Локальная ветка `feature/initial-client`;
публикация после независимого review/QA, без приватных служебных материалов.
