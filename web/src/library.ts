import { api, type ExplainBatchPart, type Filters, type GameSummary, type ImportResult, type Meta } from './api';
import type { View } from './main';
import { debounce, emptyState, fill, h, icon, modal, resultBadge, toast } from './ui';

const PAGE = 100;
let filters: Filters = {}; // kept across navigation so returning from a game keeps the view

export async function renderLibrary(root: HTMLElement, params = new URLSearchParams()): Promise<View> {
  let meta: Meta = await api.meta();
  let games: GameSummary[] = [];
  let total = 0;

  const count = h('span', { class: 'muted' });
  const analysisBox = h('div', { class: 'row gap' });
  let unexplained = 0;
  const explainBtn = h('button', { class: 'btn ghost', hidden: true, title: 'Export games without a coach summary for an AI chat, or import its replies', onClick: () => openExplain() });
  let pollTimer: number | undefined;
  let destroyed = false;
  let lastRemaining = -1;
  const banner = h('div');
  const filterBar = h('div', { class: 'filters' });
  const body = h('div');
  const search = h('input', { type: 'search', placeholder: 'Search players, notes…', title: 'Search players, openings, events and notes (/)', class: 'search' });

  root.append(
    h(
      'header',
      { class: 'page-head' },
      h('h1', {}, 'Library'),
      count,
      h('div', { class: 'spacer' }),
      analysisBox,
      explainBtn,
      h('button', { class: 'btn ghost', title: 'Write all games to one PGN file', onClick: exportAll }, icon('download'), 'Export PGN'),
      h('button', { class: 'btn ghost', title: 'Copy the database to data/backups', onClick: backup }, icon('database'), 'Back up'),
      h('button', { class: 'btn primary', onClick: () => openImport(refreshAll) }, icon('upload'), 'Import PGN'),
    ),
    banner,
    filterBar,
    body,
  );

  async function refreshAll() {
    meta = await api.meta();
    paintBanner();
    paintFilters();
    await load();
    await refreshAnalysis();
  }

  /** Header widget: "Analyze all (N)" or live queue progress; reloads the table as games finish. */
  async function refreshAnalysis() {
    clearTimeout(pollTimer);
    const s = await api.analysisStatus().catch(() => null);
    if (destroyed) return;
    if (!s) {
      pollTimer = window.setTimeout(refreshAnalysis, 5000);
      return;
    }
    const remaining = s.queued + (s.running ? 1 : 0);
    if (s.running || s.queued) {
      const pct = s.running?.total ? Math.round((100 * s.running.progress) / s.running.total) : 0;
      fill(
        analysisBox,
        h('span', { class: 'small muted' }, `Analyzing ${remaining} game${remaining === 1 ? '' : 's'}…`),
        h('div', { class: 'progress mini' }, h('div', { class: 'progress-fill', style: `width:${pct}%` })),
      );
      pollTimer = window.setTimeout(refreshAnalysis, 1200);
    } else if (s.unanalyzed && meta.engine.available) {
      fill(analysisBox, h('button', { class: 'btn ghost', title: 'Queue every game without analysis', onClick: queueAll }, icon('engine'), `Analyze all (${s.unanalyzed})`));
    } else {
      fill(analysisBox);
    }
    if (lastRemaining !== -1 && remaining !== lastRemaining) void load(); // a game finished: refresh accuracy column
    lastRemaining = remaining;
  }

  async function queueAll() {
    try {
      const res = await api.queueAll();
      toast(`Queued ${res.queued} game${res.queued === 1 ? '' : 's'} for analysis`);
      await refreshAnalysis();
    } catch (err) {
      toast((err as Error).message, { error: true });
    }
  }

  function paintBanner() {
    const show = meta.total > 0 && meta.user_games === 0;
    const names = meta.usernames.length ? meta.usernames.map((n) => `"${n}"`).join(', ') : '(not set)';
    banner.replaceChildren(
      show
        ? h(
            'div',
            { class: 'notice' },
            `None of your games match username ${names}. Set `,
            h('code', {}, 'username'),
            ' in config.toml and restart, so colors and results are shown from your side.',
          )
        : '',
    );
  }

  function select(key: keyof Filters, label: string, options: [string, string][]) {
    return h(
      'select',
      {
        class: filters[key] ? 'active' : '',
        onChange: (e: Event) => {
          (filters as Record<string, unknown>)[key] = (e.target as HTMLSelectElement).value || undefined;
          paintFilters();
          void load();
        },
      },
      h('option', { value: '' }, label),
      options.map(([v, l]) => h('option', { value: v, selected: filters[key] === v }, l)),
    );
  }

  function dateInput(key: 'date_from' | 'date_to', title: string) {
    return h('input', {
      type: 'date',
      title,
      value: filters[key] ?? '',
      class: filters[key] ? 'active' : '',
      onChange: (e: Event) => {
        filters[key] = (e.target as HTMLInputElement).value || undefined;
        paintFilters();
        void load();
      },
    });
  }

  function paintFilters() {
    if (meta.total === 0) return filterBar.replaceChildren();
    search.value = filters.q ?? '';
    const active = Object.values(filters).some(Boolean);
    fill(filterBar, 
      search,
      select('color', 'Any color', [['white', 'White'], ['black', 'Black']]),
      select('result', 'Any result', [['win', 'Won'], ['loss', 'Lost'], ['draw', 'Drawn']]),
      select('time_class', 'Any time control', meta.time_classes.map((t) => [t, cap(t)])),
      select('opening', 'Any opening', meta.openings.map((o) => [o.name, `${o.name} (${o.count})`])),
      meta.tags.length ? select('tag', 'Any tag', meta.tags.map((t) => [t.name, `${t.name} (${t.count})`])) : null,
      dateInput('date_from', 'From date'),
      dateInput('date_to', 'To date'),
      h(
        'button',
        {
          class: `btn ghost toggle${filters.starred ? ' on' : ''}`,
          title: 'Starred only',
          onClick: () => {
            filters.starred = !filters.starred || undefined;
            paintFilters();
            void load();
          },
        },
        icon('star', !!filters.starred),
      ),
      active ? h('button', { class: 'btn link', onClick: clearFilters }, 'Clear') : null,
    );
  }

  function clearFilters() {
    filters = {};
    paintFilters();
    void load();
  }

  search.addEventListener(
    'input',
    debounce(() => {
      filters.q = search.value.trim() || undefined;
      void load();
    }, 250),
  );

  let loadSeq = 0;
  async function load(append = false) {
    const my = ++loadSeq;
    let res;
    try {
      res = await api.games(filters, append ? games.length : 0, PAGE);
    } catch (err) {
      return toast((err as Error).message, { error: true });
    }
    if (my !== loadSeq || destroyed) return; // a newer filter change already asked for fresher results
    games = append ? games.concat(res.games) : res.games;
    total = res.total;
    paintTable();
    if (!append) void refreshUnexplained();
  }

  /** "Explain with AI (N)": N = analysed games matching the filters without a coach summary. */
  async function refreshUnexplained() {
    const res = await api.unexplainedCount(filters).catch(() => null);
    if (destroyed || !res) return;
    unexplained = res.count;
    fill(explainBtn, icon('coach'), unexplained ? `Explain with AI (${unexplained})` : 'Explain with AI');
    explainBtn.hidden = meta.total === 0;
  }

  // ---------- AI explanations for many games at once ----------

  function openExplain() {
    const n = unexplained;
    let scope: 'key' | 'game' = 'key';
    const scopeSeg = h('div', { class: 'seg' });
    const paintScope = () =>
      fill(scopeSeg, ([['key', 'Key moments'], ['game', 'Whole game']] as const).map(([v, l]) =>
        h('button', { class: `seg-btn${v === scope ? ' on' : ''}`, onClick: () => { scope = v; paintScope(); } }, l)));
    paintScope();
    const howMany = h('input', { type: 'number', min: 1, max: Math.max(1, n), value: String(n), class: 'num-input' });
    const perFile = h('input', { type: 'number', min: 1, max: 50, value: '10', class: 'num-input' });
    const plan = h('p', { class: 'small muted' });
    const status = h('div');
    const num = (el: HTMLInputElement, lo: number, hi: number) => {
      const v = Number(el.value);
      return Number.isInteger(v) && v >= lo && v <= hi ? v : null;
    };
    const paintPlan = () => {
      const g = num(howMany, 1, n);
      const per = num(perFile, 1, 50);
      if (g == null || per == null) {
        plan.textContent = `Enter a whole number of games from 1 to ${n}, and from 1 to 50 games per file.`;
        return;
      }
      const files = Math.ceil(g / per);
      plan.textContent = `${plural(g, 'game')}, newest first, in ${plural(files, 'file')}. Paste one file per AI chat message.`;
    };
    howMany.addEventListener('input', paintPlan);
    perFile.addEventListener('input', paintPlan);
    paintPlan();

    const exportBtn = h('button', { class: 'btn primary', disabled: !n }, icon('download'), 'Export');
    exportBtn.addEventListener('click', async () => {
      const g = num(howMany, 1, n);
      const per = num(perFile, 1, 50);
      if (g == null || per == null) return paintPlan();
      exportBtn.disabled = true;
      fill(status, h('p', { class: 'small muted' }, 'Preparing…'));
      try {
        const res = await api.explainBatch(filters, { scope, per_file: per, limit: g < n ? g : null });
        m.close();
        showParts(res.games, res.parts, scope);
      } catch (err) {
        fill(status, h('p', { class: 'error-text' }, (err as Error).message));
        exportBtn.disabled = false;
      }
    });
    const field = (label: string, input: HTMLElement, hint?: string) =>
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), input, hint ? h('span', { class: 'small muted' }, hint) : null);
    const filtered = Object.values(filters).some(Boolean);
    const m = modal(
      'Explain games with AI',
      h('div', { class: 'stack' },
        h('p', { class: 'small' }, n
          ? `${plural(n, 'analysed game has', 'analysed games have')} no coach summary yet${filtered ? ' (matching your filters)' : ''}. `
            + 'Export them as ready-made prompts, paste each file into any AI chat, then import the replies here. '
            + 'A game counts as explained once its summary is imported.'
          : `Every analysed game${filtered ? ' matching your filters' : ''} already has a coach summary. Analyze more games, or import replies you already have.`),
        n ? h('div', { class: 'stack' },
          field('Explain', scopeSeg, 'Key moments: only inaccuracies, mistakes and blunders, plus a summary per game. Whole game: every move (much longer).'),
          h('div', { class: 'row gap' }, field('Games', howMany), field('Games per file', perFile)),
          plan) : null,
        status),
      [
        h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Cancel'),
        h('button', { class: 'btn ghost', onClick: () => { m.close(); openExplainImport(); } }, icon('upload'), 'Import replies'),
        exportBtn,
      ],
    );
  }

  function showParts(gameCount: number, parts: ExplainBatchPart[], scope: 'key' | 'game') {
    const copyBtn = (text: string) => {
      const b = h('button', { class: 'btn ghost sm' }, 'Copy');
      b.addEventListener('click', async () => {
        const ok = await navigator.clipboard.writeText(text).then(() => true, () => false);
        b.textContent = ok ? 'Copied' : 'Copy failed: open the file';
      });
      return b;
    };
    const m = modal(
      `${plural(gameCount, 'game')} ready for an AI chat`,
      h('div', { class: 'stack' },
        h('p', { class: 'small' }, parts.length > 1
          ? `Split into ${parts.length} files. Paste each one into an AI chat and keep every reply. `
          : 'Paste it into any AI chat assistant and keep its whole reply. ',
          'Then use Import replies with all of them at once: paste them together or choose the saved reply files.'),
        h('ul', { class: 'part-list' }, parts.map((p, i) =>
          h('li', {},
            h('div', { class: 'part-info' },
              h('strong', {}, parts.length > 1 ? `Part ${i + 1} of ${parts.length}` : 'Prompt'),
              h('span', { class: 'small muted' }, ` ${plural(p.games.length, 'game')}, ${scope === 'key' ? plural(p.count, 'key moment') : plural(p.count, 'move')}`),
              h('div', { class: 'small muted part-path' }, p.path)),
            copyBtn(p.text)))),
      ),
      [
        h('button', { class: 'btn ghost', onClick: () => void api.openFolder('exports') }, 'Open folder'),
        h('button', { class: 'btn primary', onClick: () => { m.close(); openExplainImport(); } }, icon('upload'), 'Import replies'),
      ],
    );
  }

  function openExplainImport() {
    const drop = fileDrop('Choose reply files', '.md,.txt,text/plain,text/markdown');
    const paste = h('textarea', { class: 'paste', placeholder: '### SUMMARY 12\n…\n### KEY <fen>|<move>\nExplanation…\n\n(paste one or more replies)' });
    paste.spellcheck = false;
    const result = h('div');
    const go = h('button', { class: 'btn primary' }, 'Import');
    const closeBtn = h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Cancel');
    go.addEventListener('click', async () => {
      const texts = await drop.texts();
      if (paste.value.trim()) texts.push(paste.value);
      if (!texts.length) {
        fill(result, h('p', { class: 'error-text' }, 'Choose a reply file or paste a reply first.'));
        return;
      }
      go.disabled = true;
      try {
        const res = await api.importExplanations(texts.join('\n\n'));
        fill(result, h('p', { class: 'success-text' },
          `Imported ${plural(res.imported, 'explanation')} and ${plural(res.summaries, 'game summary', 'game summaries')}.`));
        drop.clear();
        paste.value = '';
        closeBtn.textContent = 'Done';
        await load();
      } catch (err) {
        fill(result, h('p', { class: 'error-text' }, (err as Error).message));
      } finally {
        go.disabled = false;
      }
    });
    const m = modal(
      'Import AI replies',
      h('div', { class: 'stack' },
        h('p', { class: 'small muted' }, "Import the assistant's whole replies, for one game or many. Their explanations replace the built-in ones for those moves."),
        drop.el, h('div', { class: 'or' }, 'or paste replies'), paste, result),
      [closeBtn, go],
    );
    paste.focus();
  }

  function paintTable() {
    count.textContent = meta.total ? `${total} of ${meta.total} games` : '';
    if (meta.total === 0) {
      body.replaceChildren(
        emptyState(
          'library',
          'Your library is empty',
          'Import games exported from chess.com or Lichess as PGN. Duplicates are skipped automatically.',
          [
            h('button', { class: 'btn primary', onClick: () => openImport(refreshAll) }, icon('upload'), 'Import PGN'),
            h('button', { class: 'btn ghost', onClick: importSample }, 'Try sample games'),
          ],
        ),
      );
      return;
    }
    if (games.length === 0) {
      body.replaceChildren(
        emptyState('library', 'No games match these filters', 'Try widening the date range or clearing a filter.', [
          h('button', { class: 'btn ghost', onClick: clearFilters }, 'Clear filters'),
        ]),
      );
      return;
    }
    fill(body,
      h('div', { class: 'table-scroll' }, h(
        'table',
        { class: 'games' },
        h(
          'thead',
          {},
          h('tr', {}, ['', 'Date', 'Players', 'Result', 'Accuracy', 'Opening', 'Time', 'Moves', 'Tags'].map((t) => h('th', {}, t))),
        ),
        h('tbody', {}, games.map(row)),
      )),
      games.length < total
        ? h('div', { class: 'center pad' }, h('button', { class: 'btn ghost', onClick: () => load(true) }, `Load more (${total - games.length} left)`))
        : null,
    );
  }

  function player(name: string, elo: number | null, isUser: boolean) {
    return h('span', { class: isUser ? 'me' : '' }, name, elo ? h('span', { class: 'muted small' }, ` ${elo}`) : null);
  }

  function row(g: GameSummary) {
    const star = h(
      'button',
      {
        class: `icon-btn star${g.starred ? ' on' : ''}`,
        title: g.starred ? 'Unstar' : 'Star',
        onClick: async (e: Event) => {
          e.stopPropagation();
          try {
            await api.patch(g.id, { starred: !g.starred });
          } catch (err) {
            return toast((err as Error).message, { error: true });
          }
          g.starred = !g.starred;
          star.classList.toggle('on', g.starred);
          star.replaceChildren(icon('star', g.starred));
        },
      },
      icon('star', g.starred),
    );
    return h(
      'tr',
      { onClick: () => (location.hash = `#/game/${g.id}`) },
      h('td', { class: 'w-star' }, star),
      h('td', { class: 'nowrap muted' }, g.date ?? '—'),
      h(
        'td',
        { class: 'players' },
        h('span', { class: 'piece-dot white' }),
        player(g.white, g.white_elo, g.user_color === 'white'),
        h('span', { class: 'muted' }, '  vs  '),
        h('span', { class: 'piece-dot black' }),
        player(g.black, g.black_elo, g.user_color === 'black'),
      ),
      h('td', {}, resultBadge(g)),
      h('td', { class: 'nowrap' }, accuracyCell(g)),
      h('td', { class: 'opening', title: g.opening ?? '' }, g.eco ? h('span', { class: 'muted small' }, `${g.eco} `) : null, g.opening ?? '—'),
      h('td', { class: 'nowrap' }, g.time_class ? cap(g.time_class) : '—', g.time_control ? h('span', { class: 'muted small' }, ` ${g.time_control}`) : null),
      h('td', { class: 'muted' }, String(Math.ceil(g.ply_count / 2))),
      h('td', {}, g.tags.map((t) => h('span', { class: 'chip' }, t))),
    );
  }

  async function importSample() {
    try {
      toast(importSummary(await api.importSample()));
      await refreshAll();
    } catch (err) {
      toast((err as Error).message, { error: true });
    }
  }

  async function backup() {
    try {
      const res = await api.backup();
      toast(`Backup saved: ${res.path}`, { action: { label: 'Open folder', run: () => void api.openFolder('backups') } });
    } catch (err) {
      toast(`Backup failed: ${(err as Error).message}`, { error: true });
    }
  }

  async function exportAll() {
    try {
      const res = await api.exportAll();
      toast(`Exported ${res.count} games: ${res.path}`, { action: { label: 'Open folder', run: () => void api.openFolder('exports') } });
    } catch (err) {
      toast(`Export failed: ${(err as Error).message}`, { error: true });
    }
  }

  paintBanner();
  paintFilters();
  await load();
  void refreshAnalysis();
  if (params.has('import')) {
    history.replaceState(null, '', '#/library');
    queueMicrotask(() => openImport(refreshAll));
  }

  return {
    onKey(e) {
      if (e.key === '/') {
        e.preventDefault();
        search.focus();
      } else if (e.key === 'i') {
        e.preventDefault(); // otherwise the "i" lands in the import box that just took focus
        openImport(refreshAll);
      }
    },
    destroy() {
      destroyed = true;
      clearTimeout(pollTimer);
    },
  };
}

function accuracyCell(g: GameSummary): HTMLElement | string {
  if (g.accuracy != null)
    return h('span', { class: 'acc-cell' }, g.accuracy.toFixed(1),
      g.explained ? h('span', { class: 'src-tag', title: 'Has a coach summary from an AI reply' }, 'coach') : null);
  const label: Record<string, string> = { queued: 'Queued', running: 'Analyzing…', error: 'Failed' };
  return h('span', { class: 'muted small' }, (g.analysis_status && label[g.analysis_status]) || '—');
}

function cap(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function importSummary(r: ImportResult): string {
  if (!r.added && !r.duplicates && !r.errors.length) return 'No games found. PGN text starts with tags like [Event "..."] or with moves like 1. e4 e5.';
  const parts = [`Imported ${r.added} game${r.added === 1 ? '' : 's'}`];
  if (r.duplicates) parts.push(`${r.duplicates} duplicate${r.duplicates === 1 ? '' : 's'} skipped`);
  if (r.errors.length) parts.push(`${r.errors.length} could not be read`);
  return parts.join(' · ');
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Click-or-drop picker for several text files. */
function fileDrop(title: string, accept: string) {
  let files: File[] = [];
  const fileInput = h('input', { type: 'file', multiple: true, accept, hidden: true });
  const fileList = h('div', { class: 'muted small' });
  const setFiles = (list: FileList | null) => {
    files = list ? Array.from(list) : [];
    fileList.textContent = files.map((f) => f.name).join(', ');
  };
  fileInput.addEventListener('change', () => setFiles(fileInput.files));
  const el = h(
    'div',
    {
      class: 'dropzone',
      onClick: () => fileInput.click(),
      onDragover: (e: DragEvent) => {
        e.preventDefault();
        el.classList.add('over');
      },
      onDragleave: () => el.classList.remove('over'),
      onDrop: (e: DragEvent) => {
        e.preventDefault();
        el.classList.remove('over');
        setFiles(e.dataTransfer?.files ?? null);
      },
    },
    icon('upload'),
    h('div', {}, h('strong', {}, title), ' or drop them here'),
    fileList,
    fileInput,
  );
  return {
    el,
    texts: () => Promise.all(files.map((f) => f.text())),
    clear: () => {
      setFiles(null);
      fileInput.value = '';
    },
  };
}

function openImport(onDone: () => Promise<void>) {
  const drop = fileDrop('Choose PGN files', '.pgn,text/plain');
  const paste = h('textarea', { class: 'paste', placeholder: '[Event "Live Chess"]\n…\n1. e4 e5 2. Nf3 …' });
  paste.spellcheck = false;
  const result = h('div');
  const importBtn = h('button', { class: 'btn primary', onClick: run }, 'Import');
  const closeBtn = h('button', { class: 'btn ghost', onClick: () => m.close() }, 'Cancel');


  const m = modal(
    'Import games',
    h('div', { class: 'stack' }, drop.el, h('div', { class: 'or' }, 'or paste PGN'), paste, result),
    [closeBtn, importBtn],
  );
  paste.focus();

  async function run() {
    const texts = await drop.texts();
    if (paste.value.trim()) texts.push(paste.value);
    if (!texts.length) {
      fill(result, h('p', { class: 'error-text' }, 'Choose a file or paste some PGN first.'));
      return;
    }
    importBtn.disabled = true;
    importBtn.textContent = 'Importing…';
    try {
      const res = await api.importPgn(texts.join('\n\n'));
      fill(result, 
        h('p', { class: res.added || res.duplicates ? 'success-text' : 'error-text' }, importSummary(res)),
        res.errors.length
          ? h('details', {}, h('summary', {}, 'Show problems'), h('ul', { class: 'small' }, res.errors.map((e) => h('li', {}, e))))
          : null,
      );
      importBtn.textContent = 'Import more';
      closeBtn.textContent = 'Done';
      drop.clear();
      paste.value = '';
      await onDone();
    } catch (err) {
      fill(result, h('p', { class: 'error-text' }, (err as Error).message));
      importBtn.textContent = 'Import';
    } finally {
      importBtn.disabled = false;
    }
  }
}
