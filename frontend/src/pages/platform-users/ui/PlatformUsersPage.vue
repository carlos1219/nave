<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { usePlatformOrganizations, usePlatformUsers } from "@/entities/platform";
import { useUserAccess } from "@/features/manage-platform-access";
import type { PlatformUser, Role } from "@/shared/api";
import { formatTimestamp, useTextFilter, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Modal, PageLayout, Panel, StatusTag } from "@/shared/ui";
import type { Column, Crumb } from "@/shared/ui";

const ROLES: readonly Role[] = ["VIEWER", "TRIAGER", "ADMIN"];
const users = usePlatformUsers();
const organizations = usePlatformOrganizations();
onMounted(() => {
  void users.reload();
  void organizations.reload();
});
const access = useUserAccess(async () => {
  await Promise.all([users.reload(), organizations.reload()]);
});

const search = useTextFilter(
  () => users.users.value ?? [],
  (u) => [u.display_name ?? "", u.email ?? "", ...u.memberships.map((m) => m.login)],
);
const selectedId = ref<string | null>(null);
const selected = computed(() => users.users.value?.find((u) => u.id === selectedId.value));
const addOrg = ref("");
const addRole = ref<Role>("VIEWER");
const confirmRevoke = ref(false);
const revoked = ref<number | null>(null);
const available = computed(() =>
  (organizations.organizations.value ?? []).filter((o) => !selected.value?.memberships.some((m) => m.organization_id === o.id)),
);

function nameOf(user: PlatformUser): string {
  return visibleText(user.display_name ?? user.email ?? user.id, 80);
}
function open(user: PlatformUser): void {
  access.clear();
  selectedId.value = user.id;
  addOrg.value = "";
  addRole.value = "VIEWER";
  confirmRevoke.value = false;
  revoked.value = null;
}
function onRole(organizationId: string, event: Event): void {
  if (selected.value) void access.setRole.run(selected.value.id, organizationId, (event.target as HTMLSelectElement).value as Role);
}
async function add(): Promise<void> {
  if (selected.value && addOrg.value !== "") {
    await access.setRole.run(selected.value.id, addOrg.value, addRole.value);
    addOrg.value = "";
  }
}
async function revoke(): Promise<void> {
  if (!selected.value) return;
  const count = await access.revokeSessions.run(selected.value.id);
  confirmRevoke.value = false;
  if (count !== undefined) revoked.value = count;
}

const trail: readonly Crumb[] = [{ label: "Organizaciones", to: { name: "home" } }, { label: "Usuarios" }];
const columns: readonly Column<PlatformUser>[] = [
  { key: "name", header: "Usuario" },
  { key: "memberships", header: "Organizaciones y rol" },
  { key: "last_login_at", header: "Último acceso", mono: true, text: (u) => (u.last_login_at ? formatTimestamp(u.last_login_at) : "Nunca") },
  { key: "live_sessions", header: "Sesiones", numeric: true },
  { key: "action", header: "Acción" },
];
</script>

<template>
  <PageLayout title="Usuarios" :trail="trail">
    <template #meta>
      <span>Administración de plataforma</span>
    </template>
    <ErrorNotice v-if="users.error.value" :error="users.error.value" />
    <Panel title="Usuarios de la instalación" flush>
      <template #aside>
        <span v-if="users.users.value">{{ search.filtered.value.length }} de {{ users.users.value.length }}</span>
        <label class="users__search">
          <span class="visually-hidden">Buscar usuario u organización</span>
          <input v-model="search.query.value" type="search" maxlength="100" placeholder="Buscar nombre, correo u organización" autocomplete="off" spellcheck="false">
        </label>
      </template>
      <p class="users__hint">
        Aquí se ven todos los que han iniciado sesión alguna vez. El rol de plataforma solo se concede desde la CLI del servidor.
      </p>
      <DataTable
        caption="Usuarios con sus organizaciones, roles y sesiones"
        caption-hidden
        bare
        :columns="columns"
        :rows="search.filtered.value"
        :row-key="(u: PlatformUser) => u.id"
        :loading="users.loading.value"
        :empty-text="search.query.value ? 'Ningún usuario coincide con la búsqueda.' : 'Aún no ha iniciado sesión nadie.'"
      >
        <template #cell-name="{ row }">
          <span class="users__name">
            <span class="users__display">{{ nameOf(row) }}</span>
            <span v-if="row.email" class="users__email">{{ visibleText(row.email, 120) }}</span>
          </span>
          <StatusTag v-if="row.platform_admin" label="Plataforma" tone="running" />
        </template>
        <template #cell-memberships="{ row }">
          <ul v-if="row.memberships.length > 0" class="users__orgs">
            <li v-for="m in row.memberships" :key="m.organization_id" class="users__org">
              <span>{{ visibleText(m.login, 39) }}</span>
              <span class="users__role">{{ m.role }}</span>
            </li>
          </ul>
          <span v-else class="users__muted">Sin organizaciones</span>
        </template>
        <template #cell-action="{ row }">
          <button type="button" class="users__manage" @click="open(row)">Gestionar</button>
        </template>
      </DataTable>
    </Panel>

    <Modal :open="selected !== undefined" :title="selected ? `Accesos de ${nameOf(selected)}` : 'Accesos'" @close="selectedId = null">
      <template v-if="selected">
        <ErrorNotice v-if="access.error()" :error="access.error()!" />
        <p v-if="revoked !== null" class="users__done" role="status">Cerradas {{ revoked }} sesiones.</p>
        <table class="users__table">
          <caption class="visually-hidden">Organizaciones del usuario</caption>
          <thead>
            <tr><th scope="col">Organización</th><th scope="col">Rol</th><th scope="col"><span class="visually-hidden">Acciones</span></th></tr>
          </thead>
          <tbody>
            <tr v-if="selected.memberships.length === 0">
              <td colspan="3" class="users__muted">No pertenece a ninguna organización.</td>
            </tr>
            <tr v-for="m in selected.memberships" :key="m.organization_id">
              <td class="users__mono">{{ visibleText(m.login, 39) }}</td>
              <td>
                <select :value="m.role" :disabled="access.busy()" :aria-label="`Rol en ${visibleText(m.login, 39)}`" @change="onRole(m.organization_id, $event)">
                  <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
                </select>
              </td>
              <td class="users__end">
                <button type="button" class="users__danger" :disabled="access.busy()" @click="access.setRole.run(selected.id, m.organization_id, null)">
                  Retirar
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <form class="users__add" aria-label="Añadir a una organización" @submit.prevent="add">
          <label class="users__field">
            <span class="users__label">Añadir a</span>
            <select v-model="addOrg" :disabled="available.length === 0">
              <option value="">{{ available.length === 0 ? "Ya está en todas" : "Elige organización" }}</option>
              <option v-for="o in available" :key="o.id" :value="o.id">{{ visibleText(o.login, 39) }}</option>
            </select>
          </label>
          <label class="users__field">
            <span class="users__label">Con rol</span>
            <select v-model="addRole">
              <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <button type="submit" :disabled="addOrg === '' || access.busy()">Añadir</button>
        </form>
        <div class="users__sessions">
          <span>{{ selected.live_sessions }} sesiones abiertas</span>
          <button v-if="!confirmRevoke" type="button" class="users__danger" :disabled="selected.live_sessions === 0 || access.busy()" @click="confirmRevoke = true">
            Cerrar todas sus sesiones
          </button>
          <span v-else class="users__confirm">
            <button type="button" @click="confirmRevoke = false">Cancelar</button>
            <button type="button" class="users__danger" :disabled="access.busy()" @click="revoke">Confirmar cierre</button>
          </span>
        </div>
      </template>
    </Modal>
  </PageLayout>
</template>

<style scoped>
.users__search input {
  width: 280px;
  max-width: 100%;
  min-height: 28px;
}

.users__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.users__name {
  display: inline-flex;
  flex-direction: column;
  margin-right: var(--space-3);
  vertical-align: middle;
}

.users__display {
  font-weight: 600;
}

.users__email,
.users__mono {
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.users__orgs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.users__org {
  display: inline-flex;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  border: var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 20px;
}

.users__role {
  color: var(--accent);
  font-weight: 700;
}

.users__muted {
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.users__manage,
.users__danger {
  height: 28px;
}

.users__danger:hover:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}

.users__table {
  width: 100%;
  margin-bottom: var(--space-5);
  border-collapse: separate;
  border-spacing: 0;
}

.users__end {
  text-align: right;
}

.users__add {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-4);
  padding: var(--space-4) 0;
  border-top: var(--border);
}

.users__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.users__label {
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.users__sessions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding-top: var(--space-4);
  border-top: var(--border);
  font-size: var(--fs-sm);
}

.users__confirm {
  display: inline-flex;
  gap: var(--space-3);
}

.users__done {
  margin-bottom: var(--space-4);
  color: var(--ok);
  font-weight: 600;
}
</style>
