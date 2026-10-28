<script setup lang="ts">
import { RouterLink } from "vue-router";

import type { Crumb } from "./types";

defineProps<{ title: string; trail?: readonly Crumb[] }>();
</script>

<template>
  <section class="page-layout">
    <header class="page-layout__header">
      <nav v-if="trail && trail.length > 0" class="page-layout__trail" aria-label="Ruta">
        <ol class="page-layout__crumbs">
          <li v-for="(crumb, index) in trail" :key="index" class="page-layout__crumb">
            <RouterLink v-if="crumb.to" class="page-layout__crumb-link" :to="crumb.to">{{ crumb.label }}</RouterLink>
            <span v-else class="page-layout__crumb-text">{{ crumb.label }}</span>
          </li>
        </ol>
      </nav>
      <div class="page-layout__heading">
        <div class="page-layout__titles">
          <h1 class="page-layout__title">{{ title }}</h1>
          <div v-if="$slots['meta']" class="page-layout__meta">
            <slot name="meta" />
          </div>
        </div>
        <div v-if="$slots['actions']" class="page-layout__actions">
          <slot name="actions" />
        </div>
      </div>
    </header>
    <div class="page-layout__body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.page-layout {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page-layout__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.page-layout__crumbs {
  display: flex;
  flex-wrap: wrap;
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.page-layout__crumb {
  min-width: 0;
  overflow-wrap: anywhere;
}

/* El separador es puntuación de la ruta, no un icono. */
.page-layout__crumb + .page-layout__crumb::before {
  content: "/";
  margin: 0 var(--space-3);
  color: var(--line-strong);
}

.page-layout__crumb-link {
  color: var(--text-2);
  text-decoration: none;
}

.page-layout__crumb-link:hover {
  color: var(--accent);
  text-decoration: underline;
}

.page-layout__crumb-text {
  color: var(--text-3);
}

.page-layout__heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-5);
  flex-wrap: wrap;
}

.page-layout__titles {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.page-layout__title {
  font-size: var(--fs-2xl);
  font-weight: 600;
  letter-spacing: -0.015em;
  overflow-wrap: anywhere;
}

.page-layout__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3) var(--space-5);
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.page-layout__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.page-layout__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
</style>
