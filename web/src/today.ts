import { api, THEME_LABELS, type Plan } from './api';
import { sparkline, type Chart } from './charts';
import type { View } from './main';
import { streakGrid } from './progress';
import { emptyState, h, icon, resultBadge } from './ui';

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

export async function renderToday(root: HTMLElement): Promise<View> {
  const p: Plan = await api.plan();
  const charts: Chart[] = [];
  const date = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });

  if (p.games === 0) {
    root.append(
      h('header', { class: 'today-head' }, h('div', { class: 'muted' }, date), h('h1', {}, "Today's plan")),
      emptyState('today', 'Import your games to build your plan', 'Your daily plan of puzzles, a lesson and a game to review is built from the mistakes in your own games.', [
        h('a', { class: 'btn primary', href: '#/library?import=1' }, icon('upload'), 'Import PGN'),
        h('a', { class: 'btn ghost', href: '#/lessons' }, 'Browse lessons'),
      ]),
    );
    return {};
  }

  const pz = p.puzzles;
  const l = p.lesson;
  const g = p.game;
  const done = [pz.done >= pz.target, !!l?.completed || !l, !g];
  const doneCount = done.filter(Boolean).length;
  const check = (ok: boolean) => h('span', { class: `plan-check${ok ? ' done' : ''}`, 'aria-label': ok ? 'Done' : 'To do' }, '✓');
  const task = (ok: boolean, kind: string, title: string | Node, meta: (Node | string | null)[], action: HTMLElement) =>
    h('div', { class: `task${ok ? ' done' : ''}` },
      check(ok), h('div', { class: 'task-kind' }, kind), action,
      h('div', { class: 'task-title' }, title),
      h('div', { class: 'task-meta' }, meta));

  const puzzleNote = [pz.due ? `${pz.due} due for review` : '', pz.own_unseen ? `${pz.own_unseen} from your games` : '', !pz.lichess ? 'Lichess puzzles not imported' : '']
    .filter(Boolean).join(', ');
  const tasks = h('section', { class: 'tasks' },
    task(done[0], 'Puzzles', `${pz.done} of ${pz.target} solved`, [
      h('div', { class: 'progress' }, h('div', { class: 'progress-fill', style: `width:${Math.round((100 * Math.min(pz.done, pz.target)) / pz.target)}%` })),
      ...pz.themes.map((t) => h('span', { class: 'chip' }, THEME_LABELS[t] ?? t)),
      puzzleNote ? h('span', {}, puzzleNote) : null,
    ], h('a', { class: `btn ${done[0] ? 'ghost' : 'primary'}`, href: '#/puzzles?mode=plan' }, done[0] ? 'Keep solving' : 'Start puzzles')),
    l
      ? task(done[1], 'Lesson', l.title, [h('span', {}, l.reason ?? l.summary)],
          h('a', { class: `btn ${!done[0] || done[1] ? 'ghost' : 'primary'}`, href: `#/lessons/${l.id}` }, l.completed ? 'Review lesson' : l.started ? 'Continue lesson' : 'Start lesson'))
      : task(true, 'Lesson', 'Every lesson completed', [h('span', {}, 'Revisit any lesson to keep the patterns fresh.')], h('a', { class: 'btn ghost', href: '#/lessons' }, 'Browse lessons')),
    g
      ? task(false, 'Game review', `${g.white} vs ${g.black}`, [
          g.user_result ? resultBadge({ user_result: g.user_result, result: '' }) : null,
          h('span', {}, g.date ?? ''),
          h('span', {}, g.analysed ? `${plural(g.errors ?? 0, 'mistake')} or blunders to look at` : 'Not analysed yet'),
        ], h('a', { class: `btn ${done[0] && done[1] ? 'primary' : 'ghost'}`, href: `#/game/${g.id}` }, g.analysed ? 'Review game' : 'Analyze game'))
      : task(true, 'Game review', 'All caught up', [h('span', {}, 'Every recent game has been reviewed.')], h('a', { class: 'btn ghost', href: '#/library' }, 'Open library')),
  );

  const focus = h('section', { class: 'focus-block' },
    h('div', { class: 'focus-head' }, h('h2', {}, 'What to work on'), h('a', { class: 'btn link', href: '#/insights' }, 'All insights')),
    p.weaknesses.length
      ? h('ul', { class: 'focus' }, p.weaknesses.map((w) => h('li', {}, h('strong', {}, w.label), h('span', { class: 'muted small' }, w.detail))))
      : h('p', { class: 'muted' }, 'Analyze a few games and your most costly patterns show up here.'),
    p.openings_due ? h('div', { class: 'row gap', style: 'margin-top:14px' },
      h('a', { class: 'btn ghost', href: '#/openings' }, icon('opening'), `Drill ${plural(p.openings_due, 'opening position')}`)) : null,
  );

  const s = p.streak;
  const ratingSpark = h('div', { class: 'spark' });
  const accSpark = h('div', { class: 'spark' });
  const lastAcc = p.accuracy_trend.length ? p.accuracy_trend[p.accuracy_trend.length - 1] : null;
  const rail = h('aside', { class: 'rail' },
    h('section', { class: 'card stack' },
      h('div', { class: 'stat' },
        h('div', { class: 'grow' }, h('div', { class: 'stat-label' }, 'Streak'), h('div', { class: 'plan-num' }, String(s.current), h('small', {}, s.current === 1 ? 'day' : 'days'))),
        streakGrid(s)),
      h('div', { class: 'muted small' }, s.today ? `Done for today. Best run: ${plural(s.best, 'day')}.` : s.current ? 'Finish one task today to keep it going.' : s.best ? `Best run: ${plural(s.best, 'day')}. Finish one task to start again.` : 'Finish one task today to start a streak.')),
    h('section', { class: 'card stack' },
      h('div', { class: 'stat' }, h('div', {}, h('div', { class: 'stat-label' }, 'Puzzle rating'), h('div', { class: 'plan-num' }, String(p.rating.current))), ratingSpark),
      h('div', { class: 'stat' }, h('div', {}, h('div', { class: 'stat-label' }, 'Accuracy, last game'), h('div', { class: 'plan-num' }, lastAcc == null ? '–' : lastAcc.toFixed(1))), accSpark)),
    h('div', { class: 'rail-links' }, h('a', { class: 'btn link', href: '#/progress' }, 'See progress')),
  );

  root.append(
    h('div', { class: 'today' },
      h('div', {},
        h('header', { class: 'today-head' },
          h('div', { class: 'muted' }, date),
          h('h1', {}, doneCount === 3 ? 'Done for today' : "Today's plan"),
          h('div', { class: 'sub' }, doneCount === 3 ? 'Everything on the list is finished. Extra puzzles still count toward your rating.' : `${doneCount} of 3 done`)),
        tasks, focus),
      rail),
  );
  if (p.rating.trend.length > 1) charts.push(sparkline(ratingSpark, p.rating.trend));
  if (p.accuracy_trend.length > 1) charts.push(sparkline(accSpark, p.accuracy_trend));
  return { destroy: () => charts.forEach((c) => c.destroy()) };
}
