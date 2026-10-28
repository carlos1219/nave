<script setup lang="ts">
import { computed } from "vue";

import { NAV_ICONS } from "./icons";
import type { NavIconName } from "./icons";

// Siempre acompaña a un texto (visible o nombre accesible del enlace): el icono no se anuncia.
const props = defineProps<{ name: NavIconName }>();
const paths = computed(() => NAV_ICONS[props.name]);
</script>

<template>
  <svg
    class="nav-icon"
    viewBox="0 0 24 24"
    width="18"
    height="18"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="d in paths" :key="d" :d="d" />
  </svg>
</template>

<style scoped>
.nav-icon {
  display: block;
  flex-shrink: 0;
}
</style>
