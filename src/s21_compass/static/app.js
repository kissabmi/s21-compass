"use strict";

// Session state stays in memory. The server owns the opaque HttpOnly cookie.
const state = { session: null, login: "", section: "overview", settings: {}, authBusy: false };
let pageScope = null;
let dialogScope = null;
let sessionScope = null;
let sessionGeneration = 0;
let dialogOpener = null;
const $ = (id) => document.getElementById(id);
const segment = (value) => encodeURIComponent(String(value));
const participantPath = (login = state.login) => `participants/${segment(login)}`;
const statuses = {
  ASSIGNED: "Назначен", REGISTERED: "Зарегистрирован", IN_PROGRESS: "В работе",
  IN_REVIEWS: "На проверке", ACCEPTED: "Принят", FAILED: "Не принят",
  ACTIVE: "Активен", NON_ACTIVE: "Не проводится", PLANNED: "Запланировано",
  TEMPORARY_BLOCKING: "Временно заблокирован", EXPELLED: "Отчислен", BLOCKED: "Заблокирован",
  FROZEN: "Обучение приостановлено", STUDY_COMPLETED: "Обучение завершено",
};
const types = { INDIVIDUAL: "Индивидуальный", GROUP: "Групповой", EXAM: "Экзамен", EXAM_TEST: "Экзаменационный тест", INTERNSHIP: "Стажировка", ACTIVITY: "Мероприятие", TEST: "Тест" };
const sections = {
  overview: ["Обзор", "Твой учебный маршрут: текущая работа, прогресс и кампус."],
  projects: ["Проекты", "Работа, проверки и результаты. Открой проект, чтобы увидеть условия и участников."],
  courses: ["Курсы", "Учебные направления и результаты участника."],
  progress: ["Прогресс", "История XP, навыки и полученные значки."],
  events: ["События", "План кампуса на выбранные даты. Время показано в часовом поясе браузера."],
  campus: ["Кампус", "Кластеры, рабочие места, пиры и коалиции."],
  graph: ["Дерево обучения", "Группы проектов и курсов, связанные зависимостями платформы."],
  sales: ["Распродажи", "Текущие статусы распродаж баллов проверки."],
  peers: ["Поиск пира", "Найди участника по точному никнейму платформы."],
};

function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null && text !== undefined) node.textContent = String(text);
  return node;
}
function append(parent, ...children) {
  children.filter(Boolean).forEach((child) => parent.append(child));
  return parent;
}
function button(text, action, className = "button secondary") {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}
function number(value, suffix = "") {
  return typeof value === "number" && Number.isFinite(value) ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value)}${suffix}` : "—";
}
function text(value) { return value === null || value === undefined || value === "" ? "—" : String(value); }
function dateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? text(value) : new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function list(payload, key) { return Array.isArray(payload) ? payload : Array.isArray(payload?.[key]) ? payload[key] : []; }
function status(value) {
  const style = ["IN_PROGRESS", "ACCEPTED", "ACTIVE"].includes(value) ? "status-active" : value === "IN_REVIEWS" ? "status-review" : ["FAILED", "BLOCKED", "EXPELLED"].includes(value) ? "status-failed" : ["PLANNED", "FROZEN"].includes(value) ? "status-warn" : "";
  return el("span", `status ${style}`, statuses[value] || text(value));
}
function panel(title, extra = "") {
  const node = el("section", `panel ${extra}`);
  if (title) node.append(el("h2", "", title));
  return node;
}
function facts(entries, className = "detail-facts") {
  const node = el("dl", className);
  entries.forEach(([label, value]) => node.append(append(el("div"), el("dt", "", label), value instanceof Node ? append(el("dd"), value) : el("dd", "", text(value)))));
  return node;
}
function empty(title = "Здесь пока нет данных", description = "Попробуй другой фильтр или обнови раздел.") {
  return append(el("div", "empty-state"), el("h3", "", title), el("p", "", description));
}
function loading() { const node = append(el("div", "load-state"), el("span", "loading-dot"), el("span", "", "Загружаем данные…")); node.setAttribute("role", "status"); return node; }
function inspector(data) {
  const node = el("details", "raw-inspector");
  return append(node, el("summary", "", "Полный ответ API"), el("pre", "", JSON.stringify(data, null, 2)));
}
function createScope() { return new AbortController(); }
function errorMessage(error) {
  if (error.status === 403) return "Нет доступа к этим данным. Проверь права аккаунта или обнови сессию.";
  if (error.status === 429) return "Платформа ограничила частоту запросов. Подожди немного и повтори попытку.";
  if (error.status === 404) return "Данные не найдены. Проверь никнейм или выбранный ресурс.";
  return error.message || "Не удалось загрузить данные. Повтори попытку.";
}
function errorState(error, retry) {
  const node = append(el("div", "error-state"), el("h3", "", "Не удалось получить данные"), el("p", "", errorMessage(error)));
  node.setAttribute("role", "alert");
  if (retry) node.append(button("Повторить", retry));
  return node;
}
async function request(url, { method = "GET", body, signal, auth = true } = {}) {
  const sessionVersion = sessionGeneration;
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30000);
  try {
    const headers = { Accept: "application/json" };
    if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = state.session?.csrf || ""; }
    const response = await fetch(url, { method, body: body === undefined ? undefined : JSON.stringify(body), headers, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    let payload;
    try { payload = await response.json(); }
    catch {
      if (response.ok) throw new Error("Локальный кабинет вернул некорректный ответ. Обнови раздел или повтори подключение.");
      payload = {};
    }
    if (signal?.aborted || sessionVersion !== sessionGeneration) throw new DOMException("Aborted", "AbortError");
    if (!response.ok) {
      const error = new Error(payload.error || `Запрос не выполнен (${response.status}).`);
      error.status = response.status;
      if (response.status === 401 && auth && !signal?.aborted) {
        showLogin("Сессия завершилась. Войди снова.");
        initializeSession(false);
      }
      throw error;
    }
    return payload;
  } catch (error) {
    if (timedOut) throw new Error("Время ожидания истекло. Проверь соединение и повтори попытку.");
    if (error.name === "TypeError") throw new Error("Нет соединения с локальным кабинетом. Проверь, что он запущен, и повтори попытку.");
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
function data(path, params = {}, scope = pageScope) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== "" && value !== undefined && value !== null) query.set(key, String(value)); });
  return request(`/api/data/${path}${query.size ? `?${query}` : ""}`, { signal: scope?.signal });
}
// Each block has its own request generation, in addition to the page abort signal.
function loadBlock(host, fetcher, render, scope = pageScope, { immediate = true } = {}) {
  let generation = 0;
  let pending = null;
  async function run() {
    if (scope?.signal.aborted || !host.isConnected) return;
    pending?.abort();
    const local = createScope(); pending = local;
    const abort = () => local.abort();
    scope?.signal.addEventListener("abort", abort, { once: true });
    const current = ++generation;
    host.replaceChildren(loading());
    host.setAttribute("aria-busy", "true");
    try {
      const payload = await fetcher(local);
      if (scope?.signal.aborted || current !== generation || !host.isConnected) return;
      host.replaceChildren();
      render(payload, host);
    } catch (error) {
      if (scope?.signal.aborted || current !== generation || !host.isConnected || error.name === "AbortError" || error.status === 401) return;
      host.replaceChildren(errorState(error, run));
    } finally {
      scope?.signal.removeEventListener("abort", abort);
      if (current === generation) host.removeAttribute("aria-busy");
    }
  }
  if (immediate) run();
  return run;
}
function field(label, input) {
  const node = el("div", "filter-field");
  const caption = el("label", "", label);
  caption.htmlFor = input.id;
  return append(node, caption, input);
}
function select(id, options, value = "") {
  const node = el("select"); node.id = id;
  options.forEach(([key, label]) => { const option = el("option", "", label); option.value = key; node.append(option); });
  node.value = value;
  return node;
}
function input(id, type = "text", value = "") {
  const node = el("input"); node.id = id; node.type = type; node.value = value; return node;
}
function statusOptions(course = false) {
  return [["", "Все статусы"], ...["ASSIGNED", "REGISTERED", "IN_PROGRESS", ...(course ? [] : ["IN_REVIEWS"]), "ACCEPTED", "FAILED"].map((key) => [key, statuses[key]])];
}
function pageOptions() { return [["10", "10 записей"], ["25", "25 записей"], ["50", "50 записей"]]; }
function pager(host, count, paging, run) {
  const controls = el("div", "pager-controls");
  const previous = button("Назад", () => { paging.offset = Math.max(0, paging.offset - paging.limit); run(); });
  const next = button("Далее", () => { paging.offset += paging.limit; run(); });
  previous.disabled = paging.offset === 0; next.disabled = count < paging.limit;
  controls.append(previous, next);
  const info = count ? `Записи ${paging.offset + 1}–${paging.offset + count}` : `Нет записей с позиции ${paging.offset + 1}`;
  host.append(append(el("div", "pager"), el("span", "pager-info", `${info}. Всего API не сообщает.`), controls));
}
function table(headers, rows) {
  const node = el("table");
  const heading = el("tr"); headers.forEach((name) => { const cell = el("th", "", name); cell.scope = "col"; heading.append(cell); });
  const body = el("tbody");
  rows.forEach((values) => { const row = el("tr"); values.forEach((value) => row.append(value instanceof Node ? append(el("td"), value) : el("td", "", text(value)))); body.append(row); });
  const scroll = append(el("div", "table-scroll"), append(node, append(el("thead"), heading), body));
  scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", "Таблица данных, прокрутка по горизонтали");
  return scroll;
}
function peerButton(login, scope = pageScope) {
  return button(login, () => { if (!scope?.signal.aborted) openPeer(login); });
}
function peers(host, payload, scope) {
  const entries = list(payload, "participants");
  if (!entries.length) { host.append(empty("Участников на этой странице нет")); return; }
  const node = el("ul", "peer-list");
  entries.forEach((entry) => { const login = typeof entry === "string" ? entry : entry.login; if (login) node.append(append(el("li"), peerButton(login, scope))); });
  host.append(node);
}
function peerBrowser(host, path, scope, { project = false } = {}) {
  const paging = { offset: 0, limit: 25 };
  const filters = el("form", "filters");
  const limit = select("peer-page-size", pageOptions(), "25");
  filters.append(field("На странице", limit));
  let statusFilter, campusFilter;
  if (project) {
    statusFilter = select("project-peer-status", statusOptions());
    campusFilter = input("project-peer-campus"); campusFilter.placeholder = "UUID кампуса (необязательно)";
    campusFilter.pattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
    filters.append(field("Статус проекта", statusFilter), field("Кампус", campusFilter));
  }
  const apply = el("button", "button secondary", "Применить"); apply.type = "submit"; filters.append(apply);
  const result = el("div"); host.append(filters, result);
  let params = {};
  const run = loadBlock(result, (local) => data(path, { ...params, ...paging }, local), (payload, target) => { peers(target, payload, scope); pager(target, list(payload, "participants").length, paging, run); }, scope);
  filters.addEventListener("submit", (event) => {
    event.preventDefault(); paging.offset = 0; paging.limit = Number(limit.value);
    params = project ? { status: statusFilter.value, campusId: campusFilter.value.trim() } : {};
    run();
  });
}

function beginSessionOperation() {
  sessionScope?.abort(); sessionScope = createScope();
  return ++sessionGeneration;
}
function showLogin(message = "") {
  beginSessionOperation();
  pageScope?.abort(); dialogScope?.abort();
  dialogOpener = null;
  if ($("detail-dialog").open) $("detail-dialog").close();
  state.session = null; state.login = ""; state.settings = {}; state.authBusy = false;
  $("content").replaceChildren(); $("detail-content").replaceChildren();
  $("workspace").hidden = true; $("login-screen").hidden = false;
  $("auth-message").textContent = message;
  $("auth-message").classList.toggle("error", Boolean(message));
  $("password").value = "";
  authControls(); $("username").focus({ preventScroll: true });
}
function authControls() {
  const disabled = state.authBusy || !state.session?.csrf;
  $("login-submit").disabled = disabled; $("demo-button").disabled = disabled;
  $("login-submit").textContent = state.authBusy ? "Подключаемся…" : "Войти";
  ["username", "password", "login-nickname"].forEach((id) => { $(id).disabled = state.authBusy; });
}
async function initializeSession(restore = true) {
  const current = beginSessionOperation(), scope = sessionScope;
  try {
    const session = await request("/api/session", { auth: false, signal: scope.signal });
    if (current !== sessionGeneration) return;
    state.session = session;
    if (restore && session.authenticated) enterWorkspace(session);
    else if (!$("auth-message").classList.contains("error")) $("auth-message").textContent = "";
  } catch (error) {
    if (current !== sessionGeneration || error.name === "AbortError") return;
    $("auth-message").replaceChildren(el("span", "", errorMessage(error)), button("Повторить подключение", () => initializeSession(restore), "text-button"));
    $("auth-message").classList.add("error");
  } finally { if (current === sessionGeneration) authControls(); }
}
function enterWorkspace(session) {
  state.session = session; state.login = session.login || ""; state.settings = {}; state.authBusy = false;
  $("login-screen").hidden = true; $("workspace").hidden = false;
  $("demo-badge").hidden = !session.demo;
  $("connection-label").textContent = session.demo ? "Демо без сети" : "Локальная сессия";
  $("peer-context").value = state.login;
  navigate("overview", true);
}
async function authenticate(demo, body) {
  if (state.authBusy) return;
  const current = beginSessionOperation(), scope = sessionScope;
  state.authBusy = true; authControls();
  $("auth-message").classList.remove("error"); $("auth-message").textContent = demo ? "Открываем вымышленный кампус…" : "Проверяем аккаунт…";
  try {
    const session = await request(demo ? "/api/demo" : "/api/login", { method: "POST", body: body || {}, auth: false, signal: scope.signal });
    if (current === sessionGeneration) enterWorkspace(session);
  } catch (error) {
    if (current !== sessionGeneration || error.name === "AbortError") return;
    $("auth-message").textContent = error.status === 401 ? "Не удалось войти. Проверь имя пользователя и пароль." : (error.message || "Не удалось подключиться к серверу авторизации School21."); $("auth-message").classList.add("error");
  } finally {
    if (body) body.password = "";
    if (current === sessionGeneration) { state.authBusy = false; authControls(); }
  }
}
function openPeer(login) {
  if (state.authBusy) return;
  state.login = login; state.settings = {}; $("peer-context").value = login;
  navigate("overview", true);
}
function navigate(section, focus = false) {
  if (state.authBusy || !state.session?.authenticated || !sections[section]) return;
  pageScope?.abort(); dialogScope?.abort();
  dialogOpener = null;
  if ($("detail-dialog").open) $("detail-dialog").close();
  pageScope = createScope(); state.section = section;
  $("navigation").querySelectorAll("button").forEach((node) => { if (node.dataset.section === section) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current"); });
  $("page-title").textContent = sections[section][0]; $("page-description").textContent = sections[section][1];
  $("page-context").textContent = ["overview", "projects", "courses", "progress"].includes(section) ? `Профиль: ${state.login || "укажи никнейм выше"}` : "Учебное пространство";
  $("notice").hidden = true; $("content").replaceChildren();
  if (["overview", "projects", "courses", "progress"].includes(section) && !state.login) {
    $("content").append(empty("Укажи никнейм участника", "Введи точный никнейм в поле «Профиль участника» выше."));
  } else {
    ({ overview: renderOverview, projects: () => renderStudies("projects"), courses: () => renderStudies("courses"), progress: renderProgress, events: renderEvents, campus: renderCampus, graph: renderGraph, sales: renderSales, peers: renderPeerLookup })[section]();
  }
  if (focus) $("main").focus({ preventScroll: true });
}

function projectRows(host, payload, kind, login, scope) {
  const entries = list(payload, kind);
  if (!entries.length) { host.append(empty(kind === "courses" ? "Курсов на этой странице нет" : "Проектов на этой странице нет")); return; }
  const rows = el("div", "project-list");
  entries.forEach((item) => {
    const title = append(el("div", "project-title"), button(item.title || `ID ${item.id}`, () => { if (!scope.signal.aborted) openStudy(kind, item.id ?? item.projectId ?? item.courseId, login); }, "text-button"), el("p", "project-subtitle", [types[item.type] || item.type, item.completionDateTime ? `Завершён ${dateTime(item.completionDateTime)}` : null].filter(Boolean).join(" · ")));
    rows.append(append(el("div", "project-row"), title, status(item.status), el("span", "project-score", number(item.finalPercentage, "%"))));
  });
  host.append(rows);
}
function renderOverview() {
  const scope = pageScope, login = state.login;
  const layout = el("div", "overview-layout"), work = el("div", "stack"), rail = el("div", "stack overview-rail");
  const profile = panel(null, "profile-banner"), projects = panel("Активная работа"), details = panel("Профиль и кампус"), progress = panel("Учебный прогресс"), points = panel("Баллы и время"), feedback = panel("Отзывы о проверках");
  work.append(profile, projects, details); rail.append(progress, points, feedback); layout.append(work, rail); $("content").append(layout);
  const profileHost = el("div"), detailsHost = el("div"), progressHost = el("div"), pointsHost = el("div"), feedbackHost = el("div");
  profile.append(profileHost); details.append(detailsHost); progress.append(progressHost); points.append(pointsHost); feedback.append(feedbackHost);
  const dependentHosts = [detailsHost, progressHost, pointsHost, feedbackHost];
  dependentHosts.forEach((host) => host.append(loading()));
  const overviewRun = loadBlock(profileHost, async (local) => {
    dependentHosts.forEach((host) => host.replaceChildren(loading()));
    try { return await request(`/api/overview/${segment(login)}`, { signal: local.signal }); }
    catch (error) {
      if (!local.signal.aborted && error.name !== "AbortError" && error.status !== 401) dependentHosts.forEach((host) => host.replaceChildren(errorState(error, overviewRun)));
      throw error;
    }
  }, (payload, host) => {
    const participant = payload.participant || {}, errors = payload.errors || {};
    if (errors.participant) host.append(errorState(new Error(errors.participant), overviewRun));
    else host.append(el("h2", "profile-name", participant.login || login), append(el("div", "profile-meta"), el("span", "", text(participant.campus?.shortName)), el("span", "", text(participant.parallelName)), status(participant.status)));
    progressHost.replaceChildren(); pointsHost.replaceChildren(); feedbackHost.replaceChildren(); detailsHost.replaceChildren();
    if (errors.participant) progressHost.append(errorState(new Error(errors.participant), overviewRun));
    else progressHost.append(append(el("div", "level-number"), document.createTextNode(number(participant.level)), el("small", "", "уровень")), facts([["Накоплено XP", number(participant.expValue)], ["До следующего уровня", number(participant.expToNextLevel)]], "stat-list"), button("История и навыки", () => navigate("progress", true), "text-button"));
    if (errors.points) pointsHost.append(errorState(new Error(errors.points), overviewRun));
    else pointsHost.append(facts([["Peer Review Points", number(payload.points?.peerReviewPoints)], ["Code Review Points", number(payload.points?.codeReviewPoints)], ["Монеты", number(payload.points?.coins)]], "stat-list"));
    const logHost = el("div"); pointsHost.append(logHost);
    if (errors.logtime) logHost.append(errorState(new Error(errors.logtime), overviewRun));
    else logHost.append(facts([["Среднее за неделю", number(payload.logtime, " ч")]], "stat-list"));
    const dateForm = el("form"); const logDate = input("logtime-date", "date");
    dateForm.append(field("Неделя, содержащая дату", logDate)); const submit = el("button", "text-button", "Посмотреть время"); submit.type = "submit"; dateForm.append(submit); pointsHost.append(dateForm);
    const logRun = loadBlock(logHost, (local) => data(`${participantPath(login)}/logtime`, { date: logDate.value }, local), (value, target) => target.append(facts([["Среднее за неделю", number(value, " ч")]], "stat-list")), scope, { immediate: false });
    dateForm.addEventListener("submit", (event) => { event.preventDefault(); logRun(); });
    if (errors.feedback) feedbackHost.append(errorState(new Error(errors.feedback), overviewRun));
    else feedbackHost.append(facts([["Пунктуальность", number(payload.feedback?.averageVerifierPunctuality)], ["Интерес", number(payload.feedback?.averageVerifierInterest)], ["Тщательность", number(payload.feedback?.averageVerifierThoroughness)], ["Дружелюбие", number(payload.feedback?.averageVerifierFriendliness)]], "stat-list"));
    detailsHost.append(facts([["Поток", text(participant.className)], ["Программа", text(participant.parallelName)], ["Кампус", text(participant.campus?.shortName)]], "stat-list"));
    if (errors.coalition) detailsHost.append(errorState(new Error(errors.coalition), overviewRun));
    else if (payload.coalition) {
      detailsHost.append(facts([["Коалиция", payload.coalition.name], ["Место в коалиции", number(payload.coalition.rank)]], "stat-list"));
      if (payload.coalition.coalitionId !== undefined) detailsHost.append(button("Участники коалиции", () => openPeerList("Участники коалиции", `coalitions/${segment(payload.coalition.coalitionId)}/participants`), "text-button"));
    }
    if (errors.workstation) detailsHost.append(errorState(new Error(errors.workstation), overviewRun));
    else if (payload.workstation) {
      const seat = payload.workstation;
      detailsHost.append(facts([["Кластер", seat.clusterName], ["Рабочее место", `${text(seat.row)} / ${text(seat.number)}`]], "stat-list"));
      if (seat.clusterId !== undefined) detailsHost.append(button("Места в кластере", () => openSeats(seat.clusterId, seat.clusterName), "text-button"));
    }
    detailsHost.append(inspector(payload));
    const otherErrors = Object.entries(errors).filter(([key]) => ["skills", "badges"].includes(key));
    if (otherErrors.length) detailsHost.append(el("p", "muted", `Часть данных прогресса недоступна: ${otherErrors.map(([key, value]) => `${key}: ${value}`).join("; ")}`));
  }, scope);
  const projectsHost = el("div"); projects.append(projectsHost);
  loadBlock(projectsHost, (local) => data(`${participantPath(login)}/projects`, { status: "IN_PROGRESS", limit: 10, offset: 0 }, local), (payload, host) => { projectRows(host, payload, "projects", login, scope); host.append(button("Все проекты и проверки", () => navigate("projects", true), "text-button")); }, scope);
}
function renderStudies(kind) {
  const scope = pageScope, login = state.login;
  const settings = state.settings[kind] ||= { offset: 0, limit: 10, status: "" };
  const form = el("form", "filters");
  const filter = select(`${kind}-status`, statusOptions(kind === "courses"), settings.status), limit = select(`${kind}-limit`, pageOptions(), String(settings.limit));
  const apply = el("button", "button primary", "Применить"); apply.type = "submit";
  form.append(field("Статус", filter), field("На странице", limit), apply);
  const result = panel(kind === "courses" ? "Учебные курсы" : "Проекты участника"), host = el("div"); result.append(host); $("content").append(form, result);
  const run = loadBlock(host, (local) => data(`${participantPath(login)}/${kind}`, settings, local), (payload, target) => { projectRows(target, payload, kind, login, scope); pager(target, list(payload, kind).length, settings, run); }, scope);
  form.addEventListener("submit", (event) => { event.preventDefault(); settings.status = filter.value; settings.limit = Number(limit.value); settings.offset = 0; run(); });
}
function renderProgress() {
  const scope = pageScope, login = state.login, grid = el("div", "section-grid"); $("content").append(grid);
  const skills = panel("Навыки"), badges = panel("Значки"), history = panel("История XP", "span-full"); grid.append(skills, badges, history);
  const skillsHost = el("div"), badgesHost = el("div"), historyHost = el("div"); skills.append(skillsHost); badges.append(badgesHost);
  loadBlock(skillsHost, (local) => data(`${participantPath(login)}/skills`, {}, local), (payload, host) => {
    const entries = list(payload, "skills"); if (!entries.length) return host.append(empty("Навыки пока не начислены"));
    const node = el("ul", "skill-list"); entries.forEach((item) => node.append(append(el("li"), el("span", "", item.name), el("strong", "", `${number(item.points)} баллов`)))); host.append(node);
  }, scope);
  loadBlock(badgesHost, (local) => data(`${participantPath(login)}/badges`, {}, local), (payload, host) => {
    const entries = list(payload, "badges"); if (!entries.length) return host.append(empty("Значков пока нет"));
    const node = el("ul", "badge-list"); entries.forEach((item) => { const time = el("time", "", dateTime(item.receiptDateTime)); if (item.receiptDateTime) time.dateTime = item.receiptDateTime; node.append(append(el("li"), el("span", "badge-symbol", "◇"), append(el("div"), el("p", "", item.name), time))); }); host.append(node, inspector(payload));
  }, scope);
  const paging = state.settings.experience ||= { limit: 25, offset: 0 };
  const filter = select("xp-limit", pageOptions(), String(paging.limit)); const form = el("form", "filters"); const apply = el("button", "button secondary", "Применить"); apply.type = "submit"; form.append(field("На странице", filter), apply); history.append(form, historyHost);
  const run = loadBlock(historyHost, (local) => data(`${participantPath(login)}/experience-history`, paging, local), (payload, host) => {
    const entries = list(payload, "expHistory");
    if (!entries.length) host.append(empty("Начислений XP на этой странице нет"));
    else { const node = el("ul", "timeline"); entries.forEach((item) => { const time = el("time", "", dateTime(item.accrualDateTime)); if (item.accrualDateTime) time.dateTime = item.accrualDateTime; node.append(append(el("li"), time, el("strong", "", `${typeof item.expValue === "number" && item.expValue > 0 ? "+" : ""}${number(item.expValue)} XP`))); }); host.append(node); }
    pager(host, entries.length, paging, run);
  }, scope);
  form.addEventListener("submit", (event) => { event.preventDefault(); paging.limit = Number(filter.value); paging.offset = 0; run(); });
}

function showDialog(title) {
  dialogScope?.abort(); dialogScope = createScope();
  if (!$("detail-dialog").open) dialogOpener = document.activeElement;
  $("detail-title").textContent = title; $("detail-content").replaceChildren();
  if (!$("detail-dialog").open) { $("detail-dialog").showModal(); document.body.classList.add("modal-open"); }
  $("close-dialog").focus();
  return { scope: dialogScope, host: $("detail-content") };
}
function openPeerList(title, path, project = false) {
  const { scope, host } = showDialog(title);
  peerBrowser(host, path, scope, { project });
}
function renderConditions(host, groups) {
  if (!Array.isArray(groups) || !groups.length) return;
  const section = append(el("section", "detail-section"), el("h3", "", "Условия старта"));
  const entries = el("ul", "conditions");
  groups.forEach((group) => {
    const rules = (group.rulesInGroup || []).map((rule) => {
      const value = rule.value || {};
      const operands = (value.value || []).map((item) => item.value ?? item.key).join(", ");
      return [rule.logicalOperator, value.fieldName, value.subFieldValue, value.operator, operands].filter(Boolean).join(" ");
    });
    entries.append(el("li", "", [group.logicalOperator, ...rules].filter(Boolean).join(" ") || "Условие не описано; смотри полный ответ API."));
  });
  section.append(entries); host.append(section);
}
function openStudy(kind, id, login = null) {
  if (id === undefined || id === null) return;
  const { scope, host } = showDialog(kind === "courses" ? "Курс" : "Проект");
  const catalogHost = el("div"), participantHost = el("div"); host.append(catalogHost);
  loadBlock(catalogHost, (local) => data(`${kind}/${segment(id)}`, {}, local), (catalog, target) => {
    $("detail-title").textContent = catalog.title || `${kind === "courses" ? "Курс" : "Проект"} ${id}`;
    target.append(el("p", "detail-description", text(catalog.description)), facts([["ID", text(catalog.projectId ?? catalog.courseId ?? id)], ["Тип", types[catalog.type] || text(catalog.type)], ["Длительность", number(catalog.durationHours, " ч")], ["XP за выполнение", number(catalog.xp)]]));
    if (catalog.courseId !== undefined && catalog.courseId !== null && kind === "projects") target.append(button("Открыть связанный курс", () => openStudy("courses", catalog.courseId), "text-button"));
    renderConditions(target, catalog.startConditions); target.append(inspector(catalog));
  }, scope);
  if (login) {
    const section = append(el("section", "detail-section"), el("h3", "", `Результат участника ${login}`), participantHost); host.append(section);
    loadBlock(participantHost, (local) => data(`${participantPath(login)}/${kind}/${segment(id)}`, {}, local), (item, target) => {
      target.append(facts([["Статус", status(item.status)], ["Результат", number(item.finalPercentage, "%")], ["Завершён", dateTime(item.completionDateTime)]]));
      if (Array.isArray(item.teamMembers) && item.teamMembers.length) {
        target.append(el("h3", "", "Команда")); const team = el("ul", "peer-list");
        item.teamMembers.filter(Boolean).forEach((member) => team.append(append(el("li"), peerButton(member.login, scope), member.isTeamlead ? el("span", "status", "Лидер") : null))); target.append(team);
      }
      target.append(inspector(item));
    }, scope);
  }
  if (kind === "projects") {
    const section = append(el("section", "detail-section"), el("h3", "", "Участники проекта")); host.append(section);
    peerBrowser(section, `projects/${segment(id)}/participants`, scope, { project: true });
  }
}

function isoDay(offset) { const date = new Date(); date.setDate(date.getDate() + offset); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function renderEvents() {
  const scope = pageScope;
  const settings = state.settings.events ||= { fromDate: isoDay(0), toDate: isoDay(14), type: "", limit: 25, offset: 0 };
  const form = el("form", "filters");
  const from = input("events-from", "date", settings.fromDate), to = input("events-to", "date", settings.toDate);
  from.required = true; to.required = true;
  const type = select("events-type", [["", "Все события"], ["ACTIVITY", "Мероприятия"], ["EXAM", "Экзамены"], ["TEST", "Тесты"]], settings.type);
  const limit = select("events-limit", pageOptions(), String(settings.limit)), apply = el("button", "button primary", "Показать"); apply.type = "submit";
  form.append(field("С даты", from), field("По дату включительно", to), field("Тип", type), field("На странице", limit), apply);
  const result = panel("Расписание"), host = el("div"); result.append(host); $("content").append(form, result);
  const run = loadBlock(host, (local) => {
    const start = new Date(`${settings.fromDate}T00:00:00`), end = new Date(`${settings.toDate}T23:59:59.999`);
    return data("events", { from: start.toISOString(), to: end.toISOString(), type: settings.type, limit: settings.limit, offset: settings.offset }, local);
  }, (payload, target) => {
    const entries = list(payload, "events");
    if (!entries.length) target.append(empty("На эти даты событий нет", "Выбери другой период или тип события."));
    else {
      const node = el("div", "event-list");
      entries.forEach((item) => {
        const date = new Date(item.startDateTime), valid = !Number.isNaN(date.getTime());
        const stamp = append(el("div", "event-date"), el("strong", "", valid ? date.getDate() : "—"), el("span", "", valid ? new Intl.DateTimeFormat("ru-RU", { month: "short" }).format(date) : ""));
        const body = append(el("div", "event-body"), button(item.name || `Событие ${item.id}`, () => openEvent(item), "text-button"), el("p", "", dateTime(item.startDateTime)), el("p", "", `${text(item.location)} · Записались ${number(item.registerCount)} / ${number(item.capacity)}`), status(types[item.type] || item.type));
        node.append(append(el("article", "event-row"), stamp, body));
      }); target.append(node);
    }
    pager(target, entries.length, settings, run);
  }, scope);
  form.addEventListener("submit", (event) => {
    event.preventDefault(); to.setCustomValidity(from.value > to.value ? "Дата окончания должна быть не раньше даты начала." : "");
    if (!form.reportValidity()) return;
    Object.assign(settings, { fromDate: from.value, toDate: to.value, type: type.value, limit: Number(limit.value), offset: 0 }); run();
  });
  to.addEventListener("input", () => to.setCustomValidity("")); from.addEventListener("input", () => to.setCustomValidity(""));
}
function openEvent(item) {
  const { host, scope } = showDialog(item.name || "Событие");
  host.append(el("p", "detail-description", text(item.description)), facts([["Тип", types[item.type] || text(item.type)], ["Начало", dateTime(item.startDateTime)], ["Окончание", dateTime(item.endDateTime)], ["Место", text(item.location)], ["Записались", number(item.registerCount)], ["Вместимость", number(item.capacity)]]));
  if (item.organizers?.filter(Boolean).length) { host.append(el("h3", "", "Организаторы")); const node = el("ul", "peer-list"); item.organizers.filter(Boolean).forEach((login) => node.append(append(el("li"), peerButton(login, scope)))); host.append(node); }
  host.append(inspector(item));
}

function renderCampus() {
  const scope = pageScope;
  const campuses = panel("Выбери кампус"), host = el("div"); campuses.append(host);
  const detail = el("div", "stack"); $("content").append(append(el("div", "stack"), campuses, detail));
  let selectionScope = null;
  scope.signal.addEventListener("abort", () => selectionScope?.abort(), { once: true });
  loadBlock(host, (local) => data("campuses", {}, local), (payload, target) => {
    const entries = list(payload, "campuses");
    if (!entries.length) return target.append(empty("Кампусы не найдены"));
    const picker = select("campus-choice", [["", "Выбери кампус"], ...entries.map((item) => [item.id, item.shortName || item.fullName])], state.settings.campusId || "");
    target.append(field("Кампус", picker));
    function choose() {
      if (scope.signal.aborted) return;
      selectionScope?.abort(); selectionScope = createScope(); detail.replaceChildren();
      dialogOpener = null;
      if ($("detail-dialog").open) $("detail-dialog").close();
      state.settings.campusId = picker.value;
      if (!picker.value) return;
      const campus = entries.find((item) => item.id === picker.value), local = selectionScope;
      const info = panel(campus?.fullName || campus?.shortName || "Кампус");
      info.append(button("Пиры кампуса", () => openPeerList("Пиры кампуса", `campuses/${segment(picker.value)}/participants`)));
      const clusters = panel("Кластеры"), clusterHost = el("div"), coalitions = panel("Коалиции"), coalitionHost = el("div");
      clusters.append(clusterHost); coalitions.append(coalitionHost); detail.append(info, clusters, coalitions);
      loadBlock(clusterHost, (pending) => data(`campuses/${segment(picker.value)}/clusters`, {}, pending), (response, result) => {
        const items = list(response, "clusters"); if (!items.length) return result.append(empty("Кластеры не найдены"));
        const rows = el("div", "cluster-list"); items.forEach((item) => rows.append(append(el("article", "choice-row"), append(el("div"), el("h3", "", item.name), el("p", "", `Этаж ${number(item.floor)} · Свободно ${number(item.availableCapacity)} из ${number(item.capacity)}`)), button("Места", () => openSeats(item.id, item.name))))); result.append(rows);
      }, local);
      const paging = { offset: 0, limit: 25 };
      const run = loadBlock(coalitionHost, (pending) => data(`campuses/${segment(picker.value)}/coalitions`, paging, pending), (response, result) => {
        const items = list(response, "coalitions");
        if (!items.length) result.append(empty("Коалиций на этой странице нет"));
        else { const rows = el("div", "coalition-list"); items.forEach((item) => rows.append(append(el("article", "choice-row"), append(el("div"), el("h3", "", item.name)), button("Участники", () => openPeerList(item.name || "Коалиция", `coalitions/${segment(item.coalitionId)}/participants`))))); result.append(rows); }
        pager(result, items.length, paging, run);
      }, local);
    }
    picker.addEventListener("change", choose); if (picker.value) choose();
  }, scope);
}

function openSeats(id, name) {
  const { host, scope } = showDialog(`Рабочие места · ${name || id}`);
  const paging = { offset: 0, limit: 25 }, params = { occupied: "" };
  const filter = select("seat-occupation", [["", "Все места"], ["false", "Только свободные"], ["true", "Только занятые"]]);
  const limit = select("seat-limit", pageOptions(), "25"), form = el("form", "filters"), apply = el("button", "button secondary", "Применить"); apply.type = "submit";
  form.append(field("Занятость", filter), field("На странице", limit), apply);
  const result = el("div"); host.append(form, result);
  const run = loadBlock(result, (local) => data(`clusters/${segment(id)}/map`, { ...paging, ...params }, local), (payload, target) => {
    const entries = list(payload, "clusterMap");
    if (!entries.length) target.append(empty("Мест на этой странице нет"));
    else {
      const grid = el("div", "seat-grid"); entries.forEach((item) => {
        const node = item.login ? button("", () => openPeer(item.login), "seat occupied") : el("div", "seat");
        node.append(el("strong", "", `${text(item.row)} / ${text(item.number)}`), el("span", "", item.login || "Свободно")); grid.append(node);
      }); target.append(grid);
    }
    pager(target, entries.length, paging, run);
  }, scope);
  form.addEventListener("submit", (event) => { event.preventDefault(); params.occupied = filter.value; paging.limit = Number(limit.value); paging.offset = 0; run(); });
}

function renderGraph() {
  const scope = pageScope, form = el("form", "filters"), search = input("graph-search", "search", state.settings.graphSearch || "");
  search.placeholder = "Группа, код проекта или курса";
  form.append(field("Поиск по дереву", search));
  const root = panel("Маршрут обучения"), host = el("div"); root.append(host); $("content").append(form, root);
  form.addEventListener("submit", (event) => event.preventDefault());
  loadBlock(host, (local) => data("graph", {}, local), (payload, target) => {
    const nodes = list(payload, "nodes"), edges = list(payload, "edges"), result = el("div"); target.append(result);
    const byId = new Map(nodes.map((node) => [String(node.id), node]));
    const byHandle = new Map();
    nodes.forEach((node) => (node.items || []).forEach((item) => (item.handles || []).forEach((handle) => byHandle.set(handle, item))));
    function endpoint(nodeId, handle) {
      const item = byHandle.get(handle);
      if (item) return button(item.code || `ID ${item.entityId}`, () => openStudy(item.entityType === "COURSE" ? "courses" : "projects", item.entityId), "text-button");
      return el("span", "", byId.get(String(nodeId))?.label || text(nodeId));
    }
    function draw() {
      if (scope.signal.aborted) return;
      state.settings.graphSearch = search.value;
      const query = search.value.trim().toLocaleLowerCase("ru"); result.replaceChildren();
      const matched = nodes.filter((node) => !query || `${node.label} ${(node.items || []).map((item) => `${item.code} ${item.entityId}`).join(" ")}`.toLocaleLowerCase("ru").includes(query));
      if (!matched.length) { result.append(empty(nodes.length ? "Ничего не найдено" : "Дерево пока пустое", nodes.length ? "Попробуй другое название или код." : "На платформе пока нет узлов дерева.")); return; }
      result.append(el("p", "graph-count", `Групп: ${matched.length} из ${nodes.length}. Связей в дереве: ${edges.length}.`));
      const groups = el("div", "graph-groups");
      matched.forEach((node) => {
        const group = append(el("section", "graph-group"), el("h3", "", node.label || node.id)), items = el("div", "graph-items");
        (node.items || []).forEach((item) => {
          const action = button("", () => openStudy(item.entityType === "COURSE" ? "courses" : "projects", item.entityId));
          action.append(el("span", "", item.code || item.id), el("small", "", item.entityType === "COURSE" ? "Курс" : "Проект")); items.append(action);
        }); group.append(items);
        const incoming = edges.filter((edge) => String(edge.target) === String(node.id)), outgoing = edges.filter((edge) => String(edge.source) === String(node.id));
        if (incoming.length || outgoing.length) {
          const dependencies = el("ul", "dependency-list");
          incoming.forEach((edge) => dependencies.append(append(el("li"), document.createTextNode("Предшествует: "), endpoint(edge.source, edge.sourceHandle), document.createTextNode(" → "), endpoint(edge.target, edge.targetHandle))));
          outgoing.forEach((edge) => dependencies.append(append(el("li"), document.createTextNode("Следующий шаг: "), endpoint(edge.source, edge.sourceHandle), document.createTextNode(" → "), endpoint(edge.target, edge.targetHandle))));
          group.append(dependencies);
        } else group.append(el("p", "graph-count", "У этой группы нет связей в ответе платформы."));
        groups.append(group);
      }); result.append(groups);
    }
    search.addEventListener("input", draw); draw(); target.append(inspector(payload));
  }, scope);
}

function renderSales() {
  const root = panel("Баллы проверки"), host = el("div"); root.append(host); $("content").append(root);
  loadBlock(host, (local) => data("sales", {}, local), (payload, target) => {
    const entries = list(payload, "sales");
    if (!entries.length) return target.append(empty("Распродаж пока нет", "Обнови раздел позднее, чтобы проверить статус."));
    entries.forEach((item) => {
      const name = item.type === "PRP" ? "Peer Review Points" : item.type === "CRP" ? "Code Review Points" : text(item.type);
      target.append(append(el("article", "sale-row"), append(el("div"), el("h3", "", name), status(item.status), el("p", "", `Начало: ${dateTime(item.startDateTime)}`)), facts([["Прогресс распродажи", number(item.progressPercentage, "%")]], "stat-list")));
    }); target.append(inspector(payload));
  });
}

function renderPeerLookup() {
  const root = panel("Точный никнейм"), form = el("form", "lookup-form"), nickname = input("peer-search");
  nickname.required = true; nickname.maxLength = 128; nickname.autocomplete = "off"; nickname.placeholder = "Никнейм участника";
  const submit = el("button", "button primary", "Найти пира"); submit.type = "submit";
  form.append(field("Никнейм на платформе", nickname), submit); root.append(form, el("p", "lookup-help", "Поиск открывает профиль участника. Email и имя пользователя для авторизации здесь не используются.")); $("content").append(root);
  form.addEventListener("submit", (event) => { event.preventDefault(); if (nickname.value.trim()) openPeer(nickname.value.trim()); });
}

$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.authBusy) return;
  const body = { username: $("username").value, password: $("password").value, login: $("login-nickname").value.trim() };
  $("password").value = "";
  authenticate(false, body);
});
$("demo-button").addEventListener("click", () => authenticate(true));
$("peer-context-form").addEventListener("submit", (event) => { event.preventDefault(); const login = $("peer-context").value.trim(); if (login) openPeer(login); });
$("navigation").addEventListener("click", (event) => { const target = event.target.closest("button[data-section]"); if (target) navigate(target.dataset.section, true); });
$("refresh-button").addEventListener("click", () => navigate(state.section));
$("close-dialog").addEventListener("click", () => $("detail-dialog").close());
$("detail-dialog").addEventListener("close", () => {
  if ($("detail-dialog").open) return;
  dialogScope?.abort(); $("detail-content").replaceChildren(); document.body.classList.remove("modal-open");
  if (dialogOpener?.isConnected && !$("workspace").hidden && !state.authBusy) dialogOpener.focus({ preventScroll: true });
  dialogOpener = null;
});
$("detail-dialog").addEventListener("cancel", () => dialogScope?.abort());
$("logout-button").addEventListener("click", async () => {
  const control = $("logout-button"); if (control.disabled) return;
  const current = beginSessionOperation(), scope = sessionScope;
  state.authBusy = true; pageScope?.abort(); dialogScope?.abort(); dialogOpener = null;
  if ($("detail-dialog").open) $("detail-dialog").close();
  $("workspace").inert = true;
  control.disabled = true; control.textContent = "Выходим…";
  try {
    const session = await request("/api/logout", { method: "POST", body: {}, auth: false, signal: scope.signal });
    if (current !== sessionGeneration) return;
    showLogin(); state.session = session; authControls();
  } catch (error) {
    if (current !== sessionGeneration || error.name === "AbortError") return;
    if (error.status === 401) { showLogin("Сессия завершилась. Войди снова."); initializeSession(false); return; }
    state.authBusy = false; navigate(state.section);
    $("notice").textContent = errorMessage(error); $("notice").hidden = false;
  } finally { $("workspace").inert = false; control.disabled = false; control.textContent = "Выйти"; }
});
initializeSession();
