import { api, type LessonSummary } from './api';
import { debounce, h, icon } from './ui';

export interface Command {
  label: string;
  icon: string;
  group: string;
  sub?: string;
  run: () => void;
}

let lessons: LessonSummary[] | null = null;

/** Ctrl+K: jump to a page, a game (by player or opening), a lesson, or run an action. */
export function openPalette(base: Command[]): void {
  if (document.querySelector('.palette')) return;
  const input = h('input', { placeholder: 'Search pages, games, lessons, actions', spellcheck: false, autocomplete: 'off' });
  const list = h('div', { class: 'palette-list', role: 'listbox' });
  let items: Command[] = [];
  let games: Command[] = [];
  let sel = 0;

  const close = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey, true);
  };
  const runAt = (i: number) => {
    const c = items[i];
    if (!c) return;
    close();
    c.run();
  };

  const paint = () => {
    const q = input.value.trim().toLowerCase();
    const match = (c: Command) => !q || c.label.toLowerCase().includes(q) || (c.sub ?? '').toLowerCase().includes(q);
    const lessonCmds: Command[] = q
      ? (lessons ?? []).map((l) => ({ label: l.title, sub: l.completed ? 'Done' : l.category, icon: 'lesson', group: 'Lessons', run: () => (location.hash = `#/lessons/${l.id}`) }))
      : [];
    items = [...base.filter(match), ...games, ...lessonCmds.filter(match).slice(0, 6)];
    sel = Math.min(sel, Math.max(0, items.length - 1));
    const out: HTMLElement[] = [];
    let group = '';
    items.forEach((c, i) => {
      if (c.group !== group) out.push(h('div', { class: 'palette-group' }, (group = c.group)));
      out.push(
        h('button', { class: `palette-item${i === sel ? ' on' : ''}`, role: 'option', onMousemove: () => hover(i), onClick: () => runAt(i) },
          icon(c.icon), h('span', {}, c.label), c.sub ? h('span', { class: 'sub' }, c.sub) : null),
      );
    });
    list.replaceChildren(...(out.length ? out : [h('div', { class: 'palette-empty' }, 'Nothing matches. Try a player name or an opening.')]));
    list.querySelector('.on')?.scrollIntoView({ block: 'nearest' });
  };
  const hover = (i: number) => {
    if (i === sel) return;
    sel = i;
    paint();
  };

  const searchGames = debounce(async (q: string) => {
    if (q.length < 2) {
      games = [];
      return paint();
    }
    const res = await api.games({ q }, 0, 6).catch(() => null);
    if (input.value.trim() !== q) return;
    games = (res?.games ?? []).map((g) => ({
      label: `${g.white} vs ${g.black}`,
      sub: [g.date, g.opening_family ?? g.opening].filter(Boolean).join(', '),
      icon: 'board',
      group: 'Games',
      run: () => (location.hash = `#/game/${g.id}`),
    }));
    paint();
  }, 120);

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowDown') sel = Math.min(items.length - 1, sel + 1);
    else if (e.key === 'ArrowUp') sel = Math.max(0, sel - 1);
    else if (e.key === 'Enter') runAt(sel);
    else return;
    e.preventDefault();
    e.stopPropagation();
    if (document.contains(overlay)) paint();
  };

  input.addEventListener('input', () => {
    sel = 0;
    paint();
    searchGames(input.value.trim());
  });
  const overlay = h('div', { class: 'overlay palette-overlay', onMousedown: (e: MouseEvent) => e.target === overlay && close() },
    h('div', { class: 'palette', role: 'dialog', 'aria-label': 'Command palette' }, input, list));
  document.addEventListener('keydown', onKey, true);
  document.body.append(overlay);
  input.focus();
  paint();
  if (!lessons) void api.lessons().then((l) => ((lessons = l), paint())).catch(() => {});
}
