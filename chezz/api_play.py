"""Routes for playing Stockfish: full games (saved to the library and queued for review) and endgames."""
import chess
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from . import analysis, play


class MoveBody(BaseModel):
    fen: str
    elo: int = 1200


class FinishBody(BaseModel):
    start_fen: str = chess.STARTING_FEN
    moves: list[str]
    user_color: str
    result: str
    elo: int
    termination: str = "Normal"


class EndgameResult(BaseModel):
    success: bool


def register(app: FastAPI, ctx) -> None:
    get_con = ctx.get_con

    @app.post("/api/play/move")
    def engine_move(body: MoveBody):
        try:
            move = ctx.engine.move(body.fen, max(400, min(3200, body.elo)))
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        board = chess.Board(body.fen)
        return {"uci": move.uci(), "san": board.san(move)}

    @app.post("/api/play/finish")
    def finish(body: FinishBody, con=Depends(get_con)):
        if body.result not in ("1-0", "0-1", "1/2-1/2") or body.user_color not in ("white", "black"):
            raise HTTPException(400, "Invalid result or color")
        if not body.moves:
            return {"game_id": None}
        try:
            gid = play.save_game(con, ctx.cfg.usernames, body.start_fen, body.moves, body.user_color,
                                 body.result, body.elo, body.termination)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if gid and ctx.worker.engine_path:
            analysis.queue_game(con, ctx.cfg, gid)
            ctx.worker.notify()
        return {"game_id": gid}

    @app.get("/api/endgames")
    def endgame_list(con=Depends(get_con)):
        return play.endgame_list(con)

    @app.post("/api/endgames/{endgame_id}/result")
    def endgame_result(endgame_id: str, body: EndgameResult, con=Depends(get_con)):
        try:
            return play.record_endgame(con, endgame_id, body.success)
        except KeyError:
            raise HTTPException(404, "Endgame not found")
