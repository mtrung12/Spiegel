<template>
  <div
    class="history-database"
    :class="{ 'no-projects': projects.length === 0 && !loading }"
  >
    <!-- Heading. Left-aligned on the same measure as the page title above it;
         the centred label between two fading rules was decoration that put this
         section on a different axis from every other one. -->
    <div class="section-header">
      <span class="section-title">{{ $t('history.title') }}</span>
      <span v-if="projects.length > 0" class="section-count">{{ projects.length }}</span>
    </div>

    <!-- Card grid, shown only when projects exist -->
    <div v-if="projects.length > 0" class="cards-grid">
      <!-- The first rectangle is the way in to a new project. It sits in the
           same deck as the existing ones so "start something new" reads as a
           peer of "open something old", rather than a control somewhere else
           on the page. -->
      <button
        type="button"
        class="project-card new-project-card"
        @click="emit('create-new')"
      >
        <span class="new-card-plus" aria-hidden="true">+</span>
        <span class="new-card-title">{{ $t('history.newProject') }}</span>
        <span class="new-card-hint">{{ $t('history.newProjectHint') }}</span>
      </button>

      <button
        v-for="project in projects"
        :key="project.project_id"
        type="button"
        class="project-card"
        @click="navigateToProject(project)"
      >
        <!-- Card header: what the project is called, and which stages it
             reached. This used to be a truncated project id, which is the one
             thing about a project the user never chose and cannot recognise. -->
        <div class="card-header">
          <span class="card-name">{{ getProjectTitle(project) }}</span>
          <!-- Three segments, filled left to right, rather than the ◇ ◈ ◆
               glyphs: how far a project got is a quantity, and a meter shows it
               at a glance where three different diamonds did not. -->
          <div class="card-stages">
            <span
              class="stage"
              :class="{ done: !!project.graph_id }"
              :title="$t('history.graphBuild')"
            ></span>
            <span
              class="stage"
              :class="{ done: !!project.simulation_id }"
              :title="$t('history.envSetup')"
            ></span>
            <span
              class="stage"
              :class="{ done: !!project.report_id }"
              :title="$t('history.analysisReport')"
            ></span>
          </div>
        </div>

        <!-- File list -->
        <div class="card-files-wrapper">
          <div class="files-list" v-if="project.files && project.files.length > 0">
            <div
              v-for="(file, fileIndex) in project.files.slice(0, 3)"
              :key="fileIndex"
              class="file-item"
            >
              <span class="file-tag" :class="getFileType(file.filename)">{{ getFileTypeLabel(file.filename) }}</span>
              <span class="file-name">{{ truncateFilename(file.filename, 20) }}</span>
            </div>
            <!-- Note when more files exist -->
            <div v-if="project.files.length > 3" class="files-more">
              {{ $t('history.moreFiles', { count: project.files.length - 3 }) }}
            </div>
          </div>
          <!-- Placeholder when there are no files -->
          <div class="files-empty" v-else>
            <span class="empty-file-text">{{ $t('history.noFiles') }}</span>
          </div>
        </div>

        <!-- The name lives in the header now, so what used to be a second copy
             of it here is gone; the requirement below is what it summarised. -->
        <!-- Card description: the full simulation requirement -->
        <span class="card-desc">{{ truncateText(project.simulation_requirement, 90) }}</span>

        <!-- Card footer -->
        <div class="card-footer">
          <div class="card-datetime">
            <span class="card-date">{{ formatDate(project.created_at) }}</span>
            <span class="card-time">{{ formatTime(project.created_at) }}</span>
          </div>
          <span class="card-progress" :class="getProgressClass(project)">
            <span class="status-dot" aria-hidden="true">●</span> {{ formatRounds(project) }}
          </span>
        </div>
      </button>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <span class="loading-spinner"></span>
      <span class="loading-text">{{ $t('history.loadingText') }}</span>
    </div>

    <!-- Nothing to show: say why, and offer the way forward -->
    <div v-else-if="projects.length === 0" class="empty-state">
      <span class="empty-icon">◇</span>
      <p class="empty-title">
        {{ loadFailed ? $t('history.emptyErrorTitle') : $t('history.emptyTitle') }}
      </p>
      <p class="empty-hint">
        {{ loadFailed ? $t('history.emptyErrorHint') : $t('history.emptyHint') }}
      </p>
      <button v-if="loadFailed" class="empty-action" @click="loadHistory">
        {{ $t('history.retry') }}
      </button>
      <button v-else class="empty-action" @click="emit('create-new')">
        {{ $t('history.emptyAction') }}
      </button>
    </div>

    <!-- Replay detail dialog. A native <dialog> already renders in the top
         layer, so it needs no Teleport and no hand-written focus trap. -->
    <AppDialog :open="!!selectedProject" size="lg" @close="closeModal">
      <div v-if="selectedProject" class="modal-content">
            <!-- Dialog header -->
            <div class="modal-header">
              <div class="modal-title-section">
                <h2 class="modal-name">{{ getProjectTitle(selectedProject, 60) }}</h2>
                <span class="modal-progress" :class="getProgressClass(selectedProject)">
                  <span class="status-dot" aria-hidden="true">●</span> {{ formatRounds(selectedProject) }}
                </span>
                <span class="modal-agents">{{ formatAgents(selectedProject) }}</span>
                <span class="modal-create-time">{{ formatDate(selectedProject.created_at) }} {{ formatTime(selectedProject.created_at) }}</span>
              </div>
              <button class="modal-close" :aria-label="$t('common.close')" @click="closeModal">
                <span aria-hidden="true">×</span>
              </button>
            </div>

            <!-- Dialog body -->
            <div class="modal-body">
              <!-- Simulation requirement: a summary, not the full pasted brief -->
              <div class="modal-section">
                <div class="modal-label">{{ $t('history.simRequirement') }}</div>
                <div class="modal-requirement">{{ truncateText(selectedProject.simulation_requirement, 160) || $t('common.none') }}</div>
              </div>

              <!-- Files -->
              <div class="modal-section">
                <div class="modal-label">{{ $t('history.relatedFiles') }}</div>
                <div class="modal-files" v-if="selectedProject.files && selectedProject.files.length > 0">
                  <div v-for="(file, index) in selectedProject.files" :key="index" class="modal-file-item">
                    <span class="file-tag" :class="getFileType(file.filename)">{{ getFileTypeLabel(file.filename) }}</span>
                    <span class="modal-file-name">{{ file.filename }}</span>
                  </div>
                </div>
                <div class="modal-empty" v-else>{{ $t('history.noRelatedFiles') }}</div>
              </div>
            </div>

            <!-- One way in. The step it lands on is the furthest the project
                 reached, so a finished project opens on the last step and an
                 unfinished one opens where it stopped. Every step is still
                 reachable once inside, through the stepper. -->
            <button class="modal-continue" @click="goToContinue">
              <span class="continue-text">{{ $t('history.openButton') }}</span>
              <span class="continue-stage">{{ $t(`history.stage.${selectedProject.stage || 'upload'}`) }}</span>
            </button>

            <div class="modal-footer-actions">
              <button class="delete-btn" :disabled="deleting" @click="requestDelete">
                {{ deleting ? $t('common.loading') : $t('history.deleteButton') }}
              </button>
            </div>
            <p v-if="deleteError" class="modal-delete-error" role="alert">{{ deleteError }}</p>
      </div>
    </AppDialog>

    <ConfirmDialog
      :open="confirmingDelete"
      destructive
      :title="$t('history.deleteButton')"
      :message="selectedProject ? $t('history.deleteConfirm', { name: getProjectTitle(selectedProject) }) : ''"
      :confirmLabel="$t('history.deleteButton')"
      @confirm="handleDelete"
      @cancel="confirmingDelete = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, onActivated, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { listProjects, deleteProject } from '../api/graph'
import AppDialog from './AppDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const emit = defineEmits(['create-new', 'loaded'])

// State
const projects = ref([])
const loading = ref(true)
const loadFailed = ref(false)
const selectedProject = ref(null)  // The project the dialog is showing
const deleting = ref(false)
const deleteError = ref('')

// Card placement used to be computed here: absolute transforms that fanned the
// deck out at rotated angles, plus an IntersectionObserver, a debounce timer
// and an animation lock that flipped between the fan and a grid as the section
// scrolled past. It is a plain CSS grid now - the layout is the browser's job,
// the cards hold still, and the state machine that kept them in step is gone.

// Style class for the round progress
const getProgressClass = (simulation) => {
  const current = simulation.current_round || 0
  const total = simulation.total_rounds || 0
  
  if (total === 0 || current === 0) {
    // Not started
    return 'not-started'
  } else if (current >= total) {
    // Finished
    return 'completed'
  } else {
    // In progress
    return 'in-progress'
  }
}

// Format the date only
const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    // Use local date components (like formatTime below) so the displayed day
    // matches the displayed time. toISOString() converts to UTC and can show
    // the wrong day for non-UTC clients near midnight.
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
  } catch {
    return dateStr?.slice(0, 10) || ''
  }
}

// Format the time as hh:mm
const formatTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    const hours = date.getHours().toString().padStart(2, '0')
    const minutes = date.getMinutes().toString().padStart(2, '0')
    return `${hours}:${minutes}`
  } catch {
    return ''
  }
}

// Truncate text
const truncateText = (text, maxLength) => {
  if (!text) return ''
  return text.length > maxLength ? text.slice(0, maxLength) + '...' : text
}

// What to call this project on screen. Projects created before the name field
// existed carry the "Unnamed Project" placeholder, which says less than the
// first words of their brief, so those fall back to the requirement.
const getProjectTitle = (project, maxLength = 24) => {
  const name = (project.name || '').trim()
  if (name && name !== 'Unnamed Project') return truncateText(name, maxLength)
  const requirement = project.simulation_requirement || ''
  if (!requirement) return t('history.untitledSimulation')
  return truncateText(requirement, maxLength - 4)
}

// Format the round display as current/total
const formatRounds = (simulation) => {
  const current = simulation.current_round || 0
  const total = simulation.total_rounds || 0
  if (total === 0) return t('history.notStarted')
  return t('history.roundsProgress', { current, total })
}

// Agent count is null until env setup generates agent_configs
const formatAgents = (simulation) => {
  const count = simulation.total_agents
  if (count === null || count === undefined) return t('history.agentsPending')
  return t('history.agentsCount', { count })
}

// File type, used for styling
const getFileType = (filename) => {
  if (!filename) return 'other'
  const ext = filename.split('.').pop()?.toLowerCase()
  const typeMap = {
    'pdf': 'pdf',
    'doc': 'doc', 'docx': 'doc',
    'xls': 'xls', 'xlsx': 'xls', 'csv': 'xls',
    'ppt': 'ppt', 'pptx': 'ppt',
    'txt': 'txt', 'md': 'txt', 'json': 'code',
    'jpg': 'img', 'jpeg': 'img', 'png': 'img', 'gif': 'img',
    'zip': 'zip', 'rar': 'zip', '7z': 'zip'
  }
  return typeMap[ext] || 'other'
}

// Label text for a file type
const getFileTypeLabel = (filename) => {
  if (!filename) return 'FILE'
  const ext = filename.split('.').pop()?.toUpperCase()
  return ext || 'FILE'
}

// Truncate a file name, keeping the extension
const truncateFilename = (filename, maxLength) => {
  if (!filename) return t('history.unknownFile')
  if (filename.length <= maxLength) return filename
  
  const ext = filename.includes('.') ? '.' + filename.split('.').pop() : ''
  const nameWithoutExt = filename.slice(0, filename.length - ext.length)
  const truncatedName = nameWithoutExt.slice(0, maxLength - ext.length - 3) + '...'
  return truncatedName + ext
}

// Open the project detail dialog
const navigateToProject = (project) => {
  selectedProject.value = project
  deleteError.value = ''
}

// Close the dialog
const closeModal = () => {
  selectedProject.value = null
  deleteError.value = ''
}

// Deleting is irreversible, so it goes through a confirmation step.
const confirmingDelete = ref(false)

const requestDelete = () => {
  if (!selectedProject.value || deleting.value) return
  confirmingDelete.value = true
}

// Delete the project, its graph and its simulations. The backend refuses with
// 409 while a simulation is still running, so surface that instead of hiding it.
const handleDelete = async () => {
  const project = selectedProject.value
  confirmingDelete.value = false
  if (!project || deleting.value) return

  deleting.value = true
  deleteError.value = ''
  try {
    await deleteProject(project.project_id)
    closeModal()
    await loadHistory()
  } catch (error) {
    deleteError.value = error.message || t('history.deleteFailed')
  } finally {
    deleting.value = false
  }
}

// Where the project stopped, as a route. The backend decides how far it got -
// the same furthest_step the stepper gates on - so the button and the tabs
// cannot disagree. A finished project lands on the last step; an unfinished one
// lands where it stopped. Any step whose id is missing falls back to the
// project page, which is always loadable.
const continueRoute = (project) => {
  if (!project) return null
  const toProcess = { name: 'Process', params: { projectId: project.project_id } }

  switch (project.furthest_step) {
    case 5:
      return project.report_id
        ? { name: 'Interaction', params: { reportId: project.report_id } }
        : toProcess
    case 4:
      return project.report_id
        ? { name: 'Report', params: { reportId: project.report_id } }
        : toProcess
    case 3:
      return project.simulation_id
        ? { name: 'SimulationRun', params: { simulationId: project.simulation_id } }
        : toProcess
    case 2:
      return project.simulation_id
        ? { name: 'Simulation', params: { simulationId: project.simulation_id } }
        : toProcess
    default:
      return toProcess
  }
}

const goToContinue = () => {
  const target = continueRoute(selectedProject.value)
  if (target) {
    router.push(target)
    closeModal()
  }
}

// Load the past projects. An empty list and an unreachable backend look the
// same on screen otherwise, so track which one happened.
const loadHistory = async () => {
  try {
    loading.value = true
    loadFailed.value = false
    const response = await listProjects(20)
    if (response.success) {
      projects.value = response.data || []
    } else {
      loadFailed.value = true
    }
  } catch (error) {
    console.error('failed to load the past projects:', error)
    projects.value = []
    loadFailed.value = true
  } finally {
    loading.value = false
    // The list itself, not just its length: the home page derives the default
    // name for the next project from the names already taken.
    emit('loaded', projects.value)
  }
}

// Watch the route and reload when the user comes back to the home page
watch(() => route.path, (newPath) => {
  if (newPath === '/') {
    loadHistory()
  }
})

onMounted(loadHistory)

// Under keep-alive, reload the data when the component is activated
onActivated(() => {
  loadHistory()
})
</script>

<style scoped>
/* Container. Shares the page measure and gutter with the title above it, so
   the first card lines up with the "h1" rather than sitting on its own axis. */
.history-database {
  position: relative;
  width: 100%;
  max-width: var(--page-max);
  margin: var(--space-7) auto 0;
  padding: 0 var(--page-pad);
}

/* Simplified layout when there are no projects */
.history-database.no-projects {
  min-height: auto;
}

/* Heading */
.section-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--border);
}

.section-title {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--muted);
  letter-spacing: 1.5px;
  text-transform: uppercase;
}

.section-count {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--muted-soft);
  background: var(--surface-2);
  border-radius: 999px;
  padding: 2px var(--space-2);
  line-height: 1.5;
}

/* Card grid. The column count follows the width instead of being pinned to
   four, so a narrow window reflows rather than clipping. */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-4);
  align-items: stretch;
}

/* Project card */
.project-card {
  /* Now a <button>, so the inherited control styling has to be reset. */
  font: inherit;
  text-align: left;
  appearance: none;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 236px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  cursor: pointer;
  box-shadow: var(--shadow-1);
  transition: box-shadow var(--dur) var(--ease), border-color var(--dur) var(--ease),
    transform var(--dur) var(--ease);
  /* The deck deals itself in on load; the per-card delay is set below. */
  animation: fx-rise 380ms var(--ease) both;
}

.project-card:hover {
  box-shadow: var(--shadow-2);
  border-color: var(--border-strong);
  /* A card is a target you click, not a control you type into, so a small
     lift is honest feedback rather than a moving hit area. */
  transform: translateY(-2px);
}

/* Accent rule that wipes across the top edge of the card under the cursor. */
.project-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--accent), var(--accent-strong));
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 300ms var(--ease);
}

.project-card:hover::before {
  transform: scaleX(1);
}

/* Deal order. Capped at eight so a long history does not spend two seconds
   filling in; everything past the eighth card arrives with it. */
.cards-grid > .project-card:nth-child(1) { animation-delay: 0ms; }
.cards-grid > .project-card:nth-child(2) { animation-delay: 45ms; }
.cards-grid > .project-card:nth-child(3) { animation-delay: 90ms; }
.cards-grid > .project-card:nth-child(4) { animation-delay: 135ms; }
.cards-grid > .project-card:nth-child(5) { animation-delay: 180ms; }
.cards-grid > .project-card:nth-child(6) { animation-delay: 225ms; }
.cards-grid > .project-card:nth-child(7) { animation-delay: 270ms; }
.cards-grid > .project-card:nth-child(n + 8) { animation-delay: 315ms; }

/* New-project card: same footprint as a project card, but empty inside, so it
   reads as the slot a project has not been put in yet. */
.new-project-card {
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  text-align: center;
  border-style: dashed;
  border-color: var(--border-strong);
  background: var(--surface);
  color: var(--muted);
  box-shadow: none;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease),
    color var(--dur) var(--ease);
}

.new-project-card:hover {
  border-style: dashed;
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: none;
}

/* The empty slot answers the cursor with the plus turning a quarter of the way
   into a cross - a small promise that the click opens something. */
.new-project-card:hover .new-card-plus {
  transform: rotate(90deg) scale(1.1);
}

.new-card-plus {
  transition: transform 320ms var(--ease);
  font-family: var(--font-mono);
  font-size: 1.75rem;
  line-height: 1;
  font-weight: 300;
}

.new-card-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink);
  transition: color var(--dur) var(--ease);
}

.new-project-card:hover .new-card-title {
  color: var(--accent);
}

.new-card-hint {
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--muted-soft);
  max-width: 200px;
}

/* Card header */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--border-soft);
  gap: var(--space-3);
}

.card-name {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
  /* The header is a fixed row shared with the stage meter, so a long name
     ellipsises rather than pushing it off the card. */
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color var(--dur) var(--ease);
}

.project-card:hover .card-name {
  color: var(--accent);
}

/* Stage meter: three segments, filled for each stage the project reached */
.card-stages {
  display: flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
}

.stage {
  width: 14px;
  height: 3px;
  border-radius: 999px;
  background: var(--border-strong);
  cursor: default;
  transition: transform 260ms var(--ease), background var(--dur) var(--ease);
  transform-origin: left;
}

.stage.done {
  background: var(--accent);
}

/* Hovering the card runs a light along the segments the project completed,
   left to right, in the order it did them. */
.project-card:hover .stage.done {
  transform: scaleX(1.12);
}

.project-card:hover .stage.done:nth-child(2) { transition-delay: 60ms; }
.project-card:hover .stage.done:nth-child(3) { transition-delay: 120ms; }

/* Round progress */
.card-progress {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 0.3px;
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.status-dot {
  font-size: 0.6rem;
  line-height: 1;
}

/* Only a project that is actually mid-run blinks. A finished or unstarted one
   holds still, so motion in this grid always means the same thing. */
.card-progress.in-progress .status-dot {
  animation: dot-pulse 1.6s ease-in-out infinite;
}

@keyframes dot-pulse {
  50% { opacity: 0.3; }
}

/* Progress state colours, as tinted chips rather than bare coloured text -
   at 12px the colour alone was doing too much work. */
.card-progress.completed { color: var(--success); background: var(--success-soft); }
.card-progress.in-progress { color: var(--warning); background: var(--warning-soft); }
.card-progress.not-started { color: var(--muted-soft); background: var(--surface-2); }

/* File list */
/* No max-height: the template already caps this at three files plus the
   "+n more" line, and the old 106px ceiling cut that last line in half. */
.card-files-wrapper {
  position: relative;
  width: 100%;
  min-height: 44px;
  margin-bottom: var(--space-3);
  padding: var(--space-2);
  background: var(--surface);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
}

.files-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

/* More-files note */
.files-more {
  padding: 2px var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--muted-soft);
  letter-spacing: 0.3px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  background: var(--white);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-sm);
}

/* Minimal file tag styling */
.file-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 16px;
  padding: 0 var(--space-1);
  border-radius: 2px;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 600;
  line-height: 1;
  text-transform: uppercase;
  letter-spacing: 0.2px;
  flex-shrink: 0;
  min-width: 28px;
}

/* Low-saturation palette, Morandi tones */
.file-tag.pdf { background: #f2e6e6; color: #a65a5a; }
.file-tag.doc { background: #e6eff5; color: #5a7ea6; }
.file-tag.xls { background: #e6f2e8; color: #5aa668; }
.file-tag.ppt { background: #f5efe6; color: #a6815a; }
.file-tag.txt { background: var(--surface-3); color: var(--muted-soft); }
.file-tag.code { background: #eae6f2; color: #815aa6; }
.file-tag.img { background: #e6f2f2; color: #5aa6a6; }
.file-tag.zip { background: #f2f0e6; color: #a69b5a; }
.file-tag.other { background: var(--surface-2); color: var(--muted); }

.file-name {
  font-size: var(--text-xs);
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Placeholder when there are no files */
.files-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 44px;
  color: var(--muted-soft);
}

.empty-file-text {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 0.5px;
}

/* Card description. Clamped rather than given a fixed height: the cards are
   grid items now, so the row equalises them without a magic pixel value. */
.card-desc {
  font-size: var(--text-xs);
  color: var(--muted);
  margin: 0 0 var(--space-3);
  line-height: 1.55;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

/* Card footer. Pushed to the bottom of the card so the date sits on one line
   across the whole row, whatever the description above it did. */
.card-footer {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-soft);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--muted-soft);
}

/* Date and time pair */
.card-datetime {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* Empty and loading states */
.empty-state,
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-8) var(--space-5);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--muted-soft);
}

.empty-icon {
  font-size: 1.75rem;
  color: var(--muted-soft);
  opacity: 0.6;
}

.empty-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink-2);
}

.empty-hint {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--muted-soft);
  text-align: center;
  max-width: 48ch;
}

.empty-action {
  margin-top: var(--space-1);
  border: 1px solid var(--border-strong);
  background: var(--white);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-5);
  font-family: inherit;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--ink-2);
  cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.empty-action:hover {
  background: var(--surface-2);
  border-color: var(--ink);
}

.loading-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border);
  border-top-color: var(--muted);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.loading-text {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 0.5px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Responsive: one column below the point where two 260px cards plus the gutter
   stop fitting. The grid handles the rest. */
@media (max-width: 620px) {
  .cards-grid {
    grid-template-columns: 1fr;
  }
}

/* ===== Replay detail dialog =====
   The overlay, sizing and enter/leave transitions used to live here; the
   native <dialog> in AppDialog now provides the backdrop and the top layer. */
.modal-content {
  background: var(--white);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
  padding: var(--space-5) var(--space-6);
  border-bottom: 1px solid var(--border);
  background: var(--white);
}

/* Wraps rather than overflowing: the name, the progress chip, the agent count
   and the timestamp all sat on one unbreakable row. */
.modal-title-section {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  min-width: 0;
}

.modal-name {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
  margin: 0;
  overflow-wrap: anywhere;
}

.modal-progress {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
}

.modal-progress.completed { color: var(--success); background: var(--success-soft); }
.modal-progress.in-progress { color: var(--warning); background: var(--warning-soft); }
.modal-progress.not-started { color: var(--muted-soft); background: var(--surface-2); }

.modal-agents,
.modal-create-time {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--muted-soft);
  letter-spacing: 0.3px;
}

.modal-close {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  font-size: 1.25rem;
  line-height: 1;
  color: var(--muted-soft);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
  border-radius: var(--radius-md);
}

.modal-close:hover {
  background: var(--surface-2);
  color: var(--ink);
}

/* Dialog body */
.modal-body {
  padding: var(--space-5) var(--space-6);
}

.modal-section {
  margin-bottom: var(--space-5);
}

.modal-section:last-child {
  margin-bottom: 0;
}

.modal-label {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  margin-bottom: var(--space-2);
  font-weight: 500;
}

.modal-requirement {
  font-size: var(--text-base);
  color: var(--ink-2);
  line-height: 1.6;
  padding: var(--space-4);
  background: var(--surface);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
}

.modal-files {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: 200px;
  overflow-y: auto;
  padding-right: var(--space-1);
}

/* Custom scrollbar */
.modal-files::-webkit-scrollbar {
  width: 4px;
}

.modal-files::-webkit-scrollbar-track {
  background: var(--surface-2);
  border-radius: 2px;
}

.modal-files::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 2px;
}

.modal-files::-webkit-scrollbar-thumb:hover {
  background: var(--muted-soft);
}

.modal-file-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}

.modal-file-item:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-1);
}

.modal-file-name {
  font-size: var(--text-sm);
  color: var(--ink-3);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-empty {
  font-size: var(--text-sm);
  color: var(--muted-soft);
  padding: var(--space-4);
  background: var(--surface);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  text-align: center;
}

/* Primary way out of the dialog */
.modal-continue {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  width: calc(100% - var(--space-6) * 2);
  margin: var(--space-4) var(--space-6) 0;
  padding: var(--space-4) var(--space-5);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  background: var(--accent);
  color: var(--white);
  cursor: pointer;
  box-shadow: var(--shadow-1);
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease),
    box-shadow var(--dur) var(--ease);
}

.modal-continue:hover {
  background: var(--accent-strong);
  border-color: var(--accent-strong);
  box-shadow: var(--shadow-2);
}

.continue-text {
  font-size: var(--text-md);
  font-weight: 500;
}

.continue-stage {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 1px;
  text-transform: uppercase;
  opacity: 0.85;
}

.modal-footer-actions {
  display: flex;
  justify-content: flex-end;
  padding: var(--space-4) var(--space-6) var(--space-5);
  background: var(--white);
}

.delete-btn {
  flex-shrink: 0;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 0.5px;
  color: var(--muted-soft);
  cursor: pointer;
  transition: color var(--dur) var(--ease), border-color var(--dur) var(--ease),
    background var(--dur) var(--ease);
}

.delete-btn:hover:not(:disabled) {
  color: var(--danger);
  border-color: var(--danger);
  background: rgba(220, 38, 38, 0.06);
}

.delete-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.modal-delete-error {
  margin: 0;
  padding: 0 var(--space-6) var(--space-5);
  background: var(--white);
  color: var(--danger);
  font-size: var(--text-sm);
}

</style>
