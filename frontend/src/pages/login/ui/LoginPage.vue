<script setup lang="ts">
// El login es una navegación completa al backend (ADR 0016, AM-706): sin fetch, sin
// parámetros y sin URL de retorno. Por eso es un enlace normal y no un router-link.
const LOGIN_URL = "/api/v1/auth/login";
</script>

<template>
  <section class="login-page" aria-labelledby="login-title">
    <p class="login-page__wordmark">NAVE</p>
    <p class="login-page__expansion">Nodo de Análisis y Verificación Estática</p>
    <div class="login-page__body">
      <h1 id="login-title" class="login-page__title">
        Acceso
      </h1>
      <p class="login-page__text">
        Inicia sesión con el proveedor de identidad de tu organización. Solo para uso interno.
      </p>
      <a class="login-page__action" :href="LOGIN_URL">Iniciar sesión</a>
    </div>
  </section>
</template>

<style scoped>
.login-page {
  max-width: 400px;
  margin: var(--space-8) auto;
  border: var(--border);
  border-top: 3px solid var(--accent);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.login-page__wordmark {
  padding: var(--space-6) var(--space-6) 0;
  font-family: var(--font-mono);
  font-size: var(--fs-2xl);
  font-weight: 700;
  letter-spacing: 0.22em;
}

.login-page__expansion {
  padding: 0 var(--space-6) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.login-page__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-6) var(--space-6);
}

.login-page__title {
  font-size: var(--fs-lg);
  font-weight: 600;
}

.login-page__text {
  margin-bottom: var(--space-3);
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.login-page__action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 36px;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: var(--accent-text);
  font-size: var(--fs-sm);
  font-weight: 600;
  text-decoration: none;
}

.login-page__action:hover {
  background: var(--accent-hover);
  color: var(--accent-text);
}
</style>
