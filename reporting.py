"""Daily reporting — funnel snapshot + Telegram/Discord delivery (Day 10).

Build/format/save are pure and unit-tested; channel delivery is HTTP (mockable).
Golden template follows the ТЗ example (Spent connects / Replies / Interviews /
Hires / Revenue).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


def utc_today() -> str:
    """Single source of 'today' — UTC everywhere (report label == worker dedup key)."""
    return datetime.now(timezone.utc).date().isoformat()

from database import get_db_session, DailyReport
from analytics import (
    compute_metrics,
    analytics_by_bucket,
    analytics_by_niche,
    analytics_by_budget_range,
)


def _best(rows: list[dict]):
    """Top row that actually has a win (rows are pre-sorted best-first)."""
    winners = [r for r in rows if r["win"] > 0]
    return winners[0] if winners else None


def _worst(rows: list[dict], min_volume: int = 3):
    """Worst performer: an underperformer (volume but no wins) or lowest win-rate."""
    flagged = [r for r in rows if r["underperforming"]]
    if flagged:
        return sorted(flagged, key=lambda r: (-r["sent"], -r["lost"]))[0]
    vol = [r for r in rows if r["sent"] >= min_volume]
    if vol:
        return sorted(vol, key=lambda r: (r["win_rate"], -r["lost"]))[0]
    return None


def build_report(db, task_id: int | None = None) -> dict:
    m = compute_metrics(db, task_id)
    buckets = analytics_by_bucket(db, task_id)
    niches = analytics_by_niche(db, task_id)
    ranges = analytics_by_budget_range(db, task_id)
    conn = m["connects_spent"]
    m["revenue_per_connect"] = round(m["revenue"] / conn, 2) if conn else 0.0
    # Backward-compatible budget-type fields (fixed-price/hourly).
    ranked = sorted(buckets, key=lambda b: (-b["win"], -b["win_rate"]))
    m["best_bucket"] = ranked[0]["bucket"] if ranked and ranked[0]["win"] else None
    m["underperforming"] = [b["bucket"] for b in buckets if b["underperforming"]]
    # ТЗ report fields: best/worst niche + best budget range.
    best_n, worst_n = _best(niches), _worst(niches)
    best_r = _best(ranges) or (ranges[0] if ranges else None)
    m["best_niche"] = best_n["key"] if best_n else None
    m["worst_niche"] = (
        worst_n["key"] if worst_n and (not best_n or worst_n["key"] != best_n["key"]) else None
    )
    m["best_budget_range"] = best_r["key"] if best_r else None
    return m


def format_report_text(m: dict, day: str | None = None) -> str:
    """Daily report in the ТЗ format (Spent connects / … / Best niche / Worst niche / Best budget range)."""
    day = day or utc_today()
    dash = "—"
    lines = [
        f"📊 Upwork отчёт за {day}",
        f"Spent connects: {m['connects_spent']}",
        f"Replies: {m['replies']}",
        f"Interviews: {m['interviews']}",
        f"Hires: {m['hires']}",
        f"Revenue booked: ${m['revenue']:,}",
        f"Revenue / connect: ${m['revenue_per_connect']}",
        f"Best niche: {m.get('best_niche') or dash}",
        f"Worst niche: {m.get('worst_niche') or dash}",
        f"Best budget range: {m.get('best_budget_range') or dash}",
    ]
    try:
        from connects import read_balance

        bal = read_balance()
        if bal:
            lines.append(f"Connects balance: {bal['balance']}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def save_report(db, metrics: dict, text: str, day: str | None = None, task_id: int | None = None) -> DailyReport:
    """Upsert one report per (day, task) — invariant: one report per calendar day."""
    day = day or utc_today()
    rep = (
        db.query(DailyReport)
        .filter(DailyReport.day == day, DailyReport.task_id == task_id)
        .first()
    )
    if rep is None:
        rep = DailyReport(day=day, task_id=task_id)
        db.add(rep)
    rep.metrics_json = json.dumps(metrics, ensure_ascii=False)
    rep.text = text
    rep.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(rep)
    return rep


def send_telegram(text: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False, "telegram not configured"
    try:
        import httpx

        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text}, timeout=15,
        )
        return r.status_code == 200, f"telegram {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"telegram error: {e}"


def send_discord(text: str) -> tuple[bool, str]:
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return False, "discord not configured"
    try:
        import httpx

        r = httpx.post(url, json={"content": text}, timeout=15)
        return r.status_code in (200, 204), f"discord {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"discord error: {e}"


def send_report(task_id: int | None = None, db=None, day: str | None = None) -> dict:
    """Build → save → deliver the daily report. Telegram failure ≠ data loss."""
    owns = db is None
    db = db or get_db_session()
    try:
        metrics = build_report(db, task_id)
        text = format_report_text(metrics, day)
        save_report(db, metrics, text, day=day, task_id=task_id)
        tg_ok, tg_msg = send_telegram(text)
        dc_ok, dc_msg = send_discord(text)
        return {
            "ok": True, "text": text, "sent": tg_ok or dc_ok,
            "telegram": tg_msg, "discord": dc_msg,
        }
    finally:
        if owns:
            db.close()


def send_alert(text: str) -> dict:
    """Push an anomaly alert to the configured channels."""
    tg_ok, tg_msg = send_telegram(f"🚨 {text}")
    dc_ok, dc_msg = send_discord(f"🚨 {text}")
    return {"sent": tg_ok or dc_ok, "telegram": tg_msg, "discord": dc_msg}


def list_reports(db, limit: int = 30):
    return db.query(DailyReport).order_by(DailyReport.day.desc(), DailyReport.id.desc()).limit(limit).all()
