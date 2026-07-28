<template>
  <div 
    class="history-database"
    :class="{ 'no-projects': projects.length === 0 && !loading }"
    ref="historyContainer"
  >
    <!-- Background decoration: technical grid, shown only when projects exist -->
    <div v-if="projects.length > 0 || loading" class="tech-grid-bg">
      <div class="grid-pattern"></div>
      <div class="gradient-overlay"></div>
    </div>

    <!-- Heading -->
    <div class="section-header">
      <div class="section-line"></div>
      <span class="section-title">{{ $t('history.title') }}</span>
      <div class="section-line"></div>
    </div>

    <!-- Card container, shown only when projects exist -->
    <div v-if="projects.length > 0" class="cards-container" :class="{ expanded: isExpanded }" :style="containerStyle">
      <div 
        v-for="(project, index) in projects"
        :key="project.project_id"
        class="project-card"
        :class="{ expanded: isExpanded, hovering: hoveringCard === index }"
        :style="getCardStyle(index)"
        @mouseenter="hoveringCard = index"
        @mouseleave="hoveringCard = null"
        @click="navigateToProject(project)"
      >
        <!-- Card header: project id and which stages the project reached -->
        <div class="card-header">
          <span class="card-id">{{ formatProjectId(project.project_id) }}</span>
          <div class="card-status-icons">
            <span
              class="status-icon"
              :class="{ available: project.graph_id, unavailable: !project.graph_id }"
              :title="$t('history.graphBuild')"
            >◇</span>
            <span
              class="status-icon"
              :class="{ available: project.simulation_id, unavailable: !project.simulation_id }"
              :title="$t('history.envSetup')"
            >◈</span>
            <span 
              class="status-icon" 
              :class="{ available: project.report_id, unavailable: !project.report_id }"
              :title="$t('history.analysisReport')"
            >◆</span>
          </div>
        </div>

        <!-- File list -->
        <div class="card-files-wrapper">
          <!-- Corner decoration, viewfinder style -->
          <div class="corner-mark top-left-only"></div>
          
          <!-- Files -->
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
            <span class="empty-file-icon">◇</span>
            <span class="empty-file-text">{{ $t('history.noFiles') }}</span>
          </div>
        </div>

        <!-- Card title: the project name, falling back to its requirement -->
        <h3 class="card-title">{{ getProjectTitle(project) }}</h3>

        <!-- Card description: the full simulation requirement -->
        <p class="card-desc">{{ truncateText(project.simulation_requirement, 55) }}</p>

        <!-- Card footer -->
        <div class="card-footer">
          <div class="card-datetime">
            <span class="card-date">{{ formatDate(project.created_at) }}</span>
            <span class="card-time">{{ formatTime(project.created_at) }}</span>
          </div>
          <span class="card-progress" :class="getProgressClass(project)">
            <span class="status-dot">●</span> {{ formatRounds(project) }}
          </span>
        </div>
        
        <!-- Footer rule, which grows on hover -->
        <div class="card-bottom-line"></div>
      </div>
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

    <!-- Replay detail dialog -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="selectedProject" class="modal-overlay" @click.self="closeModal">
          <div class="modal-content">
            <!-- Dialog header -->
            <div class="modal-header">
              <div class="modal-title-section">
                <span class="modal-id">{{ formatProjectId(selectedProject.project_id) }}</span>
                <span class="modal-progress" :class="getProgressClass(selectedProject)">
                  <span class="status-dot">●</span> {{ formatRounds(selectedProject) }}
                </span>
                <span class="modal-create-time">{{ formatDate(selectedProject.created_at) }} {{ formatTime(selectedProject.created_at) }}</span>
              </div>
              <button class="modal-close" @click="closeModal">×</button>
            </div>

            <!-- Dialog body -->
            <div class="modal-body">
              <!-- Simulation requirement -->
              <div class="modal-section">
                <div class="modal-label">{{ $t('history.simRequirement') }}</div>
                <div class="modal-requirement">{{ selectedProject.simulation_requirement || $t('common.none') }}</div>
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

            <!-- Primary action: pick the project up where it stopped -->
            <button class="modal-continue" @click="goToContinue">
              <span class="continue-text">{{ $t('history.continueButton') }}</span>
              <span class="continue-stage">{{ $t(`history.stage.${selectedProject.stage || 'upload'}`) }}</span>
            </button>

            <!-- Replay divider -->
            <div class="modal-divider">
              <span class="divider-line"></span>
              <span class="divider-text">{{ $t('history.replayTitle') }}</span>
              <span class="divider-line"></span>
            </div>

            <!-- Navigation buttons -->
            <div class="modal-actions">
              <button 
                class="modal-btn btn-project" 
                @click="goToProject"
                :disabled="!selectedProject.project_id"
              >
                <span class="btn-step">Step1</span>
                <span class="btn-icon">◇</span>
                <span class="btn-text">{{ $t('history.step1Button') }}</span>
              </button>
              <button
                class="modal-btn btn-simulation"
                @click="goToSimulation"
                :disabled="!selectedProject.simulation_id"
              >
                <span class="btn-step">Step2</span>
                <span class="btn-icon">◈</span>
                <span class="btn-text">{{ $t('history.step2Button') }}</span>
              </button>
              <button 
                class="modal-btn btn-report" 
                @click="goToReport"
                :disabled="!selectedProject.report_id"
              >
                <span class="btn-step">Step4</span>
                <span class="btn-icon">◆</span>
                <span class="btn-text">{{ $t('history.step4Button') }}</span>
              </button>
              <button
                class="modal-btn btn-workspace"
                @click="goToWorkspace"
                :disabled="!selectedProject.project_id"
              >
                <span class="btn-step">All</span>
                <span class="btn-icon">▣</span>
                <span class="btn-text">{{ $t('history.workspaceButton') }}</span>
              </button>
            </div>
            <!-- Notice when replay is unavailable -->
            <div class="modal-playback-hint">
              <span class="hint-text">{{ $t('history.replayHint') }}</span>
              <button class="delete-btn" :disabled="deleting" @click="handleDelete">
                {{ deleting ? $t('common.loading') : $t('history.deleteButton') }}
              </button>
            </div>
            <p v-if="deleteError" class="modal-delete-error">{{ deleteError }}</p>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { listProjects, deleteProject } from '../api/graph'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const emit = defineEmits(['create-new'])

// State
const projects = ref([])
const loading = ref(true)
const loadFailed = ref(false)
const isExpanded = ref(false)
const hoveringCard = ref(null)
const historyContainer = ref(null)
const selectedProject = ref(null)  // The project the dialog is showing
const deleting = ref(false)
const deleteError = ref('')
let observer = null
let isAnimating = false  // Animation lock, which stops the flicker
let expandDebounceTimer = null  // Debounce timer
let pendingState = null  // The target state still to apply

// Card layout, tuned to a wider aspect
const CARDS_PER_ROW = 4
const CARD_WIDTH = 280  
const CARD_HEIGHT = 280 
const CARD_GAP = 24

// Compute the container height
const containerStyle = computed(() => {
  if (!isExpanded.value) {
    // Collapsed: fixed height
    return { minHeight: '420px' }
  }
  
  // Expanded: height follows the card count
  const total = projects.value.length
  if (total === 0) {
    return { minHeight: '280px' }
  }
  
  const rows = Math.ceil(total / CARDS_PER_ROW)
  // rows * card height + (rows - 1) * gap + a little breathing room
  const expandedHeight = rows * CARD_HEIGHT + (rows - 1) * CARD_GAP + 10
  
  return { minHeight: `${expandedHeight}px` }
})

// Card styling
const getCardStyle = (index) => {
  const total = projects.value.length
  
  if (isExpanded.value) {
    // Expanded: grid layout
    const transition = 'transform 700ms cubic-bezier(0.23, 1, 0.32, 1), opacity 700ms cubic-bezier(0.23, 1, 0.32, 1), box-shadow 0.3s ease, border-color 0.3s ease'

    const col = index % CARDS_PER_ROW
    const row = Math.floor(index / CARDS_PER_ROW)
    
    // Count the cards on this row, so each row stays centred
    const currentRowStart = row * CARDS_PER_ROW
    const currentRowCards = Math.min(CARDS_PER_ROW, total - currentRowStart)
    
    const rowWidth = currentRowCards * CARD_WIDTH + (currentRowCards - 1) * CARD_GAP
    
    const startX = -(rowWidth / 2) + (CARD_WIDTH / 2)
    const colInRow = index % CARDS_PER_ROW
    const x = startX + colInRow * (CARD_WIDTH + CARD_GAP)
    
    // Expanding downwards, so add space below the heading
    const y = 20 + row * (CARD_HEIGHT + CARD_GAP)

    return {
      transform: `translate(${x}px, ${y}px) rotate(0deg) scale(1)`,
      zIndex: 100 + index,
      opacity: 1,
      transition: transition
    }
  } else {
    // Collapsed: fanned stack
    const transition = 'transform 700ms cubic-bezier(0.23, 1, 0.32, 1), opacity 700ms cubic-bezier(0.23, 1, 0.32, 1), box-shadow 0.3s ease, border-color 0.3s ease'

    const centerIndex = (total - 1) / 2
    const offset = index - centerIndex
    
    const x = offset * 35
    // Start close to the heading, but not touching it
    const y = 25 + Math.abs(offset) * 8
    const r = offset * 3
    const s = 0.95 - Math.abs(offset) * 0.05
    
    return {
      transform: `translate(${x}px, ${y}px) rotate(${r}deg) scale(${s})`,
      zIndex: 10 + index,
      opacity: 1,
      transition: transition
    }
  }
}

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

// Card title: the project name, or its requirement when the name is a placeholder
const getProjectTitle = (project) => {
  const name = (project.name || '').trim()
  if (name && name !== 'Unnamed Project') return truncateText(name, 24)
  const requirement = project.simulation_requirement || ''
  if (!requirement) return t('history.untitledSimulation')
  return truncateText(requirement, 20)
}

// Format project_id for display, first six characters
const formatProjectId = (projectId) => {
  if (!projectId) return 'PROJ_UNKNOWN'
  const prefix = projectId.replace('proj_', '').slice(0, 6)
  return `PROJ_${prefix.toUpperCase()}`
}

// Format the round display as current/total
const formatRounds = (simulation) => {
  const current = simulation.current_round || 0
  const total = simulation.total_rounds || 0
  if (total === 0) return t('history.notStarted')
  return t('history.roundsProgress', { current, total })
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

// Delete the project, its graph and its simulations. The backend refuses with
// 409 while a simulation is still running, so surface that instead of hiding it.
const handleDelete = async () => {
  const project = selectedProject.value
  if (!project || deleting.value) return
  if (!window.confirm(t('history.deleteConfirm', { name: getProjectTitle(project) }))) return

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

// Navigate to the graph build page (Project)
const goToProject = () => {
  if (selectedProject.value?.project_id) {
    router.push({
      name: 'Process',
      params: { projectId: selectedProject.value.project_id }
    })
    closeModal()
  }
}

// Navigate to the environment setup page (Simulation)
const goToSimulation = () => {
  if (selectedProject.value?.simulation_id) {
    router.push({
      name: 'Simulation',
      params: { simulationId: selectedProject.value.simulation_id }
    })
    closeModal()
  }
}

// Navigate to the analysis report page (Report)
const goToReport = () => {
  if (selectedProject.value?.report_id) {
    router.push({
      name: 'Report',
      params: { reportId: selectedProject.value.report_id }
    })
    closeModal()
  }
}

// Where the project stopped, as a route. The backend hands us the stage so the
// resume point is decided in one place instead of re-derived from loose fields.
const continueRoute = (project) => {
  if (!project) return null
  const toProcess = { name: 'Process', params: { projectId: project.project_id } }

  switch (project.stage) {
    case 'report':
      return project.report_id
        ? { name: 'Report', params: { reportId: project.report_id } }
        : toProcess
    case 'run':
      return project.simulation_id
        ? { name: 'SimulationRun', params: { simulationId: project.simulation_id } }
        : toProcess
    case 'simulation':
      return project.simulation_id
        ? { name: 'Simulation', params: { simulationId: project.simulation_id } }
        : toProcess
    default:
      // upload, graph, failed: the project page owns all three
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

// Navigate to the project workspace: reports plus follow-up chat in one place
const goToWorkspace = () => {
  if (selectedProject.value?.project_id) {
    router.push({
      name: 'Workspace',
      params: { projectId: selectedProject.value.project_id }
    })
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
  }
}

// Set up the IntersectionObserver
const initObserver = () => {
  if (observer) {
    observer.disconnect()
  }
  
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const shouldExpand = entry.isIntersecting
        
        // Record the latest target state, animating or not
        pendingState = shouldExpand
        
        // Clear the previous debounce timer; a newer scroll wins
        if (expandDebounceTimer) {
          clearTimeout(expandDebounceTimer)
          expandDebounceTimer = null
        }
        
        // Mid-animation: just record the state and handle it afterwards
        if (isAnimating) return
        
        // Target matches the current state: nothing to do
        if (shouldExpand === isExpanded.value) {
          pendingState = null
          return
        }
        
        // Debounce the switch, which stops rapid flicker.
        // Expanding is quick (50ms); collapsing waits longer (200ms) for stability.
        const delay = shouldExpand ? 50 : 200
        
        expandDebounceTimer = setTimeout(() => {
          // Still animating?
          if (isAnimating) return
          
          // Is the pending state still wanted, or did a later scroll override it?
          if (pendingState === null || pendingState === isExpanded.value) return
          
          // Take the animation lock
          isAnimating = true
          isExpanded.value = pendingState
          pendingState = null
          
          // Release the lock once the animation ends, then look for a pending change
          setTimeout(() => {
            isAnimating = false
            
            // Animation done: is there a newer target state?
            if (pendingState !== null && pendingState !== isExpanded.value) {
              // Wait a beat before applying it, to avoid whiplash
              expandDebounceTimer = setTimeout(() => {
                if (pendingState !== null && pendingState !== isExpanded.value) {
                  isAnimating = true
                  isExpanded.value = pendingState
                  pendingState = null
                  setTimeout(() => {
                    isAnimating = false
                  }, 750)
                }
              }, 100)
            }
          }, 750)
        }, delay)
      })
    },
    {
      // Several thresholds make the detection smoother
      threshold: [0.4, 0.6, 0.8],
      // rootMargin pulls the viewport bottom up, so expanding takes more scrolling
      rootMargin: '0px 0px -150px 0px'
    }
  )
  
  // Start observing
  if (historyContainer.value) {
    observer.observe(historyContainer.value)
  }
}

// Watch the route and reload when the user comes back to the home page
watch(() => route.path, (newPath) => {
  if (newPath === '/') {
    loadHistory()
  }
})

onMounted(async () => {
  // Load the data once the DOM has rendered
  await nextTick()
  await loadHistory()
  
  // Set the observer up after the DOM has rendered
  setTimeout(() => {
    initObserver()
  }, 100)
})

// Under keep-alive, reload the data when the component is activated
onActivated(() => {
  loadHistory()
})

onUnmounted(() => {
  // Tear the IntersectionObserver down
  if (observer) {
    observer.disconnect()
    observer = null
  }
  // Clear the debounce timer
  if (expandDebounceTimer) {
    clearTimeout(expandDebounceTimer)
    expandDebounceTimer = null
  }
})
</script>

<style scoped>
/* Container */
.history-database {
  position: relative;
  width: 100%;
  min-height: 280px;
  margin-top: 40px;
  padding: 35px 0 40px;
  overflow: visible;
}

/* Simplified layout when there are no projects */
.history-database.no-projects {
  min-height: auto;
  padding: 40px 0 20px;
}

/* Technical grid background */
.tech-grid-bg {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  overflow: hidden;
  pointer-events: none;
}

/* A CSS background pattern draws the evenly spaced square grid */
.grid-pattern {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-image: 
    linear-gradient(to right, rgba(0, 0, 0, 0.05) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(0, 0, 0, 0.05) 1px, transparent 1px);
  background-size: 50px 50px;
  /* Anchored top-left, so a height change only extends the bottom and leaves the existing grid in place */
  background-position: top left;
}

.gradient-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: 
    linear-gradient(to right, rgba(255, 255, 255, 0.9) 0%, transparent 15%, transparent 85%, rgba(255, 255, 255, 0.9) 100%),
    linear-gradient(to bottom, rgba(255, 255, 255, 0.8) 0%, transparent 20%, transparent 80%, rgba(255, 255, 255, 0.8) 100%);
  pointer-events: none;
}

/* Heading */
.section-header {
  position: relative;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  margin-bottom: 24px;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  padding: 0 40px;
}

.section-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, transparent, #E5E5E5, transparent);
  max-width: 300px;
}

.section-title {
  font-size: 0.8rem;
  font-weight: 500;
  color: #999999;
  letter-spacing: 3px;
  text-transform: uppercase;
}

/* Card container */
.cards-container {
  position: relative;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 0 40px;
  transition: min-height 700ms cubic-bezier(0.23, 1, 0.32, 1);
  /* min-height is computed in JS, from the card count */
}

/* Project card */
.project-card {
  position: absolute;
  width: 280px;
  background: #FFFFFF;
  border: 1px solid #E5E5E5;
  border-radius: 0;
  padding: 14px;
  cursor: pointer;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  transition: box-shadow 0.3s ease, border-color 0.3s ease, transform 700ms cubic-bezier(0.23, 1, 0.32, 1), opacity 700ms cubic-bezier(0.23, 1, 0.32, 1);
}

.project-card:hover {
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
  border-color: rgba(0, 0, 0, 0.4);
  z-index: 1000 !important;
}

.project-card.hovering {
  z-index: 1000 !important;
}

/* Card header */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #F5F5F5;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  font-size: 0.7rem;
}

.card-id {
  color: #666666;
  letter-spacing: 0.5px;
  font-weight: 500;
}

/* Feature status icons */
.card-status-icons {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-icon {
  font-size: 0.75rem;
  transition: all 0.2s ease;
  cursor: default;
}

.status-icon.available {
  opacity: 1;
}

/* One colour per feature */
.status-icon:nth-child(1).available { color: #111111; } /* graph build - blue */
.status-icon:nth-child(2).available { color: #F59E0B; } /* environment setup - orange */
.status-icon:nth-child(3).available { color: #10B981; } /* analysis report - green */

.status-icon.unavailable {
  color: #D4D4D4;
  opacity: 0.5;
}

/* Round progress */
.card-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.5px;
  font-weight: 600;
  font-size: 0.65rem;
}

.status-dot {
  font-size: 0.5rem;
}

/* Progress state colours */
.card-progress.completed { color: #10B981; }    /* finished - green */
.card-progress.in-progress { color: #F59E0B; }  /* in progress - orange */
.card-progress.not-started { color: #999999; }  /* not started - grey */
.card-status.pending { color: #999999; }

/* File list */
.card-files-wrapper {
  position: relative;
  width: 100%;
  min-height: 48px;
  max-height: 110px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: linear-gradient(135deg, #f8f9fa 0%, #f1f3f4 100%);
  border-radius: 4px;
  border: 1px solid #e8eaed;
  overflow: hidden;
}

.files-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* More-files note */
.files-more {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 3px 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  color: #666666;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 3px;
  letter-spacing: 0.3px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  background: rgba(255, 255, 255, 0.7);
  border-radius: 3px;
  transition: all 0.2s ease;
}

.file-item:hover {
  background: rgba(255, 255, 255, 1);
  transform: translateX(2px);
  border-color: #E5E5E5;
}

/* Minimal file tag styling */
.file-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 16px;
  padding: 0 4px;
  border-radius: 2px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.55rem;
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
.file-tag.txt { background: #f0f0f0; color: #757575; }
.file-tag.code { background: #eae6f2; color: #815aa6; }
.file-tag.img { background: #e6f2f2; color: #5aa6a6; }
.file-tag.zip { background: #f2f0e6; color: #a69b5a; }
.file-tag.other { background: #F5F5F5; color: #666666; }

.file-name {
  font-family: 'Inter', sans-serif;
  font-size: 0.7rem;
  color: #4D4D4D;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: 0.1px;
}

/* Placeholder when there are no files */
.files-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 48px;
  color: #999999;
}

.empty-file-icon {
  font-size: 1rem;
  opacity: 0.5;
}

.empty-file-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  letter-spacing: 0.5px;
}

/* File area on hover */
.project-card:hover .card-files-wrapper {
  border-color: #D4D4D4;
  background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
}

/* Corner decoration */
.corner-mark.top-left-only {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 8px;
  height: 8px;
  border-top: 1.5px solid rgba(0, 0, 0, 0.4);
  border-left: 1.5px solid rgba(0, 0, 0, 0.4);
  pointer-events: none;
  z-index: 10;
}

/* Card title */
.card-title {
  font-family: 'Inter', -apple-system, sans-serif;
  font-size: 0.9rem;
  font-weight: 700;
  color: #000000;
  margin: 0 0 6px 0;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color 0.3s ease;
}

.project-card:hover .card-title {
  color: #F97316;
}

/* Card description */
.card-desc {
  font-family: 'Inter', sans-serif;
  font-size: 0.75rem;
  color: #666666;
  margin: 0 0 16px 0;
  line-height: 1.5;
  height: 34px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* Card footer */
.card-footer {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 12px;
  border-top: 1px solid #F5F5F5;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem;
  color: #999999;
  font-weight: 500;
}

/* Date and time pair */
.card-datetime {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Round progress in the footer */
.card-footer .card-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.5px;
  font-weight: 600;
  font-size: 0.65rem;
}

.card-footer .status-dot {
  font-size: 0.5rem;
}

/* Progress state colours, footer */
.card-footer .card-progress.completed { color: #10B981; }
.card-footer .card-progress.in-progress { color: #F59E0B; }
.card-footer .card-progress.not-started { color: #999999; }

/* Footer rule */
.card-bottom-line {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 2px;
  width: 0;
  background-color: #000;
  transition: width 0.5s cubic-bezier(0.23, 1, 0.32, 1);
  z-index: 20;
}

.project-card:hover .card-bottom-line {
  width: 100%;
}

/* Empty state */
.empty-state, .loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 48px;
  color: #999999;
}

.empty-icon {
  font-size: 28px;
  color: #D4D4D4;
}

.empty-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #333333;
}

.empty-hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #999999;
  text-align: center;
  max-width: 420px;
}

.empty-action {
  margin-top: 4px;
  border: 1px solid #E5E5E5;
  background: #FFFFFF;
  border-radius: 6px;
  padding: 9px 20px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: #333333;
  cursor: pointer;
  transition: all 0.2s;
}

.empty-action:hover {
  background: #FAFAFA;
  border-color: #D4D4D4;
}

.empty-icon {
  font-size: 2rem;
  opacity: 0.5;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid #E5E5E5;
  border-top-color: #666666;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Responsive */
@media (max-width: 1200px) {
  .project-card {
    width: 240px;
  }
}

@media (max-width: 768px) {
  .cards-container {
    padding: 0 20px;
  }
  .project-card {
    width: 200px;
  }
}

/* ===== Replay detail dialog ===== */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  backdrop-filter: blur(4px);
}

.modal-content {
  background: #FFFFFF;
  width: 560px;
  max-width: 90vw;
  max-height: 85vh;
  overflow-y: auto;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
}

/* Transitions */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.3s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active .modal-content {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.modal-leave-active .modal-content {
  transition: all 0.2s ease-in;
}

.modal-enter-from .modal-content {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}

.modal-leave-to .modal-content {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}

/* Dialog header */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 32px;
  border-bottom: 1px solid #F5F5F5;
  background: #FFFFFF;
}

.modal-title-section {
  display: flex;
  align-items: center;
  gap: 16px;
}

.modal-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1rem;
  font-weight: 600;
  color: #000000;
  letter-spacing: 0.5px;
}

.modal-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
  background: #FAFAFA;
}

.modal-progress.completed { color: #10B981; background: rgba(16, 185, 129, 0.1); }
.modal-progress.in-progress { color: #F59E0B; background: rgba(245, 158, 11, 0.1); }
.modal-progress.not-started { color: #999999; background: #F5F5F5; }

.modal-create-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #999999;
  letter-spacing: 0.3px;
}

.modal-close {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  font-size: 1.5rem;
  color: #999999;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  border-radius: 6px;
}

.modal-close:hover {
  background: #F5F5F5;
  color: #000000;
}

/* Dialog body */
.modal-body {
  padding: 24px 32px;
}

.modal-section {
  margin-bottom: 24px;
}

.modal-section:last-child {
  margin-bottom: 0;
}

.modal-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #666666;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 10px;
  font-weight: 500;
}

.modal-requirement {
  font-size: 0.95rem;
  color: #333333;
  line-height: 1.6;
  padding: 16px;
  background: #FAFAFA;
  border: 1px solid #F5F5F5;
  border-radius: 8px;
}

.modal-files {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}

/* Custom scrollbar */
.modal-files::-webkit-scrollbar {
  width: 4px;
}

.modal-files::-webkit-scrollbar-track {
  background: #F5F5F5;
  border-radius: 2px;
}

.modal-files::-webkit-scrollbar-thumb {
  background: #D4D4D4;
  border-radius: 2px;
}

.modal-files::-webkit-scrollbar-thumb:hover {
  background: #999999;
}

.modal-file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: #FFFFFF;
  border: 1px solid #E5E5E5;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.modal-file-item:hover {
  border-color: #D4D4D4;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
}

.modal-file-name {
  font-size: 0.85rem;
  color: #4D4D4D;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-empty {
  font-size: 0.85rem;
  color: #999999;
  padding: 16px;
  background: #FAFAFA;
  border: 1px dashed #E5E5E5;
  border-radius: 6px;
  text-align: center;
}

/* Replay divider */
.modal-continue {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: calc(100% - 64px);
  margin: 16px 32px 0;
  padding: 16px 20px;
  border: none;
  background: #F97316;
  color: #FFFFFF;
  cursor: pointer;
  transition: background 0.2s ease;
}

.modal-continue:hover {
  background: #EA580C;
}

.continue-text {
  font-size: 1rem;
  font-weight: 500;
}

.continue-stage {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  letter-spacing: 1px;
  text-transform: uppercase;
  opacity: 0.85;
}

.modal-divider {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 32px 0;
  background: #FFFFFF;
}

.divider-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, transparent, #E5E5E5, transparent);
}

.divider-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #999999;
  letter-spacing: 2px;
  text-transform: uppercase;
  white-space: nowrap;
}

/* Navigation buttons */
.modal-actions {
  display: flex;
  gap: 16px;
  padding: 20px 32px;
  background: #FFFFFF;
}

.modal-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  background: #FFFFFF;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
  overflow: hidden;
}

.modal-btn:hover:not(:disabled) {
  border-color: #000000;
  transform: translateY(-2px);
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.modal-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: #FAFAFA;
}

.btn-step {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  font-weight: 500;
  color: #999999;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.btn-icon {
  font-size: 1.4rem;
  line-height: 1;
  transition: color 0.2s ease;
}

.btn-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #4D4D4D;
}

.modal-btn.btn-project .btn-icon { color: #111111; }
.modal-btn.btn-simulation .btn-icon { color: #F59E0B; }
.modal-btn.btn-report .btn-icon { color: #10B981; }
.modal-btn.btn-workspace .btn-icon { color: #6366F1; }

.modal-btn:hover:not(:disabled) .btn-text {
  color: #000000;
}

/* Replay-unavailable notice */
.modal-playback-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 32px 20px;
  background: #FFFFFF;
}

.delete-btn {
  flex-shrink: 0;
  background: none;
  border: 1px solid #E5E5E5;
  padding: 8px 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  letter-spacing: 0.5px;
  color: #999999;
  cursor: pointer;
  transition: color 0.2s ease, border-color 0.2s ease;
}

.delete-btn:hover:not(:disabled) {
  color: #DC2626;
  border-color: #DC2626;
}

.delete-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.modal-delete-error {
  margin: 0;
  padding: 0 32px 20px;
  background: #FFFFFF;
  color: #DC2626;
  font-size: 0.8rem;
}

.hint-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #999999;
  letter-spacing: 0.3px;
  text-align: center;
  line-height: 1.5;
}
</style>
