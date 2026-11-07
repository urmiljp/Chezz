import { Chess } from 'chess.js';

import { api, type Color, type DrillCard, type ExplorePosition } from './api';
import type { View } from './main';
import { PlayBoard, toKeys } from './playboard';
import { emptyState, fill, h, resultBadge, toast } from './ui';

const START = new Chess().fen();

function replay(line: string[]): { fen: string; sans: string[] } {
  const c = new Chess();
  const sans = line.map((u) => c.move({ from: u.slice(0, 2), to: u.slice(2, 4), promotion: u[4] }).san);
  return { fen: c.fen(), sans };
}

function lineLabel(sans: string[], start = 0): string {
  return sans.map((s, i) => ((i + start) % 2 === 0 ? `${Math.floor((i + start) / 2) + 1}. ${s}` : s)).join(' ');
}

export async function renderOpenings(root: HTMLElement, params: URLSearchParams): Promise<View> {
  let color: Color = params.get('color') === 'black' ? 'black' : 'white';
  let tab = params.get('tab') ?? 'drill';
  let summary = await api.openingsSummary();
  let board: PlayBoard | null = null;
  let destroyed = false;
  let timer: number | undefined;
  let seq = 0; // bumped on every screen change; async loads for an older screen are dropped
  const fail = (err: unknown) => toast((err as Error).message, { error: true });

  const colorSeg = h('div', { class: 'seg' });
  const tabSeg = h('div', { class: 'seg' });
  const content = h('div');
  root.append(
    h(
      'header',
      { class: 'page-head' },
      h('h1', {}, 'Openings'),
      colorSeg,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn ghost', title: 'Add positions from your newest games', onClick: rebuild }, 'Update from my games'),
    ),
    h('div', { class: 'row gap', style: 'margin-bottom:14px' }, tabSeg),
    content,
  );

  function paintHeader() {
    fill(
      colorSeg,
      (['white', 'black'] as Color[]).map((c) =>
        h(
          'button',
          { class: `seg-btn${c === color ? ' on' : ''}`, onClick: () => { color = c; show(); } },
          h('span', { class: `piece-dot ${c}` }),
          c === 'white' ? 'White' : 'Black',
          summary[c].due ? h('span', { class: 'count' }, String(summary[c].due)) : null,
        ),
      ),
    );
    fill(
      tabSeg,
      ([['drill', 'Drill'], ['explore', 'Repertoire'], ['deviations', 'Where you left book']] as [string, string][]).map(([t, l]) =>
        h('button', { class: `seg-btn${t === tab ? ' on' : ''}`, onClick: () => { tab = t; show(); } }, l),
      ),
    );
  }

  async function rebuild() {
    try {
      const res = await api.buildRepertoire(color);
      summary = await api.openingsSummary();
      toast(res.added ? `Added ${res.added} positions to your ${color} repertoire` : 'Repertoire is up to date');
    } catch (err) {
      return fail(err);
    }
    if (!destroyed) show();
  }

  function reset() {
    seq += 1;
    clearTimeout(timer);
    board?.destroy();
    board = null;
    content.replaceChildren();
    paintHeader();
  }

  function show() {
    reset();
    if (tab === 'deviations') void deviations();
    else if (!summary[color].cards) buildPrompt();
    else if (tab === 'explore') void explore([]);
    else void drill();
  }

  function buildPrompt() {
    content.append(
      emptyState(
        'library',
        `No ${color} repertoire yet`,
        summary[color].games
          ? `Build it from your ${summary[color].games} games as ${color}: your usual moves become the repertoire, and you can change any of them.`
          : `Import some games where you played ${color} first; your usual moves become the repertoire.`,
        [
          summary[color].games
            ? h('button', { class: 'btn primary', onClick: rebuild }, 'Build my repertoire')
            : h('a', { class: 'btn primary', href: '#/library' }, 'Go to Library'),
        ],
      ),
    );
  }

  function layout() {
    const host = h('div', { class: 'cg-host' });
    const side = h('div', { class: 'side-col' });
    content.append(h('div', { class: 'game-grid' }, h('div', { class: 'board-col' }, h('div', { class: 'board-wrap' }, host)), side));
    return { host, side };
  }

  // ---------- drill ----------
  async function drill() {
    const my = seq;
    let card: DrillCard | null;
    try {
      card = await api.drillCard(color);
    } catch (err) {
      card = null;
      fail(err);
    }
    if (!card) {
      if (destroyed || my !== seq) return;
      content.append(
        emptyState('today', 'All caught up', `No ${color} positions are due. Cards come back after 1, 3, 7, 14 and 30 days.`, [
          h('button', { class: 'btn ghost', onClick: () => { tab = 'explore'; show(); } }, 'Browse repertoire'),
        ]),
      );
      return;
    }
    if (destroyed || my !== seq) return;
    const { host, side } = layout();
    const { sans } = replay(card.line);
    const before = replay(card.line.slice(0, -1)).fen;
    const status = h('div', { class: 'status-card card status-turn' });
    const actions = h('div', { class: 'row gap' });
    side.append(
      h('div', { class: 'card stack' }, h('span', { class: 'muted small' }, `${card.due_left} due · box ${card.box}`), h('strong', {}, sans.length ? lineLabel(sans) : 'Starting position')),
      status,
      actions,
    );
    fill(status, h('strong', {}, 'Your move'), h('div', { class: 'small' }, `What do you play here as ${color}?`));
    board = new PlayBoard(host, {
      fen: card.line.length ? before : START,
      orientation: color,
      onMove: async (m) => {
        board?.setUser('none');
        const correct = m.uci === card.move_uci;
        try {
          await api.gradeCard(color, card.epd, correct);
          summary = await api.openingsSummary();
        } catch (err) {
          fail(err);
        }
        if (destroyed || my !== seq) return;
        paintHeader();
        if (correct) {
          status.className = 'status-card card status-good';
          fill(status, h('strong', {}, `${m.san} — that's your move.`));
          timer = window.setTimeout(() => show(), 700);
        } else {
          status.className = 'status-card card status-bad';
          fill(status, h('strong', {}, `Your repertoire move is ${card.san}.`), h('div', { class: 'small' }, 'This position comes back tomorrow.'));
          board?.undo();
          board?.shapes([{ orig: toKeys(card.move_uci)[0], dest: toKeys(card.move_uci)[1], brush: 'green' }]);
          fill(actions, h('button', { class: 'btn primary', onClick: () => show() }, 'Next position'));
        }
      },
    });
    if (card.line.length) setTimeout(() => board?.play(card.line[card.line.length - 1]), 300);
    setTimeout(() => board?.setUser(color), 350);
  }

  // ---------- explore ----------
  let showAll = false;
  async function explore(line: string[], keepAll = false) {
    showAll = keepAll && showAll;
    reset();
    const my = seq;
    const { fen, sans } = replay(line);
    let pos: ExplorePosition;
    try {
      pos = await api.explore(color, fen);
    } catch (err) {
      return fail(err);
    }
    if (destroyed || my !== seq) return;
    const { host, side } = layout();
    board = new PlayBoard(host, { fen, orientation: color, onMove: (m) => void explore([...line, m.uci]) });
    if (line.length) board.cg.set({ lastMove: toKeys(line[line.length - 1]) });
    board.setUser(board.turn);
    if (pos.repertoire_move && pos.your_turn) {
      const [o, d] = toKeys(pos.repertoire_move);
      board.shapes([{ orig: o, dest: d, brush: 'green' }]);
    }
    const crumbs = h(
      'div',
      { class: 'crumbs' },
      h('button', { class: 'link', onClick: () => explore([]) }, 'Start'),
      sans.map((s, i) => h('button', { class: 'link', onClick: () => explore(line.slice(0, i + 1)) }, (i % 2 === 0 ? `${i / 2 + 1}. ` : '') + s)),
    );
    // Your moves first; book-only moves are many (every first move has a name), so show a few.
    const played = pos.candidates.filter((c) => c.games || c.repertoire);
    const bookOnly = pos.candidates.filter((c) => !c.games && !c.repertoire);
    const shown = showAll ? pos.candidates : [...played, ...bookOnly.slice(0, 6)];
    const more = !showAll && bookOnly.length > 6
      ? h('button', { class: 'btn link', onClick: () => { showAll = true; void explore(line, true); } }, `Show ${bookOnly.length - 6} more book moves`)
      : null;
    side.append(
      h(
        'div',
        { class: 'card stack' },
        crumbs,
        pos.name ? h('div', {}, h('span', { class: 'muted small' }, `${pos.eco} `), pos.name)
          : h('div', { class: 'muted small' }, !line.length ? 'Starting position' : pos.candidates.some((c) => c.book) ? 'Book position' : 'Out of the opening book'),
        h('div', { class: 'small muted' }, pos.your_turn ? 'Your move. Green arrow: your repertoire choice.' : 'Opponent to move: replies seen in your games and the book.'),
      ),
      h(
        'div',
        { class: 'card' },
        pos.candidates.length
          ? h(
              'table',
              { class: 'counts cands' },
              h('tr', {}, h('th', {}, 'Move'), h('th', {}, 'Games'), h('th', {}, 'Score'), h('th', {}, ''), h('th', {}, '')),
              shown.map((c) =>
                h(
                  'tr',
                  { class: c.repertoire ? 'rep' : '' },
                  h('td', {}, h('button', { class: 'link', onClick: () => explore([...line, c.uci]) }, c.san)),
                  h('td', {}, String(c.games || '–')),
                  h('td', {}, c.score == null ? '–' : `${c.score}%`),
                  h('td', {}, c.book ? h('span', { class: 'chip' }, 'book') : null),
                  h(
                    'td',
                    {},
                    c.repertoire
                      ? h('span', { class: 'chip on' }, 'your move')
                      : pos.your_turn
                        ? h('button', { class: 'btn ghost sm', onClick: async () => {
                            try {
                              await api.setRepertoire(color, fen, line, c.uci);
                              summary = await api.openingsSummary();
                            } catch (err) {
                              return fail(err);
                            }
                            if (my === seq) void explore(line);
                          } }, 'Make my move')
                        : null,
                  ),
                ),
              ),
            )
          : h('p', { class: 'muted small' }, 'No games or book moves from here.'),
        more,
      ),
    );
  }

  // ---------- deviations ----------
  async function deviations() {
    const my = seq;
    let rows;
    try {
      rows = await api.deviations();
    } catch (err) {
      return fail(err);
    }
    if (destroyed || my !== seq) return;
    if (!rows.length) {
      content.append(emptyState('library', 'No games yet', 'Import games to see where they left the book.', [h('a', { class: 'btn primary', href: '#/library' }, 'Go to Library')]));
      return;
    }
    content.append(h('div', { class: 'table-scroll' },
      h(
        'table',
        { class: 'games' },
        h('thead', {}, h('tr', {}, ['Date', 'Game', 'Result', 'Left the book', 'Left your repertoire'].map((t) => h('th', {}, t)))),
        h(
          'tbody',
          {},
          rows.map((r) =>
            h(
              'tr',
              { onClick: () => (location.hash = `#/game/${r.game_id}/${Math.max(0, (r.left_book?.ply ?? 1) - 1)}`) },
              h('td', { class: 'nowrap muted' }, r.date ?? '—'),
              h('td', { class: 'players' }, `${r.white} vs ${r.black}`),
              h('td', {}, r.user_result ? resultBadge({ user_result: r.user_result, result: '' }) : null),
              h('td', {}, r.left_book
                ? [h('strong', {}, r.left_book.move), h('span', { class: 'muted small' }, ` by ${r.left_book.by ?? '?'}`),
                   r.left_book.book_moves.length ? h('div', { class: 'muted small' }, `Book: ${r.left_book.book_moves.join(', ')}`) : null]
                : h('span', { class: 'muted' }, 'Stayed in book')),
              h('td', {}, r.left_repertoire
                ? [h('strong', {}, r.left_repertoire.move), h('div', { class: 'muted small' }, `Repertoire: ${r.left_repertoire.expected}`)]
                : h('span', { class: 'muted' }, '—')),
            ),
          ),
        ),
      ),
    ));
  }

  show();
  return {
    destroy() {
      destroyed = true;
      clearTimeout(timer);
      board?.destroy();
    },
  };
}

