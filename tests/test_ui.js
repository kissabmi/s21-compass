"use strict";

// Local DOM/fetch fixtures: regression evidence, not browser or live-account QA.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag, root = false) {
    this.tagName = tag; this.root = root; this.children = []; this.parentNode = null;
    this.attributes = new Map(); this.listeners = new Map(); this._text = ""; this._value = "";
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }
  get isConnected() { return this.root || Boolean(this.parentNode?.isConnected); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get value() { return this._value; }
  set value(value) {
    const string = String(value);
    this._value = this.tagName === "select" && !this.children.some((child) => child.value === string) ? "" : string;
  }
  append(...nodes) { nodes.forEach((node) => { node.parentNode = this; this.children.push(node); }); }
  replaceChildren(...nodes) { this.children.forEach((node) => { node.parentNode = null; }); this.children = []; this._text = ""; this.append(...nodes); }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  removeAttribute(key) { this.attributes.delete(key); }
  addEventListener(type, listener) { const listeners = this.listeners.get(type) || []; listeners.push(listener); this.listeners.set(type, listeners); }
  emit(type) { (this.listeners.get(type) || []).forEach((listener) => listener({ preventDefault() {}, target: this })); }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.emit("close"); }
}
function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
const flush = () => new Promise((resolve) => setImmediate(resolve));
function fixture() {
  const roots = new Map(), requests = [];
  const document = {
    body: new Element("body", true),
    createElement: (tag) => new Element(tag),
    createTextNode: (value) => { const node = new Element("#text"); node.textContent = value; return node; },
    getElementById(id) {
      for (const root of roots.values()) { const found = descendants(root).find((node) => node.id === id); if (found) return found; }
      const root = new Element("div", true); root.id = id; roots.set(id, root); return root;
    },
  };
  const context = vm.createContext({ document, Node: Element, AbortController, DOMException, URLSearchParams, Intl, setTimeout, clearTimeout,
    fetch: (url, options) => {
      if (url === "/api/session") return Promise.resolve({ ok: true, json: async () => ({ authenticated: false, csrf: "fixture" }) });
      return new Promise((resolve) => requests.push({ url, signal: options.signal, respond: (payload, status = 200) => resolve({ ok: status < 400, status, json: async () => payload }) }));
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/s21_compass/static/app.js"), "utf8"), context);
  const api = vm.runInContext("({ state, renderStudies, renderCampus, peerBrowser, searchableChoice, matchesSearch, campusChoices, renderConditions, renderGraph })", context);
  vm.runInContext('pageScope = createScope(); state.login = "fixture-peer";', context);
  return { ...api, document, requests, context, get: (id) => document.getElementById(id), content: document.getElementById("content") };
}
function findButton(node, caption) { return descendants(node).find((item) => item.tagName === "button" && item.textContent === caption); }
function studies(count, status, prefix = status) { return { projects: Array.from({ length: count }, (_, index) => ({ id: index + 1, title: `${prefix} ${index + 1}`, status })) }; }

test("status and page-size changes immediately reset paging, clear old rows and supersede pending responses", async () => {
  const ui = fixture(); await flush(); ui.renderStudies("projects");
  ui.requests[0].respond(studies(10, "ASSIGNED")); await flush();
  assert.match(ui.content.textContent, /Назначен \/ ещё не начат/);
  findButton(ui.content, "Далее").emit("click");
  assert.equal(new URL(ui.requests[1].url, "http://fixture").searchParams.get("offset"), "10");
  const filter = ui.get("projects-status"); filter.value = "FAILED"; filter.emit("change");
  const filtered = ui.requests[2], params = new URL(filtered.url, "http://fixture").searchParams;
  assert.equal(params.get("status"), "FAILED"); assert.equal(params.get("offset"), "0");
  assert.equal(ui.requests[1].signal.aborted, true);
  assert.doesNotMatch(ui.content.textContent, /ASSIGNED 1/);
  filtered.respond(studies(7, "FAILED")); await flush();
  ui.requests[1].respond(studies(10, "ASSIGNED", "STALE")); await flush();
  assert.match(ui.content.textContent, /FAILED 7/); assert.doesNotMatch(ui.content.textContent, /STALE/);
  assert.match(ui.content.textContent, /Проверка не пройдена/);
  const limit = ui.get("projects-limit"); limit.value = "25"; limit.emit("change");
  const resized = new URL(ui.requests[3].url, "http://fixture").searchParams;
  assert.equal(resized.get("limit"), "25"); assert.equal(resized.get("offset"), "0"); assert.equal(resized.get("status"), "FAILED");
  ui.requests[3].respond(studies(7, "FAILED")); await flush();
});

test("page-local project search keeps server paging based on the full fetched page", async () => {
  const ui = fixture(); await flush(); ui.renderStudies("projects");
  ui.requests[0].respond(studies(10, "ASSIGNED", "Project")); await flush();
  const search = ui.get("projects-search"); search.value = "project 9"; search.emit("input");
  assert.match(ui.content.textContent, /Project 9/); assert.doesNotMatch(ui.content.textContent, /Project 8/);
  assert.match(ui.content.textContent, /только среди уже загруженных/);
  const next = findButton(ui.content, "Далее"); assert.equal(next.disabled, false); next.emit("click");
  assert.equal(new URL(ui.requests[1].url, "http://fixture").searchParams.get("offset"), "10");
  ui.requests[1].respond({ projects: [] }); await flush();
});

test("courses share the immediate exact-status filter without offering the project-only review status", async () => {
  const ui = fixture(); await flush(); ui.renderStudies("courses");
  ui.requests[0].respond({ courses: [{ id: 21, title: "Assigned course", status: "ASSIGNED" }] }); await flush();
  const filter = ui.get("courses-status");
  assert.equal(filter.children.some((option) => option.value === "IN_REVIEWS"), false);
  filter.value = "FAILED"; filter.emit("change");
  const params = new URL(ui.requests[1].url, "http://fixture").searchParams;
  assert.equal(params.get("status"), "FAILED"); assert.equal(params.get("offset"), "0");
  assert.doesNotMatch(ui.content.textContent, /Assigned course/);
  ui.requests[1].respond({ courses: [{ id: 22, title: "Failed course", status: "FAILED" }] }); await flush();
  assert.match(ui.content.textContent, /Failed course/);
});

test("campus search supports Cyrillic, transliteration, tokens and ё without inventing records or losing selection", async () => {
  const ui = fixture(); await flush();
  const choices = ui.campusChoices({ campuses: [{ id: "nsk-id", shortName: "21 Novosibirsk" }, { id: "msk-id", shortName: "21 Moscow" }, { id: "orel-id", shortName: "21 Orel" }] });
  const choice = ui.searchableChoice("fixture-choice", choices, { label: "Кампус", placeholder: "Все кампусы", value: "msk-id" });
  ui.content.append(choice.node);
  for (const query of ["Новосибирск", "новоси", "NOVOSIBIRSK", "  ново   сибирск  "]) {
    choice.search.value = query; choice.search.emit("input");
    assert.equal(choice.picker.value, "msk-id");
    assert.ok(choice.picker.children.some((option) => option.value === "nsk-id"));
    assert.ok(choice.picker.children.some((option) => option.textContent === "Выбран: 21 Moscow"));
  }
  assert.equal(ui.matchesSearch("21 Moscow", "Москва"), true);
  assert.equal(ui.matchesSearch("21 Orel", "Орёл"), true);
  assert.equal(ui.matchesSearch("21 Orel", "орел"), true);
  choice.search.value = "нет-такого-города"; choice.search.emit("input");
  assert.match(choice.node.textContent, /Совпадений нет.*Выбранный кампус сохранён/);
  assert.deepEqual(choice.picker.children.map((option) => option.value), ["", "msk-id"]);
  assert.equal(choice.search.attributes.get("aria-describedby"), "fixture-choice-help");
  assert.ok(descendants(choice.node).some((node) => node.tagName === "label" && node.htmlFor === "fixture-choice-search"));
});

test("project peers use real campus names and UUID query values; typing never changes the active filter", async () => {
  const ui = fixture(); await flush();
  const scope = new AbortController(); ui.peerBrowser(ui.content, "projects/123/participants", scope, { project: true });
  assert.equal(ui.requests[0].url, "/api/data/campuses");
  ui.requests[0].respond({ campuses: [{ id: "7f000000-0000-0000-0000-000000000001", shortName: "21 Novosibirsk" }] });
  ui.requests[1].respond({ participants: ["fixture-peer"] }); await flush();
  const search = ui.get("project-peer-campus-search"); search.value = "Новосибирск"; search.emit("input");
  assert.equal(ui.requests.length, 2);
  const picker = ui.get("project-peer-campus"); picker.value = "7f000000-0000-0000-0000-000000000001"; picker.emit("change");
  const params = new URL(ui.requests[2].url, "http://fixture").searchParams;
  assert.equal(params.get("campusId"), picker.value); assert.equal(params.get("offset"), "0");
  search.value = "Москва"; search.emit("input"); assert.equal(ui.requests.length, 3); assert.equal(picker.value, params.get("campusId"));
  ui.requests[2].respond({ participants: [] }); await flush();
});

test("main campus selection stays intact while searching and late campus-detail data is ignored", async () => {
  const ui = fixture(); await flush(); ui.renderCampus();
  ui.requests[0].respond({ campuses: [{ id: "nsk-id", shortName: "21 Novosibirsk" }, { id: "msk-id", shortName: "21 Moscow" }] }); await flush();
  const picker = ui.get("campus-choice"); picker.value = "nsk-id"; picker.emit("change");
  assert.equal(ui.state.settings.campusId, "nsk-id");
  const search = ui.get("campus-choice-search"); search.value = "Москва"; search.emit("input");
  assert.equal(picker.value, "nsk-id"); assert.equal(ui.requests.length, 3);
  picker.value = "msk-id"; picker.emit("change");
  assert.equal(ui.requests[1].signal.aborted, true); assert.equal(ui.requests[2].signal.aborted, true);
  ui.requests[1].respond({ clusters: [{ id: "old", name: "STALE CAMPUS" }] }); ui.requests[2].respond({ coalitions: [] });
  ui.requests[3].respond({ clusters: [{ id: "current", name: "Current Cluster", floor: 1 }] }); ui.requests[4].respond({ coalitions: [] }); await flush();
  assert.match(ui.content.textContent, /Current Cluster/); assert.doesNotMatch(ui.content.textContent, /STALE CAMPUS/);
  assert.equal(ui.state.settings.campusId, "msk-id");
});

test("null elements in start conditions preserve valid rules and graph help uses catalog entity IDs", async () => {
  const ui = fixture(); await flush();
  ui.renderConditions(ui.content, [null, { rulesInGroup: [null, { logicalOperator: "AND", value: { fieldName: "level", operator: ">=", value: [null, { value: 7 }] } }] }]);
  assert.match(ui.content.textContent, /level >= 7/);
  ui.content.replaceChildren(); ui.renderGraph();
  ui.requests[0].respond({ nodes: [{ id: "group-id", label: "Foundation", items: [null, { id: "item-id", code: "C2", entityType: "PROJECT", entityId: 42, handles: [] }] }], edges: [] }); await flush();
  const helps = descendants(ui.content).filter((node) => node.tagName === "details" && node.className === "section-help");
  assert.ok(helps.length >= 2); assert.ok(helps.every((node) => node.children[0].tagName === "summary"));
  assert.match(ui.content.textContent, /startConditions/); assert.match(ui.content.textContent, /не задают дату дедлайна/);
  findButton(ui.content, "C2Проект").emit("click");
  assert.ok(ui.requests.some((request) => request.url === "/api/data/projects/42"));
  assert.equal(ui.requests.some((request) => request.url.includes("projects/C2")), false);
  for (const request of ui.requests.slice(1)) request.respond(request.url === "/api/data/campuses" ? { campuses: [] } : request.url.includes("/participants") ? { participants: [] } : { projectId: 42, title: "Catalog project", startConditions: [null] });
  await flush(); assert.match(ui.get("detail-content").textContent, /Условие не описано/);
});
