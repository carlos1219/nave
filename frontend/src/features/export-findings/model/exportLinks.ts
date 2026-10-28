import type { Api, ExportFilter, ExportFormat, ExportView } from "@/shared/api";

export type ExportTarget =
  | { kind: "organization"; organizationId: string; view: ExportView; filter?: ExportFilter }
  | { kind: "repository"; repositoryId: string; filter?: ExportFilter };

export interface ExportLink {
  format: ExportFormat;
  label: string;
  href: string;
}

const FORMATS: readonly { format: ExportFormat; label: string }[] = [
  { format: "csv", label: "CSV" },
  { format: "sarif", label: "SARIF" },
];

/** Enlaces de descarga; si los identificadores no son válidos no se ofrece ninguno. */
export function exportLinks(api: Pick<Api, "organizationExportUrl" | "repositoryExportUrl">, target: ExportTarget): ExportLink[] {
  try {
    return FORMATS.map(({ format, label }) => ({
      format,
      label,
      href:
        target.kind === "organization"
          ? api.organizationExportUrl(target.organizationId, target.view, format, target.filter)
          : api.repositoryExportUrl(target.repositoryId, format, target.filter),
    }));
  } catch {
    return [];
  }
}
