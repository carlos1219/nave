<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<template>
  <section class="not-found" aria-labelledby="not-found-title">
    <p class="not-found__code">404</p>
    <h1 id="not-found-title" class="not-found__title">
      No encontrado
    </h1>
    <p class="not-found__text">
      La página no existe o el enlace está incompleto.
    </p>
    <RouterLink class="action-link" :to="{ name: 'home' }">
      Ir a organizaciones
    </RouterLink>
  </section>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-3);
  max-width: 440px;
  margin: var(--space-8) auto;
  padding: var(--space-6);
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.not-found__code {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.not-found__title {
  font-size: var(--fs-xl);
  font-weight: 600;
}

.not-found__text {
  margin-bottom: var(--space-3);
  color: var(--text-2);
}
</style>
