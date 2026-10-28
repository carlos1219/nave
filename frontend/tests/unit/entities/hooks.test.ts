import { nextTick, ref } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFindings } from "@/entities/finding";
import { useOrganizations } from "@/entities/organization";
import { useRepositories } from "@/entities/repository";
import { useScanLogs, useScanPolling, useScans } from "@/entities/scan";

import { withApi } from "./fake-api";

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
type LogsFn = (id: string, query: unknown, options?: unknown) => Promise<{ items: unknown[]; next_cursor: null }>;
const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
  await nextTick();
};

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useScanPolling", () => {
  it("consulta hasta un estado terminal y entonces para", async () => {
    const statuses = ["QUEUED", "SCANNING", "COMPLETED"];
    let i = 0;
    const scan = vi.fn(() => Promise.resolve({ id: ID, status: statuses[Math.min(i++, 2)] }));
    const { result, scope } = withApi({ scan }, () => useScanPolling(() => ID));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(result.scan.value?.status).toBe("COMPLETED");
    const calls = scan.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(scan.mock.calls.length).toBe(calls);
    scope.stop();
  });

  it("al destruir el scope deja de consultar", async () => {
    const scan = vi.fn(() => Promise.resolve({ id: ID, status: "SCANNING" }));
    const { scope } = withApi({ scan }, () => useScanPolling(() => ID));
    await vi.advanceTimersByTimeAsync(1000);
    scope.stop();
    const calls = scan.mock.calls.length;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(scan.mock.calls.length).toBe(calls);
  });
});

describe("useScanLogs", () => {
  it("pide solo lo posterior a la última secuencia y descarta repetidos", async () => {
    const terminal = ref(false);
    const batches = [
      [{ seq: 1, message: "a" }, { seq: 2, message: "b" }],
      [{ seq: 2, message: "b" }, { seq: 3, message: "c" }],
    ];
    let i = 0;
    const scanLogs = vi.fn<LogsFn>(() =>
      Promise.resolve({ items: batches[Math.min(i++, 1)] ?? [], next_cursor: null }),
    );
    const { result, scope } = withApi({ scanLogs }, () => useScanLogs(() => ID, terminal));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(result.lines.value.map((l) => l.seq)).toEqual([1, 2, 3]);
    expect(scanLogs.mock.calls[1]?.[1]).toMatchObject({ afterSeq: 2 });
    scope.stop();
  });

  it("acota el tamaño de cada mensaje al guardarlo", async () => {
    const terminal = ref(true);
    const scanLogs = vi.fn<LogsFn>(() => Promise.resolve({ items: [{ seq: 1, message: "y".repeat(100_000) }], next_cursor: null }));
    const { result, scope } = withApi({ scanLogs }, () => useScanLogs(() => ID, terminal));
    await vi.advanceTimersByTimeAsync(5000);
    expect(result.lines.value[0]?.message.length).toBe(2000);
    scope.stop();
  });

  it("acota lo guardado", async () => {
    const terminal = ref(true);
    const big = Array.from({ length: 200 }, (_, k) => ({ seq: k + 1, message: "x" }));
    let from = 0;
    const scanLogs = vi.fn<LogsFn>(() => {
      const items = from < 6000 ? big.map((e) => ({ ...e, seq: e.seq + from })) : [];
      from += 200;
      return Promise.resolve({ items, next_cursor: null });
    });
    const { result, scope } = withApi({ scanLogs }, () => useScanLogs(() => ID, terminal));
    await vi.advanceTimersByTimeAsync(300_000);
    expect(result.lines.value.length).toBeLessThanOrEqual(5000);
    scope.stop();
  });
});

describe("listados", () => {
  it("useScans pagina con cursor", async () => {
    const scans = vi
      .fn()
      .mockResolvedValueOnce({ items: [{ id: "1" }], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [{ id: "2" }], next_cursor: null });
    const { result, scope } = withApi({ scans }, () => useScans(() => ID));
    await result.reset();
    expect(result.scans.value).toHaveLength(1);
    await result.loadMore();
    expect(result.scans.value).toHaveLength(2);
    expect(scans.mock.calls[1]?.[1]).toMatchObject({ cursor: "c1" });
    expect(result.hasMore.value).toBe(false);
    scope.stop();
  });

  it("useFindings reinicia al cambiar el filtro y reenvía el filtro al servidor", async () => {
    const findings = vi.fn().mockResolvedValue({ items: [{ id: "f" }], next_cursor: null });
    const filter = ref({});
    const { result, scope } = withApi({ findings }, () => useFindings(() => ID, filter));
    await flush();
    expect(findings).toHaveBeenCalledTimes(1);
    filter.value = { severity: ["HIGH"] };
    await flush();
    expect(findings).toHaveBeenCalledTimes(2);
    expect(findings.mock.calls[1]?.[1]).toMatchObject({ severity: ["HIGH"], limit: 100 });
    expect(result.findings.value).toHaveLength(1);
    scope.stop();
  });

  it("useOrganizations y useRepositories cargan bajo demanda", async () => {
    const organizations = vi.fn().mockResolvedValue([{ id: ID, name: "o" }]);
    const repositories = vi.fn().mockResolvedValue([{ id: ID }]);
    const { result, scope } = withApi({ organizations, repositories }, () => ({
      o: useOrganizations(),
      r: useRepositories(() => ID),
    }));
    await result.o.reload();
    expect(result.o.organizations.value).toHaveLength(1);
    await result.r.reload();
    expect(result.r.repositories.value).toHaveLength(1);
    scope.stop();
  });
});
