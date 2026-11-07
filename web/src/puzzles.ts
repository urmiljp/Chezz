import { api, THEME_LABELS, type Puzzle, type PuzzleSummary } from './api';
import type { View } from './main';
import { createSolver, type Solver, type SolverResult } from './solver';
import { emptyState, fill, h, icon, toast } from './ui';

const MODES: [string, string][] = [
  ['mixed', 'Mixed'],
  ['plan', "Today's plan"],
  ['own', 'My mistakes'],
  ['review', 'Review'],
];

export async function renderPuzzles(root: HTMLElement, params: URLSearchParams): Promise<View> {
  let summary: PuzzleSummary = await api.puzzleSummary();
  let mode = params.get('mode') ?? 'mixed';
  let theme = params.get('theme') ?? '';
  const seen: number[] = [];
  let solver: Solver | null = null;
  let puzzle: Puzzle | null = null;
  let completed = false;
  let destroyed = false;

  const boardHost = h('div', { class: 'board-wrap' });
  const ratingEl = h('div', { class: 'rating-chip' });
  const tabs = h('div', { class: 'seg' });
  const themeSel = h('select', { class: theme ? 'active' : '' });
  const status = h('div', { class: 'status-card card' });
  const actions = h('div', { class: 'row gap wrap' });
  const info = h('div', { class: 'card puzzle-info' });
  const lichessBox = h('div');

  root.append(
    h('header', { class: 'page-head' }, h('h1', {}, 'Puzzles'), h('span', { class: 'spacer' }), ratingEl),
    h(
      'div',
      { class: 'game-grid' },
      h('div', { class: 'board-col' }, boardHost),
      h('div', { class: 'side-col' }, h('div', { class: 'card stack' }, tabs, themeSel), status, actions, info, lichessBox),
    ),
  );

  function paintRating(delta?: number) {
    fill(
      ratingEl,
      h('span', { class: 'muted small' }, 'Puzzle rating'),
      h('strong', {}, String(summary.rating)),
      delta ? h('span', { class: delta > 0 ? 'delta up' : 'delta down' }, `${delta > 0 ? '+' : ''}${delta}`) : null,
    );
  }

  function paintTabs() {
    fill(
      tabs,
      MODES.map(([m, label]) => {
        const count = m === 'review' ? summary.due : m === 'own' ? summary.own_unseen : m === 'plan' ? Math.max(0, 5 - summary.today) : 0;
        return h(
          'button',
          { class: `seg-btn${m === mode ? ' on' : ''}`, onClick: () => switchMode(m) },
          label,
          count ? h('span', { class: 'count' }, String(count)) : null,
        );
      }),
    );
    fill(
      themeSel,
      h('option', { value: '' }, 'All themes'),
      summary.themes.map((t) => h('option', { value: t.theme, selected: t.theme === theme }, t.label + (t.own ? ` (${t.own} yours)` : ''))),
    );
    themeSel.className = theme ? 'active' : '';
  }

  themeSel.addEventListener('change', () => {
    theme = themeSel.value;
    paintTabs();
    void next();
  });

  function switchMode(m: string) {
    mode = m;
    paintTabs();
    void next();
  }

  function setStatus(kind: string, title: string, text = '') {
    status.className = `status-card card status-${kind}`;
    fill(status, h('strong', {}, title), text ? h('div', { class: 'small' }, text) : null);
  }

  function paintActions() {
    fill(
      actions,
      !completed && puzzle ? h('button', { class: 'btn ghost', title: 'Hint (h)', onClick: () => solver?.hint() }, 'Hint') : null,
      !completed && puzzle ? h('button', { class: 'btn ghost', title: 'Take back (z)', onClick: () => solver?.undo() }, 'Undo') : null,
      !completed && puzzle ? h('button', { class: 'btn ghost', onClick: () => solver?.showSolution() }, 'Show solution') : null,
      puzzle ? h('button', { class: `btn ${completed ? 'primary' : 'ghost'}`, title: 'Next (n)', onClick: () => void next() }, completed ? 'Next puzzle' : 'Skip') : null,
    );
    actions.hidden = !puzzle;
    status.hidden = !puzzle && !completed; // the empty board already says why
  }

  function paintInfo(showThemes: boolean) {
    info.hidden = !puzzle;
    if (!puzzle) return fill(info);
    const p = puzzle;
    fill(
      info,
      h(
        'div',
        { class: 'row gap wrap' },
        h('span', { class: 'chip' }, p.source === 'own' ? 'From your game' : 'Lichess'),
        h('span', { class: 'muted small' }, `Rating ${p.rating}`),
        p.review_box ? h('span', { class: 'muted small' }, `Review box ${p.review_box}`) : null,
      ),
      showThemes && p.themes.length
        ? h('div', { class: 'row gap wrap' }, p.themes.map((t) => h('span', { class: 'chip' }, THEME_LABELS[t] ?? t)))
        : h('div', { class: 'muted small' }, 'Themes are revealed after you solve it.'),
      showThemes && p.game_id
        ? h('a', { class: 'btn link', href: `#/game/${p.game_id}/${(p.ply ?? 1) - 1}` }, 'See the moment in your game →')
        : null,
    );
  }

  /** Prefer puzzles not yet shown this visit, but never hide a due review just because it was skipped earlier:
   *  fall back to "anything but the one on screen", then to anything. */
  async function fetchNext(): Promise<Puzzle | null> {
    const prev = puzzle?.id;
    const tries = [seen, prev != null ? [prev] : null, []].filter((x): x is number[] => x !== null);
    for (const exclude of tries) {
      const p = await api.nextPuzzle(mode, theme || undefined, exclude);
      if (p) return p;
    }
    return null;
  }

  async function next() {
    solver?.destroy();
    solver = null;
    completed = false;
    boardHost.replaceChildren();
    let failed = false;
    try {
      puzzle = await fetchNext();
    } catch (err) {
      puzzle = null;
      failed = true;
      toast((err as Error).message, { error: true });
    }
    if (destroyed) return;
    paintActions();
    if (!puzzle) {
      paintInfo(false);
      const [title, msg] = failed
        ? ['Could not load a puzzle', 'Check that the app is still running, then try again.']
        : mode === 'review'
          ? ['No reviews due', 'Puzzles you miss come back here on a schedule (1, 3, 7, 14, 30 days).']
          : mode === 'own'
            ? ['No new puzzles from your games', 'Analyze more games; your mistakes become puzzles automatically.']
            : theme
              ? ['No puzzle for this theme', 'Pick another theme, or switch to All themes.']
              : ['No puzzles available', summary.lichess_count ? 'You have solved everything near your rating.' : 'Import the Lichess puzzle database below, or analyze games to get puzzles from your own mistakes.'];
      const acts = [
        failed ? h('button', { class: 'btn primary', onClick: () => void next() }, 'Try again') : null,
        !failed && theme ? h('button', { class: 'btn primary', onClick: () => { theme = ''; paintTabs(); void next(); } }, 'Show all themes') : null,
        !failed && !theme && mode !== 'mixed' ? h('button', { class: 'btn ghost', onClick: () => switchMode('mixed') }, 'Solve mixed puzzles') : null,
      ];
      boardHost.append(h('div', { class: 'board-empty' }, icon('puzzle'), h('h2', {}, title), h('p', { class: 'muted' }, msg), h('div', { class: 'row gap' }, acts)));
      setStatus('idle', title);
      return;
    }
    seen.push(puzzle.id);
    const host = h('div', { class: 'cg-host' });
    boardHost.append(host);
    const p = puzzle;
    solver = createSolver(
      host,
      { fen: p.fen, setup: p.moves[0], line: p.moves.slice(1) },
      {
        state: (s, extra) => {
          const side = solver?.solverColor === 'black' ? 'Black' : 'White';
          if (s === 'loading') setStatus('idle', 'Get ready…');
          if (s === 'your-turn') setStatus('turn', `Your turn`, `Find the best move for ${side}.`);
          if (s === 'correct') setStatus('good', `${extra} — correct!`, 'Keep going.');
          if (s === 'wrong') setStatus('bad', `${extra} is not the move`, 'Press Z (or Undo) to try again, or ask for a hint.');
          if (s === 'solved') setStatus('good', 'Solved!', extra ? `${extra} works too.` : '');
          if (s === 'done') setStatus('idle', 'Puzzle complete', 'It will come back for review.');
        },
        complete: (r) => void completed_(p, r),
      },
    );
    setTimeout(() => solver?.board.cg.redrawAll(), 0);
    paintInfo(false);
  }

  async function completed_(p: Puzzle, r: SolverResult) {
    completed = true;
    paintActions();
    paintInfo(true);
    try {
      const res = await api.attempt(p.id, r.solved, r.hints, r.seconds);
      summary = await api.puzzleSummary();
      paintRating(res.rated ? res.delta : undefined);
      paintTabs();
      if (mode === 'plan' && summary.today === 5) toast("Today's 5 puzzles are done. Nice work!");
    } catch (err) {
      toast((err as Error).message, { error: true });
    }
  }

  async function paintLichess() {
    if (summary.lichess_count) return fill(lichessBox);
    const s = await api.lichessStatus();
    if (destroyed) return;
    const path = h('input', { placeholder: 'Path to lichess_db_puzzle.csv.zst', class: 'grow' });
    const startImport = async (p: string) => {
      try {
        await api.lichessImport(p);
        void paintLichess();
      } catch (err) {
        toast((err as Error).message, { error: true });
      }
    };
    const choose = async () => {
      const r = await api.pickFile();
      if (r.path) void startImport(r.path);
      else if (!r.supported) path.focus();
    };
    if (s.import.running) {
      fill(
        lichessBox,
        h(
          'div',
          { class: 'card stack' },
          h('strong', {}, 'Importing Lichess puzzles…'),
          h('div', { class: 'progress' }, h('div', { class: 'progress-fill', style: `width:${Math.round(s.import.progress * 100)}%` })),
          h('span', { class: 'muted small' }, `${s.import.imported.toLocaleString()} puzzles so far. You can keep solving meanwhile.`),
        ),
      );
      setTimeout(() => void (destroyed ? null : refreshAfterImport()), 1500);
      return;
    }
    fill(
      lichessBox,
      h(
        'div',
        { class: 'card stack' },
        h('strong', {}, 'Add the Lichess puzzle database'),
        h(
          'p',
          { class: 'small muted' },
          'Download lichess_db_puzzle.csv.zst once from database.lichess.org (free, CC0, about 250 MB), then import it here. Popular puzzles are kept and indexed by rating and theme.',
        ),
        s.import.error ? h('p', { class: 'error-text small' }, s.import.error) : null,
        s.candidates.map((c) => h('button', { class: 'btn primary', onClick: () => startImport(c) }, `Import ${c}`)),
        h('div', { class: 'row gap' }, h('button', { class: 'btn ghost', onClick: choose }, 'Choose file…'), path,
          h('button', { class: 'btn ghost', onClick: () => path.value.trim() && startImport(path.value) }, 'Import')),
      ),
    );
  }

  async function refreshAfterImport() {
    summary = await api.puzzleSummary();
    paintTabs();
    await paintLichess();
    if (summary.lichess_count && !puzzle) void next();
  }

  paintRating();
  paintTabs();
  paintActions();
  paintInfo(false);
  void paintLichess().catch(() => {});
  if (!summary.attempts && !summary.own_total && !summary.lichess_count) {
    boardHost.append(
      emptyState('board', 'No puzzles yet', 'Analyze your games to turn your own mistakes into puzzles, or import the Lichess puzzle database.', [
        h('a', { class: 'btn primary', href: '#/library' }, 'Go to Library'),
      ]),
    );
    setStatus('idle', 'Waiting for puzzles');
  } else {
    await next();
  }

  return {
    onKey(e) {
      const k = e.key.toLowerCase();
      if (k === 'z') solver?.undo();
      else if (k === 'h') solver?.hint();
      else if (k === 'n' || (k === 'enter' && completed)) void next();
      else return;
      e.preventDefault();
    },
    destroy() {
      destroyed = true;
      solver?.destroy();
    },
  };
}
