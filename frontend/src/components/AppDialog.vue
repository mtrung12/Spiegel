<template>
  <!-- Built on the native <dialog>: focus trapping, Esc to dismiss, background
       inerting and focus restore on close are all browser behaviour, so none of
       it has to be re-implemented (and get it subtly wrong). -->
  <dialog ref="dialogEl" class="app-dialog" :class="size" @close="onClose" @click="onBackdropClick">
    <div class="dialog-inner">
      <slot />
    </div>
  </dialog>
</template>

<script setup>
import { ref, watch, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  // Clicking the backdrop dismisses by default; turn it off for destructive
  // confirmations where a stray click should not count as an answer.
  dismissOnBackdrop: { type: Boolean, default: true },
  size: { type: String, default: 'md' } // sm | md | lg
})

const emit = defineEmits(['close'])
const dialogEl = ref(null)

watch(() => props.open, async (open) => {
  await nextTick()
  const el = dialogEl.value
  if (!el) return
  if (open && !el.open) el.showModal()
  else if (!open && el.open) el.close()
}, { immediate: true })

// Fires for Esc as well as a programmatic close, so the parent's state stays
// in step with what the browser actually did.
const onClose = () => emit('close')

const onBackdropClick = (e) => {
  if (props.dismissOnBackdrop && e.target === dialogEl.value) emit('close')
}

onBeforeUnmount(() => {
  if (dialogEl.value?.open) dialogEl.value.close()
})
</script>

<style scoped>
.app-dialog {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-3);
  background: var(--white);
  color: var(--ink);
  padding: 0;
  width: min(92vw, 560px);
  max-height: 88vh;
  font-family: var(--font-sans);
  margin: auto; /* global * { margin: 0 } kills native <dialog> auto-centering */
}

.app-dialog.sm { width: min(92vw, 420px); }
.app-dialog.lg { width: min(94vw, 860px); }

/* The dialog used to appear fully formed, which reads as a screenshot swap.
   It now scales up the last few percent while the backdrop fades in behind it,
   so the eye follows one object arriving instead of the whole page changing.
   Entry only: `close()` is synchronous, and holding the element around for an
   exit animation would mean re-implementing the dismiss the browser gives us. */
.app-dialog[open] {
  animation: dialog-in 200ms var(--ease) both;
}

.app-dialog::backdrop {
  background: rgba(17, 17, 17, 0.4);
  backdrop-filter: blur(2px);
}

.app-dialog[open]::backdrop {
  animation: backdrop-in 220ms var(--ease) both;
}

@keyframes dialog-in {
  from { opacity: 0; transform: translateY(8px) scale(0.98); }
  to { opacity: 1; transform: none; }
}

@keyframes backdrop-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

/* The rounded corner has to be clipped here too, or the scrolling content
   paints square over it. */
.dialog-inner {
  max-height: 88vh;
  overflow-y: auto;
  border-radius: inherit;
}
</style>
