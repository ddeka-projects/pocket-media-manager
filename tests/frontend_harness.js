// DOM and fetch substitutes for shell behavior; layout is tested on real devices.
const timers = [], calls = [], alerts = [], historyCalls = [], clearedTimers = [];
let transport, random = 0, intervalCalls = 0;
class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.textContent = "";
    this.attributes = {};
    this.classList = { toggle() {} };
  }
  get childNodes() { return this.children; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  hasAttribute(name) {
    if (name.startsWith("data-")) return Object.hasOwn(this.dataset, name.slice(5).replace(/-([a-z])/g, (_, value) => value.toUpperCase()));
    return Object.hasOwn(this.attributes, name);
  }
  closest(selector) { return selector === "button" && this.tag === "button" ? this : null; }
}
class FakeHeaders {
  constructor(values = {}) {
    this.values = new Map(values instanceof FakeHeaders ? values.values : Object.entries(values).map(([name, value]) => [name.toLowerCase(), value]));
  }
  set(name, value) { this.values.set(name.toLowerCase(), value); }
  get(name) { return this.values.get(name.toLowerCase()) || null; }
}
class FakeURL {
  constructor(value, base = location.href) {
    const absolute = /^[a-z][a-z0-9+.-]*:/i.test(value) ? value : new FakeURL(base).origin + (value.startsWith("/") ? value : "/" + value);
    this.href = absolute;
    const match = /^([a-z][a-z0-9+.-]*:)\/\/([^/]+)(\/[^?#]*)?/i.exec(absolute);
    if (!match) throw new Error("Invalid test URL: " + value);
    this.origin = match[1] + "//" + match[2];
    this.pathname = match[3] || "/";
  }
}
class FakeRequest {
  constructor(input, init = {}) {
    this.url = new FakeURL(typeof input === "string" ? input : input.url).href;
    this.method = init.method || input.method || "GET";
    this.headers = new FakeHeaders(init.headers || input.headers);
    this.body = init.body || input.body;
    this.signal = init.signal || input.signal;
  }
}
function findData(node, name) {
  if (Object.hasOwn(node.dataset, name)) return node;
  for (const child of node.children) {
    const found = findData(child, name);
    if (found) return found;
  }
  return null;
}
function setupDocument() {
  const root = new Element("main");
  root.append(Object.assign(new Element(), { textContent: "Connecting…" }));
  const listeners = {};
  globalThis.document = {
    root, listeners, title: "Pocket Media Manager",
    querySelector(selector) {
      if (selector === "main") return root;
      return findData(root, selector.slice(6, -1).replace(/-([a-z])/g, (_, value) => value.toUpperCase()));
    },
    querySelectorAll() { return []; },
    createElement: (tag) => new Element(tag),
    addEventListener(name, action) { (listeners[name] ||= []).push(action); },
    getElementById() { return new Element(); },
  };
  globalThis.location = { origin: "http://127.0.0.1:8787", href: "http://127.0.0.1:8787/", reload() {} };
  globalThis.history = {
    pushState(_state, _title, value) { historyCalls.push(value); location.href = new FakeURL(value).href; },
    replaceState(_state, _title, value) { historyCalls.push(value); location.href = new FakeURL(value).href; },
  };
  globalThis.window = {
    fetch: (...args) => { calls.push(args); return transport(...args); },
    addEventListener() {},
    setTimeout(action, delay) { timers.push({ action, delay }); return timers.length; },
    clearTimeout(id) { clearedTimers.push(id); },
    setInterval() { intervalCalls += 1; },
    scrollTo() {}, alert(message) { alerts.push(message); },
  };
}
globalThis.crypto = { getRandomValues(bytes) { bytes.fill(++random); return bytes; } };
globalThis.Request = FakeRequest;
globalThis.URL = FakeURL;
globalThis.AbortController = class {
  constructor() {
    const listeners = [];
    this.signal = {
      aborted: false,
      addEventListener(_name, listener) { listeners.push(listener); },
      removeEventListener(_name, listener) {
        const index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      },
      notify() { [...listeners].forEach((listener) => listener()); },
    };
  }
  abort() { this.signal.aborted = true; this.signal.notify(); }
};
globalThis.URLSearchParams = class {
  constructor(body) { this.body = body; }
};
globalThis.localStorage = globalThis.sessionStorage = new Proxy({}, { get() { throw new Error("Ownership must not use copied browser storage"); } });
globalThis.DOMParser = class {
  parseFromString(source) {
    const page = JSON.parse(source), content = new Element("main");
    content.append(Object.assign(new Element(), { textContent: page.body }));
    for (const name of ["feedbackPath", "playerUrl"]) {
      if (page[name]) {
        const marker = new Element(); marker.dataset[name] = page[name]; content.append(marker);
      }
    }
    return { title: page.title, querySelector: () => content };
  }
};
function jsonReply(payload, status = 200) {
  return { ok: status < 400, status, headers: new FakeHeaders({ "content-type": "application/json" }), json: async () => payload, text: async () => JSON.stringify(payload), clone() { return this; } };
}
function htmlReply(body = "Home", path = "/", extra = {}) {
  return { ok: true, status: 200, url: location.origin + path, headers: new FakeHeaders({ "content-type": "text/html" }), text: async () => JSON.stringify({ title: body, body, ...extra }), clone() { return this; } };
}
function pathOf(input) { return typeof input === "string" ? input : new FakeURL(input.url).pathname; }
function assert(value, message) { if (!value) throw new Error(message); }
function mainText() { return document.root.children.map((node) => node.textContent).join(" "); }
function button(data, value = "true") { const result = new Element("button"); result.dataset[data] = value; return result; }
function click(target) { return document.listeners.click[0]({ target }); }
function grantTransport(value = "first-token") {
  return async (input) => pathOf(input).startsWith("/api/controller/") ? jsonReply({ token: value }) : htmlReply();
}
async function tickUntil(predicate) {
  for (let count = 0; count < 20 && !predicate(); count += 1) await Promise.resolve();
  assert(predicate(), "The expected asynchronous boundary was reached");
}
setupDocument();
