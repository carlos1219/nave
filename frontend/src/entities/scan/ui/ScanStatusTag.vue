<script setup lang="ts">
import { StatusTag } from "@/shared/ui";

import { statusLabel, statusTone } from "../model/status";

defineProps<{ status: string }>();
</script>

<template>
  <StatusTag :label="statusLabel(status)" :tone="statusTone(status)" />
</template>
