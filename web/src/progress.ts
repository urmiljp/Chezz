import { api, type StreakInfo } from './api';
import { accent, chart, movingAverage, slot, type Chart } from './charts';
import type { View } from './main';
import { emptyState, h } from './ui';

export function streakGrid(s: StreakInfo): HTMLElement {
  const max = Math.max(1, ...s.days.map((d) => d.count));
  return h(
    'div',
    { class: 'streak-grid', title: 'Activity over the last 12 weeks' },
    s.days.map((d) =>
      h('span', {
        class: 'cell',
        title: `${d.day}: ${d.count} activit${d.count === 1 ? 'y' : 'ies'}`,
        style: d.count ? `opacity:${0.35 + (0.65 * d.count) / max};background:var(--accent)` : '',
      }),
    ),
  );
}

export async function renderProgress(root: HTMLElement): Promise<View> {
  const p = await api.progress();
  const charts: Chart[] = [];
  root.append(h('header', { class: 'page-head' }, h('h1', {}, 'Progress')));

  const tile = (label: string, value: string, sub?: string) =>
    h('div', { class: 'card tile' }, h('div', { class: 'muted small' }, label), h('div', { class: 'tile-num' }, value), sub ? h('div', { class: 'muted small' }, sub) : null);

  const accs = p.games.map((g) => g.accuracy).filter((a): a is number => a != null);
  root.append(
    h(
      'div',
      { class: 'tiles' },
      tile('Streak', `${p.streak.current} day${p.streak.current === 1 ? '' : 's'}`, `Best ${p.streak.best}`),
      tile('Games analysed', String(p.totals.analysed), `of ${p.totals.games}`),
      tile('Average accuracy', accs.length ? (accs.reduce((a, b) => a + b, 0) / accs.length).toFixed(1) : '–', 'your moves'),
      tile('Puzzles solved', String(p.totals.puzzles_solved), p.rating.length ? `rating ${Math.round(p.rating[p.rating.length - 1].rating)}` : 'no rated puzzles yet'),
      tile('Lessons done', String(p.totals.lessons_done), `${p.totals.drills} opening drills`),
    ),
    h('section', { class: 'card' }, h('h3', {}, 'Activity'), streakGrid(p.streak)),
  );

  const section = (title: string, sub: string) => {
    const el = h('div', { class: 'chart' });
    root.append(h('section', { class: 'card chart-card' }, h('h3', {}, title), h('p', { class: 'muted small' }, sub), el));
    return el;
  };

  if (!p.games.length) {
    root.append(emptyState('library', 'No analysed games yet', 'Analyze your games to see accuracy and blunder trends here.', [
      h('a', { class: 'btn primary', href: '#/library' }, 'Go to Library'),
    ]));
  } else {
    const xs = p.games.map((_, i) => i + 1);
    const label = (i: number) => p.games[Math.round(i) - 1]?.date ?? '';
    const accVals = p.games.map((g) => g.accuracy);
    charts.push(chart(section('Accuracy per game', 'Your accuracy in each analysed game, oldest first, with a 5-game average.'), xs, [
      { label: 'Accuracy', values: accVals, color: accent(), points: true },
      { label: '5-game average', values: movingAverage(accVals, 5), color: 'rgba(128,128,128,0.8)', dash: [5, 4] },
    ], { yRange: [0, 100], xFmt: label }));
    charts.push(chart(section('Blunders per game', 'Moves that dropped your winning chances by 15% or more.'), xs, [
      { label: 'Blunders', values: p.games.map((g) => g.blunders), color: accent(), bars: true, fmt: (v) => String(v) },
    ], { xFmt: label, height: 180 }));
  }

  if (p.rating.length) {
    charts.push(chart(section('Puzzle rating', 'After every rated Lichess puzzle.'), p.rating.map((_, i) => i + 1), [
      { label: 'Rating', values: p.rating.map((r) => r.rating), color: accent(), fmt: (v) => String(Math.round(v)) },
    ], { xFmt: (i) => p.rating[Math.round(i) - 1]?.at.slice(0, 10) ?? '', height: 200 }));
  }

  const months = p.weakness_months;
  if (months.length) {
    // Top 4 motifs overall; categorical slots in fixed order.
    const totals: Record<string, number> = {};
    for (const m of months) for (const [t, v] of Object.entries(m.shares)) totals[t] = (totals[t] ?? 0) + v;
    const top = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([t]) => t);
    if (top.length) {
      charts.push(chart(section('Weakness shares over time', 'Share of your errors (per month) involving each tactical motif.'),
        months.map((_, i) => i), top.map((t, i) => ({
          label: p.tag_labels[t] ?? t, values: months.map((m) => m.shares[t] ?? 0), color: slot(i), points: true, fmt: (v: number) => `${v}%`,
        })), { xFmt: (i) => months[Math.round(i)]?.month ?? '', yFmt: (v) => `${v}%`, height: 220 }));
    }
  }

  return { destroy: () => charts.forEach((c) => c.destroy()) };
}
