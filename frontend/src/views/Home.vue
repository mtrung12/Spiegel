<template>
  <!-- fx-ambient paints the drifting grid and the two accent blooms behind the
       page (see the motion layer in App.vue). Decoration only: the layers are
       fixed, unclickable, and sit under everything in this container. -->
  <div class="home-container fx-ambient">
    <!-- The rule under the header runs the full width of the window; only its
         contents are held to the page measure. Clipping the rule at 1200px was
         the tell that the header was a box rather than a bar. -->
    <nav class="navbar">
      <div class="navbar-inner">
        <!-- A button, like the wordmark on every other page. As a plain div it
             was the one place in the app where clicking the logo did nothing. -->
        <button type="button" class="nav-brand" @click="router.push('/')">
          <span class="sr-only">{{ $t('a11y.backToProjectList') }}</span>
          <span aria-hidden="true">SPIEGEL</span>
        </button>
        <div class="nav-links">
          <LanguageSwitcher />
        </div>
      </div>
    </nav>

    <main id="main-content">
    <!-- Landing on the project list: pick up existing work, or start new -->
    <section class="page-head">
      <div class="page-head-text">
        <h1 class="page-title fx-rise">{{ $t('home.projectsTitle') }}</h1>
        <p class="page-sub fx-rise fx-delay-1">{{ $t('home.projectsSub') }}</p>
      </div>
      <button v-if="projectCount > 0" class="cta fx-sheen fx-rise fx-delay-2" @click="openConsole">
        {{ $t('home.ctaPrimary') }}
        <span class="cta-arrow">→</span>
      </button>
    </section>

    <!-- Existing projects. The list comes back here as well as staying in the
         component, because the default project name is derived from it. -->
    <HistoryDatabase @create-new="openConsole" @loaded="existingProjects = $event" />

    <!-- What the engine does, kept below the working surface -->
    <section class="workflow">
      <span class="section-label">{{ $t('home.workflowSequence') }}</span>
      <ol class="workflow-strip">
        <li
          v-for="(step, i) in workflowSteps"
          :key="step.title"
          class="workflow-step fx-rise"
          :class="`fx-delay-${i + 1}`"
        >
          <span class="workflow-num">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="workflow-title">{{ $t(step.title) }}</span>
          <span class="workflow-desc">{{ $t(step.desc) }}</span>
        </li>
      </ol>
    </section>
    </main>

    <!-- Console: create a new test. It opens over the project list rather than
         extending it, so the page has one subject at a time - the projects that
         exist, or the one being made. -->
    <AppDialog :open="showConsole" size="md" @close="closeConsole">
      <div class="console">
        <div class="console-head">
          <h2 class="console-title">{{ $t('home.consoleEyebrow') }}</h2>
          <button
            type="button"
            class="console-close"
            :aria-label="$t('common.close')"
            @click="closeConsole"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <div class="console-box">
          <!-- Named here rather than left to a rename later: every step page and
               the project list show this, and "Unnamed Project" told the user
               nothing about which campaign they were looking at. Optional - the
               placeholder is what gets used when it is left blank. -->
          <div class="field">
            <div class="field-head">
              <label class="field-label" for="project-name">{{ $t('home.projectNameLabel') }}</label>
              <span class="field-meta">{{ $t('home.projectNameHint') }}</span>
            </div>
            <input
              id="project-name"
              v-model="projectName"
              type="text"
              class="name-input"
              maxlength="120"
              :placeholder="defaultProjectName"
              :disabled="loading"
            />
          </div>

          <div class="field">
            <div class="field-head">
              <span class="field-label">{{ $t('home.realitySeed') }}</span>
              <span class="field-meta">{{ $t('home.supportedFormats') }}</span>
            </div>

            <!-- Dropping is a mouse-only affordance; the Browse button is the
                 keyboard path to the same file picker. -->
            <div
              class="upload-zone"
              :class="{ 'drag-over': isDragOver, 'has-files': files.length > 0 }"
              @dragover.prevent="handleDragOver"
              @dragleave.prevent="handleDragLeave"
              @drop.prevent="handleDrop"
            >
              <input
                ref="fileInput"
                id="brief-files"
                type="file"
                multiple
                accept=".pdf,.md,.txt"
                @change="handleFileSelect"
                class="sr-only"
                :disabled="loading"
              />

              <div v-if="files.length === 0" class="upload-placeholder">
                <div class="upload-icon" aria-hidden="true">↑</div>
                <div class="upload-title">{{ $t('home.dragToUpload') }}</div>
                <button type="button" class="upload-browse" :disabled="loading" @click="triggerFileInput">
                  {{ $t('home.orBrowse') }}
                </button>
              </div>

              <div v-else class="file-list">
                <div v-for="(file, index) in files" :key="index" class="file-item">
                  <span class="file-name">{{ file.name }}</span>
                  <button
                    type="button"
                    class="remove-btn"
                    :aria-label="$t('home.removeFile', { name: file.name })"
                    @click.stop="removeFile(index)"
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                </div>
                <button type="button" class="upload-browse" :disabled="loading" @click="triggerFileInput">
                  {{ $t('home.addMoreFiles') }}
                </button>
              </div>
            </div>
          </div>

          <button class="submit-btn fx-sheen" @click="startSimulation" :disabled="!canSubmit || loading">
            <span>{{ loading ? $t('home.initializing') : $t('home.startEngine') }}</span>
            <span class="btn-arrow">→</span>
          </button>
        </div>
      </div>
    </AppDialog>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import AppDialog from '../components/AppDialog.vue'
import HistoryDatabase from '../components/HistoryDatabase.vue'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'

const router = useRouter()

// One entry per pipeline stage, matching the five in-run steps
const workflowSteps = [1, 2, 3, 4, 5].map(n => ({
  title: `home.step0${n}Title`,
  desc: `home.step0${n}Desc`
}))

// File list
const files = ref([])

// What to call this project. Blank means "use the placeholder".
const projectName = ref('')

// State
const loading = ref(false)
const error = ref('')
const isDragOver = ref(false)
const showConsole = ref(false)
// null until the history list reports back, so the CTA does not flash before then
const existingProjects = ref(null)

const projectCount = computed(() => (existingProjects.value ? existingProjects.value.length : -1))

// Default name: the next number in the Project_N series already on record.
// Only names of exactly that shape count, so a project someone titled
// "Q3 launch 2026" does not push the counter to 2027.
const defaultProjectName = computed(() => {
  const highest = (existingProjects.value || []).reduce((max, project) => {
    const match = /^Project_(\d+)$/i.exec((project.name || '').trim())
    return match ? Math.max(max, parseInt(match[1], 10)) : max
  }, 0)
  return `Project_${highest + 1}`
})

// Refs
const fileInput = ref(null)

// Computed: is the form submittable?
// The uploaded brief is the only input. The audience description lives inside
// it, so a separate box for it only invited the two to disagree.
const canSubmit = computed(() => files.value.length > 0)

// Open the file picker
const triggerFileInput = () => {
  if (!loading.value) {
    fileInput.value?.click()
  }
}

// Handle a file selection
const handleFileSelect = (event) => {
  const selectedFiles = Array.from(event.target.files)
  addFiles(selectedFiles)
}

// Drag and drop handling
const handleDragOver = (e) => {
  if (!loading.value) {
    isDragOver.value = true
  }
}

const handleDragLeave = (e) => {
  isDragOver.value = false
}

const handleDrop = (e) => {
  isDragOver.value = false
  if (loading.value) return

  const droppedFiles = Array.from(e.dataTransfer.files)
  addFiles(droppedFiles)
}

// Add files
const addFiles = (newFiles) => {
  const validFiles = newFiles.filter(file => {
    const ext = file.name.split('.').pop().toLowerCase()
    return ['pdf', 'md', 'txt'].includes(ext)
  })
  files.value.push(...validFiles)
}

// Remove a file
const removeFile = (index) => {
  files.value.splice(index, 1)
}

// The console only appears once the user asks to start a new test
const openConsole = () => {
  showConsole.value = true
}

// Closing discards nothing: the files and the name survive, so reopening picks
// up where the user left off.
const closeConsole = () => {
  showConsole.value = false
}

// Start the simulation: navigate immediately; the API call happens on the Process page
const startSimulation = () => {
  if (!canSubmit.value || loading.value) return

  // An empty box means the user accepted what the placeholder showed them, so
  // that is the name the project gets - not a blank one.
  const name = projectName.value.trim() || defaultProjectName.value

  // Stash the data waiting to be uploaded
  import('../store/pendingUpload.js').then(({ setPendingUpload }) => {
    setPendingUpload(files.value, name)

    // Navigate straight to the Process page, flagged as a new project
    router.push({
      name: 'Process',
      params: { projectId: 'new' }
    })
  })
}
</script>

<style scoped>
.home-container {
  /* Palette lives in :root (App.vue); this page used to redeclare it locally,
     which is how the rest of the app drifted away from it. */
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--ink);
  padding-bottom: var(--space-9);
}

/* Every top-level band shares one measure and one gutter. Each section used to
   repeat its own max-width and padding, and they had drifted apart. */
.navbar-inner,
.page-head,
.workflow {
  max-width: var(--page-max);
  margin: 0 auto;
  padding-left: var(--page-pad);
  padding-right: var(--page-pad);
}

/* ---------- Header ---------- */
.navbar {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: saturate(180%) blur(12px);
  border-bottom: 1px solid var(--border);
}

.navbar-inner {
  height: 64px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.nav-brand {
  position: relative;
  background: none;
  border: none;
  padding: var(--space-1) var(--space-2);
  margin-left: calc(var(--space-2) * -1);
  border-radius: var(--radius-sm);
  color: inherit;
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 1.5px;
  font-size: var(--text-sm);
  transition: color var(--dur) var(--ease);
}

.nav-brand:hover {
  color: var(--accent);
}

/* The wordmark is a link back to this page, so it now says so on hover with a
   rule that wipes in from the left rather than appearing all at once. */
.nav-brand::after {
  content: '';
  position: absolute;
  left: var(--space-2);
  right: var(--space-2);
  bottom: 2px;
  height: 1px;
  background: var(--accent);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform var(--dur) var(--ease);
}

.nav-brand:hover::after,
.nav-brand:focus-visible::after {
  transform: scaleX(1);
}

.nav-links {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

/* ---------- Page head: title plus the "start new" action ---------- */
.page-head {
  padding-top: var(--space-8);
  padding-bottom: var(--space-2);
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-6);
  flex-wrap: wrap;
}

.page-title {
  font-size: clamp(1.75rem, 3vw, 2.25rem);
  line-height: 1.15;
  letter-spacing: -0.02em;
  font-weight: 500;
  margin: 0;
}

.page-sub {
  font-size: var(--text-md);
  line-height: 1.6;
  color: var(--muted);
  margin: var(--space-3) 0 0;
  max-width: 56ch;
}

/* ---------- Buttons ----------
   Both primary actions used to lift 2px on hover. A control that moves out
   from under the cursor reads as a marketing page, not a tool: the feedback is
   now a colour and elevation change on a control that stays put. */
.cta,
.submit-btn {
  background: var(--accent);
  color: var(--white);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  font-family: var(--font-sans);
  font-weight: 500;
  cursor: pointer;
  box-shadow: var(--shadow-1);
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease),
    box-shadow var(--dur) var(--ease);
}

.cta:hover,
.submit-btn:hover:not(:disabled) {
  background: var(--accent-strong);
  border-color: var(--accent-strong);
  box-shadow: var(--shadow-2);
}

.cta:active,
.submit-btn:active:not(:disabled) {
  box-shadow: none;
}

.cta {
  padding: var(--space-3) var(--space-5);
  font-size: var(--text-base);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}

/* The arrow, not the button, is what moves on hover: the control keeps its
   position under the cursor while still answering it. */
.cta-arrow,
.btn-arrow {
  font-family: var(--font-mono);
  display: inline-block;
  transition: transform var(--dur) var(--ease);
}

.cta:hover .cta-arrow,
.submit-btn:hover:not(:disabled) .btn-arrow {
  transform: translateX(4px);
}

/* ---------- Shared section label ---------- */
.section-label {
  display: block;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: var(--space-5);
}

/* ---------- Workflow strip ---------- */
.workflow {
  padding-top: var(--space-9);
}

.workflow-strip {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  border-top: 1px solid var(--border);
}

/* A hairline between columns, suppressed on the first, so the strip reads as
   one ruled table rather than five floating blocks. */
.workflow-step {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-5) var(--space-5) var(--space-6);
  border-left: 1px solid var(--border-soft);
  transition: background var(--dur) var(--ease);
}

/* Pointing at a stage draws the accent along the rule above it, which is the
   same top border the strip already had - it just fills in. */
.workflow-step::before {
  content: '';
  position: absolute;
  top: -1px;
  left: -1px;
  right: 0;
  height: 2px;
  background: var(--accent);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 320ms var(--ease);
}

.workflow-step:hover::before {
  transform: scaleX(1);
}

.workflow-step:hover {
  background: linear-gradient(to bottom, var(--accent-soft), transparent 70%);
}

.workflow-step:first-child {
  border-left: none;
  padding-left: 0;
}

.workflow-num {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 1px;
  color: var(--accent);
}

.workflow-title {
  font-size: var(--text-md);
  font-weight: 500;
  letter-spacing: -0.01em;
  line-height: 1.3;
}

.workflow-desc {
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--muted);
}

/* ---------- Console (inside the dialog) ---------- */
.console-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-6);
  border-bottom: 1px solid var(--border);
}

.console-title {
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--muted);
}

.console-close {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  font-size: 1.25rem;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}

.console-close:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.console-box {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.field-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-4);
  margin-bottom: var(--space-3);
}

.field-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--ink-2);
}

.field-meta {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--muted-soft);
}

.name-input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--white);
  padding: var(--space-3) var(--space-4);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  color: var(--ink);
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}

.name-input::placeholder {
  color: var(--muted-soft);
}

/* A focused field is announced by an accent ring rather than only a darker
   border, which was hard to spot against the surrounding hairlines. */
.name-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.name-input:disabled {
  background: var(--surface-2);
  color: var(--muted-soft);
  cursor: not-allowed;
}

.upload-zone {
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-lg);
  flex-direction: column;
  height: 170px;
  overflow-y: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
  background: var(--surface);
}

.upload-zone:hover,
.upload-zone.drag-over {
  border-color: var(--accent);
  background: var(--accent-soft);
}

/* A file is over the zone: the hatching crawls, which is the difference
   between "this is a drop target" and "let go now". Only while dragging - the
   dialog is otherwise still. */
.upload-zone.drag-over {
  background-image: repeating-linear-gradient(
    45deg,
    rgba(249, 115, 22, 0.12) 0 8px,
    transparent 8px 16px
  );
  animation: stripe-crawl 700ms linear infinite;
}

@keyframes stripe-crawl {
  to { background-position: 22.6px 0; }
}

/* The arrow lifts towards the words that describe the drop. */
.upload-icon {
  transition: transform var(--dur) var(--ease), color var(--dur) var(--ease);
}

.upload-zone:hover .upload-icon {
  transform: translateY(-3px);
  color: var(--accent);
}

.upload-zone.has-files {
  align-items: flex-start;
}

.upload-placeholder {
  text-align: center;
  padding: var(--space-4);
}

.upload-icon {
  font-family: var(--font-mono);
  font-size: var(--text-lg);
  color: var(--muted);
  margin-bottom: var(--space-2);
}

.upload-title {
  font-size: var(--text-base);
  font-weight: 500;
}

.upload-browse {
  margin-top: var(--space-3);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--white);
  padding: var(--space-2) var(--space-4);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--ink);
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.upload-browse:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--ink);
}

.upload-browse:disabled {
  color: var(--muted-soft);
  cursor: not-allowed;
}

.file-list {
  width: 100%;
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* Each accepted file arrives rather than appearing, so a drop of several is
   readable as several. `fx-rise` is the shared entrance from App.vue. */
.file-item {
  animation: fx-rise 240ms var(--ease) both;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: var(--white);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-md);
  color: var(--muted);
  line-height: 1;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}

.remove-btn:hover {
  background: var(--surface-2);
  color: var(--danger);
}

.submit-btn {
  padding: var(--space-4) var(--space-5);
  font-size: var(--text-md);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.submit-btn:disabled {
  background: var(--surface-2);
  border-color: var(--border);
  color: var(--muted-soft);
  box-shadow: none;
  cursor: not-allowed;
}

/* ---------- Responsive ---------- */
@media (max-width: 720px) {
  .page-head {
    padding-top: var(--space-7);
    align-items: flex-start;
  }

  .workflow {
    padding-top: var(--space-8);
  }

  /* The hairline rules only read as columns while there are columns. */
  .workflow-step {
    border-left: none;
    padding-left: 0;
  }

  .console-head,
  .console-box {
    padding-left: var(--space-5);
    padding-right: var(--space-5);
  }
}
</style>
