import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { DataTable, useTableSort } from "@/shared/ui";
import type { Column } from "@/shared/ui";

interface Row {
  id: string;
  rule: string;
  line: number;
}

const COLUMNS: Column<Row>[] = [
  { key: "rule", header: "Regla", sortable: true },
  { key: "line", header: "Línea", numeric: true, sortable: true },
  { key: "id", header: "Id" },
];
const ROWS: Row[] = [
  { id: "a", rule: "sql-injection", line: 12 },
  { id: "b", rule: "xss", line: 7 },
];

function make(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  return mount(DataTable<Row>, {
    props: { columns: COLUMNS, rows: ROWS, rowKey: (r: Row) => r.id, caption: "Hallazgos", ...props },
    slots,
  });
}

describe("DataTable", () => {
  it("usa semántica de tabla real con título y cabeceras de columna", () => {
    const w = make();
    expect(w.find("caption").text()).toBe("Hallazgos");
    const heads = w.findAll("thead th");
    expect(heads).toHaveLength(3);
    expect(heads.every((h) => h.attributes("scope") === "col")).toBe(true);
    expect(w.findAll("tbody tr")).toHaveLength(2);
  });

  it("anuncia el orden con aria-sort solo en las columnas ordenables", () => {
    const w = make({ sort: { key: "line", direction: "desc" } });
    const heads = w.findAll("thead th");
    expect(heads.map((h) => h.attributes("aria-sort"))).toEqual(["none", "descending", undefined]);
    expect(heads[1]?.text()).toContain("DESC");
  });

  it("pide ordenar con un botón accesible por teclado", async () => {
    const w = make();
    const buttons = w.findAll("thead button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]?.attributes("type")).toBe("button");
    await buttons[1]?.trigger("click");
    expect(w.emitted("sort")).toEqual([["line"]]);
  });

  it("muestra el texto no confiable como texto, nunca como HTML", () => {
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const w = make({ rows: [{ id: "x", rule: hostile, line: 1 }] });
    expect(w.find("img").exists()).toBe(false);
    expect(w.find("script").exists()).toBe(false);
    expect(w.find("tbody td").text()).toBe(hostile);
  });

  it("hace visibles los caracteres bidi y de control de las celdas", () => {
    const w = make({ rows: [{ id: "x", rule: `ok${String.fromCharCode(0x202e)}`, line: 1 }] });
    expect(w.find("tbody td").text()).toBe("ok<U+202E>");
  });

  it("deja que una columna pinte su celda con un slot", () => {
    const w = make({}, { "cell-rule": '<template #cell-rule="{ row }"><b class="custom">{{ row.rule }}!</b></template>' });
    expect(w.findAll(".custom").map((n) => n.text())).toEqual(["sql-injection!", "xss!"]);
  });

  it("alinea y pone en monoespaciada los datos numéricos", () => {
    const w = make();
    const cell = w.findAll("tbody tr")[0]?.findAll("td")[1];
    expect(cell?.classes()).toContain("data-table__cell--numeric");
    expect(cell?.classes()).toContain("data-table__cell--mono");
  });

  it("indica carga y ausencia de resultados con una fila de estado", () => {
    const loading = make({ rows: [], loading: true });
    expect(loading.attributes("aria-busy")).toBe("true");
    expect(loading.find(".data-table__status").text()).toBe("Cargando…");
    const empty = make({ rows: [], emptyText: "Nada que mostrar" });
    expect(empty.find(".data-table__status").text()).toBe("Nada que mostrar");
    expect(empty.find(".data-table__status").attributes("colspan")).toBe("3");
  });

  it("marca la severidad de la fila con un atributo, no con estilos en línea", () => {
    const w = make({ tone: (r: Row) => (r.id === "a" ? "critical" : undefined) });
    const rows = w.findAll("tbody tr");
    expect(rows[0]?.attributes("data-tone")).toBe("critical");
    expect(rows[1]?.attributes("data-tone")).toBeUndefined();
    expect(w.html()).not.toContain("style=");
  });

  it("ignora campos que no son texto ni número", () => {
    const w = make({ rows: [{ id: "x", rule: { toString: () => "<b>" }, line: 1 }] });
    expect(w.find("tbody td").text()).toBe("");
  });
});

describe("useTableSort", () => {
  it("alterna ascendente y descendente y reinicia al cambiar de columna", () => {
    const { sort, toggle } = useTableSort();
    expect(sort.value).toBeNull();
    toggle("a");
    expect(sort.value).toEqual({ key: "a", direction: "asc" });
    toggle("a");
    expect(sort.value).toEqual({ key: "a", direction: "desc" });
    toggle("b");
    expect(sort.value).toEqual({ key: "b", direction: "asc" });
  });
});
