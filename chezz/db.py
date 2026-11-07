import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id             INTEGER PRIMARY KEY,
    dedupe_key     TEXT NOT NULL UNIQUE,
    pgn            TEXT NOT NULL,
    link           TEXT,
    event          TEXT,
    site           TEXT,
    date           TEXT,
    white          TEXT,
    black          TEXT,
    white_elo      INTEGER,
    black_elo      INTEGER,
    result         TEXT,
    user_color     TEXT,
    user_result    TEXT,
    eco            TEXT,
    opening        TEXT,
    opening_family TEXT,
    time_control   TEXT,
    time_class     TEXT,
    termination    TEXT,
    ply_count      INTEGER,
    starred        INTEGER NOT NULL DEFAULT 0,
    notes          TEXT NOT NULL DEFAULT '',
    imported_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS games_date ON games(date);
CREATE INDEX IF NOT EXISTS games_user ON games(user_color, user_result);
CREATE INDEX IF NOT EXISTS games_time_class ON games(time_class);
CREATE INDEX IF NOT EXISTS games_opening ON games(opening_family);
CREATE INDEX IF NOT EXISTS games_starred ON games(starred);

CREATE TABLE IF NOT EXISTS game_tags (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (game_id, tag)
);
CREATE INDEX IF NOT EXISTS game_tags_tag ON game_tags(tag);

-- One row per engine pass over a game; readers use the latest 'done' run.
CREATE TABLE IF NOT EXISTS analysis_runs (
    id             INTEGER PRIMARY KEY,
    game_id        INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | error
    engine         TEXT,
    depth          INTEGER,
    movetime_ms    INTEGER,
    progress       INTEGER NOT NULL DEFAULT 0,
    total          INTEGER NOT NULL DEFAULT 0,
    white_accuracy REAL,
    black_accuracy REAL,
    error          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS analysis_runs_game ON analysis_runs(game_id, status, id);
CREATE INDEX IF NOT EXISTS analysis_runs_status ON analysis_runs(status, id);

-- ply 0 = start position; ply k = the position after move k, plus data about move k itself.
-- cp/mate/win are White's point of view; best_uci/pv are the engine's line from this position.
CREATE TABLE IF NOT EXISTS move_evals (
    run_id         INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    ply            INTEGER NOT NULL,
    fen            TEXT NOT NULL,
    move_uci       TEXT,
    san            TEXT,
    color          TEXT,
    cp             INTEGER,
    mate           INTEGER,
    win            REAL NOT NULL,
    best_uci       TEXT,
    pv             TEXT,
    classification TEXT,
    win_drop       REAL,
    accuracy       REAL,
    clock          REAL,
    time_spent     REAL,
    time_trouble   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, ply)
);
CREATE INDEX IF NOT EXISTS move_evals_class ON move_evals(classification);

-- Imported (LLM) explanations, keyed by the position before the move and the move played.
CREATE TABLE IF NOT EXISTS explanations (
    fen        TEXT NOT NULL,
    move_uci   TEXT NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (fen, move_uci)
);

-- Latest finished analysis per game (what every statistic reads).
CREATE VIEW IF NOT EXISTS latest_runs AS
    SELECT game_id, max(id) AS run_id FROM analysis_runs WHERE status = 'done' GROUP BY game_id;

-- Tactical motifs on error moves: kind 'allowed' (opponent's punishment) or 'missed' (your better move).
CREATE TABLE IF NOT EXISTS move_tags (
    run_id INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    ply    INTEGER NOT NULL,
    tag    TEXT NOT NULL,
    kind   TEXT NOT NULL,
    PRIMARY KEY (run_id, ply, tag, kind)
);
CREATE INDEX IF NOT EXISTS move_tags_tag ON move_tags(tag);

-- Puzzles in Lichess format: fen is before the opponent's setup move; moves = setup move, then solution.
CREATE TABLE IF NOT EXISTS puzzles (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,          -- own | lichess
    source_id  TEXT NOT NULL,
    fen        TEXT NOT NULL,
    moves      TEXT NOT NULL,
    rating     INTEGER NOT NULL,
    themes     TEXT NOT NULL DEFAULT '',
    game_id    INTEGER REFERENCES games(id) ON DELETE SET NULL,
    ply        INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS puzzles_source_rating ON puzzles(source, rating);
CREATE TABLE IF NOT EXISTS puzzle_themes (
    puzzle_id INTEGER NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE,
    theme     TEXT NOT NULL,
    PRIMARY KEY (theme, puzzle_id)
);
CREATE TABLE IF NOT EXISTS puzzle_attempts (
    id           INTEGER PRIMARY KEY,
    puzzle_id    INTEGER NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE,
    solved       INTEGER NOT NULL,
    hints        INTEGER NOT NULL DEFAULT 0,
    seconds      REAL,
    rated        INTEGER NOT NULL DEFAULT 0,
    rating_after REAL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS puzzle_attempts_puzzle ON puzzle_attempts(puzzle_id);
CREATE INDEX IF NOT EXISTS puzzle_attempts_time ON puzzle_attempts(created_at);
-- Leitner boxes for failed puzzles.
CREATE TABLE IF NOT EXISTS puzzle_reviews (
    puzzle_id INTEGER PRIMARY KEY REFERENCES puzzles(id) ON DELETE CASCADE,
    box       INTEGER NOT NULL,
    due       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS puzzle_reviews_due ON puzzle_reviews(due);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Opening book from the ECO table: named positions and every book move along the named lines.
CREATE TABLE IF NOT EXISTS eco (
    epd  TEXT PRIMARY KEY,
    eco  TEXT NOT NULL,
    name TEXT NOT NULL,
    ply  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS book (
    epd      TEXT NOT NULL,
    move_uci TEXT NOT NULL,
    san      TEXT NOT NULL,
    PRIMARY KEY (epd, move_uci)
);
-- Your repertoire move in each position where you are to move; doubles as a Leitner card.
CREATE TABLE IF NOT EXISTS repertoire (
    color    TEXT NOT NULL,
    epd      TEXT NOT NULL,
    fen      TEXT NOT NULL,
    line     TEXT NOT NULL,               -- UCI moves from the start position to this one
    move_uci TEXT NOT NULL,
    san      TEXT NOT NULL,
    manual   INTEGER NOT NULL DEFAULT 0,
    box      INTEGER NOT NULL DEFAULT 0,
    due      TEXT NOT NULL DEFAULT (date('now')),
    PRIMARY KEY (color, epd)
);
CREATE INDEX IF NOT EXISTS repertoire_due ON repertoire(color, due);

CREATE TABLE IF NOT EXISTS lesson_progress (
    lesson_id     TEXT PRIMARY KEY,
    examples_done INTEGER NOT NULL DEFAULT 0,
    drill_correct INTEGER,
    drill_total   INTEGER,
    completed_at  TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS endgame_progress (
    id         TEXT PRIMARY KEY,
    attempts   INTEGER NOT NULL DEFAULT 0,
    successes  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per day and activity kind; drives the streak.
CREATE TABLE IF NOT EXISTS activity (
    day   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);
"""

# Columns added after a table was first released: (table, column, declaration).
MIGRATIONS = [
    ("analysis_runs", "tagged", "INTEGER NOT NULL DEFAULT 0"),
    ("analysis_runs", "puzzled", "INTEGER NOT NULL DEFAULT 0"),
    ("move_evals", "phase", "TEXT"),
    ("games", "reviewed_at", "TEXT"),
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def init_db(path: Path) -> None:
    con = connect(path)
    con.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        if column not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    con.commit()
    con.close()


def log_activity(con: sqlite3.Connection, kind: str, n: int = 1) -> None:
    con.execute(
        "INSERT INTO activity (day, kind, count) VALUES (date('now', 'localtime'), ?, ?) "
        "ON CONFLICT (day, kind) DO UPDATE SET count = count + excluded.count",
        (kind, n),
    )


def kv_get(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def kv_set(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))


def backup(con: sqlite3.Connection, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(dest)
    try:
        con.backup(out)
    finally:
        out.close()
