import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { FindingsFilterBar, emptyFilter } from "@/features/findings-filter";

const props = { filter: emptyFilter(), scanners: ["semgrep"], active: false };

describe("FindingsFilterBar", () => {
  it("emite eventos y no usa HTML dinámico", async () => {
    const w = mount(FindingsFilterBar, { props });
    const boxes = w.findAll("input[type=checkbox]");
    await boxes[0]?.trigger("change");
    expect(w.emitted("severity")?.[0]).toEqual(["CRITICAL"]);
    expect(w.find("button").attributes("disabled")).toBeDefined();
  });

  it("muestra un escáner hostil como texto, sin interpretarlo", () => {
    const w = mount(FindingsFilterBar, { props: { ...props, scanners: ["<img src=x onerror=alert(1)>"], active: true } });
    expect(w.find("img").exists()).toBe(false);
    expect(w.text()).toContain("<img src=x");
  });
});
