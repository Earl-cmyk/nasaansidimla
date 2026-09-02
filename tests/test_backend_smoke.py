import importlib.util
import inspect
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("backend_app", ROOT / "backend" / "app.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_app_uses_postgres_configuration():
    assert hasattr(module, "DATABASE_URL")
    assert module.DATABASE_URL
    assert "postgres" in module.DATABASE_URL.lower()
    assert hasattr(module, "get_db")
    source = inspect.getsource(module.get_db)
    assert "psycopg.connect" in source


def test_schema_is_postgres_safe():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    assert "AUTOINCREMENT" not in schema.upper()
    assert "SERIAL" in schema.upper() or "IDENTITY" in schema.upper()


def test_rick_parser_detects_schedule_and_multiple_commands():
    from backend.app import parse_message

    schedule = parse_message("Schedule standup tomorrow at 9am with Alex.")
    assert schedule[0]["action"] == "schedule"
    assert schedule[0]["title"]
    assert schedule[0]["date"] or "9am" in str(schedule[0].get("time") or "")

    multi = parse_message("Note launch plan for Friday. Expense groceries $42.50 food.")
    assert len(multi) >= 2
    assert {item["action"] for item in multi} >= {"note", "expense"}


def test_rick_parser_handles_labelled_batch_commands():
    from backend.app import parse_message

    batch = parse_message(
        "schedule: team standup tomorrow at 9am.\n"
        "note: launch plan for Friday.\n"
        "expense: groceries $42.50 food."
    )
    actions = {item["action"] for item in batch}
    assert {"schedule", "note", "expense"}.issubset(actions)
    assert any(item["title"] for item in batch if item["action"] == "schedule")
