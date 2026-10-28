// Reglas de arquitectura y de seguridad del frontend (ADR 0021).
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

const SRC = join(fileURLToPath(new URL(".", import.meta.url)), "src");
// Capas de Feature-Sliced Design, de la más alta a la más baja.
const LAYERS = ["app", "pages", "widgets", "features", "entities", "shared"];

const slicesOf = (layer) => {
  try {
    return readdirSync(join(SRC, layer)).filter((n) => statSync(join(SRC, layer, n)).isDirectory());
  } catch {
    return [];
  }
};

const DEEP =
  "Importa por la API pública del slice (index.ts), no por sus ficheros internos (ADR 0021).";

// Una capa solo importa de las inferiores; un slice no importa de otro slice de su capa.
const layerRules = LAYERS.flatMap((layer, index) => {
  const forbiddenLayers = LAYERS.slice(0, index);
  const slices = layer === "shared" ? [] : slicesOf(layer);
  const configs = [];
  const base = [
    ...forbiddenLayers.map((l) => ({
      group: [`@/${l}`, `@/${l}/**`],
      message: `La capa ${layer} no puede importar de la capa superior ${l}.`,
    })),
    { group: ["@/*/*/**"], message: DEEP },
  ];
  if (layer === "app" || layer === "shared" || slices.length === 0) {
    configs.push({
      files: [`src/${layer}/**/*.{ts,vue}`],
      rules: { "no-restricted-imports": ["error", { patterns: base }] },
    });
  }
  for (const slice of slices) {
    const siblings = slices.filter((s) => s !== slice);
    configs.push({
      files: [`src/${layer}/${slice}/**/*.{ts,vue}`],
      rules: {
        "no-restricted-imports": [
          "error",
          {
            patterns: [
              ...base,
              ...siblings.map((s) => ({
                group: [`@/${layer}/${s}`, `@/${layer}/${s}/**`],
                message: `El slice ${layer}/${slice} no puede importar de ${layer}/${s} (misma capa).`,
              })),
            ],
          },
        ],
      },
    });
  }
  return configs;
});

const HTML_SINKS = [
  {
    selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
    message: "Prohibido asignar HTML: los findings son texto de un atacante (AM-701).",
  },
  {
    selector: "CallExpression[callee.property.name=/^(insertAdjacentHTML|write|writeln)$/]",
    message: "Prohibido escribir HTML en el DOM (AM-701).",
  },
  {
    selector: "CallExpression[callee.property.name='createContextualFragment']",
    message: "Prohibido construir fragmentos desde texto (AM-701).",
  },
  {
    selector: "MemberExpression[property.name=/^(localStorage|sessionStorage)$/]",
    message: "Ni sesión ni CSRF en almacenamiento del navegador (AM-705).",
  },
  {
    selector: "Identifier[name=/^(localStorage|sessionStorage)$/]",
    message: "Ni sesión ni CSRF en almacenamiento del navegador (AM-705).",
  },
];

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "src/shared/api/schema.d.ts"] },
  ...tseslint.configs.strictTypeChecked,
  ...vue.configs["flat/recommended"],
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        extraFileExtensions: [".vue"],
        parser: tseslint.parser,
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "vue/no-v-html": "error",
      // `Modal`, `DataTable`… son nombres de la API pública de shared/ui (ADR 0021).
      "vue/multi-word-component-names": ["error", { ignores: ["Modal", "Panel"] }],
      // Formato de plantilla: lo decide el equipo al leer, no un linter.
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/block-lang": ["error", { script: { lang: "ts" } }],
      "vue/component-name-in-template-casing": ["error", "PascalCase"],
      "no-restricted-syntax": ["error", ...HTML_SINKS],
      "no-console": "error",
    },
  },
  {
    files: ["**/*.vue"],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
  },
  ...layerRules,
  {
    // Los tests y la configuración leen el código fuente: son los únicos que tocan el sistema de ficheros.
    files: ["tests/**/*.ts", "*.config.{js,ts}"],
    rules: { "no-restricted-syntax": "off", "no-console": "off" },
  },
  { files: ["**/*.js"], ...tseslint.configs.disableTypeChecked },
);
