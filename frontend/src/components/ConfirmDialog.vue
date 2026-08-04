<template>
  <!-- Replaces window.confirm / window.alert: those block the tab, cannot be
       styled, and read as a browser warning rather than part of the product. -->
  <AppDialog :open="open" :dismissOnBackdrop="!destructive" size="sm" @close="$emit('cancel')">
    <div class="confirm">
      <h2 class="confirm-title">{{ title }}</h2>
      <p class="confirm-message">{{ message }}</p>

      <div class="confirm-actions">
        <button v-if="!alertOnly" class="confirm-btn ghost" @click="$emit('cancel')">
          {{ cancelLabel || $t('common.cancel') }}
        </button>
        <button
          ref="confirmBtn"
          class="confirm-btn primary"
          :class="{ destructive }"
          @click="$emit('confirm')"
        >
          {{ confirmLabel || $t('common.confirm') }}
        </button>
      </div>
    </div>
  </AppDialog>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import AppDialog from './AppDialog.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  message: { type: String, default: '' },
  confirmLabel: { type: String, default: '' },
  cancelLabel: { type: String, default: '' },
  // Destructive actions get a red primary and no dismiss-by-backdrop.
  destructive: { type: Boolean, default: false },
  // A single-button notice, standing in for window.alert.
  alertOnly: { type: Boolean, default: false }
})

defineEmits(['confirm', 'cancel'])

const confirmBtn = ref(null)

// Land the focus on the action rather than leaving it on the dialog box, so
// Enter does the obvious thing.
watch(() => props.open, async (open) => {
  if (!open) return
  await nextTick()
  confirmBtn.value?.focus()
})
</script>

<style scoped>
.confirm {
  padding: var(--space-5);
}

.confirm-title {
  font-size: var(--text-lg);
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: var(--space-2);
}

.confirm-message {
  font-size: var(--text-base);
  line-height: 1.6;
  color: var(--muted);
  margin-bottom: var(--space-5);
  overflow-wrap: anywhere;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.confirm-btn {
  padding: var(--space-2) var(--space-4);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  font-weight: 500;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--white);
  color: var(--ink);
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.confirm-btn.ghost:hover {
  background: var(--surface-2);
}

.confirm-btn.primary {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--white);
}

.confirm-btn.primary:hover {
  background: var(--ink-2);
}

.confirm-btn.primary.destructive {
  background: var(--danger);
  border-color: var(--danger);
}

.confirm-btn.primary.destructive:hover {
  background: #B91C1C;
}
</style>
