"""Routes for the weakness profile, puzzles, lessons, opening trainer, today's plan and progress."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from . import lessons, openings, plan, puzzles, weakness
from .config import Config
from .filters import game_filters


@dataclass
class Ctx:
    cfg: Config
    get_con: Callable
    fetch_game: Callable
    worker: object
    engine: object

    @property
    def lichess_path(self) -> Path:
        return self.cfg.data_dir / "lichess_puzzles.db"


class AttemptBody(BaseModel):
    solved: bool
    hints: int = 0
    seconds: float | None = None


class PathBody(BaseModel):
    path: str


class LessonProgress(BaseModel):
    examples_done: int | None = None
    drill_correct: int | None = None
    drill_total: int | None = None
    completed: bool = False


class ColorBody(BaseModel):
    color: str


class RepertoireBody(BaseModel):
    color: str
    fen: str
    line: list[str] = []
    uci: str


class DrillBody(BaseModel):
    color: str
    epd: str
    correct: bool


def _color(color: str) -> str:
    if color not in ("white", "black"):
        raise HTTPException(400, "color must be white or black")
    return color


def register(app: FastAPI, ctx: Ctx) -> None:
    get_con = ctx.get_con
    importer = puzzles.LichessImporter(ctx.lichess_path)
    app.state.lichess_importer = importer

    # ---------- weakness profile ----------

    @app.get("/api/weakness")
    def weakness_profile(filters=Depends(game_filters), con=Depends(get_con)):
        return weakness.profile(con, *filters)

    # ---------- puzzles ----------

    @app.get("/api/puzzles/summary")
    def puzzle_summary(con=Depends(get_con)):
        s = puzzles.summary(con, ctx.lichess_path)
        s["plan_themes"] = plan.plan_themes(weakness.profile(con)["weaknesses"])
        return s

    @app.get("/api/puzzles/next")
    def next_puzzle(mode: str = "mixed", theme: str | None = None, exclude: str = "", con=Depends(get_con)):
        ex = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
        prefs = plan.plan_themes(weakness.profile(con)["weaknesses"]) if mode == "plan" else None
        return puzzles.next_puzzle(con, ctx.lichess_path, mode, theme or None, ex, prefs)  # null: nothing available

    @app.get("/api/puzzles/lichess")
    def lichess_status():
        return {"count": puzzles.lichess_count(ctx.lichess_path) if not importer.state["running"] else None,
                "import": importer.state, "candidates": puzzles.find_lichess_files(ctx.cfg.data_dir)}

    @app.post("/api/puzzles/lichess/import")
    def lichess_import(body: PathBody):
        raw = body.path.strip().strip('"')
        path = Path(raw).expanduser()
        if raw.startswith(("\\\\", "//")) or path.suffix.lower() not in (".zst", ".csv"):
            raise HTTPException(400, "Choose the local lichess_db_puzzle.csv.zst (or .csv) file.")
        try:
            importer.start(path)
        except (RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc))
        return importer.state

    @app.post("/api/pick-file")
    def pick_file():
        """Native file dialog when running in the pywebview window; the UI falls back to a path field."""
        try:
            import webview

            if webview.windows:
                res = webview.windows[0].create_file_dialog(
                    webview.OPEN_DIALOG, file_types=("Lichess puzzles (*.zst;*.csv)", "All files (*.*)"))
                return {"path": res[0] if res else None, "supported": True}
        except Exception:
            pass
        return {"path": None, "supported": False}

    @app.get("/api/puzzles/{puzzle_id}")
    def get_puzzle(puzzle_id: int, con=Depends(get_con)):
        if not con.execute("SELECT 1 FROM puzzles WHERE id = ?", (puzzle_id,)).fetchone():
            raise HTTPException(404, "Puzzle not found")
        return puzzles.puzzle_dict(con, puzzle_id)

    @app.post("/api/puzzles/{puzzle_id}/attempt")
    def attempt(puzzle_id: int, body: AttemptBody, con=Depends(get_con)):
        try:
            return puzzles.record_attempt(con, puzzle_id, body.solved, body.hints, body.seconds)
        except KeyError:
            raise HTTPException(404, "Puzzle not found")

    # ---------- lessons ----------

    @app.get("/api/lessons")
    def lesson_list(con=Depends(get_con)):
        return lessons.listing(con, weakness.profile(con)["weaknesses"])

    @app.get("/api/lessons/{lesson_id}")
    def lesson(lesson_id: str, con=Depends(get_con)):
        l = lessons.get(lesson_id)
        if l is None:
            raise HTTPException(404, "Lesson not found")
        prog = con.execute("SELECT * FROM lesson_progress WHERE lesson_id = ?", (lesson_id,)).fetchone()
        return {**l, "progress": dict(prog) if prog else None}

    @app.post("/api/lessons/{lesson_id}/progress")
    def lesson_progress(lesson_id: str, body: LessonProgress, con=Depends(get_con)):
        try:
            return lessons.save_progress(con, lesson_id, body.examples_done, body.drill_correct, body.drill_total, body.completed)
        except KeyError:
            raise HTTPException(404, "Lesson not found")

    @app.post("/api/lessons/{lesson_id}/repertoire")
    def lesson_repertoire(lesson_id: str, con=Depends(get_con)):
        try:
            return lessons.add_to_repertoire(con, lesson_id)
        except KeyError:
            raise HTTPException(404, "Not an opening lesson")

    # ---------- openings ----------

    @app.get("/api/openings/summary")
    def openings_summary(con=Depends(get_con)):
        return openings.summary(con)

    @app.post("/api/openings/build")
    def build(body: ColorBody, con=Depends(get_con)):
        return {"added": openings.build_repertoire(con, _color(body.color))}

    @app.get("/api/openings/explore")
    def explore(color: str, fen: str, con=Depends(get_con)):
        try:
            return openings.explore(con, _color(color), fen)
        except ValueError:
            raise HTTPException(400, "Invalid FEN")

    @app.post("/api/openings/repertoire")
    def set_repertoire(body: RepertoireBody, con=Depends(get_con)):
        try:
            openings.set_move(con, _color(body.color), body.fen, body.line, body.uci)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return openings.explore(con, body.color, body.fen)

    @app.get("/api/openings/drill")
    def drill_card(color: str, con=Depends(get_con)):
        return openings.next_card(con, _color(color))  # null: nothing due

    @app.post("/api/openings/drill")
    def drill_grade(body: DrillBody, con=Depends(get_con)):
        try:
            return openings.grade_card(con, _color(body.color), body.epd, body.correct)
        except KeyError:
            raise HTTPException(404, "Card not found")

    @app.get("/api/openings/deviations")
    def deviations(limit: int = 30, con=Depends(get_con)):
        return openings.recent_deviations(con, min(limit, 100))

    # ---------- plan + progress ----------

    @app.get("/api/plan")
    def today_plan(con=Depends(get_con)):
        return plan.today(con, ctx.lichess_path)

    @app.get("/api/progress")
    def progress(con=Depends(get_con)):
        return plan.progress(con)
