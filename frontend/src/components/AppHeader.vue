<template>
  <div class="app-chrome">
    <header class="app-header">
      <div class="header-left">
        <!-- The only exit used to be the wordmark, whose "go back" meaning
             lived in a screen-reader-only span - so on screen the app had no
             back control at all. This one is never disabled: it does not wait
             on the progress fetch, and it still works when that fetch fails. -->
        <button class="back-btn" :aria-label="backLabel" :title="backLabel" @click="goBack">
          <span class="back-arrow" aria-hidden="true">←</span>
          <span class="back-label">{{ backLabel }}</span>
        </button>

        <span class="header-divider" aria-hidden="true"></span>

        <button class="brand" @click="router.push('/')">
          <span class="sr-only">{{ $t('a11y.backToProjectList') }}</span>
          <span aria-hidden="true">SPIEGEL</span>
        </button>

        <!-- Which campaign this is. Only step 1 ever said, so steps 2-5 were
             five identical-looking pages across every project. -->
        <!-- Editable in place: a project could only ever be named at creation,
             so a mistyped name followed the campaign through all five steps. -->
        <template v-if="displayName || editingName">
          <span class="header-divider" aria-hidden="true"></span>
          <input
            v-if="editingName"
            ref="nameInput"
            v-model="draftName"
            class="project-name-input"
            :aria-label="$t('a11y.renameProject')"
            :disabled="savingName"
            maxlength="120"
            @keyup.enter="saveName"
            @keyup.esc="cancelRename"
            @blur="saveName"
          />
          <button
            v-else
            class="project-name"
            :title="$t('a11y.renameProject')"
            :disabled="!projectId"
            @click="startRename"
          >
            <span class="sr-only">{{ $t('a11y.currentProject') }}:</span>
            {{ displayName }}
          </button>
        </template>
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
        <!-- A step that cannot be opened stays focusable and keeps its tooltip:
             `disabled` drops it out of the tab order and suppresses the title,
             so the user got a dead click with no explanation. -->
        <li v-for="(name, i) in stepNames" :key="i" class="step-item" :title="stepHint(i + 1)">
          <button
            class="step-btn"
            :class="{
              current: i + 1 === currentStep,
              done: i + 1 < currentStep,
              reachable: !!stepTarget(i + 1),
              locked: !stepTarget(i + 1) && i + 1 !== currentStep
            }"
            :aria-disabled="!stepTarget(i + 1) || i + 1 === currentStep"
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
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './LanguageSwitcher.vue'
import { setProjectTitle } from '../utils/pageTitle'
import { getProjectProgress, renameProject } from '../api/graph'

const props = defineProps({
  currentStep: { type: Number, required: true },
  status: { type: String, default: 'processing' }, // processing | completed | ready | error
  statusText: { type: String, default: '' },
  // Split-pane switcher. Pass layout=false on views without a graph panel.
  layout: { type: Boolean, default: true },
  modelValue: { type: String, default: 'split' },
  projectId: { type: [String, Number], default: null },
  simulationId: { type: [String, Number], default: null },
  reportId: { type: [String, Number], default: null },
  // Shown next to the wordmark so a step page says which campaign it belongs to.
  projectName: { type: String, default: '' }
})

const emit = defineEmits(['update:modelValue', 'readonly', 'renamed'])

const router = useRouter()
const { t, tm } = useI18n()

const stepNames = computed(() => tm('main.stepNames'))

// `tm` hands back raw messages, which are only guaranteed to be strings for
// rendering. Anything interpolated into another message goes through `t` with
// an indexed path so it cannot arrive as a compiled message function.
const stepName = (step) => t(`main.stepNames[${step - 1}]`)

const layoutLabels = computed(() => ({
  graph: t('main.layoutGraph'),
  split: t('main.layoutSplit'),
  workbench: t('main.layoutWorkbench')
}))

// How far this project actually got, asked of the server. A view only holds
// the ids its own route was given - step 1 has neither a simulation nor a
// report id - so without this a returning user finds every later step greyed
// out even though the run finished days ago.
const progress = ref(null)

watch(
  () => props.projectId,
  async (projectId) => {
    progress.value = null
    if (!projectId) return
    try {
      const response = await getProjectProgress(projectId)
      progress.value = response?.data || null
    } catch {
      // Navigation is a convenience; if this call fails the stepper falls
      // back to the ids this view already has rather than breaking the page.
      progress.value = null
    }
  },
  { immediate: true }
)

const routeNames = {
  1: 'Process',
  2: 'Simulation',
  3: 'SimulationRun',
  4: 'Report',
  5: 'Interaction'
}

const paramNames = {
  1: 'projectId',
  2: 'simulationId',
  3: 'simulationId',
  4: 'reportId',
  5: 'reportId'
}

// Fallback for the moment before the fetch lands, and if it fails: the ids
// this view was handed. Never more permissive than the server's answer.
const localRouteId = (step) => {
  switch (step) {
    case 1: return props.projectId
    case 2:
    case 3: return props.simulationId
    case 4:
    case 5: return props.reportId
    default: return null
  }
}

// A step opens only when the project reached it *and* its route has an id.
// Both halves matter: a step that never started has no id, and an id alone
// does not prove the step was ever begun.
const stepTarget = (step) => {
  const serverStep = progress.value?.steps?.find((s) => s.step === step)
  const routeId = serverStep ? serverStep.route_id : localRouteId(step)
  if (serverStep && !serverStep.reachable) return null
  if (!routeId) return null
  return { name: routeNames[step], params: { [paramNames[step]]: routeId } }
}

const goToStep = (step) => {
  const target = stepTarget(step)
  if (target && step !== props.currentStep) router.push(target)
}

// Why a step cannot be opened, said out loud. A dead click with no explanation
// reads as a broken button.
const stepHint = (step) => {
  if (step === props.currentStep) return t('a11y.stepCurrent')
  if (!stepTarget(step)) return t('a11y.stepNotReached')
  return stepName(step)
}

// The nearest earlier step this project can actually open. Walking down rather
// than assuming currentStep - 1 matters when an intermediate step never ran:
// its route has no id, so pushing to it lands on a page with nothing to load.
const backStep = computed(() => {
  for (let step = props.currentStep - 1; step >= 1; step -= 1) {
    if (stepTarget(step)) return step
  }
  return null
})

const backLabel = computed(() =>
  backStep.value
    ? t('nav.backToStep', { step: stepName(backStep.value) })
    : t('nav.projects')
)

// Falls through to the project list whenever no earlier step is open - which is
// also the state during the progress fetch and after it fails, so this control
// is never dead.
const goBack = () => {
  const target = backStep.value ? stepTarget(backStep.value) : null
  router.push(target || '/')
}

// Reopening a project lands on the step it stopped at; every other step is
// there to be read, not re-run. Without this, stepping back to an earlier tab
// offers buttons that would build a second simulation or restart a finished
// run. The header is the only place that knows both numbers, so it decides and
// the views act on it.
// Until the answer lands - and if the call fails - fail closed on every step
// past the first: those views only ever open an existing project, and the ids
// they need arrive over the network, so the seconds before `progress` resolves
// used to show a fully live setup that could re-prepare or relaunch a finished
// run. Step 1 stays open because a brand-new project has no id to ask about.
const readOnly = computed(() =>
  progress.value
    ? progress.value.editable_step !== props.currentStep
    : props.currentStep > 1
)

watch(readOnly, (value) => emit('readonly', value), { immediate: true })

// The header is the one component every step view mounts, so it is where the
// tab title learns which project it is looking at.
// Renaming is answered here rather than by each of the five step views: the
// header is the only component all of them mount, and the name is a label -
// nothing else on the page reads it. `localName` holds the saved value so the
// header does not wait on the parent to refetch the project.
const localName = ref('')
const editingName = ref(false)
const savingName = ref(false)
const draftName = ref('')
const nameInput = ref(null)

const displayName = computed(() => localName.value || props.projectName)

watch(() => props.projectName, () => { localName.value = '' })

const startRename = async () => {
  draftName.value = displayName.value
  editingName.value = true
  await nextTick()
  nameInput.value?.select()
}

const cancelRename = () => {
  editingName.value = false
}

const saveName = async () => {
  // `blur` also fires when Enter or Esc already closed the editor.
  if (!editingName.value || savingName.value) return
  const name = draftName.value.trim()
  if (!name || name === displayName.value) return cancelRename()

  savingName.value = true
  try {
    await renameProject(props.projectId, name)
    localName.value = name
    emit('renamed', name)
    editingName.value = false
  } catch {
    // The interceptor already surfaced the error; keep the editor open with
    // what was typed rather than silently dropping the rename.
  } finally {
    savingName.value = false
  }
}

watch(displayName, (name) => setProjectTitle(name), { immediate: true })
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
  gap: 12px;
  flex: 1;
}

.header-left {
  min-width: 0; /* lets .project-name ellipsis instead of pushing the row wide */
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-strong);
  background: var(--white);
  padding: 6px 12px;
  font-family: var(--font-sans);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.2s, border-color 0.2s;
}

.back-btn:hover {
  background: var(--surface-2);
  border-color: var(--ink);
}

.back-arrow {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1;
}

.header-divider {
  width: 1px;
  height: 18px;
  background: var(--border);
  flex-shrink: 0;
}

.project-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 4px 8px;
  font-family: var(--font-sans);
  text-align: left;
  transition: background 0.2s, border-color 0.2s;
}

.project-name:not(:disabled):hover {
  background: var(--surface-2);
  border-color: var(--border);
}

.project-name-input {
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  background: var(--white);
  border: 1px solid var(--accent);
  border-radius: 4px;
  padding: 4px 8px;
  min-width: 0;
  max-width: 260px;
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

.step-btn[aria-disabled="true"] {
  cursor: default;
}

/* A step the project never reached. Still focusable and still carries a
   tooltip - it just does not go anywhere. */
.step-btn.locked {
  opacity: 0.45;
}

.step-btn.reachable:not([aria-disabled="true"]):hover {
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

  /* The arrow alone still reads as back, and the accessible name survives on
     the aria-label. The project name is the first thing to go. */
  .back-label,
  .project-name {
    display: none;
  }

  .back-btn {
    padding: 6px 10px;
  }
}
</style>
