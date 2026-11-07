import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

/** Categorical slots in fixed order (validated reference palette), light / dark. */
const SLOTS = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500'],
};

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const slot = (i: number) => SLOTS[document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'][i];
export const accent = () => css('--accent');

export interface Series {
  label: string;
  values: (number | null)[];
  color: string;
  dash?: number[];
  points?: boolean;
  bars?: boolean;
  fmt?: (v: number) => string;
}

export interface Chart {
  destroy(): void;
}

/** Line/bar chart with one y-axis, recessive grid, and a live legend that doubles as the hover readout. */
export function chart(
  el: HTMLElement,
  xs: number[],
  series: Series[],
  opts: { height?: number; yRange?: [number, number]; xFmt?: (x: number) => string; yFmt?: (v: number) => string } = {},
): Chart {
  const height = opts.height ?? 220;
  const muted = css('--muted');
  const grid = css('--border');
  const axis = (fmt?: (v: number) => string): uPlot.Axis => ({
    stroke: muted,
    grid: { stroke: grid, width: 1 },
    ticks: { show: false },
    font: `11px ${css('--font-ui') || 'system-ui, sans-serif'}`,
    values: fmt ? (_u, vals) => vals.map((v) => (v == null ? '' : fmt(v))) : undefined,
  });
  const u = new uPlot(
    {
      width: el.clientWidth || 600,
      height,
      padding: [10, 12, 0, 0],
      cursor: { points: { size: 8 }, drag: { x: false, y: false, setScale: false } },
      legend: { show: true, live: true },
      scales: { x: { time: false }, y: opts.yRange ? { range: opts.yRange } : { auto: true } },
      axes: [axis(opts.xFmt), axis(opts.yFmt)],
      series: [
        { label: '', value: (_u, v) => (v == null ? '' : opts.xFmt ? opts.xFmt(v) : String(v)) },
        ...series.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: s.bars ? 0 : 2,
          fill: s.bars ? s.color : undefined,
          dash: s.dash,
          paths: s.bars ? uPlot.paths.bars!({ size: [0.6, 22], radius: 0.15 }) : undefined,
          points: { show: s.points ?? false, size: 6, fill: s.color },
          spanGaps: true,
          value: (_u: uPlot, v: number | null) => (v == null ? '–' : s.fmt ? s.fmt(v) : String(Math.round(v * 10) / 10)),
        })),
      ],
    },
    [xs, ...series.map((s) => s.values)] as uPlot.AlignedData,
    el,
  );
  const ro = new ResizeObserver(() => u.setSize({ width: el.clientWidth, height }));
  ro.observe(el);
  return {
    destroy() {
      ro.disconnect();
      u.destroy();
    },
  };
}

/** Tiny trend line without axes (stat tiles). */
export function sparkline(el: HTMLElement, values: number[], color = accent()): Chart {
  const u = new uPlot(
    {
      width: el.clientWidth || 160,
      height: 36,
      padding: [4, 2, 4, 2],
      legend: { show: false },
      cursor: { show: false },
      scales: { x: { time: false } },
      axes: [{ show: false }, { show: false }],
      series: [{}, { stroke: color, width: 2, points: { show: false } }],
    },
    [values.map((_, i) => i), values],
    el,
  );
  return { destroy: () => u.destroy() };
}

/** Moving average for trend lines. */
export function movingAverage(values: (number | null)[], n: number): (number | null)[] {
  return values.map((_, i) => {
    const win = values.slice(Math.max(0, i - n + 1), i + 1).filter((v): v is number => v != null);
    return win.length ? win.reduce((a, b) => a + b, 0) / win.length : null;
  });
}
