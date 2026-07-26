<template>
  <div class="home-container">
    <!-- Hero: dark, centered, deliberately sparse -->
    <section class="hero">
      <nav class="navbar">
        <div class="nav-brand">CAMPAIGN REACTION</div>
        <div class="nav-links">
          <LanguageSwitcher />
        </div>
      </nav>

      <div class="hero-body">
        <span class="eyebrow">{{ $t('home.tagline') }}</span>

        <h1 class="hero-title">
          {{ $t('home.heroTitle1') }}<br />
          <span class="hero-title-accent">{{ $t('home.heroTitle2') }}</span>
        </h1>

        <p class="hero-sub">{{ $t('home.heroSub') }}</p>

        <button class="cta" @click="scrollToConsole">
          {{ $t('home.ctaPrimary') }}
          <span class="cta-arrow">→</span>
        </button>

        <div class="stat-row">
          <div class="stat">
            <span class="stat-value">{{ $t('home.statCostValue') }}</span>
            <span class="stat-label">{{ $t('home.statCostLabel') }}</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ $t('home.statFeedsValue') }}</span>
            <span class="stat-label">{{ $t('home.statFeedsLabel') }}</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ $t('home.statKpiValue') }}</span>
            <span class="stat-label">{{ $t('home.statKpiLabel') }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Workflow: titles only, no descriptions -->
    <section class="workflow">
      <span class="section-label">{{ $t('home.workflowSequence') }}</span>
      <ol class="workflow-strip">
        <li v-for="(step, i) in workflowSteps" :key="step" class="workflow-step">
          <span class="workflow-num">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="workflow-title">{{ $t(step) }}</span>
        </li>
      </ol>
    </section>

    <!-- Console: the product surface -->
    <section class="console" ref="consoleRef">
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

    <!-- Past project database -->
    <HistoryDatabase />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import HistoryDatabase from '../components/HistoryDatabase.vue'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'

const router = useRouter()

// The workflow strip shows titles only; the long descriptions stay in the
// locale file for the in-run step headers.
const workflowSteps = [
  'home.step01Title',
  'home.step02Title',
  'home.step03Title',
  'home.step04Title',
  'home.step05Title'
]

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

// The hero CTA hands off to the console rather than acting on its own
const scrollToConsole = () => {
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
  --black: #000000;
  --white: #FFFFFF;
  --accent: #FF4500;
  --ink: #111111;
  --muted: #6B6B6B;
  --muted-dark: rgba(255, 255, 255, 0.55);
  --border: #E5E5E5;
  --border-dark: rgba(255, 255, 255, 0.14);
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;

  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--ink);
}

/* ---------- Hero ---------- */
.hero {
  background: var(--black);
  color: var(--white);
  padding-bottom: 96px;
}

.navbar {
  height: 72px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
  max-width: 1200px;
  margin: 0 auto;
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

/* The language switcher ships with light-surface styling; the hero is dark. */
.navbar :deep(.switcher-trigger) {
  color: var(--white);
  border-color: var(--border-dark);
}

.navbar :deep(.switcher-trigger:hover) {
  border-color: rgba(255, 255, 255, 0.4);
}

.hero-body {
  max-width: 860px;
  margin: 0 auto;
  padding: 88px 40px 0;
  text-align: center;
}

.eyebrow {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--muted-dark);
}

.hero-title {
  font-size: clamp(2.75rem, 6vw, 4.5rem);
  line-height: 1.05;
  letter-spacing: -2.5px;
  font-weight: 500;
  margin: 24px 0 0;
}

.hero-title-accent {
  color: var(--muted-dark);
}

.hero-sub {
  font-size: 1.25rem;
  line-height: 1.55;
  color: var(--muted-dark);
  max-width: 560px;
  margin: 26px auto 0;
}

.cta {
  margin-top: 42px;
  background: var(--white);
  color: var(--black);
  border: none;
  padding: 17px 32px;
  font-family: var(--font-sans);
  font-size: 1.05rem;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  transition: transform 0.2s ease, background 0.2s ease;
}

.cta:hover {
  background: var(--accent);
  color: var(--white);
  transform: translateY(-2px);
}

.cta-arrow {
  font-family: var(--font-mono);
}

.stat-row {
  display: flex;
  justify-content: center;
  gap: 56px;
  margin-top: 72px;
  padding-top: 32px;
  border-top: 1px solid var(--border-dark);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-value {
  font-family: var(--font-mono);
  font-size: 1.7rem;
  font-weight: 500;
}

.stat-label {
  font-size: 0.88rem;
  color: var(--muted-dark);
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
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  border-top: 1px solid var(--border);
}

.workflow-step {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 32px 28px 40px 0;
}

.workflow-num {
  font-family: var(--font-mono);
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--accent);
}

.workflow-title {
  font-size: 1.35rem;
  font-weight: 500;
  letter-spacing: -0.5px;
  line-height: 1.25;
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
  border: 1px dashed #D4D4D4;
  height: 170px;
  overflow-y: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: border-color 0.2s ease, background 0.2s ease;
  background: #FAFAFA;
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
  color: #9A9A9A;
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
  border: 1px solid #EEE;
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
  color: #9A9A9A;
  line-height: 1;
}

.remove-btn:hover {
  color: var(--accent);
}

.audience-input {
  width: 100%;
  border: 1px solid #DDD;
  background: #FAFAFA;
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
  background: var(--black);
  color: var(--white);
  border: 1px solid var(--black);
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
  background: var(--accent);
  border-color: var(--accent);
  transform: translateY(-2px);
}

.submit-btn:disabled {
  background: #F0F0F0;
  border-color: #E5E5E5;
  color: #A8A8A8;
  cursor: not-allowed;
}

.btn-arrow {
  font-family: var(--font-mono);
}

/* ---------- Responsive ---------- */
@media (max-width: 720px) {
  .hero-body {
    padding-top: 56px;
  }

  .stat-row {
    gap: 28px;
    margin-top: 48px;
  }

  .workflow,
  .console {
    padding-left: 24px;
    padding-right: 24px;
  }
}
</style>
