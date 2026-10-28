<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";

import { useSessionRetry, useSessionStore, useSignOut } from "@/entities/session";
import { AppSidebar } from "@/widgets/app-sidebar";

const session = useSessionStore();
const { signOut, pending, failed } = useSignOut();
const { retry } = useSessionRetry();
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <RouterLink v-if="session.authenticated" class="app-header__wordmark" :to="{ name: 'home' }" aria-label="NAVE, inicio">NAVE</RouterLink>
      <span v-else class="app-header__wordmark">NAVE</span>
      <span class="app-header__product">Análisis estático</span>
      <span v-if="session.authenticated" class="app-header__session">
        <span v-if="failed" class="app-header__error" role="alert">No se pudo cerrar la sesión.</span>
        <span class="app-header__user">{{ session.displayName }}</span>
        <button class="app-header__signout" type="button" :disabled="pending" @click="signOut">
          Salir
        </button>
      </span>
    </div>
  </header>
  <div class="app-body" :class="{ 'app-body--with-nav': session.authenticated }">
    <AppSidebar v-if="session.authenticated" />
    <main class="app-main">
      <section
        v-if="session.status === 'error'"
        class="app-main__error"
        role="alert"
        aria-labelledby="session-error-title"
      >
        <h1 id="session-error-title" class="app-main__error-title">
          Sin conexión con el servidor
        </h1>
        <p class="app-main__error-text">
          No se pudo comprobar la sesión. Comprueba la conexión y vuelve a intentarlo.
        </p>
        <button type="button" @click="retry">
          Reintentar
        </button>
      </section>
      <RouterView v-else v-slot="{ Component, route }">
        <component :is="Component" :key="route.path" />
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: var(--border);
  background: var(--surface);
}

.app-header__inner {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  height: var(--header-h);
  padding: 0 var(--space-6) 0 var(--space-5);
}

.app-header__wordmark {
  display: inline-flex;
  align-items: center;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: var(--fs-lg);
  font-weight: 700;
  letter-spacing: 0.22em;
  text-decoration: none;
}

.app-header__wordmark:hover {
  color: var(--text);
}

.app-header__product {
  padding-left: var(--space-5);
  border-left: var(--border);
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.app-header__session {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-left: auto;
  min-width: 0;
}

.app-header__user {
  overflow: hidden;
  color: var(--text-2);
  font-size: var(--fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-header__signout {
  height: 28px;
}

.app-header__error {
  color: var(--danger);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.app-body--with-nav {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
}

.app-main {
  width: 100%;
  max-width: var(--page-w);
  margin: 0 auto;
  padding: var(--space-6) var(--space-6) var(--space-8);
}

.app-main__error {
  max-width: 460px;
  margin: var(--space-8) auto;
  padding: var(--space-6);
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.app-main__error-title {
  margin-bottom: var(--space-3);
  font-size: var(--fs-lg);
  font-weight: 600;
}

.app-main__error-text {
  margin-bottom: var(--space-5);
  color: var(--text-2);
}

@media (max-width: 640px) {
  .app-header__inner,
  .app-main {
    padding-left: var(--space-4);
    padding-right: var(--space-4);
  }

  .app-header__inner {
    gap: var(--space-4);
  }

  .app-header__product,
  .app-header__user {
    display: none;
  }
}
</style>
