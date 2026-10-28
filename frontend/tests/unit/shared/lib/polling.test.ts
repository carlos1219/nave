import { effectScope } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api";
import { usePolling } from "@/shared/lib";

const apiError = (status: number, retryAfter: number | null = null) =>
  new ApiError({ status, type: "urn:nave:problem:x", title: "t", retryAfter });

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});
afterEach(() => {
  vi.useRealTimers();
});

function make(results: (number | Error)[], opts: Partial<Parameters<typeof usePolling<number>>[0]> = {}) {
  const calls: number[] = [];
  let i = 0;
  const seen: number[] = [];
  const poller = usePolling<number>({
    tick: () => {
      calls.push(Date.now());
      const next = results[Math.min(i++, results.length - 1)];
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next ?? 0);
    },
    onResult: (value) => {
      const changed = seen.at(-1) !== value;
      seen.push(value);
      return changed;
    },
    isDone: (value) => value >= 100,
    baseDelayMs: 1000,
    maxDelayMs: 8000,
    ...opts,
  });
  return { poller, calls, seen };
}

const gaps = (calls: number[]) => calls.slice(1).map((t, i) => t - (calls[i] ?? 0));

describe("usePolling", () => {
  it("consulta enseguida y aplica backoff progresivo con tope mientras nada cambia", async () => {
    const { poller, calls } = make([1]);
    poller.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls.length).toBeGreaterThan(4);
    const g = gaps(calls);
    // El primer resultado cuenta como cambio (no había nada antes).
    expect(g.slice(0, 4)).toEqual([1000, 1600, 2560, 4096]);
    expect(Math.max(...g)).toBe(8000);
    poller.stop();
  });

  it("reinicia el backoff cuando algo cambia", async () => {
    const { poller, calls } = make([1, 1, 1, 2, 2]);
    poller.start();
    await vi.advanceTimersByTimeAsync(30_000);
    const g = gaps(calls);
    expect(g.slice(0, 4)).toEqual([1000, 1600, 2560, 1000]);
    poller.stop();
  });

  it("se detiene al llegar a un estado terminal", async () => {
    const { poller, calls } = make([1, 100]);
    poller.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(2);
    expect(poller.state.value).toBe("done");
  });

  it("stop cancela la petición en vuelo y el temporizador", async () => {
    let signal: AbortSignal | undefined;
    const poller = usePolling<number>({
      tick: (s) => {
        signal = s;
        return new Promise<number>(() => undefined);
      },
      onResult: () => true,
      isDone: () => false,
    });
    poller.start();
    poller.stop();
    expect(signal?.aborted).toBe(true);
    expect(poller.state.value).toBe("idle");
    await vi.advanceTimersByTimeAsync(60_000);
  });

  it("start repetido no duplica el sondeo", async () => {
    const { poller, calls } = make([1]);
    poller.start();
    poller.start();
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(1);
    poller.stop();
  });

  it("limpia al destruir el ámbito", async () => {
    const scope = effectScope();
    const { calls } = scope.run(() => {
      const made = make([1]);
      made.poller.start();
      return made;
    }) ?? { calls: [] };
    await vi.advanceTimersByTimeAsync(0);
    scope.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(1);
  });
});

describe("pausa con la pestaña oculta", () => {
  it("no consulta oculta y reanuda al volver", async () => {
    const { poller, calls } = make([1]);
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    setHidden(true);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(poller.state.value).toBe("paused");
    const while_hidden = calls.length;
    expect(while_hidden).toBeLessThanOrEqual(2);
    setHidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.length).toBe(while_hidden + 1);
    expect(poller.state.value).toBe("polling");
    poller.stop();
  });

  it("si arranca oculta, espera a que se vea", async () => {
    setHidden(true);
    const { poller, calls } = make([1]);
    poller.start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(0);
    setHidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(1);
    poller.stop();
  });
});

describe("errores", () => {
  it("reintenta los transitorios con backoff y se recupera", async () => {
    const { poller, calls } = make([apiError(503), apiError(503), 1, 100]);
    poller.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(4);
    expect(poller.state.value).toBe("done");
    expect(poller.error.value).toBeNull();
  });

  it("se rinde tras demasiados fallos seguidos y deja reintentar a mano", async () => {
    const { poller, calls } = make([apiError(503)], { maxConsecutiveErrors: 3 });
    poller.start();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(poller.state.value).toBe("failed");
    expect(calls).toHaveLength(4);
    expect(poller.error.value?.status).toBe(503);
    poller.retry();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(5);
    poller.stop();
  });

  it.each([401, 403, 404])("no reintenta un %i", async (status) => {
    const { poller, calls } = make([apiError(status)]);
    poller.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(1);
    expect(poller.state.value).toBe("failed");
  });

  it("respeta Retry-After cuando es mayor que el backoff", async () => {
    const { poller, calls } = make([apiError(429, 12), 1]);
    poller.start();
    await vi.advanceTimersByTimeAsync(11_999);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2);
    expect(calls).toHaveLength(2);
    poller.stop();
  });

  it("un error de programación no se cuela en el estado y se trata como fallo genérico", async () => {
    const { poller } = make([new Error("secreto")], { maxConsecutiveErrors: 0 });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(poller.state.value).toBe("failed");
    expect(poller.error.value?.message).not.toContain("secreto");
  });
});
