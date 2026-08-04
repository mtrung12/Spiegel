<template>
  <div class="language-switcher" ref="switcherRef">
    <button class="switcher-trigger" @click="toggleDropdown">
      {{ currentLabel }}
      <span class="caret">{{ open ? '▲' : '▼' }}</span>
    </button>
    <ul v-if="open" class="switcher-dropdown">
      <li v-for="loc in availableLocales" :key="loc.key">
        <button
          type="button"
          class="switcher-option"
          :class="{ active: loc.key === locale }"
          :aria-current="loc.key === locale ? 'true' : undefined"
          @click="switchLocale(loc.key)"
        >
          {{ loc.label }}
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { availableLocales } from '@/i18n/index.js'

const { locale } = useI18n()
const open = ref(false)
const switcherRef = ref(null)

const currentLabel = computed(() => {
  const found = availableLocales.find(l => l.key === locale.value)
  return found ? found.label : locale.value
})

const toggleDropdown = () => {
  open.value = !open.value
}

const switchLocale = (key) => {
  locale.value = key
  localStorage.setItem('locale', key)
  document.documentElement.lang = key
  open.value = false
}

const onClickOutside = (e) => {
  if (switcherRef.value && !switcherRef.value.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  document.documentElement.lang = locale.value
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
})
</script>

<style scoped>
.language-switcher {
  position: relative;
  display: inline-block;
  font-family: var(--font-mono);
}

/* Light theme (default - for white header backgrounds) */
.switcher-trigger {
  background: transparent;
  color: var(--ink-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  padding: var(--space-1) var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.switcher-trigger:hover {
  border-color: var(--ink);
  background: var(--surface-2);
}

.caret {
  font-size: 0.6rem;
  color: var(--muted-soft);
}

.switcher-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: var(--space-1);
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  list-style: none;
  padding: var(--space-1);
  min-width: 100%;
  z-index: 1000;
  box-shadow: var(--shadow-2);
}

.switcher-option {
  /* A <button> now; reset the inherited control styling. */
  font: inherit;
  text-align: left;
  appearance: none;
  width: 100%;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--ink-2);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--dur-fast) var(--ease);
}

.switcher-option:hover {
  background: var(--surface-2);
}

/* The accent token, not a --orange variable that was never defined and so
   always fell through to a red that is not in the palette. */
.switcher-option.active {
  color: var(--accent);
}


</style>
