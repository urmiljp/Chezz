import { api, type Filters, type LessonSummary, type Meta, type Profile, type Weakness } from './api';
import { slot } from './charts';
import type { View } from './main';
import { emptyState, fill, h } from './ui';

let filters: Filters = {};

export async function renderInsights(root: HTMLElement): Promise<View> {
  const [meta, lessonList]: [Meta, LessonSummary[]] = await Promise.all([api.meta(), api.lessons()]);
  const filterBar = h('div', { class: 'filters' });
  const body = h('div', { class: 'stack' });
  root.append(h('header', { class: 'page-head' }, h('h1', {}, 'Insights'), h('span', { class: 'muted' }, 'What costs you the most, from your analysed games')), filterBar, body);

  const select = (key: keyof Filters, label: string, options: [string, string][]) => {
    const s = h('select', { class: filters[key] ? 'active' : '' }, h('option', { value: '' }, label),
      options.map(([v, l]) => h('option', { value: v, selected: filters[key] === v }, l)));
    s.addEventListener('change', () => {
      (filters as Record<string, unknown>)[key] = s.value || undefined;
      void load();
    });
    return s;
  };

  function paintFilters() {
    const from = h('input', { type: 'date', value: filters.date_from ?? '', title: 'From date', class: filters.date_from ? 'active' : '' });
    from.addEventListener('change', () => { filters.date_from = from.value || undefined; void load(); });
    fill(filterBar,
      select('color', 'Both colors', [['white', 'As White'], ['black', 'As Black']]),
      select('time_class', 'Any time control', meta.time_classes.map((t) => [t, t[0].toUpperCase() + t.slice(1)])),
      from,
    );
  }

  function lessonFor(w: Weakness): LessonSummary | undefined {
    const byTheme = w.theme ? lessonList.find((l) => l.themes.includes(w.theme!)) : undefined;
    if (byTheme) return byTheme;
    if (w.kind === 'phase' && w.phase === 'opening') return lessonList.find((l) => l.category === 'Opening principles' && !l.completed) ?? lessonList.find((l) => l.category === 'Opening principles');
    if (w.kind === 'phase' && w.phase === 'endgame') return lessonList.find((l) => l.category === 'Endgames' && !l.completed) ?? lessonList.find((l) => l.category === 'Endgames');
    return undefined;
  }

  function actions(w: Weakness): HTMLElement[] {
    const out: HTMLElement[] = [];
    const lesson = lessonFor(w);
    if (w.theme) out.push(h('a', { class: 'btn ghost sm', href: `#/puzzles?theme=${w.theme}` }, 'Practice'));
    if (w.kind === 'phase' && w.phase === 'endgame') out.push(h('a', { class: 'btn ghost sm', href: '#/play?tab=endgames' }, 'Endgame practice'));
    if (w.kind === 'opening') out.push(h('a', { class: 'btn ghost sm', href: '#/openings' }, 'Opening drill'));
    if (w.kind === 'time') out.push(h('span', { class: 'muted small' }, 'Tip: spend your time early on critical moves, not on routine ones.'));
    if (lesson) out.push(h('a', { class: 'btn ghost sm', href: `#/lessons/${lesson.id}` }, `Lesson: ${lesson.title}`));
    return out;
  }

  /** Horizontal bar; value relative to max. */
  const bar = (value: number, max: number, color = 'var(--accent)') =>
    h('div', { class: 'hbar' }, h('span', { style: `width:${max ? (100 * value) / max : 0}%;background:${color}` }));

  function paint(p: Profile) {
    const s = p.summary;
    if (!s.games) {
      fill(body, emptyState('today', 'No analysed games match', 'Analyze games in the Library (or widen the filters) to build your weakness profile.', [
        h('a', { class: 'btn primary', href: '#/library' }, 'Go to Library'),
      ]));
      return;
    }
    const per = (n: number) => (n / s.games).toFixed(1);
    const tile = (label: string, value: string) => h('div', { class: 'card tile' }, h('div', { class: 'muted small' }, label), h('div', { class: 'tile-num' }, value));

    const maxTag = Math.max(1, ...p.tactics.map((t) => t.allowed + t.missed));
    const tacticRows = p.tactics.filter((t) => t.allowed + t.missed > 0);
    const phaseRate = (ph: Profile['phases'][number]) => (ph.moves ? (100 * ph.errors) / ph.moves : 0);
    const maxPhase = Math.max(1, ...p.phases.map(phaseRate));
    const tRate = (k: 'normal' | 'trouble') => (p.time[k].moves ? (100 * p.time[k].errors) / p.time[k].moves : 0);
    const maxT = Math.max(1, tRate('normal'), tRate('trouble'));

    fill(
      body,
      h('div', { class: 'tiles' },
        tile('Games analysed', String(s.games)),
        tile('Average accuracy', s.accuracy != null ? s.accuracy.toFixed(1) : '–'),
        tile('Errors per game', per(s.errors)),
        tile('Blunders per game', per(s.blunders)),
      ),
      h('section', { class: 'card' }, h('h3', {}, 'Your biggest weaknesses'),
        p.weaknesses.length
          ? h('ol', { class: 'weak-list' }, p.weaknesses.map((w) => h('li', {},
              h('div', { class: 'row gap' }, h('strong', {}, w.label), h('span', { class: 'muted small' }, w.detail), h('span', { class: 'spacer' }),
                h('span', { class: 'chip' }, `${w.score.toFixed(1)} / game`)),
              h('div', { class: 'row gap wrap' }, actions(w)))))
          : h('p', { class: 'muted' }, 'No clear weakness yet. Analyze more games for a sharper picture.')),
      h('div', { class: 'insight-grid' },
        h('section', { class: 'card' }, h('h3', {}, 'Tactics'),
          h('div', { class: 'legend' }, h('span', {}, h('i', { style: `background:${slot(0)}` }), 'Allowed (opponent could punish)'),
            h('span', {}, h('i', { style: `background:${slot(1)}` }), 'Missed (you could have played it)')),
          tacticRows.length
            ? h('table', { class: 'bars' }, tacticRows.map((t) => h('tr', { title: `${t.label}: ${t.allowed} allowed, ${t.missed} missed` },
                h('td', {}, t.label),
                h('td', { class: 'w100' }, h('div', { class: 'hbar stacked' },
                  h('span', { style: `width:${(100 * t.allowed) / maxTag}%;background:${slot(0)}` }),
                  h('span', { style: `width:${(100 * t.missed) / maxTag}%;background:${slot(1)}` }))),
                h('td', { class: 'num' }, `${t.allowed} / ${t.missed}`))))
            : h('p', { class: 'muted small' }, 'No tactical motifs found in your errors.')),
        h('section', { class: 'card' }, h('h3', {}, 'Game phase'), h('p', { class: 'muted small' }, 'Errors per 100 of your moves'),
          h('table', { class: 'bars' }, p.phases.map((ph) => h('tr', { title: `${ph.moves} moves, ${ph.errors} errors` },
            h('td', {}, ph.phase[0].toUpperCase() + ph.phase.slice(1)),
            h('td', { class: 'w100' }, bar(phaseRate(ph), maxPhase)),
            h('td', { class: 'num' }, ph.moves ? phaseRate(ph).toFixed(1) : '–'))))),
        h('section', { class: 'card' }, h('h3', {}, 'Time pressure'), h('p', { class: 'muted small' }, 'Errors per 100 moves, with and without time trouble'),
          h('table', { class: 'bars' }, (['normal', 'trouble'] as const).map((k) => h('tr', { title: `${p.time[k].moves} moves, ${p.time[k].errors} errors` },
            h('td', {}, k === 'normal' ? 'Enough time' : 'Low clock'),
            h('td', { class: 'w100' }, bar(tRate(k), maxT)),
            h('td', { class: 'num' }, p.time[k].moves ? tRate(k).toFixed(1) : '–'))))),
      ),
      h('section', { class: 'card' }, h('h3', {}, 'Openings'),
        h('table', { class: 'games compact' },
          h('thead', {}, h('tr', {}, ['Opening', 'Games', 'Score', 'Accuracy', 'Blunders / game'].map((t) => h('th', {}, t)))),
          h('tbody', {}, p.openings.map((o) => h('tr', { onClick: () => (location.hash = '#/library'), title: 'Open the Library' },
            h('td', {}, o.name), h('td', {}, String(o.games)),
            h('td', {}, o.score != null ? `${o.score}%` : '–'),
            h('td', {}, o.accuracy != null ? o.accuracy.toFixed(1) : '–'),
            h('td', {}, o.blunders != null ? o.blunders.toFixed(1) : '–')))))),
    );
  }

  async function load() {
    paintFilters();
    paint(await api.weakness(filters));
  }

  await load();
  return {};
}
