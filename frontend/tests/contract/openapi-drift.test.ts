// @vitest-environment node
// El cliente se tipa con `schema.d.ts`, generado desde el contrato versionado del backend. Si
// el contrato cambia sin regenerar los tipos (`npm run api:types`), esto falla.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import openapiTS, { astToString } from "openapi-typescript";
import { describe, expect, it } from "vitest";

describe("contrato OpenAPI", () => {
  it("schema.d.ts coincide con contracts/openapi.json", async () => {
    const contract = resolve(process.cwd(), "../contracts/openapi.json");
    const generated = astToString(await openapiTS(new URL(`file://${contract}`)));
    const file = readFileSync(resolve(process.cwd(), "src/shared/api/schema.d.ts"), "utf8");
    // La CLI antepone un aviso de fichero generado que la API programática no incluye.
    const committed = file.replace(/^\/\*\*[\s\S]*?\*\/\s*/, "");
    expect(committed).toBe(generated);
  });
});
