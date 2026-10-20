async function checks() {
  transport = grantTransport();
  await client.takeControl();
  const identity = client.readState().pageId;
  assert(identity.length === 48 && client.readState().token === "first-token", "Initial entry claims a random in-memory page identity");
  assert(calls.length === 2 && mainText().includes("Home"), "Claim fetches the server-rendered page into the persistent shell");
  const initial = calls[0];
  assert(initial[0] === "/api/controller/claim" && JSON.parse(initial[1].body).page_id === identity, "Initial claim sends only this page's identity");
  const owned = calls[1][0];
  assert(owned.headers.get("X-Page-Id") === identity && owned.headers.get("X-Controller-Token") === "first-token", "Protected navigation uses the common ownership headers");

  transport = async (input) => {
    if (pathOf(input) === "/explore") return htmlReply("Pending feedback", "/feedback/shared", { feedbackPath: "/feedback/shared" });
    return jsonReply({ ok: true });
  };
  await client.navigate("/explore");
  assert(client.readState().pageId === identity && client.readState().token === "first-token", "SSR navigation preserves the same tab's grant");
  assert(mainText().includes("Pending feedback") && historyCalls.at(-1) === "/feedback/shared", "A pending encounter redirects the shell to the same feedback page");
  assert(!calls.some(([input]) => pathOf(input).includes("/play")), "Resuming feedback does not relaunch playback");
  await window.fetch("https://elsewhere.example/data");
  const foreign = calls.at(-1)[0];
  assert(!foreign.headers.get("X-Page-Id") && !foreign.headers.get("X-Controller-Token"), "External requests receive no controller identity or token");

  let resolveBody;
  const body = new Promise((resolve) => { resolveBody = resolve; });
  let bodyRequested = false;
  transport = async () => ({ ...htmlReply("Stale Explore", "/explore"), text: () => { bodyRequested = true; return body; } });
  const latePage = client.navigate("/explore");
  await tickUntil(() => bodyRequested);
  client.waiting("Control was transferred");
  resolveBody(JSON.stringify({ title: "Stale Explore", body: "Stale Explore" }));
  await latePage;
  assert(client.readState().token === null && mainText().includes("Take Control") && !mainText().includes("Stale Explore"), "A late HTML body cannot replace the waiting screen after ownership changed");
  assert(!alerts.length, "Discarding an obsolete response does not show a misleading failure");

  transport = grantTransport("second-token");
  await client.takeControl(true);
  assert(client.readState().token === "second-token", "Explicit takeover installs a fresh grant");
  assert(calls.some(([input]) => pathOf(input) === "/api/controller/takeover"), "Takeover uses the explicit endpoint");
  let resolveResponse;
  transport = () => new Promise((resolve) => { resolveResponse = resolve; });
  const oldRequest = window.fetch("/explore");
  client.waiting("Control moved again");
  resolveResponse(htmlReply("Old result"));
  let oldRejected = false;
  try { await oldRequest; } catch { oldRejected = true; }
  assert(oldRejected, "The fetch wrapper suppresses responses from an older grant");

  transport = grantTransport("third-token");
  await client.takeControl(true);
  transport = async () => jsonReply({ error: { code: "controller_transferred", message: "Use the other page" } }, 423);
  await client.navigate("/explore");
  assert(client.readState().token === null && mainText().includes("Take Control"), "A server ownership rejection blocks the entire app");

  transport = grantTransport("stale-release-token");
  await client.takeControl(true);
  transport = async () => jsonReply({ error: { code: "controller_transferred", message: "Use the other page" } }, 423);
  await click(button("releaseControl"));
  assert(client.readState().token === null && mainText().includes("Take Control"), "A rejected stale release also switches to the ownership gate");

  transport = grantTransport("release-token");
  await client.takeControl(true);
  let completeRelease;
  transport = () => new Promise((resolve) => { completeRelease = resolve; });
  const beforeRelease = calls.length;
  const releasing = click(button("releaseControl"));
  assert(client.readState().busy, "Release holds the operation gate until confirmed");
  await client.takeControl(true);
  await client.navigate("/explore");
  await click(button("player", "mpv"));
  assert(calls.length === beforeRelease + 1, "A pending release blocks navigation, a new grant, and player changes");
  completeRelease(jsonReply({ ok: true }));
  await releasing;
  assert(!client.readState().busy && client.readState().token === null && mainText().includes("Control released"), "Confirmed release returns to the explicit-control gate");

  transport = grantTransport("play-token");
  await client.takeControl(true);
  let completePlayer;
  transport = async () => ({ ...jsonReply({}), json: () => new Promise((resolve) => { completePlayer = resolve; }) });
  const playing = click(button("streamPath", "episode.mp4"));
  await tickUntil(() => completePlayer);
  const beforePlayer = location.href;
  client.waiting("Control moved before the player reply");
  completePlayer({ player_url: "vlc-x-callback://stale-launch" });
  await playing;
  assert(location.href === beforePlayer && mainText().includes("Take Control"), "An obsolete launch response cannot open an external player");

  transport = async (input) => pathOf(input).startsWith("/api/controller/") ? jsonReply({ token: "timer-token" }) : htmlReply("Feedback", "/feedback/shared", { feedbackPath: "/feedback/shared", playerUrl: "vlc-x-callback://delayed-launch" });
  await client.takeControl(true);
  const delayed = timers.find((timer) => timer.delay === 100);
  assert(delayed, "A newly requested player launch is scheduled");
  const beforeTimer = location.href;
  client.waiting("Control moved before launch timer");
  delayed.action();
  assert(location.href === beforeTimer, "A queued launch timer cannot run after control is lost");

  transport = grantTransport("timeout-token");
  await client.takeControl(true);
  const beforeTimeout = calls.length;
  transport = (input, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("aborted")));
  });
  const hung = client.navigate("/feedback/no_feedback", { method: "POST" });
  assert(client.readState().busy, "A pending save keeps the operation gate");
  const deadlineIndex = timers.length - 1;
  assert(timers[deadlineIndex].delay === 12_000, "Ordinary requests have a deadline, separate from ownership lifetime");
  timers[deadlineIndex].action();
  await hung;
  assert(!client.readState().busy && client.readState().token === "timeout-token", "Timeout releases busy without guessing whether the server saved");
  assert(calls.length === beforeTimeout + 1, "A timed-out save is never retried automatically");
  assert(alerts.at(-1).includes("12 seconds") && alerts.at(-1).includes("check the current page"), "Uncertain save outcome is explained before retrying");
  assert(clearedTimers.includes(deadlineIndex + 1), "Failed requests always clear their deadline timer");
  transport = grantTransport("retry-token");
  await client.takeControl(true);
  assert(client.readState().token === "retry-token", "Explicit takeover works again after a hung request times out");

  let waitingForBody = false;
  transport = async (input, init) => ({
    ...htmlReply(),
    text: () => new Promise((_resolve, reject) => {
      waitingForBody = true;
      init.signal.addEventListener("abort", () => reject(new Error("body aborted")));
    }),
  });
  const hungBody = client.navigate("/explore");
  await tickUntil(() => waitingForBody);
  timers.at(-1).action();
  await hungBody;
  assert(!client.readState().busy, "The deadline also handles a response body that never completes");

  const cancel = new AbortController();
  transport = (input, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("caller cancelled")));
  });
  const cancelled = window.fetch("/explore", { signal: cancel.signal });
  cancel.abort();
  let callerCancelled = false;
  try { await cancelled; } catch (error) { callerCancelled = error.message === "caller cancelled"; }
  assert(callerCancelled, "A caller-supplied cancellation signal is respected");

  const beforeStreaming = timers.length;
  transport = async () => jsonReply({ ok: true });
  await window.fetch("/stream/playback-token");
  assert(timers.length === beforeStreaming, "Media byte streams do not receive shell request deadlines");

  setupDocument();
  eval(CLIENT_SOURCE);
  transport = (input, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("claim aborted")));
  });
  const hungClaim = client.takeControl();
  timers.at(-1).action();
  await hungClaim;
  assert(!client.readState().busy && mainText().includes("Take Control"), "An unavailable claim also times out and allows explicit retry");
  transport = async () => jsonReply({ error: { code: "controller_occupied", message: "Another tab controls the app" } }, 423);
  await client.takeControl();
  assert(client.readState().pageId !== identity && client.readState().token === null, "A second document has a different identity and cannot inherit authority");
  assert(mainText().includes("Take Control"), "The second tab waits for explicit takeover");
  assert(intervalCalls === 0, "Ownership never starts polling");
}
checks().then(
  () => { if (typeof print === "function") print("Frontend behavior checks passed"); else console.log("Frontend behavior checks passed"); },
  (error) => { if (typeof print === "function") { print(error.stack); throw error; } else { console.error(error); process.exitCode = 1; } },
);
