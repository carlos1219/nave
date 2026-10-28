// CLAUDE.md §13: contraste WCAG AA (4,5:1) en todas las parejas de color de severidad.
import { describe, expect, it } from "vitest";

import { read } from "../files";

function token(css: string, name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(css);
  if (match?.[1] === undefined) throw new Error(`falta el token ${name}`);
  return match[1];
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(hex.slice(i, i + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (channels[0] ?? 0) + 0.7152 * (channels[1] ?? 0) + 0.0722 * (channels[2] ?? 0);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

const THEMES = (() => {
  const css = read("src/app/styles/tokens.css");
  const [light = "", dark = ""] = css.split("@media (prefers-color-scheme: dark)");
  return [
    ["claro", light, light],
    ["oscuro", dark, light],
  ] as const;
})();

describe.each(THEMES)("contraste AA en el tema %s", (_theme, own, fallback) => {
  const color = (name: string): string => {
    try {
      return token(own, name);
    } catch {
      return token(fallback, name);
    }
  };
  const pairs: [string, string, string][] = [
    ...["critical", "high", "medium", "low", "info"].map(
      (n): [string, string, string] => [`severidad ${n}`, `sev-${n}`, `sev-${n}-bg`],
    ),
    ["texto", "text", "surface"],
    ["texto sobre fondo", "text", "bg"],
    ["texto secundario", "text-2", "surface"],
    ["texto terciario", "text-3", "surface"],
    ["texto terciario sobre fondo", "text-3", "bg"],
    ["botón primario", "accent-text", "accent"],
    ["enlace", "accent", "surface"],
    ["texto secundario sobre superficie 2", "text-2", "surface-2"],
    ["texto terciario sobre superficie 2", "text-3", "surface-2"],
    ["acento sobre su fondo suave", "accent", "accent-soft"],
    ["error", "danger", "danger-bg"],
    ["correcto", "ok", "ok-bg"],
    ["aviso", "warn", "warn-bg"],
    ["registros", "log-text", "log-bg"],
    ["registros atenuados", "log-muted", "log-bg"],
    ["registros de aviso", "log-warn", "log-bg"],
    ["registros de error", "log-error", "log-error-bg"],
  ];

  it.each(pairs)("%s", (_name, fg, bg) => {
    expect(contrast(color(fg), color(bg))).toBeGreaterThanOrEqual(4.5);
  });
});
