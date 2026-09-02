import re
from datetime import datetime, timedelta, timezone


DAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _normalize_spaces(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _today_utc_date():
    return datetime.now(timezone.utc).date()


def _parse_date_fragment(value):
    if not value:
        return None
    text = value.lower().strip()
    today = _today_utc_date()
    if "today" in text:
        return today.isoformat()
    if "tomorrow" in text:
        return (today + timedelta(days=1)).isoformat()

    for day_name, idx in DAY_NAMES.items():
        if f"next {day_name}" in text or f"upcoming {day_name}" in text:
            today_idx = today.weekday()
            days_ahead = (idx - today_idx) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).isoformat()
        if day_name in text:
            today_idx = today.weekday()
            delta = (idx - today_idx) % 7
            if delta == 0:
                return today.isoformat()
            return (today + timedelta(days=delta)).isoformat()

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if iso_match:
        return iso_match.group(1)

    slash_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", value)
    if slash_match:
        try:
            parsed = datetime.strptime(slash_match.group(1), "%m/%d/%Y")
            return parsed.date().isoformat()
        except ValueError:
            try:
                parsed = datetime.strptime(slash_match.group(1), "%m/%d/%y")
                return parsed.date().isoformat()
            except ValueError:
                return None

    return None


def _parse_time_fragment(value):
    if not value:
        return None
    text = value.lower()
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    ampm = match.group(3)

    if ampm:
        if ampm.lower() == "pm" and hour < 12:
            hour += 12
        if ampm.lower() == "am" and hour == 12:
            hour = 0
    return f"{hour:02d}:{minute:02d}:00"


def _strip_known_prefixes(value):
    prefixes = [
        "schedule",
        "remind me",
        "add reminder",
        "reminder",
        "book",
        "note",
        "remember",
        "write down",
        "expense",
        "spent",
        "paid",
        "workout",
        "log workout",
        "task",
        "project",
        "todo",
    ]
    lowered = value.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            remainder = value[len(prefix):]
            return remainder.strip(" :.-")
    return value.strip(" :.-")


def _extract_title(body, fallback):
    cleaned = _normalize_spaces(body)
    if not cleaned:
        return fallback
    cleaned = re.sub(r"\b(?:on|at|for|with|from|to)\b.*$", "", cleaned, flags=re.I)
    title = cleaned.strip(" :.-")
    return title or fallback


def _extract_amount(text):
    matches = re.findall(r"\$?\s?(\d+(?:\.\d+)?)", text)
    if not matches:
        return None
    values = [float(match) for match in matches]
    return max(values) if values else None


def _extract_category(text):
    lowered = text.lower()
    if any(word in lowered for word in ["rent", "housing", "mortgage"]):
        return "housing"
    if any(word in lowered for word in ["food", "groceries", "lunch", "dinner", "coffee"]):
        return "food"
    if any(word in lowered for word in ["uber", "fuel", "gas", "transport", "train", "bus", "car"]):
        return "transport"
    if any(word in lowered for word in ["utilities", "electric", "water", "phone", "internet"]):
        return "utilities"
    if any(word in lowered for word in ["health", "pharmacy", "doctor", "gym"]):
        return "health"
    if any(word in lowered for word in ["salary", "paycheck", "income", "bonus"]):
        return "income"
    return "other"


def _extract_muscle_group(text):
    lowered = text.lower()
    for group in ["legs", "chest", "arms", "shoulders", "core", "back"]:
        if group in lowered:
            return group
    return "general"


def _split_instruction_blocks(message):
    raw_text = (message or "").strip()
    if not raw_text:
        return []

    normalized = raw_text.replace(";", "\n").replace("\r\n", "\n")
    candidate_blocks = re.split(r"\n+", normalized)
    blocks = []
    for candidate in candidate_blocks:
        fragments = re.split(r"(?<=[.!?])\s+", candidate.strip())
        for fragment in fragments:
            text = fragment.strip()
            if text:
                blocks.append(text)
    return blocks


def parse_message(message):
    raw_text = (message or "").strip()
    if not raw_text:
        return []

    items = []
    blocks = _split_instruction_blocks(raw_text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        label_match = re.match(r"^(schedule|note|expense|workout|task|project|todo|reminder|search|lookup)\s*:\s*(.*)$", block, flags=re.I)
        if label_match:
            block = f"{label_match.group(1)} {label_match.group(2)}".strip()

        lowered = block.lower()
        if any(keyword in lowered for keyword in ["schedule", "remind me", "reminder", "add reminder", "book"]):
            action = "schedule"
            body = _strip_known_prefixes(block)
            date = _parse_date_fragment(block)
            time = _parse_time_fragment(block)
            title = _extract_title(body, "New reminder")
            description = body if body else "Scheduled by Rick."
            items.append({
                "action": action,
                "title": title,
                "description": description,
                "date": date,
                "time": time,
                "raw": block,
            })
            continue

        if any(keyword in lowered for keyword in ["note", "remember", "write down"]):
            action = "note"
            body = _strip_known_prefixes(block)
            title = _extract_title(body, "Quick note")
            items.append({
                "action": action,
                "title": title,
                "description": body or title,
                "raw": block,
            })
            continue

        if any(keyword in lowered for keyword in ["expense", "spent", "paid", "bought", "purchase"]):
            action = "expense"
            body = _strip_known_prefixes(block)
            amount = _extract_amount(body) or 0
            category = _extract_category(body)
            title = _extract_title(body.replace(f"${amount}", ""), "Expense")
            items.append({
                "action": action,
                "title": title or "Expense",
                "description": body,
                "amount": amount,
                "category": category,
                "raw": block,
            })
            continue

        if any(keyword in lowered for keyword in ["workout", "gym", "log workout"]):
            action = "workout"
            body = _strip_known_prefixes(block)
            muscle_group = _extract_muscle_group(body)
            sets_match = re.search(r"(\d+\s*[xX]\s*\d+)", body)
            intensity_match = re.search(r"intensity\s*(\d+)", body, flags=re.I)
            exercise = re.sub(r"\b(?:legs|chest|arms|shoulders|core|back|workout|gym)\b.*?", "", body, flags=re.I).strip(" :.-")
            if not exercise:
                exercise = "Workout"
            items.append({
                "action": action,
                "title": f"{muscle_group.title()} workout",
                "description": body,
                "muscle_group": muscle_group,
                "exercise": exercise,
                "sets_reps": sets_match.group(1) if sets_match else "3 x 10",
                "intensity": int(intensity_match.group(1)) if intensity_match else 5,
                "date": _parse_date_fragment(block) or _today_utc_date().isoformat(),
                "raw": block,
            })
            continue

        if any(keyword in lowered for keyword in ["task", "project", "todo", "next up"]):
            action = "project"
            body = _strip_known_prefixes(block)
            title = _extract_title(body, "New task")
            items.append({
                "action": action,
                "title": title,
                "description": body or title,
                "type": "task",
                "raw": block,
            })
            continue

        if "search" in lowered or "lookup" in lowered:
            action = "search"
            items.append({
                "action": action,
                "query": block,
                "raw": block,
            })
            continue

        if block:
            items.append({
                "action": "note",
                "title": _extract_title(block, "Quick note"),
                "description": block,
                "raw": block,
            })

    return items
