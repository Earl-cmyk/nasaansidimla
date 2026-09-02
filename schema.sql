-- Reminders table
CREATE TABLE IF NOT EXISTS reminders (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    date DATE NOT NULL,
    time TIME NOT NULL,
    done BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Calendar event table (kept explicitly separate from reminders for time-based planning)
CREATE TABLE IF NOT EXISTS calendar_events (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weekly_schedules (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    start_date DATE NOT NULL,
    end_date DATE,
    weekday INTEGER NOT NULL DEFAULT 0 CHECK (weekday BETWEEN 0 AND 6),
    time TIME NOT NULL DEFAULT '09:00',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE weekly_schedules ADD COLUMN IF NOT EXISTS weekday INTEGER NOT NULL DEFAULT 0;
ALTER TABLE weekly_schedules ADD COLUMN IF NOT EXISTS time TIME NOT NULL DEFAULT '09:00';

CREATE TABLE IF NOT EXISTS weekly_schedule_exceptions (
    schedule_id BIGINT NOT NULL REFERENCES weekly_schedules(id) ON DELETE CASCADE,
    skipped_date DATE NOT NULL,
    PRIMARY KEY (schedule_id, skipped_date)
);

-- Notes table
CREATE TABLE IF NOT EXISTS notes (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    tag TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transactions table (Finance)
CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    category TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Workouts table (Fitness)
CREATE TABLE IF NOT EXISTS workouts (
    id BIGSERIAL PRIMARY KEY,
    muscle_group TEXT NOT NULL,
    exercise TEXT NOT NULL,
    sets_reps TEXT NOT NULL,
    intensity INTEGER,
    date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Muscle Progress table (Fitness)
CREATE TABLE IF NOT EXISTS muscle_progress (
    id BIGSERIAL PRIMARY KEY,
    muscle_group TEXT UNIQUE NOT NULL,
    progress_value NUMERIC(5,2) NOT NULL DEFAULT 0.0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Projects table (Workbench)
CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'document';

-- Files table (Works archive)
CREATE TABLE IF NOT EXISTS files (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    description TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Meals table (Fitness - calorie tracking)
CREATE TABLE IF NOT EXISTS meals (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    calories INTEGER NOT NULL,
    category TEXT,
    date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bmi_measurements (
    id BIGSERIAL PRIMARY KEY,
    weight NUMERIC(7,2) NOT NULL,
    height NUMERIC(7,2) NOT NULL,
    units TEXT NOT NULL DEFAULT 'metric',
    bmi NUMERIC(5,2) NOT NULL,
    measured_on DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS exercises (
    id BIGSERIAL PRIMARY KEY,
    muscle_group TEXT NOT NULL,
    name TEXT NOT NULL,
    instructions TEXT,
    UNIQUE (muscle_group, name)
);

CREATE TABLE IF NOT EXISTS financial_goals (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    target_amount NUMERIC(12,2) NOT NULL,
    current_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    due_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS debts (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    balance NUMERIC(12,2) NOT NULL,
    interest_rate NUMERIC(6,3) NOT NULL DEFAULT 0,
    minimum_payment NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity NUMERIC(12,4) NOT NULL CHECK (quantity > 0),
    price NUMERIC(12,4) NOT NULL CHECK (price > 0),
    traded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO exercises (muscle_group, name, instructions) VALUES
    ('chest', 'Push-up', 'Keep your body straight and lower your chest with control.'),
    ('chest', 'Bench press', 'Press the bar from chest level while keeping your feet grounded.'),
    ('arms', 'Biceps curl', 'Curl the weight without swinging your elbows forward.'),
    ('arms', 'Triceps dip', 'Lower your body with control and press through your palms.'),
    ('shoulders', 'Overhead press', 'Press weights overhead while keeping your ribs stacked.'),
    ('core', 'Plank', 'Brace your core and keep a straight line from shoulders to heels.'),
    ('legs', 'Bodyweight squat', 'Sit your hips back, keep your knees tracking over your toes.'),
    ('legs', 'Reverse lunge', 'Step back and lower both knees with control.'),
    ('back', 'Bent-over row', 'Pull toward your ribs while maintaining a neutral spine.')
ON CONFLICT (muscle_group, name) DO NOTHING;