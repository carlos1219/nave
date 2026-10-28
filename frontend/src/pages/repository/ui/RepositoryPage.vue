<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { canTriage, useOrganizations } from "@/entities/organization";
import { useRepositories } from "@/entities/repository";
import { useScans } from "@/entities/scan";
import { StartScanForm } from "@/features/start-scan";
import { visibleText } from "@/shared/lib";
import { ErrorNotice, LoadMore, PageLayout, Panel } from "@/shared/ui";
import type { Crumb } from "@/shared/ui";
import { ScansTable } from "@/widgets/scans-table";

const props = defineProps<{ organizationId: string; repositoryId: string }>();
const router = useRouter();
const orgs = useOrganizations();
const repos = useRepositories(() => props.organizationId);
const scans = useScans(() => props.repositoryId);
const org = computed(() => orgs.organizations.value?.find((o) => o.id.toLowerCase() === props.organizationId.toLowerCase()));
const repo = computed(() => repos.repositories.value?.find((r) => r.id.toLowerCase() === props.repositoryId.toLowerCase()));
const title = computed(() => (repo.value ? `${visibleText(repo.value.owner, 80)}/${visibleText(repo.value.name, 100)}` : "Repositorio"));
const trail = computed<Crumb[]>(() => [
  { label: "Organizaciones", to: { name: "home" } },
  { label: org.value ? visibleText(org.value.login, 80) : "Organización", to: { name: "organization", params: { organizationId: props.organizationId } } },
  { label: repo.value ? visibleText(repo.value.name, 100) : "Repositorio" },
]);

onMounted(() => {
  void orgs.reload();
  void repos.reload();
  void scans.reset();
});

function started(scanId: string): void {
  void router.push({ name: "scan", params: { scanId } });
}
</script>

<template>
  <PageLayout :title="title" :trail="trail">
    <template v-if="repo" #meta>
      <span>Rama por defecto <span class="repo__mono">{{ visibleText(repo.default_branch, 120) }}</span></span>
      <span v-if="!repo.enabled">Desactivado: no recibe escaneos automáticos</span>
    </template>
    <template #actions>
      <RouterLink class="action-link" :to="{ name: 'findings', params: { repositoryId } }">Ver hallazgos</RouterLink>
    </template>
    <Panel v-if="org && canTriage(org.role)" title="Nuevo escaneo">
      <StartScanForm :repository-id="repositoryId" @started="started" />
    </Panel>
    <ErrorNotice v-if="scans.error.value" :error="scans.error.value" />
    <Panel title="Escaneos" flush>
      <ScansTable :scans="scans.scans.value" :loading="scans.loading.value" />
    </Panel>
    <LoadMore :has-more="scans.hasMore.value" :loading="scans.loadingMore.value" :capped="scans.capped.value" @more="scans.loadMore" />
  </PageLayout>
</template>

<style scoped>
.repo__mono {
  margin-left: var(--space-2);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}
</style>
