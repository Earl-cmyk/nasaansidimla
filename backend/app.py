import json
import os
import re
import urllib.parse
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
import psycopg

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

    matches = re.findall(r'<a rel="nofollow" class="result-link" href="(.*?)">(.*?)</a>', html)
    results = []
    for idx, (link, title_raw) in enumerate(matches[:5]):
        title = re.sub(r"<.*?>", "", title_raw).strip()
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


@app.route("/")
def index():
    return render_template("index.html")


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
                    "SELECT id, title, description, date, time, done FROM reminders WHERE date >= %s AND date <= %s ORDER BY date, time",
                    (start_day.isoformat(), end_day.isoformat()),
                )
                rows = cursor.fetchall()
    except Exception:
        rows = []

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
                "SELECT id, title, content, tag, created_at FROM notes ORDER BY created_at DESC"
            )
            notes = cursor.fetchall()
    return jsonify([
        {"id": row[0], "title": row[1], "content": row[2], "tag": row[3], "created_at": row[4].isoformat() if hasattr(row[4], "isoformat") else row[4]}
        for row in notes
    ])


@app.route("/api/notes", methods=["POST"])
def api_create_note():
    data = request.get_json() or {}
    title = (data.get("title") or "Untitled note").strip()
    content = data.get("content") or ""
    tag = data.get("tag") or "general"
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO notes (title, content, tag) VALUES (%s, %s, %s)",
                (title, content, tag),
            )
    return jsonify({"status": "ok"})


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


@app.route("/api/projects", methods=["GET"])
def api_get_projects():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, type, title, description, metadata, created_at FROM projects ORDER BY created_at DESC"
            )
            projects = cursor.fetchall()
    return jsonify([
        {"id": row[0], "type": row[1], "title": row[2], "description": row[3], "metadata": row[4], "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else row[5]}
        for row in projects
    ])


@app.route("/api/projects", methods=["POST"])
def api_create_project():
    data = request.get_json() or {}
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (type, title, description, metadata) VALUES (%s, %s, %s, %s)",
                (data.get("type"), data.get("title"), data.get("description"), data.get("metadata")),
            )
    return jsonify({"status": "ok"})


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
                        "SELECT id, filename, file_type, description, uploaded_at FROM files WHERE file_type = %s ORDER BY uploaded_at DESC",
                        (file_type,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, filename, file_type, description, uploaded_at FROM files ORDER BY uploaded_at DESC"
                    )
                rows = cursor.fetchall()
        return jsonify([
            {"id": row[0], "filename": row[1], "file_type": row[2], "description": row[3], "uploaded_at": row[4].isoformat() if hasattr(row[4], "isoformat") else row[4]}
            for row in rows
        ])
    except Exception:
        return jsonify([])


@app.route("/api/files", methods=["POST"])
def api_create_file():
    data = request.get_json() or {}
    filename = (data.get("filename") or "file").strip()
    file_type = (data.get("file_type") or "document").strip()
    description = (data.get("description") or "").strip()

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO files (filename, file_type, description) VALUES (%s, %s, %s)",
                (filename, file_type, description),
            )
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)