<template>
  <div class="app-chrome">
    <header class="app-header">
      <div class="header-left">
        <button class="brand" @click="router.push('/')">
          <span class="sr-only">{{ $t('a11y.backToProjectList') }}</span>
          <span aria-hidden="true">CAMPAIGN REACTION</span>
        </button>
      </div>

      <div class="header-center">
        <div v-if="layout" class="view-switcher" role="group" :aria-label="$t('a11y.layoutSwitcher')">
          <button
            v-for="mode in ['graph', 'split', 'workbench']"
            :key="mode"
            class="switch-btn"
            :class="{ active: modelValue === mode }"
            :aria-pressed="modelValue === mode"
            @click="$emit('update:modelValue', mode)"
          >
            {{ layoutLabels[mode] }}
          </button>
        </div>
      </div>

      <div class="header-right">
        <slot name="actions" />
        <LanguageSwitcher />
        <span class="status-indicator" :class="status">
          <span class="dot" aria-hidden="true"></span>
          <span class="sr-only">{{ $t('a11y.statusLabel') }}:</span>
          {{ statusText }}
        </span>
      </div>
    </header>

    <!-- The workflow is five steps long; without a stepper the only way back was
         the brand link, which drops all the way out to the project list. -->
    <nav class="step-bar" :aria-label="$t('a11y.workflowSteps')">
      <ol class="step-list">
        <li v-for="(name, i) in stepNames" :key="i" class="step-item">
          <button
            class="step-btn"
            :class="{
              current: i + 1 === currentStep,
              done: i + 1 < currentStep,
              reachable: !!stepTarget(i + 1)
            }"
            :disabled="!stepTarget(i + 1) || i + 1 === currentStep"
            :aria-current="i + 1 === currentStep ? 'step' : undefined"
            @click="goToStep(i + 1)"
          >
            <span class="step-num" aria-hidden="true">{{ String(i + 1).padStart(2, '0') }}</span>
            <span class="step-name">{{ name }}</span>
          </button>
          <span v-if="i < stepNames.length - 1" class="step-sep" aria-hidden="true"></span>
        </li>
      </ol>
    </nav>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './LanguageSwitcher.vue'

const props = defineProps({
  currentStep: { type: Number, required: true },
  status: { type: String, default: 'processing' }, // processing | completed | ready | error
  statusText: { type: String, default: '' },
  // Split-pane switcher. Pass layout=false on views without a graph panel.
  layout: { type: Boolean, default: true },
  modelValue: { type: String, default: 'split' },
  projectId: { type: [String, Number], default: null },
  simulationId: { type: [String, Number], default: null },
  reportId: { type: [String, Number], default: null }
})

defineEmits(['update:modelValue'])

const router = useRouter()
const { t, tm } = useI18n()

const stepNames = computed(() => tm('main.stepNames'))

const layoutLabels = computed(() => ({
  graph: t('main.layoutGraph'),
  split: t('main.layoutSplit'),
  workbench: t('main.layoutWorkbench')
}))

// A step is only reachable once the id its route needs exists. Steps the run
// has not produced yet stay disabled rather than routing to a broken page.
const stepTarget = (step) => {
  switch (step) {
    case 1: return props.projectId ? { name: 'Process', params: { projectId: props.projectId } } : null
    case 2: return props.simulationId ? { name: 'Simulation', params: { simulationId: props.simulationId } } : null
    case 3: return props.simulationId ? { name: 'SimulationRun', params: { simulationId: props.simulationId } } : null
    case 4: return props.reportId ? { name: 'Report', params: { reportId: props.reportId } } : null
    case 5: return props.reportId ? { name: 'Interaction', params: { reportId: props.reportId } } : null
    default: return null
  }
}

const goToStep = (step) => {
  const target = stepTarget(step)
  if (target) router.push(target)
}
</script>

<style scoped>
.app-chrome {
  flex-shrink: 0;
  background: var(--white);
  z-index: 100;
  position: relative;
}

/* ---------- Row 1: brand, layout, status ---------- */
.app-header {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  gap: 16px;
  border-bottom: 1px solid var(--border);
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1;
}

.header-right {
  justify-content: flex-end;
}

.header-center {
  flex-shrink: 0;
}

.brand {
  font-family: var(--font-mono);
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  background: none;
  border: none;
  padding: 4px;
  color: var(--ink);
  white-space: nowrap;
}

.view-switcher {
  display: flex;
  background: var(--surface-2);
  padding: 4px;
  border-radius: 6px;
  gap: 4px;
}

.switch-btn {
  border: none;
  background: transparent;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  border-radius: 4px;
  transition: background 0.2s, color 0.2s;
}

.switch-btn.active {
  background: var(--white);
  color: var(--ink);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  font-weight: 500;
  white-space: nowrap;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--border-strong);
  flex-shrink: 0;
}

.status-indicator.processing .dot { background: var(--accent); animation: pulse 1s infinite; }
.status-indicator.completed .dot,
.status-indicator.ready .dot { background: var(--success); }
.status-indicator.error .dot { background: var(--danger); }

@keyframes pulse { 50% { opacity: 0.5; } }

/* ---------- Row 2: the stepper ---------- */
.step-bar {
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  overflow-x: auto;
}

.step-list {
  display: flex;
  align-items: center;
  list-style: none;
  margin: 0;
  padding: 0 24px;
  min-height: 40px;
}

.step-item {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.step-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: none;
  padding: 8px 10px;
  font-family: var(--font-sans);
  font-size: 12px;
  color: var(--muted-soft);
  white-space: nowrap;
  border-bottom: 2px solid transparent;
}

.step-btn:disabled {
  cursor: default;
}

.step-btn.reachable:not(:disabled):hover {
  color: var(--ink);
  background: var(--surface-2);
}

.step-btn.done {
  color: var(--muted);
}

.step-btn.current {
  color: var(--ink);
  font-weight: 700;
  border-bottom-color: var(--accent);
}

.step-num {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
}

.step-btn.current .step-num {
  color: var(--accent);
}

.step-sep {
  width: 16px;
  height: 1px;
  background: var(--border);
  flex-shrink: 0;
}

/* ---------- Narrow screens ---------- */
@media (max-width: 1100px) {
  .app-header {
    height: auto;
    flex-wrap: wrap;
    padding: 10px 16px;
  }

  .header-left,
  .header-right {
    flex: none;
  }

  .header-center {
    order: 3;
    width: 100%;
  }

  .view-switcher {
    width: 100%;
  }

  .switch-btn {
    flex: 1;
  }

  .step-list {
    padding: 0 16px;
  }

  /* Numbers alone keep all five steps reachable without a scroll on tablets. */
  .step-btn:not(.current) .step-name {
    display: none;
  }

  .step-sep {
    width: 8px;
  }
}

@media (max-width: 600px) {
  .brand {
    font-size: 14px;
  }
}
</style>
