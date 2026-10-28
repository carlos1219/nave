import { effectScope } from "vue";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api";
import { usePagedResource, useResource } from "@/shared/lib";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const apiError = (status: number) => new ApiError({ status, type: "urn:nave:problem:x", title: "t" });

describe("useResource", () => {
  it("carga, y marca el estado de carga", async () => {
    const d = deferred<number>();
    const r = useResource(() => d.promise);
    const pending = r.reload();
    expect(r.loading.value).toBe(true);
    d.resolve(7);
    await pending;
    expect([r.data.value, r.loading.value, r.error.value]).toEqual([7, false, null]);
  });

  it("guarda el ApiError y conserva el dato anterior", async () => {
    let n = 0;
    const r = useResource(() => (n++ === 0 ? Promise.resolve("a") : Promise.reject(apiError(503))));
    await r.reload();
    await r.reload();
    expect(r.data.value).toBe("a");
    expect(r.error.value?.status).toBe(503);
  });

  it("una petición nueva cancela la anterior y la anterior nunca pisa el resultado", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const signals: AbortSignal[] = [];
    let n = 0;
    const r = useResource((signal) => {
      signals.push(signal);
      return n++ === 0 ? first.promise : second.promise;
    });
    const p1 = r.reload();
    const p2 = r.reload();
    expect(signals[0]?.aborted).toBe(true);
    second.resolve("nuevo");
    first.resolve("viejo");
    await Promise.all([p1, p2]);
    expect(r.data.value).toBe("nuevo");
  });

  it("convierte errores de programación en un error genérico sin su texto", async () => {
    const r = useResource(() => Promise.reject(new Error("secreto interno /etc/passwd")));
    await r.reload();
    expect(r.error.value?.type).toBe("urn:nave:client:unexpected");
    expect(r.error.value?.message).not.toContain("passwd");
  });

  it("ignora la cancelación propia", async () => {
    const r = useResource(
      (signal) =>
        new Promise<never>((_ok, fail) => {
          signal.addEventListener("abort", () => {
            fail(new DOMException("a", "AbortError"));
          });
        }),
    );
    const pending = r.reload();
    r.cancel();
    await pending;
    expect(r.error.value).toBeNull();
    expect(r.loading.value).toBe(false);
  });

  it("cancela al destruir el ámbito", () => {
    const scope = effectScope();
    let signal: AbortSignal | undefined;
    scope.run(() => {
      const r = useResource((s) => {
        signal = s;
        return new Promise<never>(() => undefined);
      });
      void r.reload();
    });
    scope.stop();
    expect(signal?.aborted).toBe(true);
  });
});

describe("usePagedResource", () => {
  const pages = (sizes: number[]) => {
    const calls: (string | undefined)[] = [];
    const fetchPage = (cursor: string | undefined) => {
      calls.push(cursor);
      const index = cursor === undefined ? 0 : Number(cursor);
      const size = sizes[index] ?? 0;
      return Promise.resolve({
        items: Array.from({ length: size }, (_, i) => index * 100 + i),
        next_cursor: index + 1 < sizes.length ? String(index + 1) : null,
      });
    };
    return { calls, fetchPage };
  };

  it("acumula páginas siguiendo el cursor del servidor", async () => {
    const { calls, fetchPage } = pages([2, 2, 1]);
    const r = usePagedResource(fetchPage);
    await r.reset();
    expect(r.items.value).toEqual([0, 1]);
    expect(r.hasMore.value).toBe(true);
    await r.loadMore();
    await r.loadMore();
    expect(r.items.value).toEqual([0, 1, 100, 101, 200]);
    expect(r.hasMore.value).toBe(false);
    expect(calls).toEqual([undefined, "1", "2"]);
  });

  it("no pide más cuando no queda nada ni dos veces a la vez", async () => {
    const { calls, fetchPage } = pages([1, 1]);
    const r = usePagedResource(fetchPage);
    await r.reset();
    await Promise.all([r.loadMore(), r.loadMore()]);
    await r.loadMore();
    expect(calls).toEqual([undefined, "1"]);
  });

  it("acota la memoria: corta en el tope y lo indica", async () => {
    const { fetchPage } = pages([3, 3, 3]);
    const r = usePagedResource(fetchPage, { maxItems: 5 });
    await r.reset();
    await r.loadMore();
    expect(r.items.value).toHaveLength(5);
    expect(r.capped.value).toBe(true);
    expect(r.hasMore.value).toBe(false);
  });

  it("no marca el tope si cabe justo y no hay más", async () => {
    const { fetchPage } = pages([3]);
    const r = usePagedResource(fetchPage, { maxItems: 3 });
    await r.reset();
    expect(r.capped.value).toBe(false);
  });

  it("reset descarta lo acumulado y una respuesta tardía de antes no lo pisa", async () => {
    const slow = deferred<{ items: number[]; next_cursor: string | null }>();
    let n = 0;
    const r = usePagedResource<number>(() =>
      n++ === 0 ? slow.promise : Promise.resolve({ items: [9], next_cursor: null }),
    );
    const first = r.reset();
    await r.reset();
    slow.resolve({ items: [1, 2, 3], next_cursor: null });
    await first;
    expect(r.items.value).toEqual([9]);
  });

  it("un fallo al cargar más conserva lo ya visto y permite reintentar", async () => {
    let n = 0;
    const r = usePagedResource<number>(() =>
      ++n === 2 ? Promise.reject(apiError(503)) : Promise.resolve({ items: [n], next_cursor: "x" }),
    );
    await r.reset();
    await r.loadMore();
    expect(r.items.value).toEqual([1]);
    expect(r.error.value?.status).toBe(503);
    expect(r.hasMore.value).toBe(true);
    await r.loadMore();
    expect(r.items.value).toEqual([1, 3]);
    expect(r.error.value).toBeNull();
  });
});
