"""Upwork messages integration — read the inbox via the browser session.

The messenger is a dynamic app, so (unlike the SSR'd job feed) it likely has a
real JSON API. `capture_messages()` records the network responses + any embedded
state from the messages page so we can build a parser against the real payloads.
Run once, then we write the importer.
"""
from __future__ import annotations

import re

from dotenv import load_dotenv

# Load .env so MSG_AUTO_SEND is read regardless of entry point (CLI / Streamlit).
load_dotenv()

MESSAGES_URL = "https://www.upwork.com/ab/messages/"


# --------------------------------------------------------------------------
# Pure ingest logic (unit-testable) — verified against the real API 2026-06.
# Endpoints: /api/v3/rooms/rooms/simplified (list),
#            /api/v3/rooms/rooms/{roomId}/stories/simplified (messages).
# A story is INBOUND iff its userId == room.targetUserId (the client).
# --------------------------------------------------------------------------

def story_id(story: dict) -> str:
    return str(story.get("storyId") or story.get("messageId") or "")


def is_importable(story: dict) -> bool:
    return not story.get("isSystemStory") and bool((story.get("message") or "").strip())


def classify_direction(story: dict, target_user_id) -> str:
    return "inbound" if str(story.get("userId")) == str(target_user_id) else "outbound"


def ingest_threads(threads, db, llm=None) -> dict:
    """Import conversation threads into client cards. Idempotent via storyId.

    threads: [{room_id, name, target_user_id, stories: [...]}]. Adds new
    messages (append-only, deduped), then drafts a reply for any card whose
    newest message is an unanswered inbound and ai_enabled.
    """
    import clients as C

    clients_touched = imported = skipped = 0
    for t in threads or []:
        client = C.get_or_create_client(
            db, name=t.get("name"), external_id=t.get("room_id")
        )
        clients_touched += 1
        stories = sorted(t.get("stories") or [], key=lambda s: s.get("created") or 0)
        for s in stories:
            if not is_importable(s):
                continue
            sid = story_id(s)
            if C.message_exists(db, sid):
                skipped += 1
                continue
            direction = classify_direction(s, t.get("target_user_id"))
            text = s["message"].strip()
            # Reconcile our own outbound: a message we sent from the UI was stored
            # locally without a storyId; when it returns from the API, backfill the
            # storyId on that row instead of inserting a duplicate (UI dedup).
            if direction == "outbound":
                local = C.find_unsynced_outbound(db, client.id, text)
                if local is not None:
                    local.external_id = sid
                    db.commit()
                    skipped += 1
                    continue
            C.add_message(db, client.id, direction, text, external_id=sid)
            imported += 1

        # Draft a reply if the latest message is an inbound without a draft.
        history = C.get_messages(db, client.id)
        last = history[-1] if history else None
        if last is not None and last.direction == "inbound" and client.ai_enabled and not last.ai_draft:
            try:
                last.ai_draft = C.generate_draft_text(client, db, llm=llm)
            except Exception as e:  # noqa: BLE001
                last.ai_draft = f"[Ошибка LLM: {e}]"
            if client.status == "NEW":
                client.status = "REPLIED"
            db.commit()

    return {"clients": clients_touched, "imported": imported, "skipped": skipped}


def fetch_inbox(max_rooms: int = 10, progress=None) -> list[dict]:
    """Read rooms + their stories from the Upwork messenger API via the session."""
    from browser import browser_page

    captured = {"rooms": None, "stories": {}}

    def make_handler():
        def on_response(resp):
            try:
                url = resp.url
                if "/rooms/rooms/simplified" in url and "/stories" not in url:
                    captured["rooms"] = resp.json()
                m = re.search(r"/rooms/rooms/(room_[0-9a-fA-F]+)/stories/simplified", url)
                if m:
                    captured["stories"][m.group(1)] = resp.json()
            except Exception:  # noqa: BLE001
                return
        return on_response

    def _wait_until(cond, page, rounds=12, step_ms=1500):
        for _ in range(rounds):
            if cond():
                return True
            page.wait_for_timeout(step_ms)
        return cond()

    threads: list[dict] = []
    with browser_page() as page:
        page.on("response", make_handler())
        page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        # The XHR may be served from cache on a revisit; reload to force it.
        if not _wait_until(lambda: captured["rooms"] is not None, page, rounds=8):
            page.reload(wait_until="domcontentloaded")
            _wait_until(lambda: captured["rooms"] is not None, page, rounds=10)

        rooms = ((captured["rooms"] or {}).get("rooms") or [])[:max_rooms]
        total = len(rooms)
        for idx, r in enumerate(rooms, 1):
            rid = r.get("roomId")
            if not rid:
                continue
            if progress:
                progress(idx, total, r.get("roomName") or rid)
            page.goto(f"{MESSAGES_URL}rooms/{rid}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            if not _wait_until(lambda: rid in captured["stories"], page, rounds=6):
                page.reload(wait_until="domcontentloaded")
                _wait_until(lambda: rid in captured["stories"], page, rounds=6)
            stories = ((captured["stories"].get(rid) or {}).get("stories") or [])
            threads.append({
                "room_id": rid,
                "name": r.get("roomName"),
                "job_uid": r.get("jobUid"),
                "target_user_id": r.get("targetUserId"),
                "stories": stories,
            })
        return threads


def msg_auto_send_enabled() -> bool:
    import os
    return os.getenv("MSG_AUTO_SEND", "0").strip().lower() in ("1", "true", "yes")


def send_message(room_id: str, text: str, dry_run: bool | None = None) -> dict:
    """Post a reply into a room via the messenger UI.

    Dry-run by default (fills the compose box, dumps msg_compose_debug, does NOT
    send). Real send requires MSG_AUTO_SEND=1 — sending is outward-facing.
    """
    if dry_run is None:
        dry_run = not msg_auto_send_enabled()
    if not room_id or not (text or "").strip():
        return {"ok": False, "sent": False, "dry_run": dry_run, "reason": "missing room_id/text"}

    from browser import browser_page

    try:
        with browser_page() as page:
            page.goto(f"{MESSAGES_URL}rooms/{room_id}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            return _compose_and_send(page, text, dry_run)
    except ImportError:
        return {"ok": False, "sent": False, "dry_run": dry_run, "reason": "playwright missing"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "sent": False, "dry_run": dry_run, "reason": f"{type(e).__name__}: {e}"}


def _dump_compose(page) -> None:
    from pathlib import Path

    data_dir = Path(__file__).resolve().parent / "data"
    try:
        (data_dir / "msg_compose_debug.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(data_dir / "msg_compose_debug.png"), full_page=True)
    except Exception:  # noqa: BLE001
        pass


def _compose_and_send(page, text: str, dry_run: bool) -> dict:
    """Fill the compose box and (unless dry-run) send. Browser-agnostic → testable.

    Handles the three real bugs we hit: leftover-draft (clear first), dropped
    spaces (type with delay, not insert_text), multi-send (newline = Shift+Enter,
    a bare Enter sends). Verifies the box cleared before claiming success.
    """
    import upwork_selectors as S

    box = page.locator(S.MSG_COMPOSE_INPUT).first
    if not box.count():
        _dump_compose(page)
        return {"ok": False, "sent": False, "dry_run": dry_run,
                "reason": "compose box not found (debug dumped)"}
    box.click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_timeout(200)
    for i, line in enumerate(text.split("\n")):
        if i > 0:
            page.keyboard.press("Shift+Enter")
        if line:
            page.keyboard.type(line, delay=35)
    page.wait_for_timeout(800)

    if dry_run:
        _dump_compose(page)
        return {"ok": True, "sent": False, "dry_run": True, "reason": "dry-run (filled; debug dumped)"}

    send = page.locator(S.MSG_SEND_BUTTON).first
    clicked = False
    for _ in range(12):
        try:
            if send.count() and send.is_enabled():
                send.click()
                clicked = True
                break
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(400)
    if not clicked:
        page.keyboard.press("Enter")
    page.wait_for_timeout(3500)

    remaining = ""
    try:
        if box.count():
            remaining = (box.inner_text(timeout=1500) or "").strip()
    except Exception:  # noqa: BLE001
        pass
    if text.strip()[:25] in remaining:
        _dump_compose(page)
        return {"ok": False, "sent": False, "dry_run": False,
                "reason": "send not confirmed (box still has text; debug dumped)"}
    return {"ok": True, "sent": True, "dry_run": False, "reason": "sent"}


def import_messages(max_rooms: int = 10, db=None, llm=None, progress=None) -> dict:
    """Fetch the inbox from Upwork and ingest into client cards."""
    from database import get_db_session

    owns = db is None
    db = db or get_db_session()
    try:
        threads = fetch_inbox(max_rooms=max_rooms, progress=progress)
        result = ingest_threads(threads, db, llm=llm)
        result["ok"] = True
        result["rooms"] = len(threads)
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}",
                "clients": 0, "imported": 0, "skipped": 0}
    finally:
        if owns:
            db.close()


def capture_messages() -> dict:
    """Open the inbox and dump candidate JSON responses + embedded state.

    Writes data/msg_api_*.json (network) and data/msg_state.json (window state).
    """
    import json as _json
    from pathlib import Path
    from browser import browser_page

    data_dir = Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    keywords = ("message", "room", "story", "stories", "thread", "conversation", "graphql", "/api/")

    with browser_page() as page:
        def on_response(resp):
            try:
                url = resp.url.lower()
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" not in ctype or not any(k in url for k in keywords):
                    return
                body = resp.json()
                idx = len(files)
                fp = data_dir / f"msg_api_{idx:02d}.json"
                fp.write_text(
                    _json.dumps({"url": resp.url, "body": body}, ensure_ascii=False, indent=2)[:3_000_000],
                    encoding="utf-8",
                )
                files.append(fp.name)
            except Exception:  # noqa: BLE001
                return

        page.on("response", on_response)
        page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(9000)  # let the inbox + first room load
        # Click the first conversation to trigger its messages to load.
        try:
            first = page.locator('[data-test*="room"], a[href*="/messages/rooms/"], [class*="room"]').first
            if first.count():
                first.click()
                page.wait_for_timeout(6000)
        except Exception:  # noqa: BLE001
            pass

        state = page.evaluate(
            "() => { const s = window.__NUXT__ && window.__NUXT__.state; "
            "return s ? Object.keys(s) : (window.__INITIAL_STATE__ ? Object.keys(window.__INITIAL_STATE__) : null); }"
        )
        (data_dir / "msg_state.json").write_text(
            _json.dumps({"state_keys": state}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, "reason": f"captured {len(files)} responses",
                "files": files, "state_keys": state}


if __name__ == "__main__":
    print(capture_messages())
