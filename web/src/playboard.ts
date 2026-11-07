import { Chessground } from '@lichess-org/chessground';
import type { Api } from '@lichess-org/chessground/api';
import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';
import { Chess, type Move } from 'chess.js';

import type { Color } from './api';

export interface PlayedMove {
  uci: string;
  san: string;
  move: Move;
}

export interface PlayBoardOptions {
  fen: string;
  orientation: Color;
  /** Called after the user makes a legal move. */
  onMove?: (m: PlayedMove) => void;
}

type Promo = 'q' | 'r' | 'b' | 'n';
const PROMO_ROLES: [Promo, string][] = [['q', 'queen'], ['n', 'knight'], ['r', 'rook'], ['b', 'bishop']];

/** Redraw when the board's box changes size (layout, not just window resizes). redrawAll() rebuilds the
 *  board's elements, so it waits a frame: a redraw chessground already scheduled must land first, or it
 *  would draw into the discarded elements. The first callback only reports the initial size. */
export function watchSize(el: HTMLElement, cg: Api): ResizeObserver {
  let last = '';
  let frame = 0;
  const ro = new ResizeObserver(([e]) => {
    const size = `${Math.round(e.contentRect.width)}x${Math.round(e.contentRect.height)}`;
    if (last && size !== last) {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => el.isConnected && cg.redrawAll());
    }
    last = size;
  });
  ro.observe(el);
  return ro;
}

/** Chessground can still run one scheduled redraw after destroy(); clear arrows first so it draws nothing. */
export function destroyBoard(cg: Api): void {
  cg.set({ drawable: { shapes: [], autoShapes: [] } });
  cg.destroy();
}

export const toKeys = (uci: string) => [uci.slice(0, 2), uci.slice(2, 4)] as Key[];
const colorOf = (c: 'w' | 'b'): Color => (c === 'w' ? 'white' : 'black');

/** Interactive board: chessground for display/input, chess.js for rules. */
export class PlayBoard {
  readonly chess: Chess;
  readonly cg: Api;
  private user: Color | 'none' = 'none';
  private resize: ResizeObserver;

  constructor(readonly el: HTMLElement, private opts: PlayBoardOptions) {
    this.chess = new Chess(opts.fen);
    this.cg = Chessground(el, {
      fen: opts.fen,
      orientation: opts.orientation,
      coordinates: true,
      animation: { duration: 180 },
      movable: { free: false, showDests: true, events: { after: (o, d) => this.userMoved(o, d) } },
      premovable: { enabled: false },
      draggable: { showGhost: true },
      highlight: { lastMove: true, check: true },
    });
    this.resize = watchSize(el, this.cg);
    this.sync();
  }

  /** Let `color` move (or nobody). */
  setUser(color: Color | 'none') {
    this.user = color;
    this.sync();
  }

  get turn(): Color {
    return colorOf(this.chess.turn());
  }

  get fen(): string {
    return this.chess.fen();
  }

  load(fen: string, lastMove?: string) {
    this.chess.load(fen);
    this.cg.set({ fen, lastMove: lastMove ? toKeys(lastMove) : undefined });
    this.sync();
  }

  setOrientation(o: Color) {
    this.cg.set({ orientation: o });
  }

  /** Play a move programmatically (opponent, solution playback). Returns the SAN or null if illegal. */
  play(uci: string): PlayedMove | null {
    let move: Move;
    try {
      move = this.chess.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
    } catch {
      return null;
    }
    this.cg.move(move.from as Key, move.to as Key);
    if (move.promotion || move.isEnPassant() || move.isKingsideCastle() || move.isQueensideCastle()) {
      this.cg.set({ fen: this.chess.fen() });
    }
    this.sync();
    return { uci, san: move.san, move };
  }

  undo(): Move | null {
    const m = this.chess.undo();
    const hist = this.chess.history({ verbose: true });
    const last = hist[hist.length - 1];
    this.cg.set({ fen: this.chess.fen(), lastMove: last ? ([last.from, last.to] as Key[]) : undefined });
    this.sync();
    return m;
  }

  shapes(shapes: DrawShape[]) {
    this.cg.setAutoShapes(shapes);
  }

  destroy() {
    this.closePicker?.(null);
    this.resize.disconnect();
    destroyBoard(this.cg);
  }

  private closePicker: ((p: Promo | null) => void) | null = null;

  /** Lichess-style picker: the four pieces stacked on the promotion file, from the promotion square
   *  towards the centre. Click outside (or Escape) cancels the move. */
  private pickPromotion(dest: Key, color: Color): Promise<Promo | null> {
    return new Promise((resolve) => {
      const file = dest.charCodeAt(0) - 97;
      const flipped = this.cg.state.orientation === 'black';
      const left = (flipped ? 7 - file : file) * 12.5;
      const fromTop = (dest[1] === '8') !== flipped;
      const overlay = document.createElement('div');
      overlay.className = 'promo-picker';
      const done = (p: Promo | null) => {
        overlay.remove();
        document.removeEventListener('keydown', onKey, true);
        this.closePicker = null;
        resolve(p);
      };
      const onKey = (e: KeyboardEvent) => {
        const pick = PROMO_ROLES.find(([k]) => k === e.key.toLowerCase());
        if (e.key === 'Escape' || pick) {
          e.preventDefault();
          e.stopPropagation();
          done(pick ? pick[0] : null);
        }
      };
      overlay.addEventListener('pointerdown', (e) => {
        if (e.target === overlay) done(null);
      });
      PROMO_ROLES.forEach(([key, role], i) => {
        const btn = document.createElement('button');
        btn.className = 'promo-choice';
        btn.title = `${role[0].toUpperCase()}${role.slice(1)} (${key.toUpperCase()})`;
        btn.style.left = `${left}%`;
        btn.style[fromTop ? 'top' : 'bottom'] = `${i * 12.5}%`;
        btn.append(Object.assign(document.createElement('piece'), { className: `${role} ${color}` }));
        btn.addEventListener('pointerdown', (e) => {
          e.stopPropagation();
          done(key);
        });
        overlay.append(btn);
      });
      this.closePicker = done;
      document.addEventListener('keydown', onKey, true);
      this.el.append(overlay);
    });
  }

  private async userMoved(orig: Key, dest: Key) {
    let promo: Promo | null = null;
    if (this.isPromotion(orig, dest)) {
      promo = await this.pickPromotion(dest, this.turn);
      if (!promo) {
        this.cg.set({ fen: this.chess.fen(), lastMove: undefined });
        this.sync();
        return;
      }
    }
    const uci = `${orig}${dest}${promo ?? ''}`;
    const played = this.play(uci);
    if (!played) {
      this.cg.set({ fen: this.chess.fen() });
      this.sync();
      return;
    }
    this.opts.onMove?.(played);
  }

  private isPromotion(orig: string, dest: string): boolean {
    const p = this.chess.get(orig as never);
    return !!p && p.type === 'p' && (dest[1] === '8' || dest[1] === '1');
  }

  private sync() {
    const dests = new Map<Key, Key[]>();
    const turn = this.turn;
    if (this.user === turn && !this.chess.isGameOver()) {
      for (const m of this.chess.moves({ verbose: true })) {
        const list = dests.get(m.from as Key) ?? [];
        list.push(m.to as Key);
        dests.set(m.from as Key, list);
      }
    }
    this.cg.set({
      turnColor: turn,
      check: this.chess.inCheck() ? turn : false,
      movable: { color: this.user === 'none' ? undefined : this.user, dests },
    });
  }
}
