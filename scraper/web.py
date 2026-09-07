r"""
The web app: your deadlines, with check and X buttons, on any device.

    python -m pip install fastapi uvicorn
    python web.py

Then open http://localhost:8000 -- or, from your phone on the same wifi,
http://<your-laptop-ip>:8000 (the address is printed on startup).
"""

import socket
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import store

HERE = Path(__file__).parent
STATIC = HERE / "static"

app = FastAPI(title="Brightspace deadlines")


class Decision(BaseModel):
    id: int
    status: str


def days_until(due):
    if not due:
        return None
    try:
        return (datetime.strptime(due, "%Y-%m-%d").date() - date.today()).days
    except ValueError:
        return None


def as_card(row):
    due = row["due_date"]
    left = days_until(due)
    when = ""
    if due:
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
            when = d.strftime("%a %-d %b") if sys.platform != "win32" else d.strftime("%a %d %b")
        except ValueError:
            when = due
    return {
        "id": row["id"],
        "title": row["title"],
        "course": row["course_name"],
        "courseShort": (row["course_name"] or "")[:8].strip(),
        "kind": row["kind"] or "other",
        "confidence": row["confidence"] or "high",
        "date": due,
        "when": when,
        "time": row["due_time"] or "",
        "days": left,
        "pending": bool(row["pending"]),
        "evidence": " ".join((row["source_excerpt"] or "").split()),
    }


@app.get("/api/cards")
def get_cards(status: str = "new", sessions: str = "all", all_sections: bool = False):
    db = store.connect()
    try:
        rows = store.cards(db, status, not all_sections)
        cards = [as_card(r) for r in rows]
    finally:
        db.close()

    if sessions == "hide":
        cards = [c for c in cards if c["kind"] != "session"]
    elif sessions == "only":
        cards = [c for c in cards if c["kind"] == "session"]

    courses = sorted({c["course"] for c in cards})
    return {"cards": cards, "courses": courses}


@app.get("/api/summary")
def get_summary():
    db = store.connect()
    try:
        return store.summary(db)
    finally:
        db.close()


@app.post("/api/decide")
def decide(decision: Decision):
    if decision.status not in ("accepted", "dismissed", "new"):
        raise HTTPException(400, "status must be accepted, dismissed, or new")
    db = store.connect()
    try:
        # Accepting is where a Google Calendar event will be created; the
        # decision is recorded either way so nothing is lost in the meantime.
        store.decide(db, decision.id, decision.status)
        db.commit()
    finally:
        db.close()
    return {"ok": True, "id": decision.id, "status": decision.status}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def my_address():
    """Best guess at the address a phone on the same wifi should use."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "your-laptop-ip"


if __name__ == "__main__":
    import uvicorn

    print("\n  On this computer:  http://localhost:8000")
    print(f"  On your phone:     http://{my_address()}:8000")
    print("  (same wifi; stop with Ctrl+C)\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
