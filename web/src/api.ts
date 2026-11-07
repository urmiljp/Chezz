export type Color = 'white' | 'black';
export type Outcome = 'win' | 'loss' | 'draw';

export interface GameSummary {
  id: number;
  date: string | null;
  white: string;
  black: string;
  white_elo: number | null;
  black_elo: number | null;
  result: string;
  user_color: Color | null;
  user_result: Outcome | null;
  eco: string | null;
  opening: string | null;
  opening_family: string | null;
  time_control: string | null;
  time_class: string | null;
  termination: string | null;
  ply_count: number;
  starred: boolean;
  link: string | null;
  tags: string[];
  accuracy?: number | null;
  analysis_status?: RunStatus | null;
}

export type RunStatus = 'queued' | 'running' | 'done' | 'error';
export type Classification = 'best' | 'good' | 'inaccuracy' | 'mistake' | 'blunder';
export type ClassCounts = Record<Classification, number>;

export interface AnalysisRun {
  id: number;
  status: RunStatus;
  engine: string | null;
  depth: number | null;
  movetime_ms: number | null;
  progress: number;
  total: number;
  white_accuracy: number | null;
  black_accuracy: number | null;
  error: string | null;
  finished_at: string | null;
  counts?: Record<Color, ClassCounts>;
  time_trouble?: Record<Color, number>;
}

export interface LineMove {
  san: string;
  uci: string;
  fen: string;
}

export interface MoveReview {
  ply: number;
  san: string;
  uci: string;
  color: Color;
  classification: Classification;
  win_before: number;
  win_after: number;
  win_drop: number;
  accuracy: number;
  eval_before: string;
  eval_after: string;
  best_uci: string | null;
  best_san: string | null;
  best_line: LineMove[];
  refutation_san: string | null;
  next_best_uci: string | null;
  clock: number | null;
  clock_before: number | null;
  time_spent: number | null;
  time_trouble: boolean;
  fen_before: string;
  explanation: string;
  explanation_source: 'template' | 'llm';
  phase: string | null;
  tags: MoveTag[];
}

export interface Review {
  run: AnalysisRun | null;
  pending: AnalysisRun | null;
  positions: { ply: number; win: number; eval: string }[];
  moves: MoveReview[];
  coach_summary: string | null;
}

export interface AnalysisStatus {
  queued: number;
  running: { id: number; game_id: number; progress: number; total: number; white: string; black: string } | null;
  unanalyzed: number;
}

export interface Move {
  san: string;
  uci: string;
  fen: string;
  check: boolean;
  clock: number | null;
}

export interface GameDetail extends GameSummary {
  pgn: string;
  notes: string;
  event: string | null;
  site: string | null;
  start_fen: string;
  moves: Move[];
  opening_deviations: Deviation;
}

export interface Counted {
  name: string;
  count: number;
}

export interface Meta {
  usernames: string[];
  total: number;
  user_games: number;
  time_classes: string[];
  openings: Counted[];
  tags: Counted[];
  record: { result: Outcome; count: number }[];
  data_dir: string;
  engine: { available: boolean; path: string | null; depth: number; movetime_ms: number };
}

export interface ImportResult {
  added: number;
  duplicates: number;
  errors: string[];
  ids: number[];
}

export interface Filters {
  q?: string;
  color?: string;
  result?: string;
  time_class?: string;
  opening?: string;
  tag?: string;
  date_from?: string;
  date_to?: string;
  starred?: boolean;
}

export interface MoveTag {
  tag: string;
  kind: 'allowed' | 'missed';
  label: string;
}

export interface Deviation {
  left_book: { ply: number; move: string; by: 'you' | 'opponent' | null; book_moves: string[] } | null;
  left_repertoire: { ply: number; move: string; expected: string } | null;
}

export interface Weakness {
  key: string;
  kind: 'tactic' | 'phase' | 'time' | 'opening';
  label: string;
  detail: string;
  score: number;
  theme?: string;
  tag?: string;
  phase?: string;
  opening?: string;
}

export interface Profile {
  summary: { games: number; moves: number; errors: number; inaccuracies: number; mistakes: number; blunders: number; accuracy: number | null };
  phases: { phase: string; moves: number; accuracy: number | null; errors: number; blunders: number }[];
  tactics: { tag: string; label: string; theme: string; allowed: number; missed: number; per_game: number }[];
  time: Record<'normal' | 'trouble', { moves: number; errors: number; blunders: number }>;
  openings: { name: string; games: number; wins: number; draws: number; losses: number; score: number | null; accuracy: number | null; blunders: number | null }[];
  weaknesses: Weakness[];
}

export interface Puzzle {
  id: number;
  source: 'own' | 'lichess';
  fen: string;
  moves: string[];
  rating: number;
  themes: string[];
  game_id: number | null;
  ply: number | null;
  review_box: number | null;
  attempts: number;
}

export interface PuzzleSummary {
  rating: number;
  rd: number;
  attempts: number;
  solved: number;
  today: number;
  due: number;
  in_review: number;
  own_total: number;
  own_unseen: number;
  lichess_count: number;
  themes: { theme: string; label: string; own: number }[];
  plan_themes: string[];
}

export interface LichessStatus {
  count: number | null;
  import: { running: boolean; imported: number; progress: number; error: string | null };
  candidates: string[];
}

export interface LessonExample {
  fen: string;
  setup?: string | null; // opponent's first move (opening lines played as Black)
  solution: string[];
  accept: string[][];
  prompt: string;
  explain: string;
  orientation: Color;
  san?: string[]; // opening lines: every move from the start, with a note per move
  notes?: (string | null)[];
}

export interface LessonSummary {
  id: string;
  title: string;
  category: string;
  summary: string;
  themes: string[];
  examples: number;
  completed: boolean;
  started: boolean;
  drill_correct: number | null;
  drill_total: number | null;
  reason: string | null;
  recommended: boolean;
}

export interface Lesson {
  id: string;
  title: string;
  category: string;
  summary: string;
  body: string;
  examples: LessonExample[];
  side?: Color; // opening lessons
  drill: { themes: string[]; count: number; mode?: 'lines' };
  progress: { examples_done: number; drill_correct: number | null; drill_total: number | null; completed_at: string | null } | null;
}

export interface Candidate {
  uci: string;
  san: string;
  games: number;
  score: number | null;
  book: boolean;
  repertoire: boolean;
}

export interface ExplorePosition {
  fen: string;
  your_turn: boolean;
  eco: string | null;
  name: string | null;
  candidates: Candidate[];
  repertoire_move: string | null;
  manual: boolean;
}

export interface DrillCard {
  epd: string;
  fen: string;
  line: string[];
  move_uci: string;
  san: string;
  box: number;
  color: Color;
  due_left: number;
}

export interface StreakInfo {
  current: number;
  best: number;
  today: boolean;
  days: { day: string; count: number }[];
}

export interface Plan {
  games: number;
  puzzles: { target: number; done: number; themes: string[]; due: number; own_unseen: number; lichess: boolean };
  lesson: LessonSummary | null;
  game: { id: number; white: string; black: string; date: string | null; user_result: Outcome | null; opening: string | null; errors: number | null; analysed: boolean } | null;
  openings_due: number;
  streak: StreakInfo;
  rating: { current: number; trend: number[] };
  accuracy_trend: number[];
  weaknesses: Weakness[];
}

export interface ProgressData {
  games: { id: number; date: string | null; white: string; black: string; user_color: Color; user_result: Outcome | null; accuracy: number | null; blunders: number; mistakes: number }[];
  rating: { at: string; rating: number }[];
  weakness_months: { month: string; errors: number; shares: Record<string, number> }[];
  tag_labels: Record<string, string>;
  streak: StreakInfo;
  totals: { games: number; analysed: number; puzzles_solved: number; lessons_done: number; drills: number };
}

export interface Endgame {
  id: string;
  title: string;
  group: string;
  fen: string;
  side: Color;
  goal: 'promote' | 'mate' | 'hold';
  moves?: number;
  description: string;
  attempts: number;
  successes: number;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j.detail, () => null);
    throw Object.assign(new Error(typeof detail === 'string' ? detail : `${res.status} ${res.statusText}`), { status: res.status });
  }
  return res.json();
}

function query(f: Filters, offset: number, limit: number): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, String(v));
  p.set('offset', String(offset));
  p.set('limit', String(limit));
  return p.toString();
}

export const api = {
  meta: () => req<Meta>('GET', '/api/meta'),
  games: (f: Filters, offset = 0, limit = 100) =>
    req<{ total: number; games: GameSummary[] }>('GET', `/api/games?${query(f, offset, limit)}`),
  game: (id: number) => req<GameDetail>('GET', `/api/games/${id}`),
  patch: (id: number, body: { starred?: boolean; notes?: string; tags?: string[] }) =>
    req<GameSummary>('PATCH', `/api/games/${id}`, body),
  remove: (id: number) => req<{ ok: boolean }>('DELETE', `/api/games/${id}`),
  importPgn: (pgn: string) => req<ImportResult>('POST', '/api/import', { pgn }),
  importSample: () => req<ImportResult>('POST', '/api/import-sample'),
  backup: () => req<{ path: string }>('POST', '/api/backup'),
  exportAll: () => req<{ path: string; count: number }>('POST', '/api/export'),
  openFolder: (which: 'backups' | 'exports') => req<{ ok: boolean }>('POST', '/api/open-folder', { which }),
  review: (id: number) => req<Review>('GET', `/api/games/${id}/review`),
  analyze: (id: number) => req<Review>('POST', `/api/games/${id}/analyze`),
  queueAll: () => req<{ queued: number }>('POST', '/api/analysis/queue-all'),
  analysisStatus: () => req<AnalysisStatus>('GET', '/api/analysis/status'),
  /** scope 'game': every move; 'key': inaccuracies, mistakes and blunders. */
  llmBatch: (id: number, scope: 'game' | 'key') =>
    req<{ path: string; count: number; text: string }>('POST', `/api/games/${id}/llm-batch?scope=${scope}`),
  importExplanations: (text: string) => req<{ imported: number; summaries: number }>('POST', '/api/explanations/import', { text }),
  exit: () => req<{ ok: boolean }>('POST', '/api/exit'),
  markReviewed: (id: number) => req<{ ok: boolean }>('POST', `/api/games/${id}/reviewed`),

  weakness: (f: Filters) => req<Profile>('GET', `/api/weakness?${query(f, 0, 0)}`),

  puzzleSummary: () => req<PuzzleSummary>('GET', '/api/puzzles/summary'),
  /** null when nothing matches. */
  nextPuzzle: (mode: string, theme?: string, exclude: number[] = []) =>
    req<Puzzle | null>('GET', `/api/puzzles/next?${new URLSearchParams({ mode, theme: theme ?? '', exclude: exclude.join(',') })}`),
  puzzle: (id: number) => req<Puzzle>('GET', `/api/puzzles/${id}`),
  attempt: (id: number, solved: boolean, hints: number, seconds: number) =>
    req<{ rating: number; delta: number; rated: boolean; box: number | null; due_in_days: number | null }>(
      'POST', `/api/puzzles/${id}/attempt`, { solved, hints, seconds }),
  lichessStatus: () => req<LichessStatus>('GET', '/api/puzzles/lichess'),
  lichessImport: (path: string) => req<LichessStatus['import']>('POST', '/api/puzzles/lichess/import', { path }),
  pickFile: () => req<{ path: string | null; supported: boolean }>('POST', '/api/pick-file'),

  lessons: () => req<LessonSummary[]>('GET', '/api/lessons'),
  lesson: (id: string) => req<Lesson>('GET', `/api/lessons/${id}`),
  lessonProgress: (id: string, body: { examples_done?: number; drill_correct?: number; drill_total?: number; completed?: boolean }) =>
    req<unknown>('POST', `/api/lessons/${id}/progress`, body),
  lessonRepertoire: (id: string) => req<{ color: Color; added: number; kept: number }>('POST', `/api/lessons/${id}/repertoire`),

  openingsSummary: () => req<Record<Color, { cards: number; due: number; games: number }>>('GET', '/api/openings/summary'),
  buildRepertoire: (color: Color) => req<{ added: number }>('POST', '/api/openings/build', { color }),
  explore: (color: Color, fen: string) => req<ExplorePosition>('GET', `/api/openings/explore?${new URLSearchParams({ color, fen })}`),
  setRepertoire: (color: Color, fen: string, line: string[], uci: string) =>
    req<ExplorePosition>('POST', '/api/openings/repertoire', { color, fen, line, uci }),
  /** null when nothing is due. */
  drillCard: (color: Color) => req<DrillCard | null>('GET', `/api/openings/drill?color=${color}`),
  gradeCard: (color: Color, epd: string, correct: boolean) =>
    req<{ box: number; due_in_days: number }>('POST', '/api/openings/drill', { color, epd, correct }),
  deviations: () =>
    req<({ game_id: number; date: string | null; white: string; black: string; user_color: Color; user_result: Outcome | null } & Deviation)[]>(
      'GET', '/api/openings/deviations'),

  plan: () => req<Plan>('GET', '/api/plan'),
  progress: () => req<ProgressData>('GET', '/api/progress'),

  engineMove: (fen: string, elo: number) => req<{ uci: string; san: string }>('POST', '/api/play/move', { fen, elo }),
  finishGame: (body: { start_fen: string; moves: string[]; user_color: Color; result: string; elo: number; termination: string }) =>
    req<{ game_id: number | null }>('POST', '/api/play/finish', body),
  endgames: () => req<Endgame[]>('GET', '/api/endgames'),
  endgameResult: (id: string, success: boolean) => req<unknown>('POST', `/api/endgames/${id}/result`, { success }),
};

export const THEME_LABELS: Record<string, string> = {
  hangingPiece: 'Hanging piece', fork: 'Fork', pin: 'Pin', skewer: 'Skewer', discoveredAttack: 'Discovered attack',
  backRankMate: 'Back-rank mate', mate: 'Checkmate', mateIn1: 'Mate in 1', mateIn2: 'Mate in 2', mateIn3: 'Mate in 3',
  sacrifice: 'Sacrifice', deflection: 'Deflection', attraction: 'Attraction', trappedPiece: 'Trapped piece',
  doubleCheck: 'Double check', promotion: 'Promotion', opening: 'Opening', middlegame: 'Middlegame', endgame: 'Endgame',
  pawnEndgame: 'Pawn endgame', rookEndgame: 'Rook endgame', queenEndgame: 'Queen endgame',
  kingsideAttack: 'Kingside attack', defensiveMove: 'Defensive move',
};
