import type { Color } from './api';
import { PlayBoard, toKeys } from './playboard';

/** A move-sequence exercise: optional setup move by the opponent, then solver/opponent alternate. */
export interface Exercise {
  fen: string;
  setup?: string;
  line: string[]; // solver, reply, solver, ... (ends on a solver move)
  accept?: string[][]; // per solver step: other moves that also count as correct (ends the exercise)
}

export type SolverState = 'loading' | 'your-turn' | 'correct' | 'wrong' | 'solved' | 'done';

export interface SolverResult {
  solved: boolean; // first try, no hints, no solution shown
  mistakes: number;
  hints: number;
  seconds: number;
  viewedSolution: boolean;
  alternative: string | null; // SAN of an accepted alternative move
}

export interface Solver {
  board: PlayBoard;
  solverColor: Color;
  hint(): void;
  undo(): void;
  showSolution(): Promise<void>;
  destroy(): void;
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function createSolver(
  el: HTMLElement,
  ex: Exercise,
  on: { state: (s: SolverState, info?: string) => void; complete: (r: SolverResult) => void },
): Solver {
  const firstTurn: Color = ex.fen.split(' ')[1] === 'w' ? 'white' : 'black';
  const solverColor: Color = ex.setup ? (firstTurn === 'white' ? 'black' : 'white') : firstTurn;
  let step = 0; // index into ex.line of the next expected solver move
  let mistakes = 0;
  let hints = 0;
  let viewed = false;
  let finished = false;
  let destroyed = false;
  const started = performance.now();

  const board = new PlayBoard(el, {
    fen: ex.fen,
    orientation: solverColor,
    onMove: (m) => void userMoved(m.uci, m.san),
  });

  const finish = (alternative: string | null = null) => {
    finished = true;
    board.setUser('none');
    on.state(viewed || mistakes || hints ? 'done' : 'solved', alternative ?? undefined);
    on.complete({
      solved: !viewed && mistakes === 0 && hints === 0,
      mistakes,
      hints,
      seconds: Math.round((performance.now() - started) / 100) / 10,
      viewedSolution: viewed,
      alternative,
    });
  };

  async function userMoved(uci: string, san: string) {
    board.shapes([]);
    const expected = ex.line[step];
    const alts = ex.accept?.[step / 2] ?? [];
    // Any checkmate is as good as the expected move.
    if (uci === expected || board.chess.isCheckmate()) {
      step += 1;
      if (step >= ex.line.length || board.chess.isGameOver()) return finish();
      on.state('correct', san);
      board.setUser('none');
      await wait(350);
      if (destroyed) return;
      board.play(ex.line[step]);
      step += 1;
      board.setUser(solverColor);
      on.state('your-turn');
      return;
    }
    if (alts.includes(uci)) return finish(san);
    mistakes += 1;
    board.setUser('none');
    on.state('wrong', san);
  }

  async function start() {
    on.state('loading');
    if (ex.setup) {
      await wait(450);
      if (destroyed) return;
      board.play(ex.setup);
    }
    board.setUser(solverColor);
    on.state('your-turn');
  }

  void start();

  return {
    board,
    solverColor,
    hint() {
      if (finished || board.turn !== solverColor) return;
      hints += 1;
      const [from, to] = toKeys(ex.line[step]);
      board.shapes(hints === 1 ? [{ orig: from, brush: 'green' }] : [{ orig: from, dest: to, brush: 'green' }]);
    },
    undo() {
      if (finished || board.turn === solverColor) return;
      board.undo();
      board.setUser(solverColor);
      on.state('your-turn');
    },
    async showSolution() {
      if (finished) return;
      viewed = true;
      board.setUser('none');
      board.shapes([]);
      if (board.turn !== solverColor) board.undo(); // take back a wrong move
      while (step < ex.line.length) {
        await wait(550);
        if (destroyed) return;
        board.play(ex.line[step]);
        step += 1;
      }
      finish();
    },
    destroy() {
      destroyed = true;
      board.destroy();
    },
  };
}
