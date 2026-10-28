<script setup lang="ts">
import { onMounted } from "vue";

import type { Role } from "@/shared/api";
import { visibleText } from "@/shared/lib";
import { ErrorNotice, Modal } from "@/shared/ui";

import { ROLES, useMembers } from "../model/useMembers";

const props = defineProps<{ organizationId: string; currentUserId: string | null }>();
const m = useMembers(() => props.organizationId);

onMounted(() => void m.reload());

function onRole(userId: string, event: Event): void {
  void m.changeRole(userId, (event.target as HTMLSelectElement).value as Role);
}
function nameOf(userId: string, displayName: string | null, email: string | null): string {
  return visibleText(displayName ?? email ?? userId, 80);
}
</script>

<template>
  <section class="members" aria-label="Miembros">
    <div v-if="m.loadError.value || m.actionError()" class="members__notices">
      <ErrorNotice v-if="m.loadError.value" :error="m.loadError.value" />
      <ErrorNotice v-if="m.actionError()" :error="m.actionError()!" />
    </div>
    <p v-if="m.loading.value" class="members__status">Cargando…</p>
    <table v-else-if="m.members.value" class="members__table">
      <caption class="visually-hidden">Miembros de la organización</caption>
      <thead>
        <tr>
          <th scope="col" class="members__head">Miembro</th>
          <th scope="col" class="members__head">Correo</th>
          <th scope="col" class="members__head">Rol</th>
          <th scope="col" class="members__head"><span class="visually-hidden">Acciones</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="m.members.value.length === 0">
          <td class="members__status" colspan="4">Sin miembros.</td>
        </tr>
        <tr v-for="member in m.members.value" :key="member.user_id" class="members__row">
          <td class="members__cell members__name">
            {{ nameOf(member.user_id, member.display_name, member.email) }}
            <span v-if="member.user_id === currentUserId" class="members__you">(tú)</span>
          </td>
          <td class="members__cell members__email">{{ member.email ? visibleText(member.email, 120) : "—" }}</td>
          <td class="members__cell">
            <select
              class="members__role"
              :value="member.role"
              :disabled="m.busy()"
              :aria-label="`Rol de ${nameOf(member.user_id, member.display_name, member.email)}`"
              @change="onRole(member.user_id, $event)"
            >
              <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
            </select>
          </td>
          <td class="members__cell members__actions">
            <button type="button" class="members__remove" :disabled="m.busy()" @click="m.pendingRemoval.value = member.user_id">
              Retirar
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <Modal :open="m.pendingRemoval.value !== null" title="Retirar miembro" @close="m.pendingRemoval.value = null">
      <p>
        {{ m.pendingRemoval.value === currentUserId ? "Vas a retirarte a ti mismo de la organización." : "El miembro perderá el acceso a la organización." }}
      </p>
      <template #footer>
        <button type="button" @click="m.pendingRemoval.value = null">Cancelar</button>
        <button
          type="button"
          class="members__confirm"
          :disabled="m.busy()"
          @click="m.pendingRemoval.value && m.remove(m.pendingRemoval.value)"
        >
          Retirar
        </button>
      </template>
    </Modal>
  </section>
</template>

<style scoped>
.members {
  overflow-x: auto;
}

.members__notices {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5) 0;
}

.members__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.members__head:first-child,
.members__cell:first-child {
  padding-left: var(--space-5);
}

.members__head:last-child,
.members__cell:last-child {
  padding-right: var(--space-5);
}

.members__row:hover > td {
  background: var(--surface-hover);
}

.members__name {
  font-weight: 600;
}

.members__you {
  color: var(--text-3);
  font-weight: 400;
}

.members__email {
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.members__role {
  min-height: 28px;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.members__actions {
  text-align: right;
}

.members__remove {
  height: 28px;
}

.members__remove:hover:not(:disabled),
.members__confirm:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}

.members__confirm:hover:not(:disabled) {
  background: var(--danger-bg);
}

.members__status {
  padding: var(--space-5);
  color: var(--text-3);
}
</style>
