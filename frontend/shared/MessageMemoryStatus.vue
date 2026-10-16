<script setup lang="ts">
import { channelLabel,processingLabel } from './memoryStatus'
defineProps<{message:any}>()
</script>
<template><div class="message-memory-state"><span>{{ channelLabel(message.origin) }}</span><span v-if="message.role==='user'" :class="{attention:['failed','needs_assistance'].includes(message.memory_processing?.state)}">{{ processingLabel(message.memory_processing) }}</span></div></template>
<style scoped>.message-memory-state{display:flex;gap:5px 12px;flex-wrap:wrap;font-size:11px;line-height:1.7;color:#74856f;margin:7px 0}.message-memory-state .attention{color:#a36b31}</style>
