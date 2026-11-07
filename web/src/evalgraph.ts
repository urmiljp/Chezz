import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

import type { Classification } from './api';

const MARKED = ['inaccuracy', 'mistake', 'blunder'] as const;
/** For DOM styles; the canvas reads the resolved values at draw time so theme switches apply. */
export const CLASS_COLORS: Record<Classification, string> = {
  best: 'var(--c-best)',
  good: 'var(--c-good)',
  inaccuracy: 'var(--c-inaccuracy)',
  mistake: 'var(--c-mistake)',
  blunder: 'var(--c-blunder)',
};
const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export interface EvalGraph {
  setPly(ply: number): void;
  destroy(): void;
}

/** White's win% over the game (white area = White's share). Click anywhere to jump to that ply. */
export function evalGraph(
  el: HTMLElement,
  wins: number[],
  marks: { ply: number; cls: Classification }[],
  onPick: (ply: number) => void,
): EvalGraph {
  const xs = wins.map((_, i) => i);
  const markSeries = MARKED.map((cls) => xs.map((i) => (marks.some((m) => m.ply === i && m.cls === cls) ? wins[i] : null)));
  let current = 0;
  const height = 96;

  const u = new uPlot(
    {
      width: el.clientWidth || 360,
      height,
      padding: [4, 0, 4, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false, setScale: false }, points: { show: false }, y: false },
      scales: { x: { time: false }, y: { range: [0, 100] } },
      axes: [{ show: false }, { show: false }],
      series: [
        {},
        { stroke: () => cssVar('--graph-fill'), width: 1, fill: () => cssVar('--graph-fill'), points: { show: false } },
        ...MARKED.map((cls) => ({
          stroke: () => cssVar(`--c-${cls}`),
          width: 0,
          paths: () => null,
          points: { show: true, size: 9, width: 2, fill: () => cssVar(`--c-${cls}`), stroke: () => cssVar('--graph-bg') },
        })),
      ],
      hooks: {
        draw: [
          (u) => {
            const ctx = u.ctx;
            const { left, width, top, height: h } = u.bbox;
            const mid = u.valToPos(50, 'y', true);
            ctx.save();
            ctx.strokeStyle = 'rgba(128,128,128,0.45)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(left, mid);
            ctx.lineTo(left + width, mid);
            ctx.stroke();
            const x = u.valToPos(current, 'x', true);
            ctx.strokeStyle = cssVar('--accent');
            ctx.lineWidth = 2 * devicePixelRatio;
            ctx.beginPath();
            ctx.moveTo(x, top);
            ctx.lineTo(x, top + h);
            ctx.stroke();
            ctx.restore();
          },
        ],
      },
    },
    [xs, wins, ...markSeries] as uPlot.AlignedData,
    el,
  );

  u.over.addEventListener('click', () => {
    if (u.cursor.idx != null) onPick(u.cursor.idx);
  });
  const resize = new ResizeObserver(() => u.setSize({ width: el.clientWidth, height }));
  resize.observe(el);
  const repaint = () => u.redraw(false);
  window.addEventListener('themechange', repaint);

  return {
    setPly(ply) {
      current = ply;
      u.redraw(false);
    },
    destroy() {
      window.removeEventListener('themechange', repaint);
      resize.disconnect();
      u.destroy();
    },
  };
}
