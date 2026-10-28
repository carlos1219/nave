// Cadena de suministro (F7-22): `npm ci` solo verifica un tarball si el lockfile guarda su
// hash. Se llegó a tener 264 de 301 paquetes sin `integrity` ni `resolved`.
import { describe, expect, it } from "vitest";

import { read } from "../files";

interface LockEntry {
  version?: string;
  resolved?: string;
  integrity?: string;
  link?: boolean;
}

const lock = JSON.parse(read("package-lock.json")) as {
  lockfileVersion: number;
  packages: Record<string, LockEntry>;
};
const entries = Object.entries(lock.packages).filter(([path]) => path !== "");

describe("package-lock.json", () => {
  it("usa el formato con hashes por paquete", () => {
    expect(lock.lockfileVersion).toBeGreaterThanOrEqual(3);
    expect(entries.length).toBeGreaterThan(0);
  });

  it("todo paquete tiene hash de integridad SHA-512", () => {
    const missing = entries.filter(([, e]) => !e.integrity).map(([path]) => path);
    expect(missing).toEqual([]);
    const weak = entries.filter(([, e]) => !/^sha512-[A-Za-z0-9+/]{86}==$/.test(e.integrity ?? ""));
    expect(weak.map(([path]) => path)).toEqual([]);
  });

  it("todo paquete se descarga del registro público por HTTPS", () => {
    const foreign = entries
      .filter(([, e]) => !(e.resolved ?? "").startsWith("https://registry.npmjs.org/"))
      .map(([path, e]) => `${path} -> ${e.resolved ?? "sin resolved"}`);
    expect(foreign).toEqual([]);
  });

  it("las dependencias directas están fijadas a una versión exacta", () => {
    const pkg = JSON.parse(read("package.json")) as {
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
    };
    const loose = Object.entries({ ...pkg.dependencies, ...pkg.devDependencies })
      .filter(([, range]) => !/^\d+\.\d+\.\d+$/.test(range))
      .map(([name, range]) => `${name}@${range}`);
    expect(loose).toEqual([]);
  });
});
