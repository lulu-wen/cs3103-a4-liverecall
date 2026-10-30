/*
 * LiveRecall browser client.
 *
 * Sections:
 *   1. Live view: revision state + rendering (P1)            <- implemented
 *   2. Connection, reconnect/resume, HISTORY merge, recall (P2) <- still starter
 *
 * Contract for section 2: every caption that reaches the client (live or from
 * HISTORY) goes through applyCaption(msg). It returns "accepted", "stale",
 * "duplicate" or "invalid", and only accepted captions change the display.
 */

const WS_URL = "ws://127.0.0.1:8765";
const statusEl = document.getElementById("status");
const liveEl = document.getElementById("live");
const recallEl = document.getElementById("recall");

/* ======================================================================
 * 1. LIVE VIEW (P1): one line per segment, newer revisions replace older.
 * ====================================================================== */

// Bounds: keep this many segments in memory and on screen. Older segments are
// forgotten; any later update for them is treated as stale (never re-shown).
const MAX_TRACKED_SEGMENTS = 2000;
const MAX_RENDERED_SEGMENTS = 300;
// Auto-scroll only while the reader is within this many pixels of the bottom.
const STICK_TO_BOTTOM_PX = 48;

// segment -> { revision, final, text, source_ts, el }
const segments = new Map();
let forgottenUpTo = 0;  // every segment <= this has been dropped from memory
let lastSeg = null;  // the last segment that was finalized and accepted

const jumpButton = document.getElementById("jump-live");
let followLive = true;

const LocalRecallStore = [];

function isPositiveInt(value) {
  return Number.isInteger(value) && value >= 1;
}

function isValidCaption(msg) {
  return msg && msg.type === "CAPTION_UPDATE"
    && isPositiveInt(msg.segment) && isPositiveInt(msg.revision)
    && typeof msg.final === "boolean" && typeof msg.text === "string"
    && typeof msg.source_ts === "number" && Number.isFinite(msg.source_ts);
}

/*
 * The client-side revision rule (mirrors the server's CaptionState):
 * accept only if the segment is not final and the revision is newer.
 * Updates the in-memory state only; rendering is separate.
 */
function acceptUpdate(msg) {
  if (!isValidCaption(msg)) return "invalid";
  if (msg.segment <= forgottenUpTo) return "stale";
  const prev = segments.get(msg.segment);
  if (prev) {
    if (msg.revision === prev.revision) return "duplicate";
    if (prev.final || msg.revision < prev.revision) return "stale";
  }
  const entry = prev || { el: null };
  entry.revision = msg.revision;
  entry.final = msg.final;
  entry.text = msg.text;
  entry.source_ts = msg.source_ts;
  segments.set(msg.segment, entry);

  if (msg.final && (!lastSeg || msg.segment > lastSeg.segment)) {
    lastSeg = { segment: msg.segment, revision: msg.revision };
  }

  return "accepted";
}

function createLine(segment) {
  const el = document.createElement("p");
  el.className = "caption";
  el.dataset.segment = String(segment);
  // Keep lines in segment order even if an older segment arrives late
  // (e.g. recovered from HISTORY after a live provisional was shown).
  let before = null;
  for (let node = liveEl.lastElementChild; node; node = node.previousElementSibling) {
    if (Number(node.dataset.segment) < segment) break;
    before = node;
  }
  liveEl.insertBefore(el, before);
  return el;
}

function renderSegment(segment) {
  const entry = segments.get(segment);
  if (!entry) return;
  if (!entry.el) entry.el = createLine(segment);
  const el = entry.el;
  el.textContent = entry.text;
  el.classList.toggle("final", entry.final);
  el.classList.toggle("provisional", !entry.final);
  el.title = `Segment ${segment}, revision ${entry.revision}`
    + (entry.final ? " (final)" : " (provisional, may still change)");
}

function pruneOldSegments() {
  // Drop DOM lines beyond the on-screen limit (the state entry stays).
  while (liveEl.childElementCount > MAX_RENDERED_SEGMENTS) {
    const first = liveEl.firstElementChild;
    const entry = segments.get(Number(first.dataset.segment));
    if (entry) entry.el = null;
    first.remove();
  }
  // Forget the oldest state entries beyond the memory limit.
  if (segments.size > MAX_TRACKED_SEGMENTS) {
    const ordered = [...segments.keys()].sort((a, b) => a - b);
    for (const seg of ordered.slice(0, segments.size - MAX_TRACKED_SEGMENTS)) {
      const entry = segments.get(seg);
      if (entry.el) entry.el.remove();
      segments.delete(seg);
      forgottenUpTo = Math.max(forgottenUpTo, seg);
    }
  }
}

function nearBottom() {
  return liveEl.scrollHeight - liveEl.scrollTop - liveEl.clientHeight <= STICK_TO_BOTTOM_PX;
}

function scrollToLive() {
  liveEl.scrollTop = liveEl.scrollHeight;
  followLive = true;
  jumpButton.hidden = true;
}

liveEl.addEventListener("scroll", () => {
  followLive = nearBottom();
  if (followLive) jumpButton.hidden = true;
});
jumpButton.addEventListener("click", scrollToLive);

/* Single entry point for section 2: accept, then render if accepted. */
function applyCaption(msg) {
  const result = acceptUpdate(msg);
  if (result === "accepted") {
    renderSegment(msg.segment);
    pruneOldSegments();
    if (followLive) {
      liveEl.scrollTop = liveEl.scrollHeight;
    } else {
      jumpButton.hidden = false;
    }
    if (msg.final) {
      addToLocalRecallStore(msg);
    }
  }
  return result;
}

/* ======================================================================
 * 2. CONNECTION / RECOVERY / RECALL (P2): still the starter behaviour.
 * ====================================================================== */

let ws;

function numericClientId() {
  const key = "liverecall-client-id";
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = String(Math.floor(100000 + Math.random() * 900000));
    sessionStorage.setItem(key, value);
  }
  return Number(value);
}

function getMissedCaptionsSince(seconds) {
  const cutoff = Date.now() /1000 - seconds;
  const cleanCutoff = Date.now() / 1000 - 60;

  while (LocalRecallStore.length > 0 && LocalRecallStore[0].source_ts < cleanCutoff) {
    LocalRecallStore.shift();
  }

  return LocalRecallStore.filter(msg => msg.source_ts >= cutoff);
}

function addToLocalRecallStore(msg) {
  LocalRecallStore.push(msg);
  LocalRecallStore.sort((a, b) => a.source_ts - b.source_ts);

  const cutoff = Date.now() / 1000 - 60;  // 60 seconds ago
  while (LocalRecallStore.length > 0 && LocalRecallStore[0].source_ts < cutoff) {
    LocalRecallStore.shift();
  }

}

function connect() {
  statusEl.textContent = "connecting";
  ws = new WebSocket(WS_URL);

  ws.addEventListener("open", () => {
    statusEl.textContent = "connected";
    ws.send(JSON.stringify({
      type: "CLIENT_HELLO",
      client_id: numericClientId(),
      session: "L1",
      last_segment: lastSeg ? lastSeg.segment : 0,
      last_revision: lastSeg ? lastSeg.revision : 0,
      history_seconds: 60
    }));
  });

  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "CAPTION_UPDATE") {
      applyCaption(msg);
    } else if (msg.type === "HISTORY") {
      //merge history via applyCaption(item) for each item, and add accepted finals to the local recall store in applycaption.
      for(const item of msg.items || []) {
        applyCaption(item); 
      }
    }
  });

  ws.addEventListener("close", () => {
    // TODO: automatic reconnect after a short delay, using finalized resume state.
    statusEl.textContent = "disconnected, reconnecting in 3s...";
    setTimeout(() => {
      connect();
    }, 3000);
  });

  ws.addEventListener("error", () => {
    statusEl.textContent = "connection error";
  });
}

for (const button of document.querySelectorAll("button[data-seconds]")) {
  button.addEventListener("click", () => {
    const seconds = Number(button.dataset.seconds);
    missedCaptions = getMissedCaptionsSince(seconds);
    recallEl.textContent = missedCaptions.map(msg => `[${new Date(msg.source_ts * 1000).toLocaleTimeString()}] ${msg.text}`).join("\n") || "(no captions)";
    });
}

connect();
