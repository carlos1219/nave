import { nextTick, ref, useTemplateRef, watch } from "vue";
import type { Ref } from "vue";

/** Mantiene el final visible mientras llegan líneas, salvo que la persona haya subido a leer. */
export function useFollowTail(lineCount: Ref<number>, refName: string) {
  const container = useTemplateRef<HTMLElement>(refName);
  const following = ref(true);

  function onScroll(): void {
    const el = container.value;
    if (el === null) return;
    following.value = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
  }

  watch(lineCount, async () => {
    if (!following.value) return;
    await nextTick();
    const el = container.value;
    if (el !== null) el.scrollTop = el.scrollHeight;
  });

  return { following, onScroll };
}
