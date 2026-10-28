export type SortDirection = "asc" | "desc";

export interface SortState {
  key: string;
  direction: SortDirection;
}

export type Tone = "critical" | "high" | "medium" | "low" | "info";

export interface Column<Row> {
  key: string;
  header: string;
  sortable?: boolean;
  /** Datos tabulares: monoespaciada y alineada a la derecha. */
  numeric?: boolean;
  mono?: boolean;
  /** Valor en texto de la celda; por defecto, `row[key]`. */
  text?: (row: Row) => string;
}
