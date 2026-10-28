<script setup lang="ts">
import { computed, onMounted } from "vue";

import { useOrganizations } from "@/entities/organization";
import { visibleText } from "@/shared/lib";
import { ErrorNotice, PageLayout } from "@/shared/ui";
import type { Crumb } from "@/shared/ui";

// Las secciones de la organización se navegan desde la barra lateral (ADR 0029).
const props = defineProps<{ organizationId: string; section: string; parent?: Crumb }>();
const orgs = useOrganizations();
onMounted(() => void orgs.reload());

const org = computed(() => orgs.organizations.value?.find((o) => o.id.toLowerCase() === props.organizationId.toLowerCase()));
const title = computed(() => (org.value ? visibleText(org.value.login, 80) : "Organización"));
const trail = computed<Crumb[]>(() => [
  { label: "Organizaciones", to: { name: "home" } },
  { label: title.value, to: { name: "organization", params: { organizationId: props.organizationId } } },
  ...(props.parent ? [props.parent] : []),
  { label: props.section },
]);
</script>

<template>
  <PageLayout :title="title" :trail="trail">
    <template v-if="org" #meta>
      <span class="org-shell__section">{{ section }}</span>
      <span>Tu rol: <span class="org-shell__role">{{ org.role }}</span></span>
      <span v-if="org.status !== 'ACTIVE'" class="org-shell__suspended">Instalación suspendida</span>
    </template>
    <template v-if="$slots['actions']" #actions>
      <slot name="actions" />
    </template>
    <ErrorNotice v-if="orgs.error.value" :error="orgs.error.value" />
    <p v-if="orgs.organizations.value && !org" class="org-shell__note">La organización no existe o no es accesible.</p>
    <slot v-if="org" :org="org" />
  </PageLayout>
</template>

<style scoped>
.org-shell__section {
  color: var(--text);
  font-weight: 600;
}

.org-shell__role {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.org-shell__suspended {
  color: var(--warn);
  font-weight: 600;
}

.org-shell__note {
  color: var(--text-2);
}
</style>
