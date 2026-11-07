import { api, type Lesson, type LessonExample, type LessonSummary, type Puzzle } from './api';
import type { View } from './main';
import { createSolver, type Solver } from './solver';
import { fill, h, icon, toast } from './ui';

/** Minimal markdown: paragraphs, "- " and "1. " lists, **bold**. Input is our own content. */
function markdown(src: string): HTMLElement {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  const html = src
    .trim()
    .split(/\n\s*\n/)
    .map((block) => {
      const lines = block.split('\n');
      if (lines.every((l) => /^- /.test(l))) return `<ul>${lines.map((l) => `<li>${esc(l.slice(2))}</li>`).join('')}</ul>`;
      if (lines.every((l) => /^\d+\. /.test(l))) return `<ol>${lines.map((l) => `<li>${esc(l.replace(/^\d+\. /, ''))}</li>`).join('')}</ol>`;
      if (lines.length > 1 && lines.slice(1).every((l) => /^- /.test(l)))
        return `<p>${esc(lines[0])}</p><ul>${lines.slice(1).map((l) => `<li>${esc(l.slice(2))}</li>`).join('')}</ul>`;
      return `<p>${esc(block)}</p>`;
    })
    .join('');
  return h('div', { class: 'prose', html });
}

export async function renderLessons(root: HTMLElement): Promise<View> {
  const items = await api.lessons();
  const done = items.filter((l) => l.completed).length;
  root.append(
    h('header', { class: 'page-head' }, h('h1', {}, 'Lessons'), h('span', { class: 'muted' }, `${done} of ${items.length} completed`)),
  );
  const rec = items.filter((l) => l.recommended);
  if (rec.length) {
    root.append(h('h3', {}, 'Recommended for you'), h('div', { class: 'lesson-grid' }, rec.map(card)), h('div', { class: 'gap-lg' }));
  }
  const cats = [...new Set(items.map((l) => l.category))];
  for (const c of cats) {
    root.append(h('h3', {}, c), h('div', { class: 'lesson-grid' }, items.filter((l) => l.category === c).map(card)), h('div', { class: 'gap-lg' }));
  }
  return {};
}

function card(l: LessonSummary): HTMLElement {
  return h(
    'a',
    { class: `card lesson-card${l.recommended ? ' rec' : ''}`, href: `#/lessons/${l.id}` },
    h(
      'div',
      { class: 'row gap' },
      h('strong', {}, l.title),
      h('span', { class: 'spacer' }),
      l.completed ? h('span', { class: 'badge badge-win' }, 'Done') : l.started ? h('span', { class: 'badge' }, 'Started') : null,
    ),
    h('p', { class: 'muted small' }, l.summary),
    l.reason ? h('div', { class: 'reason small' }, icon('today'), l.reason) : null,
    l.drill_total ? h('div', { class: 'muted small' }, `Drill: ${l.drill_correct}/${l.drill_total}`) : null,
  );
}

export async function renderLesson(root: HTMLElement, id: string): Promise<View> {
  const lesson: Lesson = await api.lesson(id);
  let solver: Solver | null = null;
  let destroyed = false;
  let stage: 'intro' | number | 'drill' | 'summary' = 'intro';
  let drillIndex = 0;
  let drillCorrect = 0;
  const drillSeen: number[] = [];

  const steps = h('div', { class: 'steps' });
  const body = h('div');
  root.append(
    h(
      'header',
      { class: 'page-head' },
      h('a', { class: 'icon-btn', href: '#/lessons', title: 'All lessons' }, icon('back')),
      h('h1', {}, lesson.title),
      h('span', { class: 'muted' }, lesson.category),
    ),
    steps,
    body,
  );

  function paintSteps() {
    const labels = ['Idea', ...lesson.examples.map((_, i) => `${lesson.side ? 'Line' : 'Example'} ${i + 1}`), 'Drill'];
    const idx = stage === 'intro' ? 0 : stage === 'drill' || stage === 'summary' ? labels.length - 1 : stage + 1;
    fill(steps, labels.map((l, i) => h('span', { class: `step${i === idx ? ' on' : i < idx ? ' past' : ''}` }, l)));
  }

  function clear() {
    solver?.destroy();
    solver = null;
    body.replaceChildren();
    paintSteps();
  }

  function intro() {
    stage = 'intro';
    clear();
    body.append(
      h(
        'div',
        { class: 'card lesson-intro' },
        h('p', { class: 'lead' }, lesson.summary),
        markdown(lesson.body),
        h('div', { class: 'row gap' }, h('button', { class: 'btn primary', onClick: () => example(0) }, lesson.side ? `Play the ${lesson.examples.length} lines` : 'Try the examples')),
      ),
    );
  }

  /** Board on the left, prompt/feedback on the right. */
  function exerciseLayout(prompt: string) {
    const host = h('div', { class: 'cg-host' });
    const status = h('div', { class: 'status-card card status-turn' }, h('strong', {}, prompt));
    const actions = h('div', { class: 'row gap wrap' });
    const side = h('div', { class: 'side-col' }, status, actions);
    body.append(h('div', { class: 'game-grid' }, h('div', { class: 'board-col' }, h('div', { class: 'board-wrap' }, host)), side));
    return { host, status, actions, side };
  }

  /** Move list for an opening line, filled in as the moves are played (with notes while learning). */
  function moveList(ex: LessonExample, withNotes: boolean) {
    const el = h('ol', { class: 'line-notes' });
    const paint = () => {
      const plies = solver?.board.chess.history().length ?? 0;
      fill(
        el,
        (ex.san ?? []).slice(0, plies).map((san, j) =>
          h(
            'li',
            { class: j === plies - 1 ? 'on' : '' },
            h('span', { class: 'mono' }, `${Math.floor(j / 2) + 1}${j % 2 ? '...' : '.'} ${san}`),
            withNotes && ex.notes?.[j] ? h('span', { class: 'note' }, ex.notes[j]!) : null,
          ),
        ),
      );
      el.scrollTop = el.scrollHeight;
    };
    return { el, paint };
  }

  function example(i: number) {
    stage = i;
    clear();
    const ex = lesson.examples[i];
    const isLine = !!ex.san;
    const title = isLine ? `Line ${i + 1} of ${lesson.examples.length}: ${ex.prompt}` : ex.prompt;
    const { host, status, actions, side } = exerciseLayout(title);
    const moves = isLine ? moveList(ex, true) : null;
    if (moves) side.append(moves.el);
    const nextBtn = h('button', { class: 'btn primary', onClick: () => (i + 1 < lesson.examples.length ? example(i + 1) : drill()) },
      i + 1 < lesson.examples.length ? (isLine ? 'Next line' : 'Next example') : 'Start the drill');
    fill(
      actions,
      h('button', { class: 'btn ghost', onClick: () => solver?.hint() }, 'Hint'),
      h('button', { class: 'btn ghost', onClick: () => solver?.undo() }, 'Undo'),
      h('button', { class: 'btn ghost', onClick: () => solver?.showSolution() }, 'Show me'),
    );
    solver = createSolver(host, { fen: ex.fen, setup: ex.setup ?? undefined, line: ex.solution, accept: ex.accept }, {
      state: (s, extra) => {
        if (s === 'wrong')
          fill(status, h('strong', {}, isLine ? `${extra} is not the move in this line.` : `${extra} isn't it.`),
            h('div', { class: 'small' }, 'Undo (Z) and try again, or take a hint (H).'));
        if (s === 'correct') {
          moves?.paint();
          fill(status, h('strong', {}, `${extra} — good.`), h('div', { class: 'small' }, 'Continue.'));
        }
        if (s === 'your-turn') {
          moves?.paint();
          fill(status, h('strong', {}, title), h('div', { class: 'small' }, isLine ? `Play the next ${lesson.side === 'black' ? 'Black' : 'White'} move.` : 'Your move.'));
        }
      },
      complete: (r) => {
        moves?.paint();
        status.className = `status-card card ${r.solved ? 'status-good' : 'status-idle'}`;
        const head = r.alternative ? `${r.alternative} works too!` : isLine ? (r.solved ? 'Line complete, no mistakes!' : 'Line complete') : r.solved ? 'Exactly right!' : 'Here is the idea';
        fill(status, h('strong', {}, head), h('p', { class: 'small' }, ex.explain));
        fill(actions, nextBtn);
        void api.lessonProgress(lesson.id, { examples_done: i + 1 }).catch(() => {});
      },
    });
  }

  /** Opening lessons: replay every line from memory, in random order, without notes. */
  function linesDrill() {
    const order = lesson.examples.map((_, i) => i).sort(() => Math.random() - 0.5);
    const run = () => {
      clear();
      if (drillIndex >= order.length) return void summary();
      const ex = lesson.examples[order[drillIndex]];
      const head = `From memory, ${drillIndex + 1} of ${order.length}: ${ex.prompt}`;
      const { host, status, actions, side } = exerciseLayout(head);
      const moves = moveList(ex, false);
      side.append(moves.el);
      fill(
        actions,
        h('button', { class: 'btn ghost', onClick: () => solver?.hint() }, 'Hint'),
        h('button', { class: 'btn ghost', onClick: () => solver?.undo() }, 'Undo'),
        h('button', { class: 'btn ghost', onClick: () => solver?.showSolution() }, 'Show the line'),
      );
      solver = createSolver(host, { fen: ex.fen, setup: ex.setup ?? undefined, line: ex.solution }, {
        state: (s, extra) => {
          if (s === 'your-turn' || s === 'correct') moves.paint();
          if (s === 'your-turn') fill(status, h('strong', {}, head), h('div', { class: 'small' }, 'Play the line from memory.'));
          if (s === 'wrong') fill(status, h('strong', {}, `${extra} is not the move in this line.`), h('div', { class: 'small' }, 'Undo (Z) and try again.'));
        },
        complete: (r) => {
          moves.paint();
          if (r.solved) drillCorrect += 1;
          status.className = `status-card card ${r.solved ? 'status-good' : 'status-idle'}`;
          fill(status, h('strong', {}, r.solved ? 'Perfect recall!' : 'Line complete'), h('div', { class: 'small' }, `${drillCorrect} of ${drillIndex + 1} lines without a mistake.`));
          fill(actions, h('button', { class: 'btn primary', onClick: () => { drillIndex += 1; run(); } }, drillIndex + 1 < order.length ? 'Next line' : 'Finish'));
        },
      });
    };
    run();
  }

  /** Opening lessons: copy the lines into the opening trainer's spaced-repetition repertoire. */
  function repertoireButton(): HTMLElement | null {
    if (!lesson.side) return null;
    const out = h('span', { class: 'muted small' });
    const btn = h('button', {
      class: 'btn ghost',
      onClick: async () => {
        btn.setAttribute('disabled', '');
        let r;
        try {
          r = await api.lessonRepertoire(lesson.id);
        } catch (err) {
          btn.removeAttribute('disabled');
          return toast((err as Error).message, { error: true });
        }
        fill(out, `${r.added} positions added to your ${r.color === 'white' ? 'White' : 'Black'} repertoire${r.kept ? `; ${r.kept} kept your existing move` : ''}. `,
          h('a', { href: '#/openings' }, 'Drill them'));
      },
    }, 'Add these lines to my repertoire');
    return h('div', { class: 'row gap wrap' }, btn, out);
  }

  async function drill() {
    stage = 'drill';
    drillIndex = 0;
    drillCorrect = 0;
    if (lesson.drill.mode === 'lines') return linesDrill();
    await drillNext();
  }

  async function fetchDrillPuzzle(): Promise<Puzzle | null> {
    const themes = [...lesson.drill.themes].sort(() => Math.random() - 0.5);
    for (const mode of ['lichess', 'own']) {
      for (const t of themes) {
        try {
          const p = await api.nextPuzzle(mode, t, drillSeen);
          if (p) return p;
        } catch {
          /* try the next theme/source */
        }
      }
    }
    return null;
  }

  async function drillNext() {
    clear();
    if (drillIndex >= lesson.drill.count) return summary();
    const p = await fetchDrillPuzzle();
    if (destroyed) return;
    if (!p) {
      body.append(
        h(
          'div',
          { class: 'card stack' },
          h('strong', {}, drillIndex ? 'No more drill puzzles available' : 'No drill puzzles available yet'),
          h('p', { class: 'muted small' }, 'Drills use the Lichess puzzle database and puzzles from your own games. Import the database on the Puzzles page to unlock full 10-puzzle drills.'),
          h('div', { class: 'row gap' }, h('a', { class: 'btn ghost', href: '#/puzzles' }, 'Go to Puzzles'),
            h('button', { class: 'btn primary', onClick: () => summary() }, 'Finish lesson')),
        ),
      );
      return;
    }
    drillSeen.push(p.id);
    const { host, status, actions } = exerciseLayout(`Drill ${drillIndex + 1} of ${lesson.drill.count}`);
    const next = h('button', { class: 'btn primary', onClick: () => { drillIndex += 1; void drillNext(); } }, 'Next');
    fill(
      actions,
      h('button', { class: 'btn ghost', onClick: () => solver?.hint() }, 'Hint'),
      h('button', { class: 'btn ghost', onClick: () => solver?.undo() }, 'Undo'),
      h('button', { class: 'btn ghost', onClick: () => solver?.showSolution() }, 'Show solution'),
    );
    solver = createSolver(host, { fen: p.fen, setup: p.moves[0], line: p.moves.slice(1) }, {
      state: (s, extra) => {
        if (s === 'your-turn') fill(status, h('strong', {}, `Drill ${drillIndex + 1} of ${lesson.drill.count}`), h('div', { class: 'small' }, `Find the best move for ${solver?.solverColor === 'black' ? 'Black' : 'White'}.`));
        if (s === 'wrong') fill(status, h('strong', {}, `${extra} isn't it.`), h('div', { class: 'small' }, 'Undo (Z) and try again.'));
      },
      complete: (r) => {
        if (r.solved) drillCorrect += 1;
        status.className = `status-card card ${r.solved ? 'status-good' : 'status-idle'}`;
        fill(status, h('strong', {}, r.solved ? 'Solved!' : 'Done'), h('div', { class: 'small' }, `${drillCorrect} correct so far.`));
        fill(actions, next);
        void api.attempt(p.id, r.solved, r.hints, r.seconds).catch(() => {});
      },
    });
  }

  async function summary() {
    stage = 'summary';
    clear();
    const total = drillIndex;
    try {
      await api.lessonProgress(lesson.id, { examples_done: lesson.examples.length, drill_correct: drillCorrect, drill_total: total, completed: true });
    } catch (err) {
      toast(`Could not save your progress: ${(err as Error).message}`, { error: true });
    }
    if (destroyed) return;
    body.append(
      h(
        'div',
        { class: 'card stack center-card' },
        h('div', { class: 'empty-icon' }, icon('today')),
        h('h2', {}, 'Lesson complete'),
        total ? h('p', {}, `Drill score: ${drillCorrect} / ${total}`) : h('p', { class: 'muted' }, 'Drill skipped for now.'),
        repertoireButton(),
        h('div', { class: 'row gap' }, h('a', { class: 'btn ghost', href: '#/lessons' }, 'All lessons'), h('a', { class: 'btn primary', href: '#/today' }, "Back to today's plan")),
      ),
    );
  }

  intro();
  return {
    onKey(e) {
      const k = e.key.toLowerCase();
      if (k === 'z') solver?.undo();
      else if (k === 'h') solver?.hint();
      else return;
      e.preventDefault();
    },
    destroy() {
      destroyed = true;
      solver?.destroy();
    },
  };
}
