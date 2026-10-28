import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

// Vitest se ejecuta siempre desde la raíz del proyecto.
export const ROOT = process.cwd();

export function walk(dir: string, extensions: readonly string[]): string[] {
  const found: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (name !== "node_modules" && name !== "dist") found.push(...walk(path, extensions));
    } else if (extensions.some((e) => name.endsWith(e))) {
      found.push(path);
    }
  }
  return found;
}

export function sources(extensions: readonly string[]): { path: string; text: string }[] {
  return walk(join(ROOT, "src"), extensions).map((path) => ({
    path: relative(ROOT, path).replaceAll("\\", "/"),
    text: readFileSync(path, "utf8"),
  }));
}

export function read(relativePath: string): string {
  return readFileSync(join(ROOT, relativePath), "utf8");
}
