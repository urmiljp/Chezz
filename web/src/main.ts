import '@fontsource-variable/hanken-grotesk';
import '@fontsource-variable/fraunces/opsz.css';
import '@lichess-org/chessground/assets/chessground.base.css';
import '@lichess-org/chessground/assets/chessground.cburnett.css';
import './board.css';
import './style.css';

import { renderGame } from './game';
import { renderInsights } from './insights';
import { renderLesson, renderLessons } from './lessons';
import { renderLibrary } from './library';
import { renderOpenings } from './openings';
import { openPalette, type Command } from './palette';
import { renderPlay } from './play';
import { renderProgress } from './progress';
import { renderPuzzles } from './puzzles';
import { renderToday } from './today';
import { api } from './api';
import { emptyState, h, icon, modal, toast } from './ui';

/** A view renders into `root` and may return a key handler and a cleanup function. */
export interface View {
  onKey?: (e: KeyboardEvent) => void;
  destroy?: () => void;
}

const NAV = [
  { hash: '#/today', label: 'Today', icon: 'today' },
  { hash: '#/library', label: 'Library', icon: 'library' },
  { hash: '#/insights', label: 'Insights', icon: 'insights' },
  { hash: '#/puzzles', label: 'Puzzles', icon: 'puzzle' },
  { hash: '#/lessons', label: 'Lessons', icon: 'lesson' },
  { hash: '#/openings', label: 'Openings', icon: 'opening' },
  { hash: '#/play', label: 'Play', icon: 'board' },
  { hash: '#/progress', label: 'Progress', icon: 'progress' },
];

/** Brand mark: a 4x4 board whose solid squares draw a Z (the app icon is assets/logo.svg). */
const MARK = '<svg viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="currentColor" opacity="0.16"/><rect x="5" y="5" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="10.8" y="5" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="16.6" y="5" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="22.4" y="5" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="5" y="10.8" width="4.6" height="4.6" rx="1" fill="currentColor" opacity=".22"/><rect x="10.8" y="10.8" width="4.6" height="4.6" rx="1" fill="currentColor" opacity=".22"/><rect x="16.6" y="10.8" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="22.4" y="10.8" width="4.6" height="4.6" rx="1" fill="currentColor" opacity=".22"/><rect x="5" y="16.6" width="4.6" height="4.6" rx="1" fill="currentColor" opacity=".22"/><rect x="10.8" y="16.6" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="16.6" y="16.6" width="4.6" height="4.6" rx="1" fill="currentColor" opacity=".22"/><rect x="22.4" y="16.6" width="4.6" height="4.6" rx="1" fill="currentColor" opacity=".22"/><rect x="5" y="22.4" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="10.8" y="22.4" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="16.6" y="22.4" width="4.6" height="4.6" rx="1" fill="currentColor"/><rect x="22.4" y="22.4" width="4.6" height="4.6" rx="1" fill="currentColor"/></svg>';
const brandMark = () => h('span', { class: 'brand-mark', html: MARK });

const main = h('main', { class: 'main' });
const loadbar = h('div', { id: 'loadbar' });
const counts: Record<string, HTMLElement> = {};
const nav = h(
  'nav',
  { class: 'nav' },
  NAV.map((n, i) =>
    h('a', { href: n.hash, 'data-hash': n.hash, title: `${n.label} (${i + 1})` }, icon(n.icon), h('span', {}, n.label),
      (counts[n.hash] = h('span', { class: 'nav-count', hidden: true }))),
  ),
);
const themeBtn = h('button', { class: 'nav-foot-btn', onClick: toggleTheme });
const exitBtn = h('button', { class: 'nav-foot-btn icon-only', title: 'Exit Chezz', onClick: confirmExit }, icon('power'));
const searchBtn = h('button', { class: 'search-btn', onClick: () => palette() }, icon('search'), h('span', {}, 'Search'), h('kbd', {}, 'Ctrl K'));

document.getElementById('app')!.append(
  loadbar,
  h('div', { class: 'shell' },
    h('aside', { class: 'sidebar' }, h('div', { class: 'brand' }, brandMark(), 'Chezz'), searchBtn, nav, h('div', { class: 'sidebar-foot' }, themeBtn, exitBtn)),
    main),
);

function paintThemeButton() {
  const dark = document.documentElement.dataset.theme === 'dark';
  themeBtn.title = dark ? 'Switch to light theme' : 'Switch to dark theme';
  themeBtn.replaceChildren(icon(dark ? 'sun' : 'moon'), h('span', {}, dark ? 'Light theme' : 'Dark theme'));
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('theme', next);
  paintThemeButton();
  window.dispatchEvent(new Event('themechange'));
  // Chart axes are painted once; redraw the chart-only pages (no in-progress state to lose).
  if (/^#\/(today|insights|progress)?$/.test(location.hash.split('?')[0] || '#/')) void route();
}

/** Sidebar counts: reviews due on Puzzles and Openings. Cheap; refreshed after each navigation. */
async function refreshCounts() {
  const p = await api.plan().catch(() => null);
  if (!p) return;
  const set = (hash: string, n: number, what: string) => {
    counts[hash].hidden = !n;
    counts[hash].textContent = String(n);
    counts[hash].title = `${n} ${what} due`;
  };
  set('#/puzzles', p.puzzles.due, 'puzzle reviews');
  set('#/openings', p.openings_due, 'opening positions');
}

function palette() {
  const go = (hash: string) => () => (location.hash = hash);
  const cmds: Command[] = [
    ...NAV.map((n) => ({ label: n.label, icon: n.icon, group: 'Go to', run: go(n.hash) })),
    { label: 'Import PGN', icon: 'upload', group: 'Actions', run: go('#/library?import=1') },
    { label: 'Analyze all games', icon: 'engine', group: 'Actions', run: () => void api.queueAll().then((r) => toast(r.queued ? `Queued ${r.queued} games for analysis` : 'Every game is already analysed'), (e) => toast(e.message, { error: true })) },
    { label: 'Back up library', icon: 'database', group: 'Actions', run: () => void api.backup().then((r) => toast(`Backed up to ${r.path}`), (e) => toast(e.message, { error: true })) },
    { label: 'Play against Stockfish', icon: 'board', group: 'Actions', run: go('#/play') },
    { label: document.documentElement.dataset.theme === 'dark' ? 'Light theme' : 'Dark theme', icon: 'moon', group: 'Actions', run: toggleTheme },
    { label: 'Keyboard shortcuts', icon: 'keyboard', group: 'Actions', run: shortcuts },
  ];
  openPalette(cmds);
}

function shortcuts() {
  const keys: [string, string][] = [
    ['Ctrl K', 'Search pages, games and lessons'],
    ['1 – 8', 'Jump to a section'],
    ['← →', 'Step through moves'],
    ['↑ ↓', 'First / last move'],
    ['F', 'Flip the board'],
    ['S', 'Star the game'],
    ['Z', 'Undo a puzzle move'],
    ['H', 'Puzzle hint'],
    ['N', 'Next puzzle'],
    ['/', 'Search the library'],
    ['I', 'Import PGN (library)'],
    ['?', 'Show this list'],
  ];
  modal('Keyboard shortcuts', h('dl', { class: 'keys' }, keys.flatMap(([k, d]) => [h('dt', {}, h('kbd', {}, k)), h('dd', {}, d)])));
}

async function confirmExit() {
  const status = await api.analysisStatus().catch(() => null);
  const busy = status && (status.running || status.queued);
  const exit = h('button', { class: 'btn primary' }, 'Exit');
  const m = modal(
    'Exit Chezz?',
    h('p', {}, busy
      ? 'Analysis is still running. It stops now and continues where it left off the next time you start the app.'
      : 'Everything is saved. The app and its engine will close.'),
    [h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Cancel'), exit],
  );
  exit.addEventListener('click', async () => {
    try {
      await api.exit();
    } catch (err) {
      m.close();
      toast((err as Error).message, { error: true });
      return;
    }
    m.close();
    shown?.destroy?.();
    // In the native window the window closes itself; in a browser tab, say so.
    document.body.replaceChildren(
      h('div', { class: 'closed' }, brandMark(), h('h2', {}, 'Chezz has closed'),
        h('p', { class: 'muted' }, 'You can close this tab. Start it again with run.bat or run.sh.')),
    );
  });
}

let current: View | void; // receives keys
let shown: View | void; // owns the visible page
let routeSeq = 0;

async function route() {
  const seq = ++routeSeq;
  current = undefined;
  const [path, qs] = (location.hash || '#/today').split('?');
  const params = new URLSearchParams(qs ?? '');
  const section = path.startsWith('#/game/') ? '#/library' : '#/' + (path.split('/')[1] ?? 'today');
  for (const a of nav.querySelectorAll('a')) a.classList.toggle('active', a.dataset.hash === section);

  // Render off-screen (laid out but hidden) and swap in when ready: no blank flash between pages,
  // and a slow, superseded view never writes into the visible page.
  const root = h('div', { class: 'page pending' });
  main.append(root);
  const slow = setTimeout(() => loadbar.classList.add('on'), 120);

  const game = path.match(/^#\/game\/(\d+)(?:\/(\d+))?/); // #/game/<id>[/<ply>]
  const lesson = path.match(/^#\/lessons\/([\w-]+)/);
  let view: View | void = undefined;
  try {
    if (game) view = await renderGame(root, Number(game[1]), game[2] ? Number(game[2]) : undefined);
    else if (lesson) view = await renderLesson(root, lesson[1]);
    else if (path === '#/library') view = await renderLibrary(root, params);
    else if (path === '#/insights') view = await renderInsights(root);
    else if (path === '#/puzzles') view = await renderPuzzles(root, params);
    else if (path === '#/lessons') view = await renderLessons(root);
    else if (path === '#/openings') view = await renderOpenings(root, params);
    else if (path === '#/play') view = await renderPlay(root, params);
    else if (path === '#/progress') view = await renderProgress(root);
    else view = await renderToday(root);
  } catch (err) {
    const missing = (err as { status?: number }).status === 404;
    const actions: HTMLElement[] = [h('a', { class: 'btn primary', href: '#/today' }, 'Go to Today')];
    if (!missing) actions.push(h('button', { class: 'btn ghost', onClick: () => void route() }, 'Try again'));
    const box = emptyState(missing ? 'library' : 'power', missing ? 'This item no longer exists' : 'This page could not load',
      missing ? `${(err as Error).message}. It may have been deleted.` : `${(err as Error).message}. If the app was closed, start it again with run.bat or run.sh.`,
      actions);
    box.classList.add('route-error');
    root.replaceChildren(box);
  }
  if (seq !== routeSeq) {
    view?.destroy?.();
    root.remove();
    return;
  }
  clearTimeout(slow);
  loadbar.classList.remove('on');
  shown?.destroy?.();
  for (const el of [...main.children]) if (el !== root) el.remove();
  root.classList.remove('pending');
  main.scrollTop = 0;
  shown = current = view;
  void refreshCounts();
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (!document.querySelector('.overlay')) palette();
    return;
  }
  const t = e.target as HTMLElement;
  if (t.closest('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return;
  if (document.querySelector('.overlay')) return;
  current?.onKey?.(e);
  if (e.defaultPrevented) return;
  const n = Number(e.key);
  if (n >= 1 && n <= NAV.length) location.hash = NAV[n - 1].hash;
  else if (e.key === '?') shortcuts();
});

window.addEventListener('hashchange', () => void route());
paintThemeButton();
void route();
