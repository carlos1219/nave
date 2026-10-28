<script setup lang="ts">
import { useId } from "vue";

defineProps<{ title: string; flush?: boolean }>();

const headingId = useId();
</script>

<template>
  <section class="panel" :aria-labelledby="headingId">
    <header class="panel__header">
      <h2 :id="headingId" class="panel__title">{{ title }}</h2>
      <div v-if="$slots['aside']" class="panel__aside">
        <slot name="aside" />
      </div>
    </header>
    <div class="panel__body" :class="{ 'panel__body--flush': flush === true }">
      <slot />
    </div>
  </section>
</template>

<style scoped>
/* `overflow: hidden` recorta el contenido a ras (tablas, registro) a las esquinas redondeadas. */
.panel {
  min-width: 0;
  overflow: hidden;
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  min-height: 44px;
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
}

.panel__title {
  font-size: var(--fs-md);
  font-weight: 600;
}

.panel__aside {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.panel__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
}

.panel__body--flush {
  gap: 0;
  padding: 0;
}
</style>
