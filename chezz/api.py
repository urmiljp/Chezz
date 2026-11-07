import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, api_play, api_train, db, ingest, openings, play, tactics
from .filters import game_filters
from .config import ROOT, Config

WEB_DIST = ROOT / "web" / "dist"
SAMPLE_PGN = ROOT / "samples" / "games.pgn"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}

LIST_COLUMNS = ", ".join("g." + c for c in (
    "id", "date", "white", "black", "white_elo", "black_elo", "result", "user_color", "user_result", "eco",
    "opening", "opening_family", "time_control", "time_class", "termination", "ply_count", "starred", "link",
)) + """,
    (SELECT CASE g.user_color WHEN 'white' THEN r.white_accuracy WHEN 'black' THEN r.black_accuracy END
       FROM analysis_runs r WHERE r.game_id = g.id AND r.status = 'done' ORDER BY r.id DESC LIMIT 1) AS accuracy,
    (SELECT r.status FROM analysis_runs r WHERE r.game_id = g.id ORDER BY r.id DESC LIMIT 1) AS analysis_status"""

# Games whose latest run is not 'done' and that have nothing queued/running.
UNANALYZED = """NOT EXISTS (SELECT 1 FROM analysis_runs r WHERE r.game_id = g.id
                            AND r.status IN ('done', 'queued', 'running'))"""


class ImportBody(BaseModel):
    pgn: str


class TextBody(BaseModel):
    text: str


class GamePatch(BaseModel):
    starred: bool | None = None
    notes: str | None = None
    tags: list[str] | None = None


class FolderBody(BaseModel):
    which: str


def _tags(value: str | None) -> list[str]:
    return sorted(value.split("\x1f")) if value else []


def _game_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["starred"] = bool(d["starred"])
    d["tags"] = _tags(d.pop("tag_list", None))
    return d



def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_app(cfg: Config, start_worker: bool = True) -> FastAPI:
    db.init_db(cfg.db_path)
    con = db.connect(cfg.db_path)
    ingest.refresh_user_fields(con, cfg.usernames)
    if openings.load_eco(con):  # first run or updated ECO table: name openings consistently
        openings.reclassify_all(con)
    if db.kv_get(con, "tagger_version") != tactics.VERSION:  # tactic heuristics changed: re-tag in the background
        con.execute("UPDATE analysis_runs SET tagged = 0")
        db.kv_set(con, "tagger_version", tactics.VERSION)
        con.commit()
    con.close()
    worker = analysis.AnalysisWorker(cfg)
    engine = play.PlayEngine(worker.engine_path)

    @asynccontextmanager
    async def lifespan(_app):
        if start_worker:
            worker.start()
        yield
        worker.stop()
        engine.close()

    app = FastAPI(title="Chezz", lifespan=lifespan)
    app.state.worker = worker
    app.state.play_engine = engine

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """Only serve the app's own page. A foreign Host means DNS rebinding; a foreign Origin or a
        cross-site fetch means some other website is calling the local API from your browser."""
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if (urlsplit("//" + host).hostname not in LOCAL_HOSTS
                or (origin is not None and origin != f"http://{host}")
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"detail": "Forbidden: Chezz only accepts requests from its own window."}, 403)
        return await call_next(request)

    def get_con():
        con = db.connect(cfg.db_path)
        try:
            yield con
        finally:
            con.close()

    def fetch_game(con: sqlite3.Connection, game_id: int) -> sqlite3.Row:
        row = con.execute(
            "SELECT g.*, (SELECT group_concat(tag, char(31)) FROM game_tags WHERE game_id = g.id) AS tag_list "
            "FROM games g WHERE g.id = ?",
            (game_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Game not found")
        return row

    @app.get("/api/meta")
    def meta(con=Depends(get_con)):
        q = lambda sql: [dict(r) for r in con.execute(sql)]
        return {
            "usernames": cfg.usernames,
            "total": con.execute("SELECT count(*) FROM games").fetchone()[0],
            "user_games": con.execute("SELECT count(*) FROM games WHERE user_color IS NOT NULL").fetchone()[0],
            "time_classes": [r["v"] for r in q("SELECT DISTINCT time_class AS v FROM games WHERE time_class IS NOT NULL ORDER BY 1")],
            "openings": q("SELECT opening_family AS name, count(*) AS count FROM games WHERE opening_family IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1"),
            "tags": q("SELECT tag AS name, count(*) AS count FROM game_tags GROUP BY 1 ORDER BY 1"),
            "record": q("SELECT user_result AS result, count(*) AS count FROM games WHERE user_result IS NOT NULL GROUP BY 1"),
            "data_dir": str(cfg.data_dir),
            "engine": {"available": bool(worker.engine_path), "path": worker.engine_path,
                       "depth": cfg.depth, "movetime_ms": cfg.movetime_ms},
        }

    @app.get("/api/games")
    def list_games(filters=Depends(game_filters), limit: int = 100, offset: int = 0, con=Depends(get_con)):
        where, params = filters
        total = con.execute(f"SELECT count(*) FROM games g{where}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT {LIST_COLUMNS}, "
            "(SELECT group_concat(tag, char(31)) FROM game_tags WHERE game_id = g.id) AS tag_list "
            f"FROM games g{where} ORDER BY g.date DESC, g.id DESC LIMIT ? OFFSET ?",
            params + [min(limit, 500), offset],
        ).fetchall()
        return {"total": total, "games": [_game_dict(r) for r in rows]}

    @app.get("/api/games/{game_id}")
    def get_game(game_id: int, con=Depends(get_con)):
        game = _game_dict(fetch_game(con, game_id))
        game.update(ingest.game_moves(game["pgn"]))
        game["opening_deviations"] = openings.deviations(con, game["pgn"], game["user_color"])
        return game

    @app.post("/api/games/{game_id}/reviewed")
    def mark_reviewed(game_id: int, con=Depends(get_con)):
        fetch_game(con, game_id)
        cur = con.execute("UPDATE games SET reviewed_at = datetime('now') WHERE id = ? AND reviewed_at IS NULL", (game_id,))
        if cur.rowcount:
            db.log_activity(con, "review")
        con.commit()
        return {"ok": True}

    @app.patch("/api/games/{game_id}")
    def patch_game(game_id: int, body: GamePatch, con=Depends(get_con)):
        fetch_game(con, game_id)
        if body.starred is not None:
            con.execute("UPDATE games SET starred = ? WHERE id = ?", (int(body.starred), game_id))
        if body.notes is not None:
            con.execute("UPDATE games SET notes = ? WHERE id = ?", (body.notes, game_id))
        if body.tags is not None:
            tags = {t.strip()[:40] for t in body.tags if t.strip()}
            con.execute("DELETE FROM game_tags WHERE game_id = ?", (game_id,))
            con.executemany("INSERT INTO game_tags (game_id, tag) VALUES (?, ?)", [(game_id, t) for t in tags])
        con.commit()
        return _game_dict(fetch_game(con, game_id))

    @app.delete("/api/games/{game_id}")
    def delete_game(game_id: int, con=Depends(get_con)):
        fetch_game(con, game_id)
        con.execute("DELETE FROM games WHERE id = ?", (game_id,))
        con.execute("DELETE FROM kv WHERE key = ?", (analysis.summary_key(game_id),))
        con.commit()
        return {"ok": True}

    @app.post("/api/import")
    def import_games(body: ImportBody, con=Depends(get_con)):
        return ingest.import_pgn(con, body.pgn, cfg.usernames).__dict__

    @app.post("/api/import-sample")
    def import_sample(con=Depends(get_con)):
        return ingest.import_pgn(con, SAMPLE_PGN.read_text(encoding="utf-8"), cfg.usernames).__dict__

    @app.post("/api/backup")
    def backup(con=Depends(get_con)):
        dest = cfg.data_dir / "backups" / f"chezz-{_stamp()}.db"
        db.backup(con, dest)
        return {"path": str(dest)}

    @app.post("/api/export")
    def export_all(con=Depends(get_con)):
        rows = con.execute("SELECT pgn FROM games ORDER BY date, id").fetchall()
        dest = cfg.data_dir / "exports" / f"all-games-{_stamp()}.pgn"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n\n".join(r["pgn"] for r in rows) + "\n", encoding="utf-8")
        return {"path": str(dest), "count": len(rows)}

    # ---------- analysis ----------

    def require_engine():
        if not worker.engine_path:
            raise HTTPException(400, "Stockfish not found. Install it and set [engine] path in config.toml.")

    def queue_sql(where: str) -> str:
        return (f"INSERT INTO analysis_runs (game_id, depth, movetime_ms) "
                f"SELECT g.id, {int(cfg.depth)}, {int(cfg.movetime_ms) or 'NULL'} FROM games g WHERE {where} ORDER BY g.date DESC, g.id DESC")

    @app.post("/api/games/{game_id}/analyze")
    def analyze_game(game_id: int, con=Depends(get_con)):
        """Queue a (re-)analysis unless one is already queued or running."""
        require_engine()
        fetch_game(con, game_id)
        con.execute(queue_sql("g.id = ? AND NOT EXISTS (SELECT 1 FROM analysis_runs r WHERE r.game_id = g.id "
                              "AND r.status IN ('queued', 'running'))"), (game_id,))
        con.commit()
        worker.notify()
        return analysis.load_review(con, game_id)

    @app.post("/api/analysis/queue-all")
    def queue_all(con=Depends(get_con)):
        require_engine()
        cur = con.execute(queue_sql(UNANALYZED))
        con.commit()
        worker.notify()
        return {"queued": cur.rowcount}

    @app.get("/api/analysis/status")
    def analysis_status(con=Depends(get_con)):
        running = con.execute(
            "SELECT r.id, r.game_id, r.progress, r.total, g.white, g.black FROM analysis_runs r "
            "JOIN games g ON g.id = r.game_id WHERE r.status = 'running' LIMIT 1"
        ).fetchone()
        return {
            "queued": con.execute("SELECT count(*) FROM analysis_runs WHERE status = 'queued'").fetchone()[0],
            "running": dict(running) if running else None,
            "unanalyzed": con.execute(f"SELECT count(*) FROM games g WHERE {UNANALYZED}").fetchone()[0],
        }

    @app.get("/api/games/{game_id}/review")
    def game_review(game_id: int, con=Depends(get_con)):
        fetch_game(con, game_id)
        return analysis.load_review(con, game_id)

    @app.post("/api/games/{game_id}/llm-batch")
    def llm_batch(game_id: int, scope: str = "key", con=Depends(get_con)):
        """scope 'game': every move; 'key': inaccuracies, mistakes and blunders."""
        if scope not in ("game", "key"):
            raise HTTPException(400, "scope must be 'game' or 'key'")
        game = fetch_game(con, game_id)
        text, count = analysis.llm_batch(con, game, scope)
        if count == 0:
            raise HTTPException(400, "Analyze this game first." if scope == "game" else
                                "No inaccuracies, mistakes or blunders in this game. Export the whole game instead.")
        dest = cfg.data_dir / "exports" / f"game-{game_id}-{'whole-game' if scope == 'game' else 'key-moments'}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return {"path": str(dest), "count": count, "text": text}

    @app.post("/api/explanations/import")
    def import_explanations(body: TextBody, con=Depends(get_con)):
        res = analysis.import_explanations(con, body.text)
        if not res["imported"] and not res["summaries"]:
            raise HTTPException(400, "No '### KEY <fen>|<move>' or '### SUMMARY' blocks found in the pasted text.")
        return res

    @app.post("/api/exit")
    def exit_app():
        """Shut the app down (window, server, worker, engines). The launcher sets app.state.request_exit."""
        request_exit = getattr(app.state, "request_exit", None)
        if request_exit is None:
            raise HTTPException(400, "Exit is not available when the app was started this way.")
        threading.Timer(0.3, request_exit).start()  # let this response reach the UI first
        return {"ok": True}

    @app.post("/api/open-folder")
    def open_folder(body: FolderBody):
        if body.which not in ("backups", "exports"):
            raise HTTPException(400, "Unknown folder")
        folder = cfg.data_dir / body.which
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - local desktop app
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        return {"ok": True}

    ctx = api_train.Ctx(cfg, get_con, fetch_game, worker, engine)
    api_train.register(app, ctx)
    api_play.register(app, ctx)

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app
