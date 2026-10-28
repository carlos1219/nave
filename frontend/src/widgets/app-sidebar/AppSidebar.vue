<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { canAdminister, useOrganizations } from "@/entities/organization";
import { usePlatformAccess } from "@/entities/platform";
import { isUuid, visibleText } from "@/shared/lib";
import { NavIcon } from "@/shared/ui";

import { GENERAL_NAV, GLOBAL_ROUTES, NAV_PARENT, PLATFORM_NAV, visibleGroups } from "./model/nav";
import type { NavItem } from "./model/nav";
import { useSidebarState } from "./model/useSidebarState";

const route = useRoute();
const { collapsed, narrow, toggle } = useSidebarState();
const orgs = useOrganizations();
const access = usePlatformAccess();
onMounted(() => void access.reload());
const platformAdmin = computed(() => access.me.value?.platform_admin === true);

const organizationId = computed(() => {
  const raw = route.params["organizationId"];
  return typeof raw === "string" && isUuid(raw) ? raw : null;
});
const org = computed(() =>
  orgs.organizations.value?.find((o) => o.id.toLowerCase() === organizationId.value?.toLowerCase()),
);
const groups = computed(() => visibleGroups(org.value !== undefined && canAdminister(org.value.role)));
const active = computed(() => {
  const name = typeof route.name === "string" ? route.name : "";
  return NAV_PARENT[name] ?? name;
});

// Solo hace falta para el nombre y el rol; se pide al entrar en una organización que aún no
// figura en la lista cargada.
watch(
  organizationId,
  (id) => {
    if (id !== null && org.value === undefined && !orgs.loading.value) void orgs.reload();
  },
  { immediate: true },
);

function target(item: NavItem) {
  return GLOBAL_ROUTES.has(item.name)
    ? { name: item.name }
    : { name: item.name, params: { organizationId: organizationId.value ?? "" } };
}
</script>

<template>
  <aside class="app-sidebar" :class="{ 'app-sidebar--collapsed': collapsed }">
    <nav id="app-sidebar-nav" class="app-sidebar__nav" aria-label="Navegación principal">
      <ul class="app-sidebar__list">
        <li v-for="item in GENERAL_NAV.items" :key="item.name">
          <RouterLink
            class="app-sidebar__link"
            :class="{ 'app-sidebar__link--active': item.name === active }"
            :to="target(item)"
            :title="collapsed ? item.label : undefined"
            :aria-current="item.name === active ? 'page' : undefined"
          >
            <NavIcon :name="item.icon" />
            <span :class="collapsed ? 'visually-hidden' : 'app-sidebar__label'">{{ item.label }}</span>
          </RouterLink>
        </li>
      </ul>

      <section v-if="platformAdmin" class="app-sidebar__group app-sidebar__group--platform" :aria-label="PLATFORM_NAV.label">
        <h2 :class="collapsed ? 'visually-hidden' : 'app-sidebar__heading'">{{ PLATFORM_NAV.label }}</h2>
        <ul class="app-sidebar__list">
          <li v-for="item in PLATFORM_NAV.items" :key="item.name">
            <RouterLink
              class="app-sidebar__link"
              :class="{ 'app-sidebar__link--active': item.name === active }"
              :to="target(item)"
              :title="collapsed ? item.label : undefined"
              :aria-current="item.name === active ? 'page' : undefined"
            >
              <NavIcon :name="item.icon" />
              <span :class="collapsed ? 'visually-hidden' : 'app-sidebar__label'">{{ item.label }}</span>
            </RouterLink>
          </li>
        </ul>
      </section>

      <template v-if="organizationId !== null">
        <div class="app-sidebar__org" :title="collapsed && org ? visibleText(org.login, 80) : undefined">
          <span v-if="!collapsed" class="app-sidebar__org-label">Organización</span>
          <span v-if="!collapsed" class="app-sidebar__org-name">{{ org ? visibleText(org.login, 80) : "…" }}</span>
          <span v-else class="app-sidebar__org-initial" aria-hidden="true">{{ org ? visibleText(org.login, 2).slice(0, 2) : "" }}</span>
        </div>
        <section v-for="group in groups" :key="group.label" class="app-sidebar__group" :aria-label="group.label">
          <h2 :class="collapsed ? 'visually-hidden' : 'app-sidebar__heading'">{{ group.label }}</h2>
          <ul class="app-sidebar__list">
            <li v-for="item in group.items" :key="item.name">
              <RouterLink
                class="app-sidebar__link"
                :class="{ 'app-sidebar__link--active': item.name === active }"
                :to="target(item)"
                :title="collapsed ? item.label : undefined"
                :aria-current="item.name === active ? 'page' : undefined"
              >
                <NavIcon :name="item.icon" />
                <span :class="collapsed ? 'visually-hidden' : 'app-sidebar__label'">{{ item.label }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
      </template>
    </nav>

    <button
      v-if="!narrow"
      type="button"
      class="app-sidebar__toggle"
      aria-controls="app-sidebar-nav"
      :aria-expanded="!collapsed"
      :title="collapsed ? 'Desplegar menú' : undefined"
      @click="toggle"
    >
      <NavIcon :name="collapsed ? 'expand' : 'collapse'" />
      <span :class="collapsed ? 'visually-hidden' : 'app-sidebar__label'">{{ collapsed ? "Desplegar menú" : "Contraer menú" }}</span>
    </button>
  </aside>
</template>

<style scoped>
.app-sidebar {
  position: sticky;
  top: var(--header-h);
  display: flex;
  flex-direction: column;
  width: var(--sidebar-w);
  height: calc(100vh - var(--header-h));
  border-right: var(--border);
  background: var(--surface);
}

.app-sidebar--collapsed {
  width: var(--sidebar-w-collapsed);
}

.app-sidebar__nav {
  flex: 1;
  overflow-x: hidden;
  overflow-y: auto;
  padding: var(--space-4) var(--space-3);
}

.app-sidebar__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.app-sidebar__link,
.app-sidebar__toggle {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  width: 100%;
  min-height: 34px;
  padding: 0 var(--space-4);
  border: 0;
  border-left: 2px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  font-family: var(--font-ui);
  font-size: var(--fs-sm);
  font-weight: 500;
  text-align: left;
  text-decoration: none;
  white-space: nowrap;
}

.app-sidebar__link:hover,
.app-sidebar__toggle:hover {
  background: var(--surface-hover);
  color: var(--text);
}

/* La sección actual: fondo de acento suave, filete a la izquierda y aria-current. */
.app-sidebar__link--active,
.app-sidebar__link--active:hover {
  border-left-color: var(--accent);
  background: var(--accent-soft);
  color: var(--text);
  font-weight: 600;
}

.app-sidebar__link--active :deep(.nav-icon) {
  color: var(--accent);
}

.app-sidebar__link:focus-visible,
.app-sidebar__toggle:focus-visible {
  outline-offset: -2px;
}

.app-sidebar__label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-sidebar__org {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: var(--space-5) 0 var(--space-3);
  padding: var(--space-4) var(--space-4) 0;
  border-top: var(--border);
}

.app-sidebar__org-label {
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.app-sidebar__org-name {
  overflow: hidden;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-sidebar__org-initial {
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 700;
  text-align: center;
  text-transform: uppercase;
}

.app-sidebar__group--platform {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: var(--border);
}

.app-sidebar__group + .app-sidebar__group {
  margin-top: var(--space-4);
}

.app-sidebar__heading {
  margin: 0 0 var(--space-2);
  padding: 0 var(--space-4);
  color: var(--text-3);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.app-sidebar--collapsed .app-sidebar__group--platform {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: var(--border);
}

.app-sidebar__group + .app-sidebar__group {
  padding-top: var(--space-3);
  border-top: var(--border);
}

.app-sidebar--collapsed .app-sidebar__link,
.app-sidebar--collapsed .app-sidebar__toggle {
  justify-content: center;
  padding: 0;
}

.app-sidebar--collapsed .app-sidebar__org {
  align-items: center;
  padding: var(--space-3) 0 0;
}

.app-sidebar__toggle {
  flex-shrink: 0;
  width: auto;
  margin: var(--space-3);
  cursor: pointer;
}
</style>
