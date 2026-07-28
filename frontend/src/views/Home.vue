<template>
  <div class="home-container">
    <nav class="navbar">
      <div class="nav-brand">CAMPAIGN REACTION</div>
      <div class="nav-links">
        <LanguageSwitcher />
      </div>
    </nav>

    <!-- Landing on the project list: pick up existing work, or start new -->
    <section class="page-head">
      <div class="page-head-text">
        <h1 class="page-title">{{ $t('home.projectsTitle') }}</h1>
        <p class="page-sub">{{ $t('home.projectsSub') }}</p>
      </div>
      <button v-if="projectCount > 0" class="cta" @click="openConsole">
        {{ $t('home.ctaPrimary') }}
        <span class="cta-arrow">→</span>
      </button>
    </section>

    <!-- Existing projects -->
    <HistoryDatabase @create-new="openConsole" @loaded="projectCount = $event" />

    <!-- Console: create a new test. Hidden until the user asks for a new one,
         so the landing page is just the project list. -->
    <section v-if="showConsole" class="console" ref="consoleRef">
      <span class="section-label">{{ $t('home.consoleEyebrow') }}</span>

      <div class="console-box">
        <div class="field">
          <div class="field-head">
            <span class="field-label">{{ $t('home.realitySeed') }}</span>
            <span class="field-meta">{{ $t('home.supportedFormats') }}</span>
          </div>

          <div
            class="upload-zone"
            :class="{ 'drag-over': isDragOver, 'has-files': files.length > 0 }"
            @dragover.prevent="handleDragOver"
            @dragleave.prevent="handleDragLeave"
            @drop.prevent="handleDrop"
            @click="triggerFileInput"
          >
            <input
              ref="fileInput"
              type="file"
              multiple
              accept=".pdf,.md,.txt"
              @change="handleFileSelect"
              style="display: none"
              :disabled="loading"
            />

            <div v-if="files.length === 0" class="upload-placeholder">
              <div class="upload-icon">↑</div>
              <div class="upload-title">{{ $t('home.dragToUpload') }}</div>
              <div class="upload-hint">{{ $t('home.orBrowse') }}</div>
            </div>

            <div v-else class="file-list">
              <div v-for="(file, index) in files" :key="index" class="file-item">
                <span class="file-name">{{ file.name }}</span>
                <button @click.stop="removeFile(index)" class="remove-btn">×</button>
              </div>
            </div>
          </div>
        </div>

        <div class="field">
          <div class="field-head">
            <span class="field-label">{{ $t('home.simulationPrompt') }}</span>
          </div>
          <textarea
            v-model="formData.simulationRequirement"
            class="audience-input"
            :placeholder="$t('home.promptPlaceholder')"
            rows="4"
            :disabled="loading"
          ></textarea>
        </div>

        <button class="submit-btn" @click="startSimulation" :disabled="!canSubmit || loading">
          <span>{{ loading ? $t('home.initializing') : $t('home.startEngine') }}</span>
          <span class="btn-arrow">→</span>
        </button>
      </div>
    </section>

    <!-- What the engine does, kept below the working surface -->
    <section v-if="showConsole" class="workflow">
      <span class="section-label">{{ $t('home.workflowSequence') }}</span>
      <ol class="workflow-strip">
        <li v-for="(step, i) in workflowSteps" :key="step.title" class="workflow-step">
          <span class="workflow-num">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="workflow-title">{{ $t(step.title) }}</span>
          <span class="workflow-desc">{{ $t(step.desc) }}</span>
        </li>
      </ol>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import HistoryDatabase from '../components/HistoryDatabase.vue'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'

const router = useRouter()

// One entry per pipeline stage, matching the five in-run steps
const workflowSteps = [1, 2, 3, 4, 5].map(n => ({
  title: `home.step0${n}Title`,
  desc: `home.step0${n}Desc`
}))

// Form data
const formData = ref({
  simulationRequirement: ''
})

// File list
const files = ref([])

// State
const loading = ref(false)
const error = ref('')
const isDragOver = ref(false)
const showConsole = ref(false)
// -1 until the history list reports back, so the CTA does not flash before then
const projectCount = ref(-1)

// Refs
const fileInput = ref(null)
const consoleRef = ref(null)

// Computed: is the form submittable?
const canSubmit = computed(() => {
  return formData.value.simulationRequirement.trim() !== '' && files.value.length > 0
})

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
const openConsole = async () => {
  showConsole.value = true
  await nextTick()
  consoleRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Start the simulation: navigate immediately; the API call happens on the Process page
const startSimulation = () => {
  if (!canSubmit.value || loading.value) return

  // Stash the data waiting to be uploaded
  import('../store/pendingUpload.js').then(({ setPendingUpload }) => {
    setPendingUpload(files.value, formData.value.simulationRequirement)

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
  /* Palette: orange, white, black. Backgrounds stay white or near-white. */
  --white: #FFFFFF;
  --accent: #F97316;
  --accent-strong: #EA580C;
  --ink: #111111;
  --muted: #666666;
  --muted-soft: #999999;
  --surface: #FAFAFA;
  --border: #E5E5E5;
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;

  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--ink);
}

/* ---------- Header ---------- */
.navbar {
  height: 72px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
  max-width: 1200px;
  margin: 0 auto;
  border-bottom: 1px solid var(--border);
}

.nav-brand {
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 0.85rem;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 16px;
}

/* ---------- Page head: title plus the "start new" action ---------- */
.page-head {
  max-width: 1200px;
  margin: 0 auto;
  padding: 56px 40px 8px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  flex-wrap: wrap;
}

.page-title {
  font-size: clamp(1.8rem, 3.4vw, 2.6rem);
  line-height: 1.1;
  letter-spacing: -1px;
  font-weight: 500;
  margin: 0;
}

.page-sub {
  font-size: 1.02rem;
  line-height: 1.55;
  color: var(--muted);
  margin: 12px 0 0;
  max-width: 520px;
}

.cta {
  background: var(--accent);
  color: var(--white);
  border: none;
  padding: 15px 28px;
  font-family: var(--font-sans);
  font-size: 1rem;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  white-space: nowrap;
  transition: transform 0.2s ease, background 0.2s ease;
}

.cta:hover {
  background: var(--accent-strong);
  color: var(--white);
  transform: translateY(-2px);
}

.cta-arrow {
  font-family: var(--font-mono);
}

/* ---------- Shared section label ---------- */
.section-label {
  display: block;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 32px;
}

/* ---------- Workflow strip ---------- */
.workflow {
  max-width: 1200px;
  margin: 0 auto;
  padding: 96px 40px 0;
}

.workflow-strip {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  border-top: 1px solid var(--border);
}

.workflow-step {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 32px 28px 40px 0;
}

.workflow-num {
  font-family: var(--font-mono);
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--accent);
}

.workflow-title {
  font-size: 1.15rem;
  font-weight: 500;
  letter-spacing: -0.3px;
  line-height: 1.25;
}

.workflow-desc {
  font-size: 0.92rem;
  line-height: 1.55;
  color: var(--muted);
  padding-right: 12px;
}

/* ---------- Console ---------- */
.console {
  max-width: 760px;
  margin: 0 auto;
  padding: 80px 40px 96px;
}

.console-box {
  border: 1px solid var(--border);
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.field-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 14px;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  color: var(--muted);
}

.upload-zone {
  border: 1px dashed var(--border);
  height: 170px;
  overflow-y: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: border-color 0.2s ease, background 0.2s ease;
  background: var(--surface);
}

.upload-zone:hover,
.upload-zone.drag-over {
  border-color: var(--ink);
  background: #F5F5F5;
}

.upload-zone.has-files {
  align-items: flex-start;
  cursor: default;
}

.upload-placeholder {
  text-align: center;
}

.upload-icon {
  font-family: var(--font-mono);
  color: var(--muted);
  margin-bottom: 10px;
}

.upload-title {
  font-size: 1.1rem;
  font-weight: 500;
}

.upload-hint {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  color: var(--muted);
  margin-top: 6px;
}

.file-list {
  width: 100%;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--white);
  padding: 8px 12px;
  border: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 0.8rem;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.1rem;
  color: var(--muted);
  line-height: 1;
}

.remove-btn:hover {
  color: var(--accent);
}

.audience-input {
  width: 100%;
  border: 1px solid var(--border);
  background: var(--surface);
  padding: 18px;
  font-family: var(--font-sans);
  font-size: 1.02rem;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  color: var(--ink);
}

.audience-input:focus {
  border-color: var(--ink);
  background: var(--white);
}

.submit-btn {
  background: var(--accent);
  color: var(--white);
  border: 1px solid var(--accent);
  padding: 19px 26px;
  font-family: var(--font-sans);
  font-size: 1.05rem;
  font-weight: 500;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
}

.submit-btn:hover:not(:disabled) {
  background: var(--accent-strong);
  border-color: var(--accent-strong);
  transform: translateY(-2px);
}

.submit-btn:disabled {
  background: #F5F5F5;
  border-color: var(--border);
  color: #A3A3A3;
  cursor: not-allowed;
}

.btn-arrow {
  font-family: var(--font-mono);
}

/* ---------- Responsive ---------- */
@media (max-width: 720px) {
  .page-head {
    padding: 40px 24px 8px;
    align-items: flex-start;
  }

  .workflow,
  .console {
    padding-left: 24px;
    padding-right: 24px;
  }
}
</style>
