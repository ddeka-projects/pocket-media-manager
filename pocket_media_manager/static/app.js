/* One identity per live document. No cookies or copied sessionStorage grant control. */
(() => {
  "use strict";
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  const pageId = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  let token = null;
  let busy = false;
  let ownershipEpoch = 0;
  const originalFetch = window.fetch.bind(window);
  const main = () => document.querySelector("main");

  class OwnershipChangedError extends Error {}

  function isOwnershipFailure(payload) {
    return ["controller_occupied", "controller_required", "controller_transferred"].includes(payload.error?.code);
  }

  function requireCurrentOwner(epoch) {
    if (epoch !== ownershipEpoch) throw new OwnershipChangedError("Control changed while the request was running.");
  }

  async function shellFetch(input, init = {}) {
    const controller = new AbortController();
    const callerSignal = init.signal || (input instanceof Request ? input.signal : null);
    const abort = () => controller.abort();
    if (callerSignal?.aborted) abort();
    else callerSignal?.addEventListener("abort", abort, {once: true});
    const timeout = window.setTimeout(abort, 12_000);
    try {
      const response = await originalFetch(input, {...init, signal: controller.signal});
      const type = response.headers.get("content-type") || "";
      // Shell pages and JSON are small. Read a clone so the deadline covers
      // the body too, while callers retain the ordinary Response interface.
      if (type.includes("text/html") || type.includes("application/json")) await response.clone().text();
      return response;
    } catch (error) {
      if (controller.signal.aborted && !callerSignal?.aborted) throw new Error("The PC did not respond within 12 seconds.");
      throw error;
    } finally {
      window.clearTimeout(timeout);
      callerSignal?.removeEventListener("abort", abort);
    }
  }

  function waiting(message = "This app is open in another page. Take Control to use it here.") {
    ownershipEpoch += 1;
    token = null;
    main().replaceChildren();
    const heading = document.createElement("h1");
    heading.textContent = "Pocket Media Manager";
    const text = document.createElement("p");
    text.textContent = message;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.dataset.takeControl = "true";
    button.textContent = "Take Control";
    main().append(heading, text, button);
  }

  window.fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    const protectedRequest = url.origin === location.origin && !url.pathname.startsWith("/api/controller/");
    if (!protectedRequest) return originalFetch(request);
    // Media bytes have their own lifetime and need no browser ownership grant.
    if (url.pathname.startsWith("/media/")) return originalFetch(request);
    const epoch = ownershipEpoch;
    request.headers.set("X-Page-Id", pageId);
    if (token) request.headers.set("X-Controller-Token", token);
    const response = await shellFetch(request);
    requireCurrentOwner(epoch);
    if (!response.ok && (response.headers.get("content-type") || "").includes("application/json")) {
      const payload = await response.clone().json();
      requireCurrentOwner(epoch);
      const error = payload.error || {};
      if (isOwnershipFailure(payload)) {
        waiting(error.message);
        throw new Error(error.message);
      }
    }
    return response;
  };

  function activatePage() {
    document.querySelectorAll("time[datetime]").forEach(element => {
      element.textContent = new Date(element.dateTime).toLocaleString();
    });
    const feedback = document.querySelector("[data-feedback-path]");
    if (feedback) history.replaceState(null, "", feedback.dataset.feedbackPath);
    const player = document.querySelector("[data-player-url]");
    const epoch = ownershipEpoch;
    if (player) window.setTimeout(() => {
      if (epoch === ownershipEpoch && token) location.href = player.dataset.playerUrl;
    }, 100);
  }

  // Light up on contact; feedback never holds up a request or a new screen.
  const TAP_TARGETS = "button, a.button, a.explore-card, label.select-card, .crumbs a";
  const tapTimers = new WeakMap();
  let pressed = null;

  function tapTarget(event) {
    const target = event.target.closest?.(TAP_TARGETS);
    return target?.matches(":disabled") ? null : target;
  }

  function flashTap(element) {
    window.clearTimeout(tapTimers.get(element));
    element.classList.remove("tapped");
    void element.offsetWidth; // restart the animation on repeated taps
    element.classList.add("tapped");
    tapTimers.set(element, window.setTimeout(() => element.classList.remove("tapped"), 450));
  }

  function clearPress() {
    if (pressed) pressed.element.classList.remove("pressing");
    pressed = null;
  }

  async function navigate(url, options = {}, push = true) {
    if (busy || !token) return;
    busy = true;
    const epoch = ownershipEpoch;
    try {
      const response = await window.fetch(url, {
        ...options,
        headers: {"Accept": "text/html", ...(options.headers || {})}
      });
      const type = response.headers.get("content-type") || "";
      if (!type.includes("text/html")) {
        const payload = await response.json();
        throw new Error(payload.error?.message || "The request could not be completed.");
      }
      const html = await response.text();
      requireCurrentOwner(epoch);
      const documentPage = new DOMParser().parseFromString(html, "text/html");
      const content = documentPage.querySelector("main");
      if (!content) throw new Error("The server returned an incomplete page.");
      main().replaceChildren(...content.childNodes);
      document.title = documentPage.title;
      if (push) history.pushState(null, "", response.url);
      else history.replaceState(null, "", response.url);
      activatePage();
      window.scrollTo(0, 0);
    } catch (error) {
      // Ownership failures have already replaced the app with the control screen.
      if (token && !(error instanceof OwnershipChangedError)) window.alert(`${error.message} If this followed a save, check the current page before retrying.`);
    } finally {
      busy = false;
    }
  }

  async function takeControl(takeover = false) {
    if (busy) return;
    busy = true;
    const epoch = ++ownershipEpoch;
    token = null;
    try {
      const response = await shellFetch(`/api/controller/${takeover ? "takeover" : "claim"}`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({page_id: pageId})
      });
      const payload = await response.json();
      requireCurrentOwner(epoch);
      if (!response.ok) {
        waiting(payload.error?.message || "Could not connect. Check that the server is running.");
        return;
      }
      token = payload.token;
    } catch (error) {
      if (!(error instanceof OwnershipChangedError)) waiting(`Could not connect: ${error.message}`);
    } finally {
      busy = false;
    }
    if (token) await navigate(location.href, {}, false);
  }

  document.addEventListener("click", async event => {
    const button = event.target.closest("button");
    if (button?.hasAttribute("data-take-control")) {
      await takeControl(true);
      return;
    }
    if (button?.hasAttribute("data-select")) {
      document.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = button.dataset.select === "all"; });
      return;
    }
    if (button?.hasAttribute("data-player")) {
      if (busy || !token) return;
      busy = true;
      const epoch = ownershipEpoch;
      const body = new URLSearchParams({player: button.dataset.player});
      try {
        const response = await window.fetch("/settings/player", {method: "POST", body});
        if (!response.ok) { const payload = await response.json(); throw new Error(payload.error?.message || "Player could not be saved."); }
        requireCurrentOwner(epoch);
        document.querySelectorAll("[data-player]").forEach(item => { item.classList.toggle("active", item === button); });
        document.querySelectorAll('input[name="player"]').forEach(input => { input.value = button.dataset.player; });
      } catch (error) {
        if (token && !(error instanceof OwnershipChangedError)) window.alert(error.message);
      } finally { busy = false; }
      return;
    }
    if (button?.hasAttribute("data-stream-path")) {
      if (busy || !token) return;
      busy = true;
      const epoch = ownershipEpoch;
      const status = document.getElementById("stream-status");
      status.textContent = "Opening player…";
      try {
        const response = await window.fetch("/stream/play", {method: "POST", body: new URLSearchParams({path: button.dataset.streamPath})});
        const payload = await response.json();
        requireCurrentOwner(epoch);
        if (!response.ok) throw new Error(payload.error?.message || "Could not open this file.");
        if (payload.player_url) location.href = payload.player_url;
        status.textContent = payload.launched ? "Opened in mpv." : "";
      } catch (error) {
        if (token && !(error instanceof OwnershipChangedError)) status.textContent = error.message;
      } finally { busy = false; }
      return;
    }
    const link = event.target.closest("a[href]");
    if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !link.target && !link.download) {
      const url = new URL(link.href);
      if (url.origin === location.origin) {
        event.preventDefault();
        await navigate(url.href);
      }
    }
  });

  document.addEventListener("submit", async event => {
    const form = event.target;
    event.preventDefault();
    const body = new URLSearchParams(new FormData(form));
    const url = new URL(form.action, location.href);
    if (form.method.toLowerCase() === "get") {
      url.search = body.toString();
      await navigate(url.href);
    } else {
      await navigate(url.href, {method: "POST", body});
    }
  });

  document.addEventListener("pointerdown", event => {
    if (!event.isPrimary || event.button !== 0) return;
    clearPress();
    const target = tapTarget(event);
    if (!target) return;
    window.clearTimeout(tapTimers.get(target));
    target.classList.remove("tapped");
    target.classList.add("pressing");
    pressed = {element: target, pointerId: event.pointerId};
  }, {passive: true});
  document.addEventListener("pointerup", event => {
    if (event.pointerId !== pressed?.pointerId) return;
    const element = pressed.element;
    clearPress();
    if (tapTarget(event) === element) flashTap(element);
  }, {passive: true});
  document.addEventListener("pointercancel", event => {
    if (event.pointerId === pressed?.pointerId) clearPress();
  }, {passive: true});
  window.addEventListener("blur", clearPress);
  // Keyboard and assistive activation have no preceding pointer press.
  document.addEventListener("click", event => {
    if (event.detail !== 0 || event.target.matches?.("input")) return;
    const target = tapTarget(event);
    if (target) flashTap(target);
  }, true);
  // iPhone Safari only shows :active press states when a touch listener exists.
  document.addEventListener("touchstart", () => {}, {passive: true});

  window.addEventListener("popstate", () => navigate(location.href, {}, false));
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  takeControl();
})();
