<script setup lang="ts">
defineProps<{ hasMore: boolean; loading: boolean; capped: boolean }>();
const emit = defineEmits<{ more: [] }>();
</script>

<template>
  <div class="load-more">
    <button v-if="hasMore" type="button" :disabled="loading" @click="emit('more')">
      {{ loading ? "Cargando…" : "Cargar más" }}
    </button>
    <p v-if="capped" class="load-more__note">
      Se muestran los primeros resultados. Afina el filtro para ver el resto.
    </p>
  </div>
</template>

<style scoped>
.load-more {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
}

.load-more:empty {
  display: none;
}

.load-more__note {
  color: var(--text-3);
  font-size: var(--fs-sm);
}
</style>
