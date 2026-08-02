<template>
  <!-- A failure used to reach the user as a log line and a coloured dot, which
       is indistinguishable from a slow step: people sat watching a spinner on a
       process that had already died. This is the visible sink for that. -->
  <div v-if="message" class="app-banner" :class="tone" :role="tone === 'error' ? 'alert' : 'status'">
    <span class="banner-text">{{ message }}</span>

    <button v-if="retryable" class="banner-btn primary" :disabled="busy" @click="$emit('retry')">
      {{ busy ? $t('common.loading') : (retryLabel || $t('common.retry')) }}
    </button>

    <slot name="actions" />

    <button v-if="dismissible" class="banner-btn" @click="$emit('dismiss')">
      {{ $t('banner.dismiss') }}
    </button>
  </div>
</template>

<script setup>
defineProps({
  message: { type: String, default: '' },
  // error: the step cannot continue. warn: it continued on stale or partial data.
  tone: { type: String, default: 'error' },
  retryable: { type: Boolean, default: false },
  // Names the specific repair when a bare "Retry" would be ambiguous - the same
  // banner can offer to replay a graph build or re-run ontology generation.
  retryLabel: { type: String, default: '' },
  dismissible: { type: Boolean, default: true },
  busy: { type: Boolean, default: false }
})

defineEmits(['retry', 'dismiss'])
</script>

<style scoped>
.app-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 24px;
  font-size: 14px;
  flex-wrap: wrap;
}

.app-banner.error {
  background: #FEF2F2;
  border-bottom: 1px solid #FECACA;
  color: #B91C1C;
}

.app-banner.warn {
  background: #FFF7ED;
  border-bottom: 1px solid #FDBA74;
  color: #9A3412;
}

.banner-text {
  flex: 1;
  min-width: 200px;
  word-break: break-word;
}

.banner-btn {
  flex-shrink: 0;
  border: 1px solid currentColor;
  background: none;
  padding: 7px 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: inherit;
}

/* Filled, so the recovery action reads as the primary one. The colours are
   named rather than `currentColor` because the label has to invert against the
   fill, and `currentColor` would paint both the same. */
.app-banner.error .banner-btn.primary {
  background: #B91C1C;
  border-color: #B91C1C;
  color: var(--white);
}

.app-banner.warn .banner-btn.primary {
  background: #9A3412;
  border-color: #9A3412;
  color: var(--white);
}

.banner-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
