import { Chessground } from '@lichess-org/chessground';
import type { Api } from '@lichess-org/chessground/api';
import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';

import { api, type Classification, type Color, type GameDetail, type LineMove, type Meta, type Review } from './api';
import { CLASS_COLORS, evalGraph, type EvalGraph } from './evalgraph';
import type { View } from './main';
import { destroyBoard, watchSize } from './playboard';
import { debounce, fill, fmtClock, h, icon, modal, resultBadge, toast } from './ui';

const CLASSES: Classification[] = ['best', 'good', 'inaccuracy', 'mistake', 'blunder'];
const GLYPH: Record<Classification, string> = { best: '', good: '', inaccuracy: '?!', mistake: '?', blunder: '??' };
const LABEL: Record<Classification, string> = {
  best: 'Best',
  good: 'Good',
  inaccuracy: 'Inaccuracy',
  mistake: 'Mistake',
  blunder: 'Blunder',
};
const isError = (c: Classification) => c === 'inaccuracy' || c === 'mistake' || c === 'blunder';
const keys = (uci: string) => [uci.slice(0, 2), uci.slice(2, 4)] as Key[];

export async function renderGame(root: HTMLElement, id: number, startPly?: number): Promise<View> {
  const [g, meta, firstReview]: [GameDetail, Meta, Review] = await Promise.all([api.game(id), api.meta(), api.review(id)]);
  let review = firstReview;
  const startWhite = g.start_fen.split(' ')[1] === 'w';
  const firstMoveNo = Number(g.start_fen.split(' ')[5] ?? 1);
  let ply = startPly ?? g.moves.length; // final position by default, like most review tools
  let orientation: Color = g.user_color ?? 'white';
  let preview: LineMove | null = null;
  let graph: EvalGraph | undefined;
  let pollTimer: number | undefined;
  let destroyed = false;

  const moverOf = (i: number): Color => ((i % 2 === 0) === startWhite ? 'white' : 'black');
  const moveLabel = (i: number) =>
    `${Math.floor((i + (startWhite ? 0 : 1)) / 2) + firstMoveNo}${moverOf(i) === 'white' ? '.' : '...'}`;
  const analysed = () => !!review.run && review.moves.length === g.moves.length;

  // ---------- board ----------
  const boardEl = h('div', { class: 'cg-host' });
  const evalFill = h('div', { class: 'eval-fill' });
  const evalBar = h('div', { class: 'eval-bar' }, evalFill);
  const topBar = h('div', { class: 'player-bar' });
  const bottomBar = h('div', { class: 'player-bar' });
  const starBtn = h('button', { class: 'icon-btn star', title: 'Star (s)', onClick: toggleStar });

  // Created once boardEl is in the page (below): chessground measures the board when it starts,
  // and a detached element measures 0x0, which breaks arrow drawing.
  let cg!: Api;
  let resize!: ResizeObserver;

  // ---------- move list ----------
  const moveList = h('div', { class: 'move-list' });
  const cells: HTMLButtonElement[] = [];
  const rows: HTMLElement[] = [];
  g.moves.forEach((_, i) => {
    if (moverOf(i) === 'white' || i === 0) {
      const row = h('div', { class: 'move-row' }, h('span', { class: 'move-no' }, moveLabel(i).replace(/\.+$/, '.')));
      if (moverOf(i) === 'black') row.append(h('span', { class: 'move empty' }, '…'));
      rows.push(row);
    }
    const cell = h('button', { class: 'move', onClick: () => go(i + 1) });
    cells.push(cell);
    rows[rows.length - 1].append(cell);
  });
  moveList.append(...rows);
  const movesCard = h('section', { class: 'card moves-card' }, moveList);

  function paintCells() {
    g.moves.forEach((m, i) => {
      const r = analysed() ? review.moves[i] : null;
      const cell = cells[i];
      cell.className = `move${r && isError(r.classification) ? ` cls-${r.classification}` : ''}`;
      fill(
        cell,
        h('span', {}, m.san, r ? h('span', { class: 'glyph' }, GLYPH[r.classification]) : null),
        h(
          'span',
          { class: 'move-clock' },
          r?.time_trouble ? h('span', { class: 'tt', title: 'Played in time trouble' }, icon('clock')) : null,
          fmtClock(m.clock),
        ),
      );
    });
    cells.forEach((c, i) => c.classList.toggle('current', i === ply - 1));
  }

  function clockFor(color: Color): number | null {
    for (let i = ply - 1; i >= 0; i--) if (moverOf(i) === color) return g.moves[i].clock;
    return null;
  }

  function paintBars() {
    const bar = (color: Color) => {
      const clock = clockFor(color);
      return [
        h('span', { class: `piece-dot ${color}` }),
        h('span', { class: g.user_color === color ? 'me' : '' }, color === 'white' ? g.white : g.black),
        (color === 'white' ? g.white_elo : g.black_elo) ? h('span', { class: 'muted small' }, ` ${color === 'white' ? g.white_elo : g.black_elo}`) : null,
        h('span', { class: 'spacer' }),
        clock != null ? h('span', { class: `clock${clock < 30 ? ' low' : ''}` }, fmtClock(clock)) : null,
      ];
    };
    fill(topBar, bar(orientation === 'white' ? 'black' : 'white'));
    fill(bottomBar, bar(orientation));
  }

  function paintEvalBar() {
    const pos = analysed() ? review.positions[ply] : null;
    evalBar.hidden = !pos;
    if (!pos) return;
    evalFill.style.height = `${pos.win}%`;
    evalBar.classList.toggle('flipped', orientation === 'black');
    evalBar.title = `Evaluation: ${pos.eval}`;
  }

  function bestArrow(): DrawShape[] {
    if (!analysed() || preview) return [];
    const best = ply === 0 ? review.moves[0]?.best_uci : review.moves[ply - 1]?.next_best_uci;
    if (!best) return [];
    const [orig, dest] = keys(best);
    return [{ orig, dest, brush: 'paleBlue' }];
  }

  function go(target: number) {
    ply = Math.max(0, Math.min(g.moves.length, target));
    preview = null;
    const m = g.moves[ply - 1];
    const fen = m ? m.fen : g.start_fen;
    const turn: Color = fen.split(' ')[1] === 'w' ? 'white' : 'black';
    cg.set({ fen, turnColor: turn, check: m?.check ? turn : false, lastMove: m ? keys(m.uci) : undefined });
    cg.setAutoShapes(bestArrow());
    cells.forEach((c, i) => c.classList.toggle('current', i === ply - 1));
    scrollMoveList(cells[ply - 1]);
    graph?.setPly(ply);
    paintBars();
    paintEvalBar();
    paintMoveCard();
  }

  /** Keep the current move visible inside the move list without scrolling the page itself. */
  function scrollMoveList(cell: HTMLElement | undefined) {
    if (!cell) {
      movesCard.scrollTop = 0;
      return;
    }
    const top = cell.offsetTop;
    const bottom = top + cell.offsetHeight;
    if (top < movesCard.scrollTop) movesCard.scrollTop = top - 8;
    else if (bottom > movesCard.scrollTop + movesCard.clientHeight) movesCard.scrollTop = bottom - movesCard.clientHeight + 8;
  }

  function showPreview(lm: LineMove) {
    preview = lm;
    const turn: Color = lm.fen.split(' ')[1] === 'w' ? 'white' : 'black';
    cg.set({ fen: lm.fen, turnColor: turn, check: lm.san.includes('+') || lm.san.includes('#') ? turn : false, lastMove: keys(lm.uci) });
    cg.setAutoShapes([]);
    paintMoveCard();
  }

  function flip() {
    orientation = orientation === 'white' ? 'black' : 'white';
    cg.set({ orientation });
    paintBars();
    paintEvalBar();
  }

  // ---------- current move card ----------
  const moveCard = h('section', { class: 'card move-card' });

  function paintMoveCard() {
    moveCard.hidden = !analysed();
    if (!analysed()) return;
    if (ply === 0) {
      fill(moveCard, h('p', { class: 'muted' }, 'Start position. Step through the game with ← and →, or click the graph.'));
      return;
    }
    const m = review.moves[ply - 1];
    const better = m.best_uci && m.best_uci !== m.uci && m.classification !== 'best';
    fill(
      moveCard,
      h(
        'div',
        { class: 'mc-head' },
        h('span', { class: `cls-badge cls-${m.classification}` }, LABEL[m.classification]),
        h('strong', {}, `${moveLabel(ply - 1)} ${m.san}${GLYPH[m.classification]}`),
        h('span', { class: 'spacer' }),
        h('span', { class: 'eval-chip', title: "Evaluation after the move (White's view)" }, m.eval_after),
      ),
      h('p', { class: 'explanation' }, m.explanation, m.explanation_source === 'llm' ? h('span', { class: 'src-tag' }, 'coach') : null),
      m.tags.length
        ? h('div', { class: 'row gap wrap tag-row' }, m.tags.map((t) =>
            h('span', { class: `chip motif-${t.kind}`, title: t.kind === 'allowed' ? 'Your opponent could punish this with' : 'The better move used' },
              `${t.kind === 'allowed' ? 'Allows' : 'Missed'}: ${t.label}`)))
        : null,
      better
        ? h(
            'div',
            { class: 'best-line' },
            h('span', { class: 'muted small' }, `Best was ${m.best_san} (${m.eval_before}):`),
            m.best_line.map((lm) =>
              h('button', { class: `line-move${preview === lm ? ' on' : ''}`, title: 'Show on board', onClick: () => showPreview(lm) }, lm.san),
            ),
          )
        : null,
      preview ? h('div', { class: 'muted small' }, 'Previewing engine line · press ← or → to return to the game') : null,
      m.time_spent != null
        ? h(
            'div',
            { class: `muted small${m.time_trouble ? ' tt-text' : ''}` },
            m.time_trouble ? icon('clock') : null,
            `Spent ${fmtClock(m.time_spent)}`,
            m.clock != null ? ` · ${fmtClock(m.clock)} left` : '',
            m.time_trouble ? ' · time trouble' : '',
          )
        : null,
    );
  }

  // ---------- analysis card ----------
  const progressEl = h('div');
  const analysisBody = h('div', { class: 'stack' });
  const analysisCard = h('section', { class: 'card analysis-card' }, progressEl, analysisBody);

  function paintProgress() {
    const p = review.pending;
    if (!p || p.status === 'done') return fill(progressEl);
    if (p.status === 'error') return fill(progressEl, h('p', { class: 'error-text' }, `Analysis failed: ${p.error}`));
    const pct = p.total ? Math.round((100 * p.progress) / p.total) : 0;
    fill(
      progressEl,
      h(
        'div',
        { class: 'progress-row' },
        h('span', { class: 'small' }, p.status === 'queued' ? 'Waiting for the engine…' : `Analyzing move ${Math.max(0, p.progress - 1)} of ${p.total - 1}…`),
        h('div', { class: 'progress' }, h('div', { class: 'progress-fill', style: `width:${pct}%` })),
      ),
    );
  }

  // ---------- explanations card: export for an AI chat, import its reply, re-run the engine ----------
  const coachCard = h('section', { class: 'card coach-card' });

  function paintCoach() {
    const run = review.run;
    coachCard.hidden = !run || !analysed();
    if (!run || !analysed()) return;
    const busy = review.pending && review.pending.status !== 'error';
    const keyCount = review.moves.filter((m) => isError(m.classification)).length;
    const fromCoach = review.moves.filter((m) => m.explanation_source === 'llm').length;
    const exportBtn = (scope: 'game' | 'key', label: string, sub: string, n: number) =>
      h('button', { class: 'btn ghost export-btn', disabled: !n, title: n ? '' : 'Nothing to export', onClick: () => exportBatch(scope) },
        h('span', { class: 'row gap' }, icon('download'), label), h('small', {}, sub));
    fill(
      coachCard,
      h('div', { class: 'row gap' }, h('h3', {}, 'Explanations'), h('span', { class: 'spacer' }),
        fromCoach ? h('span', { class: 'src-tag' }, `${fromCoach} of ${review.moves.length} from your coach`) : null),
      review.coach_summary ? h('div', { class: 'coach-summary' }, h('strong', {}, 'Coach summary'), h('p', {}, review.coach_summary)) : null,
      h('p', { class: 'muted small' },
        'Explanations are written from the engine data. For a coach-style review, export this game, paste it into any AI chat, then import its reply here.'),
      h('div', { class: 'export-row' },
        exportBtn('game', 'Whole game', `Every move, ${review.moves.length} in all`, review.moves.length),
        exportBtn('key', 'Key moments', keyCount === 1 ? 'The 1 inaccuracy, mistake or blunder' : keyCount ? `The ${keyCount} inaccuracies, mistakes and blunders` : 'No errors in this game', keyCount)),
      h('button', { class: 'btn primary', onClick: importExplanations }, icon('upload'), 'Import reply'),
      h('div', { class: 'analysis-foot' },
        h('span', { class: 'muted small' }, `${run.engine ?? 'Engine'}, ${run.movetime_ms ? `${run.movetime_ms} ms per move` : `depth ${run.depth}`}`),
        h('span', { class: 'spacer' }),
        h('button', { class: 'btn ghost sm', onClick: analyze, disabled: !!busy || !meta.engine.available, title: 'Analyze this game again with the current engine settings' },
          icon('engine'), 'Re-run analysis')),
    );
  }

  function paintAnalysis() {
    graph?.destroy();
    graph = undefined;
    paintProgress();
    paintCoach();
    const run = review.run;
    const busy = review.pending && review.pending.status !== 'error';

    if (!run || !analysed()) {
      if (busy) return fill(analysisBody);
      fill(
        analysisBody,
        meta.engine.available
          ? h(
              'div',
              { class: 'analyze-cta' },
              h(
                'div',
                {},
                h('strong', {}, 'Review this game with Stockfish'),
                h('div', { class: 'muted small' }, 'Classifies every move, finds the critical moments and draws the evaluation graph.'),
              ),
              h('button', { class: 'btn primary', onClick: analyze }, review.pending ? 'Retry' : 'Analyze'),
            )
          : h('div', { class: 'notice' }, 'Stockfish was not found. Install it and set ', h('code', {}, 'path'), ' under [engine] in config.toml, then restart.'),
      );
      return;
    }

    const acc = (color: Color) =>
      h(
        'div',
        { class: `acc${g.user_color === color ? ' me' : ''}` },
        h('div', { class: 'acc-num' }, (color === 'white' ? run.white_accuracy : run.black_accuracy)?.toFixed(1) ?? '–'),
        h('div', { class: 'muted small' }, h('span', { class: `piece-dot ${color}` }), color === 'white' ? g.white : g.black),
      );

    const errorsInTrouble = (c: Color) => review.moves.filter((m) => m.color === c && m.time_trouble && isError(m.classification)).length;
    const counts = h(
      'table',
      { class: 'counts' },
      h('tr', {}, h('th', {}, ''), h('th', {}, 'White'), h('th', {}, 'Black')),
      CLASSES.map((c) =>
        h(
          'tr',
          {},
          h('td', {}, h('span', { class: 'cls-dot', style: `background:${CLASS_COLORS[c]}` }), LABEL[c]),
          h('td', {}, String(run.counts?.white[c] ?? 0)),
          h('td', {}, String(run.counts?.black[c] ?? 0)),
        ),
      ),
      h(
        'tr',
        { title: 'Moves started with little time left (errors in brackets)' },
        h('td', {}, h('span', { class: 'cls-dot tt-dot' }), 'Time trouble'),
        (['white', 'black'] as Color[]).map((c) => {
          const n = run.time_trouble?.[c] ?? 0;
          return h('td', {}, n ? `${n} (${errorsInTrouble(c)})` : '0');
        }),
      ),
    );

    const critical = review.moves.filter((m) => m.classification === 'mistake' || m.classification === 'blunder');
    const criticalList = h(
      'div',
      { class: 'critical' },
      h('h3', {}, 'Critical moments'),
      critical.length
        ? critical.map((m) =>
            h(
              'button',
              { class: `crit cls-${m.classification}`, onClick: () => go(m.ply) },
              h('span', { class: `piece-dot ${m.color}` }),
              h('span', { class: 'crit-move' }, `${moveLabel(m.ply - 1)} ${m.san}${GLYPH[m.classification]}`),
              h('span', { class: 'muted small' }, `−${Math.round(m.win_drop)}%`),
              m.time_trouble ? h('span', { class: 'tt', title: 'Time trouble' }, icon('clock')) : null,
            ),
          )
        : h('p', { class: 'muted small' }, 'No mistakes or blunders in this game.'),
    );

    const graphEl = h('div', { class: 'graph', title: 'Click to jump to a move' });
    fill(
      analysisBody,
      h('div', { class: 'acc-row' }, acc('white'), acc('black')),
      graphEl,
      h('div', { class: 'analysis-grid' }, counts, criticalList),
    );
    graph = evalGraph(
      graphEl,
      review.positions.map((p) => p.win),
      review.moves.filter((m) => isError(m.classification)).map((m) => ({ ply: m.ply, cls: m.classification })),
      go,
    );
    graph.setPly(ply);
  }

  let markedReviewed = false;
  function applyReview() {
    if (analysed() && !markedReviewed) {
      markedReviewed = true;
      void api.markReviewed(g.id).catch(() => (markedReviewed = false));
    }
    paintCells();
    paintAnalysis();
    go(ply);
  }

  async function analyze() {
    try {
      review = await api.analyze(g.id);
      paintAnalysis();
      poll();
    } catch (err) {
      toast((err as Error).message, { error: true });
    }
  }

  function poll() {
    clearTimeout(pollTimer);
    if (destroyed || !review.pending || review.pending.status === 'error') return;
    pollTimer = window.setTimeout(async () => {
      const next = await api.review(g.id).catch(() => null);
      if (destroyed) return;
      if (!next) return void (pollTimer = window.setTimeout(poll, 3000)); // app busy or restarting: try again
      const changed = next.run?.id !== review.run?.id || next.pending?.status !== review.pending?.status;
      review = next;
      if (changed) applyReview();
      else paintProgress();
      poll();
    }, 700);
  }

  async function exportBatch(scope: 'game' | 'key') {
    try {
      const res = await api.llmBatch(g.id, scope);
      const text = h('textarea', { class: 'paste', readOnly: true, value: res.text });
      const copy = h('button', { class: 'btn primary' }, 'Copy to clipboard');
      copy.addEventListener('click', async () => {
        const ok = await navigator.clipboard.writeText(res.text).then(() => true, () => false);
        if (!ok) text.select();
        copy.textContent = ok ? 'Copied' : 'Selected: press Ctrl+C';
      });
      const m = modal(
        scope === 'game' ? 'Whole game, ready for an AI chat' : 'Key moments, ready for an AI chat',
        h(
          'div',
          { class: 'stack' },
          h('p', { class: 'small' }, `${res.count} move${res.count === 1 ? '' : 's'} with the engine's verdicts. Copy it into any AI chat assistant, `,
            'then come back and use Import reply with its whole answer. Also saved to ', h('code', {}, res.path), '.'),
          text,
        ),
        [
          h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Close'),
          h('button', { class: 'btn ghost', onClick: () => { m.close(); importExplanations(); } }, 'Import reply'),
          copy,
        ],
      );
    } catch (err) {
      toast((err as Error).message, { error: true });
    }
  }

  function importExplanations() {
    const text = h('textarea', { class: 'paste', placeholder: '### SUMMARY …\n…\n### KEY <fen>|<move>\nExplanation…' });
    const status = h('div');
    const go2 = h('button', { class: 'btn primary' }, 'Import');
    go2.addEventListener('click', async () => {
      try {
        const res = await api.importExplanations(text.value);
        m.close();
        const parts = [res.imported ? `${res.imported} explanation${res.imported === 1 ? '' : 's'}` : '', res.summaries ? 'the game summary' : ''];
        toast(`Imported ${parts.filter(Boolean).join(' and ')}`);
        review = await api.review(g.id);
        applyReview();
      } catch (err) {
        fill(status, h('p', { class: 'error-text' }, (err as Error).message));
      }
    });
    const m = modal('Import reply', h('div', { class: 'stack' }, h('p', { class: 'small muted' }, "Paste the assistant's whole reply. Its explanations replace the built-in ones for those moves."), text, status), [
      h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Cancel'),
      go2,
    ]);
    text.focus();
  }

  // ---------- star / tags / notes / delete ----------
  function paintStar() {
    starBtn.classList.toggle('on', g.starred);
    fill(starBtn, icon('star', g.starred));
  }

  const fail = (err: unknown) => toast((err as Error).message, { error: true });

  async function toggleStar() {
    try {
      await api.patch(g.id, { starred: !g.starred });
    } catch (err) {
      return fail(err);
    }
    g.starred = !g.starred;
    paintStar();
  }

  const tagBox = h('div', { class: 'tags' });
  const tagInput = h('input', { class: 'tag-input', placeholder: 'Add tag…', maxLength: 40 });
  tagInput.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter' && tagInput.value.trim()) {
      await saveTags([...g.tags, tagInput.value.trim()]);
      tagInput.value = '';
    }
  });
  async function saveTags(tags: string[]) {
    try {
      g.tags = (await api.patch(g.id, { tags: [...new Set(tags)] })).tags;
    } catch (err) {
      return fail(err);
    }
    paintTags();
  }
  function paintTags() {
    fill(
      tagBox,
      g.tags.map((t) =>
        h('span', { class: 'chip' }, t, h('button', { class: 'chip-x', title: 'Remove', onClick: () => saveTags(g.tags.filter((x) => x !== t)) }, '×')),
      ),
      tagInput,
    );
  }

  const saved = h('span', { class: 'muted small' });
  const notes = h('textarea', { class: 'notes', placeholder: 'What did you learn from this game?', value: g.notes });
  const saveNotes = debounce(async () => {
    try {
      await api.patch(g.id, { notes: notes.value });
    } catch (err) {
      saved.textContent = 'Not saved';
      return fail(err);
    }
    saved.textContent = 'Saved';
    setTimeout(() => (saved.textContent = ''), 1500);
  }, 600);
  notes.addEventListener('input', () => {
    saved.textContent = 'Saving…';
    saveNotes();
  });

  function confirmDelete() {
    const del = h('button', { class: 'btn danger' }, 'Delete');
    const m = modal('Delete this game?', h('p', {}, `${g.white} vs ${g.black} will be removed from your library, with its analysis, notes and tags.`), [
      h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Cancel'),
      del,
    ]);
    del.addEventListener('click', async () => {
      try {
        await api.remove(g.id);
      } catch (err) {
        m.close();
        return fail(err);
      }
      m.close();
      toast('Game deleted');
      location.hash = '#/library';
    });
  }

  const metaRows: [string, string | null][] = [
    ['Event', g.event],
    ['Date', g.date],
    ['Result', [g.result, g.termination].filter(Boolean).join(' · ')],
    ['Opening', [g.eco, g.opening].filter(Boolean).join(' ') || null],
    ['Time control', [g.time_class, g.time_control].filter(Boolean).join(' · ') || null],
    ['Link', g.link],
    ['Left book', g.opening_deviations?.left_book
      ? `${g.opening_deviations.left_book.move}${g.opening_deviations.left_book.by ? ` (${g.opening_deviations.left_book.by})` : ''}` +
        (g.opening_deviations.left_book.book_moves.length ? ` · book: ${g.opening_deviations.left_book.book_moves.join(', ')}` : '')
      : null],
    ['Repertoire', g.opening_deviations?.left_repertoire
      ? `You played ${g.opening_deviations.left_repertoire.move}; your repertoire move is ${g.opening_deviations.left_repertoire.expected}`
      : null],
  ];

  root.append(
    h(
      'header',
      { class: 'page-head' },
      h('a', { class: 'icon-btn', href: '#/library', title: 'Back to library' }, icon('back')),
      h('h1', {}, `${g.white} vs ${g.black}`),
      resultBadge(g),
      starBtn,
      h('div', { class: 'spacer' }),
      h('button', { class: 'btn ghost', onClick: confirmDelete }, icon('trash'), 'Delete'),
    ),
    h(
      'div',
      { class: 'game-grid' },
      h(
        'div',
        { class: 'board-col' },
        topBar,
        h('div', { class: 'board-row' }, evalBar, h('div', { class: 'board-wrap' }, boardEl)),
        bottomBar,
        h(
          'div',
          { class: 'controls' },
          h('button', { class: 'icon-btn', title: 'Start (Home)', onClick: () => go(0) }, icon('first')),
          h('button', { class: 'icon-btn', title: 'Previous (←)', onClick: () => go(ply - 1) }, icon('prev')),
          h('button', { class: 'icon-btn', title: 'Next (→)', onClick: () => go(ply + 1) }, icon('next')),
          h('button', { class: 'icon-btn', title: 'End (End)', onClick: () => go(g.moves.length) }, icon('last')),
          h('button', { class: 'icon-btn', title: 'Flip board (f)', onClick: flip }, icon('flip')),
        ),
      ),
      h(
        'div',
        { class: 'side-col' },
        analysisCard,
        moveCard,
        movesCard,
        coachCard,
        h('section', { class: 'card' }, h('dl', { class: 'meta' }, metaRows.filter(([, v]) => v).flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]))),
        h('section', { class: 'card' }, h('h3', {}, 'Tags'), tagBox),
        h('section', { class: 'card' }, h('div', { class: 'row' }, h('h3', {}, 'Notes'), h('span', { class: 'spacer' }), saved), notes),
      ),
    ),
  );

  cg = Chessground(boardEl, {
    fen: g.start_fen,
    orientation,
    viewOnly: true,
    coordinates: true,
    animation: { duration: 160 },
  });
  resize = watchSize(boardEl, cg);
  paintStar();
  paintTags();
  applyReview();
  poll();

  return {
    onKey(e) {
      const actions: Record<string, () => void> = {
        ArrowLeft: () => go(preview ? ply : ply - 1),
        ArrowRight: () => go(preview ? ply : ply + 1),
        ArrowUp: () => go(0),
        ArrowDown: () => go(g.moves.length),
        Home: () => go(0),
        End: () => go(g.moves.length),
        f: flip,
        s: () => void toggleStar(),
      };
      const fn = actions[e.key];
      if (fn) {
        e.preventDefault();
        fn();
      }
    },
    destroy() {
      destroyed = true;
      clearTimeout(pollTimer);
      graph?.destroy();
      resize.disconnect();
      destroyBoard(cg);
    },
  };
}
