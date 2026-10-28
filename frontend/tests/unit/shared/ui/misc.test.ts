import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { nextTick } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

import { LogViewer, Modal, PageLayout, Panel, SeverityBadge, StatusTag } from "@/shared/ui";
import type { LogLine, SeverityLevel } from "@/shared/ui";

describe("SeverityBadge", () => {
  it.each<SeverityLevel>(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"])(
    "%s siempre lleva su etiqueta de texto, no solo color",
    (severity) => {
      const w = mount(SeverityBadge, { props: { severity } });
      expect(w.text()).toBe(severity);
      expect(w.classes()).toContain(`severity-badge--${severity.toLowerCase()}`);
    },
  );
});

describe("PageLayout", () => {
  it("pone el título en un h1 y las acciones solo si hay", () => {
    const bare = mount(PageLayout, { props: { title: "Escaneos" }, slots: { default: "<p>cuerpo</p>" } });
    expect(bare.find("h1").text()).toBe("Escaneos");
    expect(bare.find(".page-layout__actions").exists()).toBe(false);
    const withActions = mount(PageLayout, {
      props: { title: "Escaneos" },
      slots: { actions: "<button>Nuevo</button>" },
    });
    expect(withActions.find(".page-layout__actions button").text()).toBe("Nuevo");
  });

  it("escapa el título", () => {
    const w = mount(PageLayout, { props: { title: "<b>x</b>" } });
    expect(w.find("h1 b").exists()).toBe(false);
    expect(w.find("h1").text()).toBe("<b>x</b>");
  });

  it("pinta la ruta como lista navegable y la escapa", () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", name: "home", component: { render: () => null } }] });
    const w = mount(PageLayout, {
      props: { title: "t", trail: [{ label: "Inicio", to: { name: "home" } }, { label: "<i>actual</i>" }] },
      global: { plugins: [router] },
    });
    expect(w.find("nav").attributes("aria-label")).toBe("Ruta");
    const crumbs = w.findAll("li");
    expect(crumbs.map((c) => c.text())).toEqual(["Inicio", "<i>actual</i>"]);
    expect(crumbs[0]?.find("a").exists()).toBe(true);
    expect(crumbs[1]?.find("a").exists()).toBe(false);
    expect(w.find("li i").exists()).toBe(false);
  });

  it("sin ruta no pinta navegación", () => {
    expect(mount(PageLayout, { props: { title: "t" } }).find("nav").exists()).toBe(false);
  });
});

describe("Panel", () => {
  it("es una sección con nombre accesible tomado de su título", () => {
    const w = mount(Panel, { props: { title: "Escaneos" }, slots: { default: "<p>x</p>", aside: "<span>3</span>" } });
    const id = w.find("h2").attributes("id");
    expect(id).toBeTruthy();
    expect(w.find("section").attributes("aria-labelledby")).toBe(id);
    expect(w.find(".panel__aside").text()).toBe("3");
  });
});

describe("StatusTag", () => {
  it("lleva siempre el texto además del tono", () => {
    const w = mount(StatusTag, { props: { label: "Fallido", tone: "fail" } });
    expect(w.text()).toBe("Fallido");
    expect(w.classes()).toContain("status-tag--fail");
  });
});

describe("Modal", () => {
  it("se abre y se cierra con la propiedad, y avisa al cerrarse", async () => {
    const w = mount(Modal, { props: { open: false, title: "Confirmar" }, slots: { default: "<p>texto</p>" } });
    const dialog = w.find("dialog").element;
    expect(dialog.hasAttribute("open")).toBe(false);
    await w.setProps({ open: true });
    expect(dialog.hasAttribute("open")).toBe(true);
    await w.setProps({ open: false });
    expect(dialog.hasAttribute("open")).toBe(false);
  });

  it("tiene nombre accesible y un botón de cierre", async () => {
    const w = mount(Modal, { props: { open: true, title: "Confirmar" } });
    await nextTick();
    expect(w.find("dialog").attributes("aria-labelledby")).toBe("modal-title");
    expect(w.find("#modal-title").text()).toBe("Confirmar");
    await w.find(".modal__close").trigger("click");
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("Escape (evento cancel) pide cerrar", async () => {
    const w = mount(Modal, { props: { open: true, title: "t" } });
    await w.find("dialog").trigger("cancel");
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("muestra el pie solo si se da", () => {
    expect(mount(Modal, { props: { open: true, title: "t" } }).find("footer").exists()).toBe(false);
    const w = mount(Modal, { props: { open: true, title: "t" }, slots: { footer: "<button>OK</button>" } });
    expect(w.find("footer button").text()).toBe("OK");
  });
});

describe("LogViewer", () => {
  const line = (seq: number, message: string, level: LogLine["level"] = "INFO"): LogLine => ({
    seq,
    at: "2026-10-12T17:41:05Z",
    level,
    message,
  });

  it("pinta cada línea como texto plano con hora y nivel", () => {
    const w = mount(LogViewer, { props: { label: "Registro", lines: [line(1, "hola"), line(2, "mal", "ERROR")] } });
    const pres = w.findAll("pre");
    expect(pres).toHaveLength(2);
    expect(pres[0]?.text()).toBe("2026-10-12 17:41:05 INFO    hola");
    expect(pres[1]?.text()).toContain("ERROR");
    expect(pres[1]?.classes()).toContain("log-viewer__line--error");
  });

  it("es una región de registro enfocable con nombre", () => {
    const w = mount(LogViewer, { props: { label: "Registro del escaneo", lines: [] } });
    const root = w.find("[role=log]");
    expect(root.attributes("aria-label")).toBe("Registro del escaneo");
    expect(root.attributes("tabindex")).toBe("0");
    expect(w.text()).toContain("Sin registros todavía.");
  });

  it("no interpreta HTML ni secuencias de control del código escaneado", () => {
    const esc = String.fromCharCode(0x1b);
    const hostile = `<script>alert(1)</script>${esc}[31mrojo${esc}[0m${String.fromCharCode(0x202e)}`;
    const w = mount(LogViewer, { props: { label: "r", lines: [line(1, hostile)] } });
    expect(w.find("script").exists()).toBe(false);
    expect(w.find("pre").text()).toBe("2026-10-12 17:41:05 INFO    <script>alert(1)</script>rojo<U+202E>");
  });

  it("recorta los mensajes largos y las listas enormes", () => {
    const many = Array.from({ length: 5003 }, (_, i) => line(i + 1, "x"));
    const w = mount(LogViewer, { props: { label: "r", lines: many } });
    expect(w.findAll("pre")).toHaveLength(5000);
    expect(w.text()).toContain("3 líneas anteriores no se muestran");
    const long = mount(LogViewer, { props: { label: "r", lines: [line(1, "y".repeat(10_000))] } });
    expect(long.find("pre").text().length).toBeLessThan(2100);
  });
});
