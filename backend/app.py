import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, after_this_request, jsonify, render_template, request, send_file
import bleach
import markdown
import psycopg
from bleach.css_sanitizer import CSSSanitizer

from backend.rick_nlu import parse_message

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "frontend" / "templates"),
    static_folder=str(BASE_DIR / "frontend" / "static"),
)


def _format_db_error(action, exc):
    return f"Rick tried to {action} but the database was unavailable: {exc}"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if "?pgbouncer=true" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?pgbouncer=true", 1)[0]
if "connect_timeout=" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL + "?connect_timeout=5" if "?" not in DATABASE_URL else DATABASE_URL + "&connect_timeout=5"
SCHEMA_PATH = BASE_DIR / "schema.sql"
MARKET_CACHE = {"timestamp": 0, "quotes": {}}
MARKET_CACHE_SECONDS = 300

EDITOR_TAGS = set(bleach.sanitizer.ALLOWED_TAGS).union({
    "h1", "h2", "h3", "h4", "p", "br", "hr", "pre", "code", "table", "thead", "tbody",
    "tr", "th", "td", "u", "mark", "span", "del", "ins", "ol", "ul", "li", "blockquote",
})
EDITOR_ATTRIBUTES = {
    "a": ["href", "title", "rel"],
    "span": ["style"],
    "mark": ["style"],
    "table": ["class"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
EDITOR_CSS = CSSSanitizer(allowed_css_properties={
    "background-color", "color", "font-family", "font-size", "font-weight", "text-align",
})


def render_note_content(source):
    """Render editor Markdown while retaining only safe formatting and links."""
    raw_content = source or ""
    rendered = markdown.markdown(
        raw_content,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    return bleach.clean(
        rendered,
        tags=EDITOR_TAGS,
        attributes=EDITOR_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        css_sanitizer=EDITOR_CSS,
        strip=True,
    )


def initialize_db():
    if not DATABASE_URL:
        warnings.warn("DATABASE_URL is not configured; skipping Postgres schema initialization.")
        return

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - environment-dependent startup guard
        warnings.warn(f"Postgres initialization skipped because the database is unavailable: {exc}")


def get_db():
    """Get a live Postgres connection backed by the Supabase connection string."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured. Add the Supabase/Postgres connection string to the .env file.")
    return psycopg.connect(DATABASE_URL)


initialize_db()


def fetch_duckduckgo_results(query):
    search_url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    request_obj = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urllib.request.urlopen(request_obj, timeout=8) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return [
            {
                "title": "DuckDuckGo unavailable",
                "url": "https://duckduckgo.com/",
                "snippet": "The live web search is unavailable right now, but the app shell is ready to wire into the real results when the network is available.",
            }
        ]

    matches = re.findall(r'<a[^>]+class="[^"]*result(?:__a|-link)[^"]*"[^>]+href="(.*?)"[^>]*>(.*?)</a>', html, flags=re.IGNORECASE | re.DOTALL)
    results = []
    for idx, (link, title_raw) in enumerate(matches[:5]):
        title = re.sub(r"<.*?>", "", title_raw).strip()
        title = re.sub(r"&(?:amp|lt|gt|quot|#39);", " ", title).strip()
        if not title:
            continue
        results.append({
            "title": title,
            "url": link,
            "snippet": f"Search result {idx + 1} from DuckDuckGo for '{query}'.",
        })

    if not results:
        results.append(
            {
                "title": f"Search for '{query}'",
                "url": "https://duckduckgo.com/?q=" + urllib.parse.quote(query),
                "snippet": "No parsed results came back from the live page, but this is the search target the app will use.",
            }
        )

    return results


def fetch_market_quotes(symbols):
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        return {symbol: {"symbol": symbol, "status": "unconfigured", "message": "Add ALPHA_VANTAGE_API_KEY to enable delayed quotes."} for symbol in symbols}
    now = time.monotonic()
    if now - MARKET_CACHE["timestamp"] < MARKET_CACHE_SECONDS and all(symbol in MARKET_CACHE["quotes"] for symbol in symbols):
        return {symbol: MARKET_CACHE["quotes"][symbol] for symbol in symbols}
    quotes = {}
    for symbol in symbols:
        url = "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=%s&apikey=%s" % (urllib.parse.quote(symbol), urllib.parse.quote(api_key))
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            quote = payload.get("Global Quote", {})
            if not quote:
                quotes[symbol] = {"symbol": symbol, "status": "unavailable", "message": payload.get("Note") or payload.get("Information") or "No quote returned."}
            else:
                quotes[symbol] = {"symbol": symbol, "status": "ok", "price": float(quote.get("05. price") or 0), "change": float(quote.get("09. change") or 0), "change_percent": quote.get("10. change percent", "")}
        except Exception:
            quotes[symbol] = {"symbol": symbol, "status": "unavailable", "message": "Quote provider unavailable."}
    MARKET_CACHE["timestamp"] = now
    MARKET_CACHE["quotes"].update(quotes)
    return quotes


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/dashboard")
def api_dashboard():
    empty = {"notes": 0, "upcoming": [], "workouts": 0, "calories": 0, "bmi": None, "balance": 0, "projects": 0, "files": 0}
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM notes")
                empty["notes"] = cursor.fetchone()[0]
                cursor.execute("SELECT title, date, time FROM reminders WHERE date >= CURRENT_DATE AND done = FALSE ORDER BY date, time LIMIT 5")
                empty["upcoming"] = [{"title": row[0], "date": str(row[1]), "time": str(row[2])} for row in cursor.fetchall()]
                cursor.execute("SELECT COUNT(*) FROM workouts WHERE date >= CURRENT_DATE - INTERVAL '30 days'")
                empty["workouts"] = cursor.fetchone()[0]
                cursor.execute("SELECT COALESCE(SUM(calories), 0) FROM meals WHERE date = CURRENT_DATE")
                empty["calories"] = cursor.fetchone()[0]
                cursor.execute("SELECT bmi, measured_on FROM bmi_measurements ORDER BY measured_on DESC, id DESC LIMIT 1")
                row = cursor.fetchone()
                empty["bmi"] = {"value": float(row[0]), "date": str(row[1])} if row else None
                cursor.execute("SELECT COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) - COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) FROM transactions")
                empty["balance"] = float(cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM projects")
                empty["projects"] = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM files")
                empty["files"] = cursor.fetchone()[0]
        return jsonify(empty)
    except Exception:
        return jsonify(empty)


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"query": "", "results": []})
    return jsonify({"query": query, "results": fetch_duckduckgo_results(query)})


@app.route("/assist")
def assist():
    return render_template("index.html", assistant_view=True)


@app.route("/assistant-message", methods=["POST"])
def assistant_message():
    message = request.form.get("message", "").strip()
    if not message:
        return jsonify({"reply": "Rick is listening. Send a command or question and I’ll turn it into structured action."})

    commands = parse_message(message)
    if not commands:
        return jsonify({"reply": f"Rick heard: {message}"})

    responses = []
    for command in commands:
        action = command.get("action")
        try:
            if action == "note":
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO notes (title, content, tag) VALUES (%s, %s, %s)",
                            (command.get("title") or "Quick note", command.get("description") or command.get("title") or "Created from Rick.", "assistant"),
                        )
                responses.append(f"Saved note: {command.get('title') or 'Quick note'}")
            elif action == "expense":
                amount = float(command.get("amount") or 0)
                category = command.get("category") or "other"
                title = command.get("title") or f"{category.title()} spend"
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO transactions (title, type, category, amount, date) VALUES (%s, %s, %s, %s, CURRENT_DATE)",
                            (title, "expense", category, amount),
                        )
                responses.append(f"Logged expense for {title}: ${amount:.2f}")
            elif action == "workout":
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO workouts (muscle_group, exercise, sets_reps, intensity, date) VALUES (%s, %s, %s, %s, CURRENT_DATE)",
                            (
                                command.get("muscle_group") or "general",
                                command.get("exercise") or "Workout",
                                command.get("sets_reps") or "3 x 10",
                                int(command.get("intensity") or 5),
                            ),
                        )
                responses.append(f"Logged {command.get('muscle_group') or 'general'} workout: {command.get('exercise') or 'Workout'}")
            elif action == "schedule":
                title = command.get("title") or "New reminder"
                description = command.get("description") or "Scheduled by Rick."
                date_value = command.get("date") or "CURRENT_DATE"
                time_value = command.get("time") or "00:00:00"
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO reminders (title, description, date, time) VALUES (%s, %s, %s, %s)",
                            (title, description, date_value, time_value),
                        )
                responses.append(f"Scheduled reminder: {title} on {date_value} at {time_value}")
            elif action == "project":
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO projects (type, title, description, metadata) VALUES (%s, %s, %s, %s)",
                            (command.get("type") or "task", command.get("title") or "New task", command.get("description") or "Task created by Rick.", {"source": "rick_nlu"}),
                        )
                responses.append(f"Created task: {command.get('title') or 'New task'}")
            else:
                responses.append(f"Rick heard: {command.get('raw') or message}")
        except Exception as exc:
            responses.append(_format_db_error(action or "process the request", exc))

    return jsonify({"reply": " | ".join(responses)})


@app.route("/notes")
def notes():
    return render_template("notes.html")


@app.route("/calendar")
def calendar():
    return render_template("calendar.html")


@app.route("/api/reminders", methods=["GET"])
def api_get_reminders():
    start_date = request.args.get("from")
    end_date = request.args.get("to")
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                if start_date and end_date:
                    cursor.execute(
                        "SELECT id, title, description, date, time, done FROM reminders WHERE date >= %s AND date <= %s ORDER BY date, time",
                        (start_date, end_date),
                    )
                else:
                    cursor.execute(
                        "SELECT id, title, description, date, time, done FROM reminders ORDER BY date, time LIMIT 50"
                    )
                rows = cursor.fetchall()
        return jsonify([
            {
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "date": str(row[3]),
                "time": str(row[4]),
                "done": bool(row[5]),
            }
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/reminders", methods=["POST"])
def api_create_reminder():
    data = request.get_json() or {}
    title = (data.get("title") or "New reminder").strip()
    reminder_date = data.get("date") or datetime.now(timezone.utc).date().isoformat()
    reminder_time = data.get("time") or "09:00"
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO reminders (title, description, date, time) VALUES (%s, %s, %s, %s) RETURNING id",
                    (title, data.get("description") or "", reminder_date, reminder_time),
                )
                reminder_id = cursor.fetchone()[0]
        return jsonify({"status": "ok", "id": reminder_id}), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/reminders/<int:reminder_id>", methods=["PUT", "PATCH", "DELETE"])
def api_manage_reminder(reminder_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                if request.method == "DELETE":
                    cursor.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
                else:
                    data = request.get_json() or {}
                    cursor.execute(
                        """UPDATE reminders SET title = %s, description = %s, date = %s, time = %s, done = %s
                           WHERE id = %s""",
                        ((data.get("title") or "New reminder").strip(), data.get("description") or "", data.get("date"), data.get("time") or "09:00", bool(data.get("done", False)), reminder_id),
                    )
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/weekly-schedules", methods=["GET", "POST"])
def api_weekly_schedules():
    if request.method == "POST":
        data = request.get_json() or {}
        try:
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO weekly_schedules (title, description, start_date, end_date, weekday, time) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        ((data.get("title") or "Weekly schedule").strip(), data.get("description") or "", data.get("start_date") or datetime.now(timezone.utc).date().isoformat(), data.get("end_date") or None, int(data.get("weekday", 0)), data.get("time") or "09:00"),
                    )
                    schedule_id = cursor.fetchone()[0]
            return jsonify({"status": "ok", "id": schedule_id}), 201
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, title, description, start_date, end_date, weekday, time, active FROM weekly_schedules ORDER BY created_at DESC")
                rows = cursor.fetchall()
            return jsonify([{"id": row[0], "title": row[1], "description": row[2], "start_date": str(row[3]), "end_date": str(row[4]) if row[4] else None, "weekday": row[5], "time": str(row[6]), "active": row[7]} for row in rows])
    except Exception:
        return jsonify([])


@app.route("/api/weekly-schedules/<int:schedule_id>/stop", methods=["POST"])
def api_stop_weekly_schedule(schedule_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE weekly_schedules SET active = FALSE, end_date = COALESCE(end_date, CURRENT_DATE) WHERE id = %s", (schedule_id,))
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/weekly-schedules/<int:schedule_id>/skip", methods=["POST"])
def api_skip_weekly_schedule(schedule_id):
    skipped_date = (request.get_json() or {}).get("date")
    if not skipped_date:
        return jsonify({"status": "error", "message": "A date is required."}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO weekly_schedule_exceptions (schedule_id, skipped_date) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (schedule_id, skipped_date),
                )
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/calendar", methods=["GET"])
def api_get_calendar():
    view = request.args.get("view", "month").lower()
    selected_date = request.args.get("date") or datetime.now(timezone.utc).date().isoformat()

    try:
        selected = date.fromisoformat(selected_date)
    except ValueError:
        selected = datetime.now(timezone.utc).date()

    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                if view == "day":
                    start_day = selected
                    end_day = selected
                elif view == "week":
                    start_day = selected - timedelta(days=selected.weekday())
                    end_day = start_day + timedelta(days=6)
                else:
                    first_day = selected.replace(day=1)
                    start_day = first_day - timedelta(days=first_day.weekday())
                    month_end = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
                    end_day = month_end - timedelta(days=1)
                    end_day = end_day + timedelta(days=(6 - end_day.weekday()))

                cursor.execute(
                    "SELECT id, title, description, date, time, done, NULL::BIGINT AS schedule_id FROM reminders WHERE date >= %s AND date <= %s ORDER BY date, time",
                    (start_day.isoformat(), end_day.isoformat()),
                )
                rows = cursor.fetchall()
                cursor.execute(
                    """SELECT id, title, description, start_date, end_date, weekday, time
                       FROM weekly_schedules
                       WHERE active = TRUE AND start_date <= %s AND (end_date IS NULL OR end_date >= %s)""",
                    (end_day.isoformat(), start_day.isoformat()),
                )
                schedules = cursor.fetchall()
                cursor.execute(
                    "SELECT schedule_id, skipped_date FROM weekly_schedule_exceptions WHERE skipped_date >= %s AND skipped_date <= %s",
                    (start_day.isoformat(), end_day.isoformat()),
                )
                exceptions = {(row[0], str(row[1])) for row in cursor.fetchall()}
    except Exception:
        rows = []
        schedules = []
        exceptions = set()

    for schedule in schedules:
        schedule_id, title, description, start_date, end_date, weekday, schedule_time = schedule
        current = max(start_day, start_date)
        while current <= end_day:
            if current.weekday() == weekday and (end_date is None or current <= end_date) and (schedule_id, current.isoformat()) not in exceptions:
                rows.append((schedule_id, title, description, current, schedule_time, False, schedule_id))
            current += timedelta(days=1)
    rows.sort(key=lambda row: (str(row[3]), str(row[4])))

    reminder_map = {}
    for row in rows:
        reminder_date = str(row[3])
        reminder_map.setdefault(reminder_date, []).append({
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "date": str(row[3]),
            "time": str(row[4]),
            "done": bool(row[5]),
            "schedule_id": row[6],
        })

    if view == "day":
        dates = [selected]
    elif view == "week":
        dates = [selected - timedelta(days=selected.weekday()) + timedelta(days=offset) for offset in range(7)]
    else:
        first_day = selected.replace(day=1)
        start_grid = first_day - timedelta(days=first_day.weekday())
        dates = [start_grid + timedelta(days=offset) for offset in range(42)]

    return jsonify({
        "view": view,
        "date": selected.isoformat(),
        "days": [
            {
                "date": current.isoformat(),
                "events": reminder_map.get(current.isoformat(), []),
                "is_current_month": view == "month" and current.month == selected.month,
                "is_selected": current == selected,
            }
            for current in dates
        ],
    })


@app.route("/fitness")
def fitness():
    return render_template("fitness.html")


@app.route("/workbench")
def workbench():
    return render_template("workbench.html")


@app.route("/works")
def works():
    return render_template("works.html")


@app.route("/wallets")
def wallets():
    return render_template("wallets.html")


# ===== API Routes for data management =====

@app.route("/api/notes", methods=["GET"])
def api_get_notes():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, title, content, tag, created_at, updated_at FROM notes ORDER BY updated_at DESC"
            )
            notes = cursor.fetchall()
    return jsonify([
        {
            "id": row[0],
            "title": row[1],
            "content": row[2] or "",
            "html": render_note_content(row[2]),
            "tag": row[3],
            "created_at": row[4].isoformat() if hasattr(row[4], "isoformat") else row[4],
            "updated_at": row[5].isoformat() if hasattr(row[5], "isoformat") else row[5],
        }
        for row in notes
    ])


@app.route("/api/notes", methods=["POST"])
def api_create_note():
    data = request.get_json() or {}
    title = (data.get("title") or "Untitled note").strip()
    content = data.get("content") if data.get("content") is not None else ""
    tag = (data.get("tag") or "general").strip()
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO notes (title, content, tag) VALUES (%s, %s, %s)",
                (title, content, tag),
            )
    return jsonify({"status": "ok"})


@app.route("/api/notes/<int:note_id>", methods=["PUT", "PATCH"])
def api_update_note(note_id):
    data = request.get_json() or {}
    title = (data.get("title") or "Untitled note").strip()
    content = data.get("content") if data.get("content") is not None else ""
    tag = (data.get("tag") or "general").strip()
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE notes
                       SET title = %s, content = %s, tag = %s, updated_at = NOW()
                       WHERE id = %s
                       RETURNING id, title, content, tag, created_at, updated_at""",
                    (title, content, tag, note_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return jsonify({"status": "error", "message": "Note not found"}), 404
        return jsonify({
            "status": "ok", "id": row[0], "title": row[1], "content": row[2],
            "html": render_note_content(row[2]), "tag": row[3],
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/notes/search", methods=["GET"])
def api_search_notes():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT id, title, content, tag, updated_at
                       FROM notes
                       WHERE title ILIKE %s OR tag ILIKE %s OR content ILIKE %s
                       ORDER BY updated_at DESC LIMIT 10""",
                    (f"%{query}%", f"%{query}%", f"%{query}%"),
                )
                rows = cursor.fetchall()
        return jsonify([
            {"id": row[0], "title": row[1], "content": row[2] or "", "html": render_note_content(row[2]), "tag": row[3]}
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def api_delete_note(note_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM notes WHERE id = %s", (note_id,))
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/transactions", methods=["GET"])
def api_get_transactions():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, title, type, category, amount, date FROM transactions ORDER BY date DESC"
            )
            transactions = cursor.fetchall()
    return jsonify([
        {"id": row[0], "title": row[1], "type": row[2], "category": row[3], "amount": float(row[4]), "date": str(row[5])}
        for row in transactions
    ])


@app.route("/api/transactions", methods=["POST"])
def api_create_transaction():
    data = request.get_json() or {}
    title = (data.get("title") or "Transaction").strip()
    type_name = (data.get("type") or "expense").strip()
    category = (data.get("category") or "other").strip()
    amount = float(data.get("amount") or 0)
    transaction_date = data.get("date") or datetime.now(timezone.utc).date().isoformat()

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO transactions (title, type, category, amount, date) VALUES (%s, %s, %s, %s, %s)",
                (title, type_name, category, amount, transaction_date),
            )
    return jsonify({"status": "ok"})


@app.route("/api/goals", methods=["GET", "POST"])
def api_goals():
    if request.method == "POST":
        data = request.get_json() or {}
        try:
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("INSERT INTO financial_goals (title, target_amount, current_amount, due_date) VALUES (%s, %s, %s, %s) RETURNING id", ((data.get("title") or "Goal").strip(), float(data.get("target_amount") or 0), float(data.get("current_amount") or 0), data.get("due_date") or None))
                    goal_id = cursor.fetchone()[0]
            return jsonify({"status": "ok", "id": goal_id}), 201
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, title, target_amount, current_amount, due_date FROM financial_goals ORDER BY created_at DESC")
                rows = cursor.fetchall()
        return jsonify([{"id": row[0], "title": row[1], "target_amount": float(row[2]), "current_amount": float(row[3]), "due_date": str(row[4]) if row[4] else None} for row in rows])
    except Exception:
        return jsonify([])


@app.route("/api/debts", methods=["GET", "POST"])
def api_debts():
    if request.method == "POST":
        data = request.get_json() or {}
        try:
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("INSERT INTO debts (title, balance, interest_rate, minimum_payment) VALUES (%s, %s, %s, %s) RETURNING id", ((data.get("title") or "Debt").strip(), float(data.get("balance") or 0), float(data.get("interest_rate") or 0), float(data.get("minimum_payment") or 0)))
                    debt_id = cursor.fetchone()[0]
            return jsonify({"status": "ok", "id": debt_id}), 201
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, title, balance, interest_rate, minimum_payment FROM debts ORDER BY created_at DESC")
                rows = cursor.fetchall()
        return jsonify([{"id": row[0], "title": row[1], "balance": float(row[2]), "interest_rate": float(row[3]), "minimum_payment": float(row[4])} for row in rows])
    except Exception:
        return jsonify([])


@app.route("/api/paper-trades", methods=["GET", "POST"])
def api_paper_trades():
    if request.method == "POST":
        data = request.get_json() or {}
        symbol = (data.get("symbol") or "SPY").strip().upper()
        side = (data.get("side") or "buy").strip().lower()
        quantity = float(data.get("quantity") or 0)
        price = float(data.get("price") or 0)
        if side not in {"buy", "sell"} or quantity <= 0 or price <= 0:
            return jsonify({"status": "error", "message": "Use a positive quantity and price."}), 400
        try:
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("INSERT INTO paper_trades (symbol, side, quantity, price) VALUES (%s, %s, %s, %s) RETURNING id", (symbol, side, quantity, price))
                    trade_id = cursor.fetchone()[0]
            return jsonify({"status": "ok", "id": trade_id}), 201
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, symbol, side, quantity, price, traded_at FROM paper_trades ORDER BY traded_at DESC LIMIT 50")
                rows = cursor.fetchall()
        return jsonify([{"id": row[0], "symbol": row[1], "side": row[2], "quantity": float(row[3]), "price": float(row[4]), "traded_at": row[5].isoformat()} for row in rows])
    except Exception:
        return jsonify([])


@app.route("/api/market/quotes")
def api_market_quotes():
    requested = [item.strip().upper() for item in request.args.get("symbols", "SPY,VOO").split(",")]
    symbols = [symbol for symbol in requested if re.fullmatch(r"[A-Z]{1,5}", symbol)][:10] or ["SPY", "VOO"]
    return jsonify({"quotes": fetch_market_quotes(symbols), "cached_for_seconds": MARKET_CACHE_SECONDS})


@app.route("/api/converter/youtube", methods=["POST"])
def api_convert_youtube():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    output_format = (data.get("format") or "mp3").strip().lower()
    quality = str(data.get("quality") or ("192" if output_format == "mp3" else "720"))
    allowed_hosts = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in allowed_hosts:
        return jsonify({"status": "error", "message": "Only YouTube URLs are supported."}), 400
    if output_format not in {"mp3", "mp4"} or (output_format == "mp3" and quality not in {"128", "192", "320"}) or (output_format == "mp4" and quality not in {"720", "1080"}):
        return jsonify({"status": "error", "message": "Unsupported format or quality."}), 400
    try:
        import yt_dlp
        with tempfile.TemporaryDirectory(prefix="earl-convert-") as temp_dir:
            inspect_options = {"quiet": True, "noplaylist": True, "skip_download": True}
            with yt_dlp.YoutubeDL(inspect_options) as downloader:
                info = downloader.extract_info(url, download=False)
            if (info.get("duration") or 0) > 15 * 60:
                return jsonify({"status": "error", "message": "Videos must be 15 minutes or shorter."}), 413
            if (info.get("filesize") or info.get("filesize_approx") or 0) > 250 * 1024 * 1024:
                return jsonify({"status": "error", "message": "Source files must be 250 MB or smaller."}), 413
            output_template = os.path.join(temp_dir, "converted.%(ext)s")
            options = {"quiet": True, "noplaylist": True, "outtmpl": output_template, "max_filesize": 250 * 1024 * 1024}
            if output_format == "mp3":
                options.update({"format": "bestaudio/best", "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}]})
            else:
                options["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([url])
            candidates = [path for path in Path(temp_dir).glob("converted.*") if path.is_file()]
            if not candidates or candidates[0].stat().st_size > 250 * 1024 * 1024:
                return jsonify({"status": "error", "message": "Converted output exceeded 250 MB."}), 413
            output_path = candidates[0]
            from io import BytesIO
            output_bytes = output_path.read_bytes()
            @after_this_request
            def cleanup(response):
                shutil.rmtree(temp_dir, ignore_errors=True)
                return response
            return send_file(BytesIO(output_bytes), as_attachment=True, download_name=f"{Path(info.get('title') or 'download').stem}.{output_format}")
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Conversion unavailable: {exc}"}), 503


@app.route("/api/workouts", methods=["GET"])
def api_get_workouts():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, muscle_group, exercise, sets_reps, intensity, date FROM workouts ORDER BY date DESC"
            )
            workouts = cursor.fetchall()
    return jsonify([
        {"id": row[0], "muscle_group": row[1], "exercise": row[2], "sets_reps": row[3], "intensity": row[4], "date": str(row[5])}
        for row in workouts
    ])


@app.route("/api/workouts", methods=["POST"])
def api_create_workout():
    data = request.get_json() or {}
    muscle_group = data.get("muscle_group") or "general"
    exercise = data.get("exercise") or "Workout"
    sets_reps = data.get("sets_reps") or "3 x 10"
    intensity = data.get("intensity") or 5
    workout_date = data.get("date") or "2026-01-01"

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO workouts (muscle_group, exercise, sets_reps, intensity, date) VALUES (%s, %s, %s, %s, %s)",
                (muscle_group, exercise, sets_reps, int(intensity), workout_date),
            )
            cursor.execute(
                """
                INSERT INTO muscle_progress (muscle_group, progress_value)
                VALUES (%s, %s)
                ON CONFLICT (muscle_group) DO UPDATE SET
                    progress_value = LEAST(10.0, muscle_progress.progress_value + EXCLUDED.progress_value),
                    last_updated = NOW()
                """,
                (muscle_group, float(intensity)),
            )
    return jsonify({"status": "ok"})


@app.route("/api/muscle-progress", methods=["GET"])
def api_get_muscle_progress():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT muscle_group, progress_value FROM muscle_progress")
            rows = cursor.fetchall()
    return jsonify({row[0]: float(row[1]) for row in rows})


@app.route("/api/exercises", methods=["GET"])
def api_get_exercises():
    muscle_group = request.args.get("muscle_group", "").strip().lower()
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                if muscle_group:
                    cursor.execute(
                        "SELECT id, muscle_group, name, instructions FROM exercises WHERE muscle_group = %s ORDER BY name",
                        (muscle_group,),
                    )
                else:
                    cursor.execute("SELECT id, muscle_group, name, instructions FROM exercises ORDER BY muscle_group, name")
                rows = cursor.fetchall()
        return jsonify([{"id": row[0], "muscle_group": row[1], "name": row[2], "instructions": row[3] or ""} for row in rows])
    except Exception:
        return jsonify([])


@app.route("/api/bmi", methods=["GET", "POST"])
def api_bmi():
    if request.method == "POST":
        data = request.get_json() or {}
        weight = float(data.get("weight") or 0)
        height = float(data.get("height") or 0)
        units = (data.get("units") or "metric").strip().lower()
        if weight <= 0 or height <= 0 or units not in {"metric", "imperial"}:
            return jsonify({"status": "error", "message": "Weight and height must be positive."}), 400
        bmi = (weight / ((height / 100) ** 2)) if units == "metric" else (703 * weight / (height ** 2))
        measured_on = data.get("measured_on") or datetime.now(timezone.utc).date().isoformat()
        try:
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO bmi_measurements (weight, height, units, bmi, measured_on) VALUES (%s, %s, %s, %s, %s) RETURNING id, measured_on",
                        (weight, height, units, round(bmi, 2), measured_on),
                    )
                    row = cursor.fetchone()
            return jsonify({"status": "ok", "id": row[0], "bmi": round(bmi, 2), "measured_on": str(row[1])})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, weight, height, units, bmi, measured_on FROM bmi_measurements ORDER BY measured_on DESC, id DESC LIMIT 50")
                rows = cursor.fetchall()
        return jsonify([
            {"id": row[0], "weight": float(row[1]), "height": float(row[2]), "units": row[3], "bmi": float(row[4]), "measured_on": str(row[5])}
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/projects", methods=["GET"])
def api_get_projects():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, type, title, description, metadata, content, mode, created_at FROM projects ORDER BY created_at DESC"
            )
            projects = cursor.fetchall()
    return jsonify([
        {"id": row[0], "type": row[1], "title": row[2], "description": row[3], "metadata": row[4], "content": row[5] or row[3] or "", "mode": row[6] or "document", "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7]}
        for row in projects
    ])


@app.route("/api/projects", methods=["POST"])
def api_create_project():
    data = request.get_json() or {}
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (type, title, description, metadata, content, mode) VALUES (%s, %s, %s, %s, %s, %s)",
                (data.get("type") or data.get("mode") or "document", data.get("title") or "Untitled project", data.get("description") or "", data.get("metadata") or {}, data.get("content") or data.get("description") or "", data.get("mode") or "document"),
            )
    return jsonify({"status": "ok"})


@app.route("/api/projects/<int:project_id>", methods=["PUT", "PATCH"])
def api_update_project(project_id):
    data = request.get_json() or {}
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE projects SET title = %s, content = %s, mode = %s, description = %s, updated_at = NOW() WHERE id = %s RETURNING id",
                    ((data.get("title") or "Untitled project").strip(), data.get("content") or "", data.get("mode") or "document", data.get("description") or "", project_id),
                )
                row = cursor.fetchone()
        if not row:
            return jsonify({"status": "error", "message": "Project not found"}), 404
        return jsonify({"status": "ok", "id": row[0]})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/meals", methods=["GET"])
def api_get_meals():
    date_filter = request.args.get("date")
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                if date_filter:
                    cursor.execute(
                        "SELECT id, title, calories, category, date FROM meals WHERE date = %s ORDER BY created_at DESC",
                        (date_filter,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, title, calories, category, date FROM meals ORDER BY date DESC, created_at DESC LIMIT 100"
                    )
                rows = cursor.fetchall()
        return jsonify([
            {"id": row[0], "title": row[1], "calories": row[2], "category": row[3], "date": str(row[4])}
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/meals", methods=["POST"])
def api_create_meal():
    data = request.get_json() or {}
    title = (data.get("title") or "Meal").strip()
    calories = int(data.get("calories") or 0)
    category = (data.get("category") or "other").strip()
    meal_date = data.get("date") or datetime.now(timezone.utc).date().isoformat()

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO meals (title, calories, category, date) VALUES (%s, %s, %s, %s)",
                (title, calories, category, meal_date),
            )
    return jsonify({"status": "ok"})


@app.route("/api/files", methods=["GET"])
def api_get_files():
    file_type = request.args.get("type")
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                if file_type:
                    cursor.execute(
                        "SELECT id, filename, file_type, description, mime_type, file_size, content IS NOT NULL, uploaded_at FROM files WHERE file_type = %s ORDER BY uploaded_at DESC",
                        (file_type,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, filename, file_type, description, mime_type, file_size, content IS NOT NULL, uploaded_at FROM files ORDER BY uploaded_at DESC"
                    )
                rows = cursor.fetchall()
        return jsonify([
            {"id": row[0], "filename": row[1], "file_type": row[2], "description": row[3], "mime_type": row[4], "file_size": row[5], "preview_url": f"/api/files/{row[0]}/preview" if row[6] and row[2] == "image" else None, "uploaded_at": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7]}
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/files", methods=["POST"])
def api_create_file():
    if request.files.get("file"):
        upload = request.files["file"]
        filename = (upload.filename or "image").strip()
        file_type = (request.form.get("file_type") or "image").strip()
        description = (request.form.get("description") or "").strip()
        mime_type = (upload.mimetype or "").lower()
        if file_type != "image" or not mime_type.startswith("image/"):
            return jsonify({"status": "error", "message": "Only image files can be uploaded here."}), 415
        content = upload.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            return jsonify({"status": "error", "message": "Images must be 10 MB or smaller."}), 413
    else:
        data = request.get_json() or {}
        filename = (data.get("filename") or "file").strip()
        file_type = (data.get("file_type") or "document").strip()
        description = (data.get("description") or "").strip()
        mime_type = None
        content = None

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO files (filename, file_type, description, mime_type, file_size, content) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (filename, file_type, description, mime_type, len(content) if content else None, content),
            )
            file_id = cursor.fetchone()[0]
    return jsonify({"status": "ok", "id": file_id, "preview_url": f"/api/files/{file_id}/preview" if file_type == "image" else None}), 201


@app.route("/api/files/<int:file_id>/preview")
def api_preview_file(file_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT filename, mime_type, content FROM files WHERE id = %s AND file_type = 'image'", (file_id,))
                row = cursor.fetchone()
        if not row or not row[2]:
            return jsonify({"status": "error", "message": "Image not found"}), 404
        from io import BytesIO
        return send_file(BytesIO(bytes(row[2])), mimetype=row[1] or "application/octet-stream", download_name=row[0], as_attachment=False, conditional=True)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/files/<int:file_id>/download")
def api_download_file(file_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT filename, description FROM files WHERE id = %s", (file_id,))
                row = cursor.fetchone()
        if not row:
            return jsonify({"status": "error", "message": "File not found"}), 404
        from io import BytesIO
        return send_file(BytesIO((row[1] or "").encode("utf-8")), as_attachment=True, download_name=row[0], mimetype="application/octet-stream")
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


TRACK_MAX_BYTES = 250 * 1024 * 1024
TRACK_MIME_TYPES = {
    "audio/aac", "audio/flac", "audio/m4a", "audio/mp4", "audio/mpeg",
    "audio/ogg", "audio/wav", "audio/webm", "audio/x-flac", "audio/x-m4a", "audio/x-wav",
}

TRACK_EXTENSION_MIME_TYPES = {
    ".aac": "audio/aac", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg", ".oga": "audio/ogg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".weba": "audio/webm",
}


@app.route("/api/tracks", methods=["GET"])
def api_get_tracks():
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, title, filename, mime_type, file_size, duration_seconds, description, uploaded_at "
                    "FROM tracks ORDER BY uploaded_at DESC"
                )
                rows = cursor.fetchall()
        return jsonify([
            {
                "id": row[0], "title": row[1], "filename": row[2], "mime_type": row[3],
                "file_size": row[4], "duration": float(row[5]) if row[5] is not None else None,
                "description": row[6] or "", "uploaded_at": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
            }
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/tracks", methods=["POST"])
def api_create_track():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"status": "error", "message": "Choose an audio file to upload."}), 400

    mime_type = (upload.mimetype or "").lower()
    if mime_type not in TRACK_MIME_TYPES:
        mime_type = TRACK_EXTENSION_MIME_TYPES.get(Path(upload.filename).suffix.lower(), "")
    if mime_type not in TRACK_MIME_TYPES:
        return jsonify({"status": "error", "message": "Only common audio file types are supported."}), 415

    content = upload.read(TRACK_MAX_BYTES + 1)
    if len(content) > TRACK_MAX_BYTES:
        return jsonify({"status": "error", "message": "Tracks must be 250 MB or smaller."}), 413

    title = (request.form.get("title") or Path(upload.filename).stem or "Untitled track").strip()
    description = (request.form.get("description") or "").strip()
    duration = request.form.get("duration") or None
    try:
        duration = float(duration) if duration is not None else None
    except ValueError:
        duration = None

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tracks (title, filename, mime_type, file_size, duration_seconds, description, content) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (title, upload.filename, mime_type, len(content), duration, description, content),
            )
            track_id = cursor.fetchone()[0]
    return jsonify({"status": "ok", "id": track_id}), 201


@app.route("/api/tracks/<int:track_id>/stream")
def api_stream_track(track_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT filename, mime_type, content FROM tracks WHERE id = %s", (track_id,))
                row = cursor.fetchone()
        if not row:
            return jsonify({"status": "error", "message": "Track not found"}), 404
        from io import BytesIO
        return send_file(BytesIO(bytes(row[2])), mimetype=row[1], download_name=row[0], as_attachment=False, conditional=True)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/tracks/<int:track_id>", methods=["DELETE"])
def api_delete_track(track_id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM tracks WHERE id = %s RETURNING id", (track_id,))
            deleted = cursor.fetchone()
    if not deleted:
        return jsonify({"status": "error", "message": "Track not found"}), 404
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)