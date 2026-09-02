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