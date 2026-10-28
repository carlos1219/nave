import { getCurrentScope, onScopeDispose, ref } from "vue";
import type { Ref } from "vue";

export const NARROW_QUERY = "(max-width: 899px)";

// Preferencia de la visita, en memoria: el almacenamiento del navegador está vetado (AM-705).
const preferred = ref(false);

/**
 * Menú contraído o desplegado (ADR 0029). En pantallas estrechas va siempre contraído y el
 * botón no se muestra: no hay sitio para el texto.
 */
export function useSidebarState(): { collapsed: Ref<boolean>; narrow: Ref<boolean>; toggle: () => void } {
  const narrow = ref(false);
  const collapsed = ref(preferred.value);
  const media = typeof window.matchMedia === "function" ? window.matchMedia(NARROW_QUERY) : null;

  const sync = (): void => {
    narrow.value = media?.matches === true;
    collapsed.value = narrow.value || preferred.value;
  };
  sync();
  media?.addEventListener("change", sync);
  if (getCurrentScope() !== undefined) onScopeDispose(() => media?.removeEventListener("change", sync));

  function toggle(): void {
    if (narrow.value) return;
    preferred.value = !preferred.value;
    sync();
  }

  return { collapsed, narrow, toggle };
}
