# Revision state and bounded history (P1)

Covers spec section 3 #1, #2, #7 (server side), #8 and section 4.1. Code:
`captions/state.py`, `captions/history.py`, the source path in
`solution_server.py` (`handle_source`) and section 1 of `client/app.js`.

## Server: `CaptionState.apply(msg, raw) -> Event | None`

| Incoming update | Decision | Counter |
| --- | --- | --- |
| New segment, valid fields | accept | `accepted` |
| Revision greater than the highest accepted, segment not final | accept (replaces) | `accepted` |
| Revision lower than the highest accepted | drop | `stale` |
| Same revision as accepted (incl. repeated final) | drop | `duplicate` |
| Any newer revision after the segment's final | drop | `after_final` |
| Final whose revision is not newer than an accepted provisional | drop | `stale_final` |
| Segment older than the remembered range | drop | `too_old` |
| A used field missing or the wrong type (`segment`/`revision` not int, `final` not bool, `source_ts` not a number, `text` not a string), non-object, binary frame | drop, never raise | `malformed` |

- **Why an older revision can never overwrite a newer one:** acceptance needs
  `revision > stored revision` (`_decide`), and a finalized segment rejects
  every later update before revisions are even compared.
- **`stale_final` decision:** if a source ever sent a final with a revision not
  newer than one already forwarded, clients would treat it as stale anyway
  (`load_clients.py` classifies by highest revision), so the server rejects it
  and counts it separately to make the anomaly visible. The instructor source
  never does this.
- **Malformed updates are rejected, not repaired:** messages come from
  `caption_source.py`, so `is_well_formed()` only checks that the fields the
  server uses have the types that source sends (no conversion, no range
  checks). The check is still needed: comparing a wrong-typed value (e.g. a
  string segment) with an int raises `TypeError` in the source handler, which
  would close the source connection and stop every client's captions.
  `type(x) is int` is used because `True` is also an int in Python. Not-JSON
  messages are already skipped in `handle_source` and are not counted.
- **Forward the original string:** an accepted update is forwarded exactly as
  the source sent it (`Event.raw` is the received string). The same string
  goes to every client queue and into history, so nothing is ever re-encoded.
  Unknown fields are allowed and pass through to clients, which ignore them.
  Trade-off (measured): rebuilding a compact message would cost ~7 us once per
  source message but send ~6% fewer bytes to every client (207 B vs 220 B for
  a typical caption, more if the source adds fields). We chose the simpler
  path; the bandwidth cost only matters under the constrained-bandwidth test.
- **Bounded:** at most `max_active_segments` (1000) unfinished segments and
  `max_remembered_finals` (10000) finalized segment numbers. Forgotten finals
  advance a watermark (`forgotten_upto`); updates at or below it are rejected,
  so a forgotten segment can never be reopened.
- **Source reconnect:** state is module-level in `solution_server.py`, so a new
  `SOURCE_HELLO` continues the same lecture state (tested).
- Totals are printed on `SOURCE_DONE` (`source finished; caption state: {...}`).

## Server: `History`

- Stores accepted **finals only**, in arrival order (oldest -> newest).
- **Time bound:** pruned by the server's monotonic receive time, window
  `HISTORY_WINDOW_SECONDS = 90` (spec minimum 60 s; the margin covers a client
  evicted for overload plus its reconnect time). The server's own clock is used,
  not `source_ts`: retention is a server resource decision and must not depend
  on the source clock.
- **Count bound:** `HISTORY_MAX_ITEMS = 5000` finals, a hard memory cap under
  bursty sources. Trade-off: under an extreme burst (> 55 finals/s sustained)
  the cap, not the clock, limits history and recovery could cover < 60 s;
  `stats()["pruned_by_count"]` shows whether that ever happened.
- `since(last_segment, last_revision, seconds=None, max_bytes=None)`:
  finals the client has not reported, oldest first.
  - `seconds` optionally limits to finals received in the last `seconds`
    (`<= 0` returns nothing). How it applies to resuming clients is P2's decision.
  - `max_bytes` bounds the complete HISTORY message; if items do not fit, the
    **oldest** are dropped so the client keeps the most recent context. Pass the
    smaller of the client queue byte limit and the clients' 1 MiB message limit.
- `history_message(items)` builds the HISTORY JSON by joining pre-serialized
  items (no re-encoding; cheap during reconnect storms).
- In `handle_source`, a final is added to history **before** it is broadcast,
  with no `await` in between, so a client registering at that moment gets it
  either in HISTORY or live, never neither.

## Browser live view (`client/app.js`, section 1)

- `applyCaption(msg)` is the single entry point for every caption, live or
  recovered. It calls `acceptUpdate(msg)` (the same rule as the server) and,
  if accepted, `renderSegment(segment)`. Returns `accepted`, `stale`,
  `duplicate` or `invalid`.
- One `<p>` per segment, updated in place; lines stay in segment order even if
  an older segment arrives late (e.g. from HISTORY).
- Provisional: grey italic with a trailing ellipsis. Final: solid.
- Auto-scroll follows live text only while the reader is at the bottom; when
  they scroll up a "Jump to live" button appears.
- Bounded: 300 lines on screen, 2000 segments in memory, older ones forgotten
  with a watermark (never re-shown).

## Verification

- `python -m unittest discover tests`: `test_caption_state.py` (17),
  `test_history.py` (10), `test_caption_pipeline.py` (2 loopback tests through
  the real server), plus the existing delivery tests.
- Headless-browser check (two tabs, real server and `caption_source.py`):
  12 segments -> 12 lines, all final, provisional visible mid-stream; injected
  stale/duplicate/after-final updates rejected; late older segment inserted in
  order; scroll-up shows "Jump to live".
- T1 (`run_demo.py --clients 20 --duration 20 --runs 3`), one Linux container,
  not yet the team's Ubuntu VM: 0 duplicate, 0 stale, 100% final completeness,
  100% Normal Update Coverage, median P95 caption age 2.35 ms.

## Recovery integration status

- `handle_client` serves finalized HISTORY followed by `STATE.active_snapshot()`.
- Initial messages are admitted before registration with no intervening await;
  insufficient queue capacity closes the client before any context is sent.
- HISTORY is limited to the smaller of queue bytes and the client's 1 MiB limit.
  Older context may be omitted under that budget; chunked recovery is not implemented.
- Browser HISTORY items use `applyCaption(item)`; full browser recovery validation
  and semantic overload handling remain pending.
