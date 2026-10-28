<script setup lang="ts">
import { onMounted, useTemplateRef, watch } from "vue";

const props = defineProps<{ open: boolean; title: string }>();
const emit = defineEmits<{ close: [] }>();

const dialog = useTemplateRef<HTMLDialogElement>("dialog");

function sync(): void {
  const element = dialog.value;
  if (element === null) return;
  if (props.open && !element.open) {
    // `showModal` da trampa de foco, `Escape` y foco de vuelta al cerrar sin código propio.
    if (typeof element.showModal === "function") element.showModal();
    else element.setAttribute("open", "");
  } else if (!props.open && element.open) {
    if (typeof element.close === "function") element.close();
    else element.removeAttribute("open");
  }
}

onMounted(sync);
watch(() => props.open, sync);
</script>

<template>
  <dialog ref="dialog" class="modal" aria-labelledby="modal-title" @close="emit('close')" @cancel="emit('close')">
    <header class="modal__header">
      <h2 id="modal-title" class="modal__title">{{ title }}</h2>
      <button type="button" class="modal__close" @click="emit('close')">Cerrar</button>
    </header>
    <div class="modal__body">
      <slot />
    </div>
    <footer v-if="$slots['footer']" class="modal__footer">
      <slot name="footer" />
    </footer>
  </dialog>
</template>

<style scoped>
.modal {
  width: min(520px, calc(100vw - 32px));
  overflow: hidden;
  padding: 0;
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-md);
  color: var(--text);
}

.modal::backdrop {
  background: var(--backdrop);
}

.modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border-bottom: var(--border);
}

.modal__title {
  font-size: var(--fs-lg);
}

.modal__close {
  height: 28px;
  padding: 0 var(--space-3);
  border-color: transparent;
  background: transparent;
  color: var(--text-2);
}

.modal__body {
  padding: var(--space-5);
  color: var(--text-2);
}

.modal__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-top: var(--border);
  background: var(--surface-2);
}
</style>
