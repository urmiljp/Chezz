import { Chess } from 'chess.js';

import { api, type Color, type Endgame } from './api';
import type { View } from './main';
import { PlayBoard } from './playboard';
import { fill, h, icon, toast } from './ui';

const START = new Chess().fen();
const STRENGTHS: [number, string][] = [
  [600, 'Beginner'], [900, 'Novice'], [1200, 'Club player'], [1500, 'Intermediate'],
  [1800, 'Advanced'], [2200, 'Expert'], [3000, 'Maximum'],
];

function strengthLabel(elo: number) {
  return [...STRENGTHS].reverse().find(([e]) => elo >= e)?.[1] ?? 'Beginner';
}

interface GameSetup {
  fen: string;
  userColor: Color;
  elo: number;
  endgame?: Endgame;
}

export async function renderPlay(root: HTMLElement, params: URLSearchParams): Promise<View> {
  let tab = params.get('tab') ?? 'game';
  let board: PlayBoard | null = null;
  let destroyed = false;
  let flipped = false;
  const tabSeg = h('div', { class: 'seg' });
  const content = h('div');
  root.append(h('header', { class: 'page-head' }, h('h1', {}, 'Play'), tabSeg), content);

  function paintTabs() {
    fill(tabSeg, ([['game', 'vs Stockfish'], ['endgames', 'Endgames']] as [string, string][]).map(([t, l]) =>
      h('button', { class: `seg-btn${t === tab ? ' on' : ''}`, onClick: () => { tab = t; home(); } }, l)));
  }

  function reset() {
    board?.destroy();
    board = null;
    content.replaceChildren();
    paintTabs();
  }

  function home() {
    reset();
    if (tab === 'endgames') void endgameList();
    else setupForm();
  }

  function setupForm() {
    let elo = Number(localStorage.getItem('play-elo') ?? 1200);
    let side: Color | 'random' = (localStorage.getItem('play-side') as Color | 'random') ?? 'white';
    const label = h('strong');
    const paint = () => (label.textContent = `${strengthLabel(elo)} · about ${elo}`);
    const slider = h('input', { type: 'range', min: 600, max: 3000, step: 100, value: String(elo), class: 'grow' });
    slider.addEventListener('input', () => { elo = Number(slider.value); paint(); });
    const sideSeg = h('div', { class: 'seg' });
    const paintSide = () => fill(sideSeg, (['white', 'random', 'black'] as const).map((s) =>
      h('button', { class: `seg-btn${s === side ? ' on' : ''}`, onClick: () => { side = s; paintSide(); } },
        s === 'random' ? 'Random' : [h('span', { class: `piece-dot ${s}` }), s === 'white' ? 'White' : 'Black'])));
    paint();
    paintSide();
    content.append(
      h(
        'div',
        { class: 'card setup-card stack' },
        h('h2', {}, 'New game against Stockfish'),
        h('div', { class: 'stack' }, h('span', { class: 'muted small' }, 'Strength'), h('div', { class: 'row gap' }, slider), label),
        h('div', { class: 'stack' }, h('span', { class: 'muted small' }, 'You play'), sideSeg),
        h('p', { class: 'muted small' }, 'Finished games are saved to your Library (tag "vs-engine") and queued for review.'),
        h('button', {
          class: 'btn primary',
          onClick: () => {
            localStorage.setItem('play-elo', String(elo));
            localStorage.setItem('play-side', side);
            const userColor: Color = side === 'random' ? (Math.random() < 0.5 ? 'white' : 'black') : side;
            startGame({ fen: START, userColor, elo });
          },
        }, 'Start game'),
      ),
    );
  }

  async function endgameList() {
    const list = await api.endgames();
    if (destroyed) return;
    const groups = [...new Set(list.map((e) => e.group))];
    for (const g of groups) {
      content.append(
        h('h3', {}, g),
        h(
          'div',
          { class: 'lesson-grid' },
          list.filter((e) => e.group === g).map((e) =>
            h(
              'button',
              { class: 'card lesson-card', onClick: () => startGame({ fen: e.fen, userColor: e.side, elo: 3000, endgame: e }) },
              h('div', { class: 'row gap' }, h('strong', {}, e.title), h('span', { class: 'spacer' }),
                e.successes ? h('span', { class: 'badge badge-win' }, `${e.successes}/${e.attempts}`) : e.attempts ? h('span', { class: 'badge' }, `0/${e.attempts}`) : null),
              h('p', { class: 'muted small' }, e.description),
              h('span', { class: 'chip' }, goalText(e)),
            ),
          ),
        ),
        h('div', { class: 'gap-lg' }),
      );
    }
  }

  function goalText(e: Endgame) {
    return e.goal === 'mate' ? 'Goal: checkmate' : e.goal === 'promote' ? 'Goal: promote a pawn' : `Goal: hold for ${e.moves} moves`;
  }

  function startGame(setup: GameSetup) {
    reset();
    flipped = false;
    const host = h('div', { class: 'cg-host' });
    const status = h('div', { class: 'status-card card status-turn' });
    const moveList = h('div', { class: 'san-list' });
    const actions = h('div', { class: 'row gap wrap' });
    const eg = setup.endgame;
    const moves: string[] = [];
    let over = false;
    let userMoves = 0;
    let thinking = false;
    let engineError = false;

    content.append(
      h(
        'div',
        { class: 'game-grid' },
        h('div', { class: 'board-col' }, h('div', { class: 'board-wrap' }, host)),
        h(
          'div',
          { class: 'side-col' },
          h(
            'div',
            { class: 'card stack' },
            h('strong', {}, eg ? eg.title : `Stockfish · ${strengthLabel(setup.elo)} (${setup.elo})`),
            eg ? h('span', { class: 'muted small' }, `${eg.description} ${goalText(eg)}.`) : h('span', { class: 'muted small' }, `You play ${setup.userColor}.`),
          ),
          status,
          h('div', { class: 'card moves-card' }, moveList),
          actions,
        ),
      ),
    );

    board = new PlayBoard(host, { fen: setup.fen, orientation: setup.userColor, onMove: (m) => void afterMove(m.uci, true) });
    const b = board;

    const setStatus = (kind: string, title: string, text = '') => {
      status.className = `status-card card status-${kind}`;
      fill(status, h('strong', {}, title), text ? h('div', { class: 'small' }, text) : null);
    };

    function paintMoves() {
      const c = new Chess(setup.fen);
      const sans = moves.map((u) => c.move({ from: u.slice(0, 2), to: u.slice(2, 4), promotion: u[4] }).san);
      const startBlack = setup.fen.split(' ')[1] === 'b';
      const first = Number(setup.fen.split(' ')[5] ?? 1);
      fill(moveList, sans.map((s, i) => {
        const ply = i + (startBlack ? 1 : 0);
        return h('span', { class: 'san' }, ply % 2 === 0 ? `${Math.floor(ply / 2) + first}. ` : i === 0 ? `${first}… ` : '', s);
      }));
      moveList.parentElement!.scrollTop = 1e6;
    }

    function paintActions() {
      fill(
        actions,
        !over && engineError ? h('button', { class: 'btn primary', onClick: () => void engineTurn() }, 'Retry engine move') : null,
        !over ? h('button', { class: 'btn ghost', title: 'Take back your last move', onClick: takeback, disabled: userMoves === 0 }, 'Takeback') : null,
        h('button', { class: 'btn ghost', title: 'Flip (f)', onClick: flip }, icon('flip'), 'Flip'),
        !over ? h('button', { class: 'btn ghost', onClick: () => end(setup.userColor === 'white' ? '0-1' : '1-0', eg ? 'Gave up' : 'Resignation') }, eg ? 'Give up' : 'Resign') : null,
        over ? h('button', { class: 'btn primary', onClick: () => startGame(setup) }, eg ? 'Try again' : 'Rematch') : null,
        over ? h('button', { class: 'btn ghost', onClick: home }, eg ? 'All endgames' : 'New game') : null,
      );
    }

    function flip() {
      flipped = !flipped;
      b.setOrientation(flipped ? (setup.userColor === 'white' ? 'black' : 'white') : setup.userColor);
    }

    function takeback() {
      if (over || thinking || userMoves === 0) return;
      // Undo the engine's reply (if any) and your last move, so it is your turn again.
      do {
        b.undo();
        moves.pop();
      } while (moves.length && b.turn !== setup.userColor);
      userMoves -= 1;
      paintMoves();
      paintActions();
      b.setUser(setup.userColor);
      setStatus('turn', 'Your move');
    }

    function goalReached(lastUci: string, byUser: boolean): boolean | null {
      if (!eg) return null;
      const c = b.chess;
      if (eg.goal === 'promote' && byUser && lastUci.length === 5) return true;
      if (eg.goal === 'mate' && c.isCheckmate()) return byUser;
      if (eg.goal === 'hold') {
        if (c.isCheckmate()) return !byUser ? false : true;
        if (!byUser && lastUci.length === 5) return false; // opponent promoted
        if (c.isDraw()) return true;
        if (userMoves >= (eg.moves ?? 30)) return true;
      }
      if (c.isGameOver()) return eg.goal === 'hold';
      return null;
    }

    async function afterMove(uci: string, byUser: boolean) {
      if (byUser) {
        moves.push(uci);
        userMoves += 1;
      }
      paintMoves();
      const goal = goalReached(uci, byUser);
      if (goal !== null) return void finishEndgame(goal);
      if (b.chess.isGameOver()) return void endByRules();
      if (byUser) await engineTurn();
    }

    async function engineTurn() {
      if (over) return;
      b.setUser('none');
      thinking = true;
      setStatus('idle', 'Stockfish is thinking…');
      try {
        const r = await api.engineMove(b.fen, setup.elo);
        if (destroyed || over) return;
        b.play(r.uci);
        moves.push(r.uci);
        thinking = false;
        await afterMove(r.uci, false);
        if (!over) {
          b.setUser(setup.userColor);
          setStatus('turn', 'Your move', b.chess.inCheck() ? 'You are in check.' : '');
        }
        engineError = false;
        paintActions();
      } catch (err) {
        thinking = false;
        if (destroyed || over) return;
        engineError = true;
        paintActions();
        setStatus('bad', 'Stockfish did not answer', (err as Error).message);
      }
    }

    function endByRules() {
      const c = b.chess;
      if (c.isCheckmate()) {
        const winner: Color = c.turn() === 'w' ? 'black' : 'white';
        return end(winner === 'white' ? '1-0' : '0-1', 'Checkmate');
      }
      const why = c.isStalemate() ? 'Stalemate' : c.isThreefoldRepetition() ? 'Repetition' : c.isInsufficientMaterial() ? 'Insufficient material' : '50-move rule';
      end('1/2-1/2', why);
    }

    async function finishEndgame(success: boolean) {
      over = true;
      b.setUser('none');
      setStatus(success ? 'good' : 'bad', success ? 'Goal reached!' : 'Not this time', success ? 'Well played.' : 'Try again - the description has the key idea.');
      paintActions();
      await api.endgameResult(eg!.id, success).catch((err) => toast((err as Error).message, { error: true }));
    }

    async function end(result: string, termination: string) {
      if (over) return;
      if (eg) return void finishEndgame(false);
      over = true;
      b.setUser('none');
      const youWon = (result === '1-0') === (setup.userColor === 'white') && result !== '1/2-1/2';
      const title = result === '1/2-1/2' ? 'Draw' : youWon ? 'You won!' : 'You lost';
      setStatus(result === '1/2-1/2' ? 'idle' : youWon ? 'good' : 'bad', title, termination);
      paintActions();
      try {
        const r = await api.finishGame({ start_fen: setup.fen, moves, user_color: setup.userColor, result, elo: setup.elo, termination });
        if (r.game_id) {
          actions.prepend(h('a', { class: 'btn primary', href: `#/game/${r.game_id}` }, 'Review this game'));
          toast('Game saved to your Library and queued for analysis');
        }
      } catch (err) {
        toast((err as Error).message, { error: true });
      }
    }

    paintActions();
    paintMoves();
    if (b.turn === setup.userColor) {
      b.setUser(setup.userColor);
      setStatus('turn', 'Your move');
    } else {
      void engineTurn();
    }
  }

  home();
  return {
    onKey(e) {
      if (e.key === 'f' && board) {
        flipped = !flipped;
        board.setOrientation(board.cg.state.orientation === 'white' ? 'black' : 'white');
        e.preventDefault();
      }
    },
    destroy() {
      destroyed = true;
      board?.destroy();
    },
  };
}
