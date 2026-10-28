import { flushPromises, mount } from "@vue/test-utils";
import type { createApp } from "vue";
import { describe, expect, it, vi } from "vitest";

import { ApiError, installApi } from "@/shared/api";
import type { Api } from "@/shared/api";
import { newIdempotencyKey, useAction } from "@/shared/lib";
import { CancelScanButton } from "@/features/cancel-scan";
import { MembersPanel } from "@/features/manage-members";
import { StartScanForm, validateCommit, validateRef } from "@/features/start-scan";
import { TriageForm, buildTriageRequest } from "@/features/triage-finding";
import { ErrorNotice } from "@/shared/ui";


const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SHA = "a".repeat(40);
const apiError = (status: number) => new ApiError({ status, type: "urn:nave:problem:x", title: "Fijo", requestId: "r1" });

function mountWith(component: unknown, fake: Partial<Record<keyof Api, unknown>>, props: Record<string, unknown>) {
  return mount(component as never, {
    props: props as never,
    global: { plugins: [(app: ReturnType<typeof createApp>) => {
          installApi(app, fake as unknown as Api);
        }] },
  });
}

describe("newIdempotencyKey", () => {
  it("son 128 bits en hexadecimal, distintos y válidos para el cliente", () => {
    const keys = new Set(Array.from({ length: 50 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(50);
    for (const key of keys) expect(key).toMatch(/^[0-9a-f]{32}$/);
  });

  it("no usa randomUUID (que exige contexto seguro)", () => {
    const spy = vi.spyOn(crypto, "randomUUID");
    newIdempotencyKey();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("useAction", () => {
  it("no duplica envíos en curso y guarda el error", async () => {
    let release!: () => void;
    const perform = vi.fn(() => new Promise<number>((r) => {
      release = () => {
        r(1);
      };
    }));
    const a = useAction(perform);
    const first = a.run();
    expect(await a.run()).toBeUndefined();
    release();
    expect(await first).toBe(1);
    expect(perform).toHaveBeenCalledTimes(1);
    const b = useAction(() => Promise.reject(apiError(403)));
    await b.run();
    expect(b.error.value?.status).toBe(403);
    expect(b.pending.value).toBe(false);
  });
});

describe("validación de escaneo", () => {
  it("commit y ref", () => {
    expect(validateCommit(SHA)).toBeNull();
    for (const bad of ["abc", "g".repeat(40), SHA + "0", ""]) expect(validateCommit(bad)).not.toBeNull();
    for (const ok of ["main", "feature/x-1", "v1.2.3"]) expect(validateRef(ok)).toBeNull();
    for (const bad of ["", "-x", "a..b", "a//b", "a/", "a.lock", "a b", "a;rm", "a/.b", "x".repeat(201)]) {
      expect(validateRef(bad)).not.toBeNull();
    }
  });
});

describe("StartScanForm", () => {
  it("no llama a la API con datos inválidos", async () => {
    const startScan = vi.fn();
    const w = mountWith(StartScanForm, { startScan }, { repositoryId: ID });
    await w.find("form").trigger("submit");
    expect(startScan).not.toHaveBeenCalled();
    expect(w.text()).toContain("SHA-1");
  });

  it("reutiliza la clave al reintentar con los mismos datos y la cambia si cambian", async () => {
    const startScan = vi
      .fn()
      .mockRejectedValueOnce(apiError(503))
      .mockResolvedValueOnce({ created: true, scan: { scan_id: ID, created: true } });
    const w = mountWith(StartScanForm, { startScan }, { repositoryId: ID });
    const [ref, commit] = w.findAll("input");
    await ref?.setValue("main");
    await commit?.setValue(SHA.toUpperCase());
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(w.find("[role=alert]").exists()).toBe(true);
    await w.find("form").trigger("submit");
    await flushPromises();
    const keys = startScan.mock.calls.map((c) => c[2] as string);
    expect(keys[0]).toBe(keys[1]);
    expect(startScan.mock.calls[0]?.[1]).toEqual({ ref: "main", commit: SHA });
    expect(w.emitted("started")?.[0]).toEqual([ID]);
  });
});

describe("CancelScanButton", () => {
  it("solo aparece si el estado es cancelable", async () => {
    const cancelScan = vi.fn().mockResolvedValue({ cancelled: true });
    expect(mountWith(CancelScanButton, { cancelScan }, { scanId: ID, status: "COMPLETED" }).find("button").exists()).toBe(false);
    const w = mountWith(CancelScanButton, { cancelScan }, { scanId: ID, status: "SCANNING" });
    await w.find("button").trigger("click");
    await flushPromises();
    expect(cancelScan).toHaveBeenCalledWith(ID);
    expect(w.emitted("cancelled")).toHaveLength(1);
  });
});

describe("triage", () => {
  const now = new Date("2026-01-01T00:00:00Z");
  it("exige justificación y caducidad futura solo donde toca", () => {
    expect(buildTriageRequest({ state: "FALSE_POSITIVE", justification: "corto", expiresLocal: "" }, now).ok).toBe(false);
    expect(buildTriageRequest({ state: "FIXED", justification: "", expiresLocal: "" }, now)).toEqual({
      ok: true,
      request: { state: "FIXED", justification: null, expires_at: null },
    });
    const risk = { state: "ACCEPTED_RISK" as const, justification: "riesgo asumido por X" };
    expect(buildTriageRequest({ ...risk, expiresLocal: "" }, now).ok).toBe(false);
    expect(buildTriageRequest({ ...risk, expiresLocal: "2025-01-01T00:00" }, now).ok).toBe(false);
    const ok = buildTriageRequest({ ...risk, expiresLocal: "2027-01-01T00:00" }, now);
    expect(ok.ok && ok.request.expires_at).toMatch(/Z$/);
  });

  it("el formulario no envía si no valida y envía si valida", async () => {
    const triage = vi.fn().mockResolvedValue({ decision_id: ID, state: "FALSE_POSITIVE" });
    const w = mountWith(TriageForm, { triage }, { findingId: ID });
    await w.find("form").trigger("submit");
    expect(triage).not.toHaveBeenCalled();
    await w.find("textarea").setValue("no es explotable aquí");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(triage).toHaveBeenCalledWith(ID, expect.objectContaining({ state: "FALSE_POSITIVE" }));
    expect(w.emitted("done")).toHaveLength(1);
  });
});

describe("MembersPanel", () => {
  it("lista con nombre visible, cambia rol y retira tras confirmar", async () => {
    const members = vi.fn().mockResolvedValue([
      { user_id: ID, display_name: "Ana‮", email: null, role: "VIEWER" },
    ]);
    const setMemberRole = vi.fn().mockResolvedValue(undefined);
    const removeMember = vi.fn().mockResolvedValue(undefined);
    const w = mountWith(MembersPanel, { members, setMemberRole, removeMember }, { organizationId: ID, currentUserId: null });
    await flushPromises();
    expect(w.text()).not.toContain("‮");
    const select = w.find("select");
    await select.setValue("ADMIN");
    await flushPromises();
    expect(setMemberRole).toHaveBeenCalledWith(ID, ID, "ADMIN");
    await w.find("tbody button").trigger("click");
    expect(removeMember).not.toHaveBeenCalled();
    const confirm = w.findAll("dialog button").at(-1);
    await confirm?.trigger("click");
    await flushPromises();
    expect(removeMember).toHaveBeenCalledWith(ID, ID);
  });
});

describe("ErrorNotice", () => {
  it("muestra texto fijo y referencia sin interpretarlo", () => {
    const w = mount(ErrorNotice, { props: { error: apiError(403) } });
    expect(w.text()).toContain("permiso");
    expect(w.text()).toContain("r1");
  });
});

