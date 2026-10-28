/** Geometría de gráficas SVG sin dependencias: funciones puras, deterministas y testeables. */

export interface Box {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface Tick {
  value: number;
  y: number;
}

export interface Band<K extends string> {
  key: K;
  /** Polígono cerrado del área apilada, en coordenadas del SVG. */
  path: string;
}

/** Máximo «redondo» (1, 2, 5 × 10^n) que cubre `max`; nunca menor que 1. */
export function niceMax(max: number): number {
  if (!Number.isFinite(max) || max <= 1) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  for (const step of [1, 2, 5, 10]) {
    if (step * magnitude >= max) return step * magnitude;
  }
  return 10 * magnitude;
}

/** Marcas del eje Y en pasos enteros iguales (5, 4, 2 o 1 tramos) de 0 al máximo. */
export function yTicks(max: number, box: Box): Tick[] {
  const plot = box.height - box.top - box.bottom;
  const parts = [5, 4, 2, 1].find((n) => Number.isInteger(max / n)) ?? 1;
  const values = Array.from({ length: parts + 1 }, (_, i) => (i * max) / parts);
  return values.map((value) => ({ value, y: round(box.top + plot - (value / max) * plot) }));
}

/** Áreas apiladas: la primera clave queda abajo. */
export function stackedAreas<K extends string>(
  points: readonly Readonly<Record<K, number>>[],
  keys: readonly K[],
  box: Box,
  max: number,
): Band<K>[] {
  if (points.length === 0) return [];
  const plotW = box.width - box.left - box.right;
  const plotH = box.height - box.top - box.bottom;
  const step = points.length > 1 ? plotW / (points.length - 1) : 0;
  const x = (i: number): number => round(box.left + i * step);
  const y = (v: number): number => round(box.top + plotH - (Math.min(v, max) / max) * plotH);
  const base = points.map(() => 0);
  return keys.map((key) => {
    const lower = [...base];
    points.forEach((p, i) => {
      base[i] = (base[i] ?? 0) + Math.max(0, p[key]);
    });
    const top = base.map((v, i) => `${String(x(i))},${String(y(v))}`);
    const bottom = lower.map((v, i) => `${String(x(i))},${String(y(v))}`).reverse();
    return { key, path: `M${top.join("L")}L${bottom.join("L")}Z` };
  });
}

/** Segmentos de una barra apilada horizontal, con anchos que suman `width · total / max`. */
export function stackSegments<K extends string>(
  values: readonly { key: K; value: number }[],
  width: number,
  max: number,
): { key: K; x: number; width: number }[] {
  const scale = max > 0 ? width / max : 0;
  let x = 0;
  return values
    .filter((v) => v.value > 0)
    .map((v) => {
      const segment = { key: v.key, x: round(x), width: round(v.value * scale) };
      x += v.value * scale;
      return segment;
    });
}

function round(n: number): number {
  return Math.round(n * 10) / 10;
}
