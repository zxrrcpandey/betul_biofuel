<template>
  <div class="flex flex-col gap-2" aria-hidden="true">
    <div v-for="n in count" :key="n" class="skeleton min-h-row"></div>
  </div>
</template>

<script setup>
defineProps({ count: { type: Number, default: 4 } })
</script>
