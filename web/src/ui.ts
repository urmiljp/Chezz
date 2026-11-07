type Child = Node | string | number | null | undefined | false;
type Props = Record<string, unknown>;

/** Tiny DOM builder: h('button', { class: 'btn', onClick }, 'Label'). */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props = {},
  ...children: (Child | Child[])[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
    else if (k === 'class') el.className = String(v);
    else if (k === 'html') el.innerHTML = String(v);
    else if (k in el && !['list', 'style', 'form'].includes(k)) (el as unknown as Props)[k] = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

/** replaceChildren that skips null/false children. */
export function fill(el: Element, ...children: (Child | Child[])[]): void {
  el.replaceChildren(...children.flat().flatMap((c) => (c == null || c === false ? [] : [c instanceof Node ? c : String(c)])));
}

const ICONS: Record<string, string> = {
  today: '<path d="M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>',
  library: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  first: '<path d="M18 17l-5-5 5-5M11 17l-5-5 5-5"/>',
  prev: '<path d="M15 18l-6-6 6-6"/>',
  next: '<path d="M9 18l6-6-6-6"/>',
  last: '<path d="M6 17l5-5-5-5M13 17l5-5-5-5"/>',
  flip: '<path d="M7 16V4M7 4 3 8M7 4l4 4M17 8v12M17 20l4-4M17 20l-4-4"/>',
  back: '<path d="M19 12H5M12 19l-7-7 7-7"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  insights: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
  puzzle: '<path d="M10 3h4v3a2 2 0 1 0 4 0V3h3v7h-3a2 2 0 1 0 0 4h3v7h-7v-3a2 2 0 1 0-4 0v3H3v-7h3a2 2 0 1 0 0-4H3V3h7z"/>',
  lesson: '<path d="M2 6l10-4 10 4-10 4L2 6z"/><path d="M6 8v6c0 2 3 4 6 4s6-2 6-4V8"/>',
  opening: '<path d="M6 3v12M18 9v12M6 15a6 6 0 0 0 6-6h6"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/>',
  progress: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  engine: '<path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  board: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
};

export function icon(name: string, filled = false): HTMLElement {
  const span = document.createElement('span');
  span.className = 'icon';
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  return span;
}

export function toast(message: string, opts: { error?: boolean; action?: { label: string; run: () => void } } = {}) {
  let host = document.getElementById('toasts');
  if (!host) document.body.append((host = h('div', { id: 'toasts' })));
  const el = h(
    'div',
    { class: `toast${opts.error ? ' toast-error' : ''}` },
    h('span', {}, message),
    opts.action && h('button', { class: 'link', onClick: () => opts.action!.run() }, opts.action.label),
  );
  host.append(el);
  setTimeout(() => el.remove(), opts.error ? 8000 : 5000);
}

export function modal(title: string, body: Node, actions: HTMLElement[] = []): { close: () => void; root: HTMLElement } {
  const close = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey, true);
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
    }
  };
  const overlay = h(
    'div',
    { class: 'overlay', onMousedown: (e: MouseEvent) => e.target === overlay && close() },
    h(
      'div',
      { class: 'modal', role: 'dialog' },
      h('div', { class: 'modal-head' }, h('h2', {}, title), h('button', { class: 'icon-btn', title: 'Close', onClick: close }, icon('x'))),
      h('div', { class: 'modal-body' }, body),
      actions.length ? h('div', { class: 'modal-foot' }, actions) : null,
    ),
  );
  document.addEventListener('keydown', onKey, true);
  document.body.append(overlay);
  return { close, root: overlay };
}

export function emptyState(iconName: string, title: string, text: string, actions: HTMLElement[]): HTMLElement {
  return h(
    'div',
    { class: 'empty' },
    h('div', { class: 'empty-icon' }, icon(iconName)),
    h('h2', {}, title),
    h('p', { class: 'muted' }, text),
    h('div', { class: 'row gap' }, actions),
  );
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number): (...a: A) => void {
  let t: number | undefined;
  return (...a: A) => {
    clearTimeout(t);
    t = window.setTimeout(() => fn(...a), ms);
  };
}

export function fmtClock(sec: number | null): string {
  if (sec == null) return '';
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const sStr = s < 20 ? ss.toFixed(1).padStart(4, '0') : String(Math.floor(ss)).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sStr}` : `${m}:${sStr}`;
}

export function resultBadge(g: { user_result: string | null; result: string }): HTMLElement {
  if (g.user_result) {
    const label = { win: 'Won', loss: 'Lost', draw: 'Draw' }[g.user_result] ?? g.user_result;
    return h('span', { class: `badge badge-${g.user_result}` }, label);
  }
  const label: Record<string, string> = { '1/2-1/2': '½-½', '*': 'Unfinished' };
  return h('span', { class: 'badge' }, label[g.result] ?? g.result);
}
