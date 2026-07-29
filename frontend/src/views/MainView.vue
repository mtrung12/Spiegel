<template>
  <div class="main-view">
    <AppHeader
      :currentStep="1"
      :status="statusClass"
      :statusText="statusText"
      v-model="viewMode"
      :projectId="currentProjectId === 'new' ? null : currentProjectId"
      :simulationId="null"
      :reportId="null"
    />

    <!-- Failure banner: without it a failed project is a dead end on screen -->
    <div v-if="error" class="error-banner" role="alert">
      <span class="error-text">{{ error }}</span>
      <button v-if="canRetry" class="error-retry" :disabled="retrying" @click="retryProject">
        {{ retrying ? $t('common.loading') : $t('main.retryBuild') }}
      </button>
      <button class="error-back" @click="router.push('/')">{{ $t('main.backToProjects') }}</button>
    </div>

    <!-- A stale graph looks identical to a fresh one, so a failed refresh has to
         say so rather than only reaching the console. -->
    <div v-if="graphError" class="warn-banner" role="status">
      <span class="warn-text">{{ graphError }}</span>
      <button class="warn-retry" @click="refreshGraph">{{ $t('common.retry') }}</button>
    </div>

    <!-- Main Content Area -->
    <main id="main-content" class="content-area" :data-mode="viewMode">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left">
        <GraphPanel
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="currentPhase"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right panel: step 1, graph build. Later steps are separate routes;
           this view only ever renders step 1. -->
      <div class="panel-wrapper right">
        <Step1GraphBuild
          :currentPhase="currentPhase"
          :projectData="projectData"
          :ontologyProgress="ontologyProgress"
          :buildProgress="buildProgress"
          :graphData="graphData"
          :systemLogs="systemLogs"
          :stopping="stoppingBuild"
          @stop-build="handleStopBuild"
        />
      </div>
    </main>

    <ConfirmDialog
      :open="confirmingStop"
      destructive
      :title="$t('step1.stopBuild')"
      :message="$t('step1.stopConfirm')"
      :confirmLabel="$t('step1.stopBuild')"
      @confirm="doStopBuild"
      @cancel="confirmingStop = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import GraphPanel from '../components/GraphPanel.vue'
import Step1GraphBuild from '../components/Step1GraphBuild.vue'
import { generateOntology, getProject, buildGraph, getTaskStatus, getGraphData, resetProject, cancelTask } from '../api/graph'
import { getPendingUpload, clearPendingUpload } from '../store/pendingUpload'
import AppHeader from '../components/AppHeader.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { useSplitLayout, useSystemLog } from '../composables/useWorkbench'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const { viewMode, toggleMaximize } = useSplitLayout('split')
const { systemLogs, addLog } = useSystemLog(100)

// Data State
const currentProjectId = ref(route.params.projectId)
const loading = ref(false)
const graphLoading = ref(false)
const error = ref('')
// A refresh failure is recoverable and must not replace the whole view the way
// `error` does, so it gets its own dismissible notice.
const graphError = ref('')
const retrying = ref(false)
const projectData = ref(null)
const graphData = ref(null)
const currentPhase = ref(-1) // -1: Upload, 0: Ontology, 1: Build, 2: Complete
const ontologyProgress = ref(null)
const buildProgress = ref(null)
// The build task currently being polled, so it can be stopped.
const buildTaskId = ref(null)
const stoppingBuild = ref(false)

// Polling timers
let pollTimer = null
let graphPollTimer = null

// --- Status Computed ---
const statusClass = computed(() => {
  if (error.value) return 'error'
  if (currentPhase.value >= 2) return 'completed'
  return 'processing'
})

const statusText = computed(() => {
  if (error.value) return t('main.statusError')
  if (currentPhase.value >= 2) return t('main.statusReady')
  if (currentPhase.value === 1) return t('main.statusBuildingGraph')
  if (currentPhase.value === 0) return t('main.statusGeneratingOntology')
  return t('main.statusInitializing')
})

// --- Data Logic ---

const initProject = async () => {
  addLog('Project view initialized.')
  if (currentProjectId.value === 'new') {
    await handleNewProject()
  } else {
    await loadProject()
  }
}

const handleNewProject = async () => {
  const pending = getPendingUpload()
  if (!pending.isPending || pending.files.length === 0) {
    error.value = 'No pending files found.'
    addLog('Error: No pending files found for new project.')
    return
  }
  
  try {
    loading.value = true
    currentPhase.value = 0
    ontologyProgress.value = { message: 'Uploading and analyzing docs...' }
    addLog('Starting ontology generation: Uploading files...')
    
    const formData = new FormData()
    pending.files.forEach(f => formData.append('files', f))

    const res = await generateOntology(formData)
    if (res.success) {
      clearPendingUpload()
      currentProjectId.value = res.data.project_id
      projectData.value = res.data
      
      router.replace({ name: 'Process', params: { projectId: res.data.project_id } })
      ontologyProgress.value = null
      addLog(`Ontology generated successfully for project ${res.data.project_id}`)
      await startBuildGraph()
    } else {
      error.value = res.error || 'Ontology generation failed'
      addLog(`Error generating ontology: ${error.value}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in handleNewProject: ${err.message}`)
  } finally {
    loading.value = false
  }
}

const loadProject = async () => {
  try {
    loading.value = true
    addLog(`Loading project ${currentProjectId.value}...`)
    const res = await getProject(currentProjectId.value)
    if (res.success) {
      projectData.value = res.data
      updatePhaseByStatus(res.data.status)
      addLog(`Project loaded. Status: ${res.data.status}`)
      
      if (res.data.status === 'ontology_generated' && !res.data.graph_id) {
        await startBuildGraph()
      } else if (res.data.status === 'graph_building' && res.data.graph_build_task_id) {
        currentPhase.value = 1
        startPollingTask(res.data.graph_build_task_id)
      } else if (res.data.status === 'graph_completed' && res.data.graph_id) {
        currentPhase.value = 2
        await loadGraph(res.data.graph_id)
      }
    } else {
      error.value = res.error
      addLog(`Error loading project: ${res.error}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in loadProject: ${err.message}`)
  } finally {
    loading.value = false
  }
}

const updatePhaseByStatus = (status) => {
  switch (status) {
    case 'created':
    case 'ontology_generated': currentPhase.value = 0; break;
    case 'graph_building': currentPhase.value = 1; break;
    case 'graph_completed': currentPhase.value = 2; break;
    case 'failed': error.value = projectData.value?.error || t('main.projectFailed'); break;
  }
}

// Reset only rewinds to the last good state, so it can only replay the graph
// build. A project that never produced an ontology has nothing to rebuild from.
const canRetry = computed(() =>
  currentProjectId.value !== 'new' && !!projectData.value?.ontology
)

const retryProject = async () => {
  if (retrying.value) return
  retrying.value = true
  try {
    addLog(`Resetting project ${currentProjectId.value}...`)
    await resetProject(currentProjectId.value)
    error.value = ''
    await loadProject()
  } catch (err) {
    error.value = err.message
    addLog(`Reset failed: ${err.message}`)
  } finally {
    retrying.value = false
  }
}

const startBuildGraph = async () => {
  try {
    currentPhase.value = 1
    buildProgress.value = { progress: 0, message: 'Starting build...' }
    addLog('Initiating graph build...')
    
    const res = await buildGraph({ project_id: currentProjectId.value })
    if (res.success) {
      if (res.data.reused && res.data.graph_id) {
        currentPhase.value = 2
        buildProgress.value = null
        const projectRes = await getProject(currentProjectId.value)
        if (projectRes.success) {
          projectData.value = projectRes.data
        }
        await loadGraph(res.data.graph_id)
        return
      }

      addLog(`Graph build task started. Task ID: ${res.data.task_id}`)
      startPollingTask(res.data.task_id)
    } else {
      error.value = res.error
      addLog(`Error starting build: ${res.error}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in startBuildGraph: ${err.message}`)
  }
}

const startGraphPolling = () => {
  addLog('Started polling for graph data...')
  fetchGraphData()
  graphPollTimer = setInterval(fetchGraphData, 10000)
}

const fetchGraphData = async () => {
  try {
    // Refresh project info to check for graph_id
    const projRes = await getProject(currentProjectId.value)
    if (projRes.success && projRes.data.graph_id) {
      const gRes = await getGraphData(projRes.data.graph_id)
      if (gRes.success) {
        graphData.value = gRes.data
        graphError.value = ''
        const nodeCount = gRes.data.node_count || gRes.data.nodes?.length || 0
        const edgeCount = gRes.data.edge_count || gRes.data.edges?.length || 0
        addLog(`Graph data refreshed. Nodes: ${nodeCount}, Edges: ${edgeCount}`)
      } else {
        graphError.value = t('main.graphRefreshFailed', { error: gRes.error || t('common.unknownError') })
      }
    }
  } catch (err) {
    // A stale graph is indistinguishable from a current one on screen, so the
    // failure has to reach the user and not just the console.
    graphError.value = t('main.graphRefreshFailed', { error: err.message })
    addLog(`Graph fetch error: ${err.message}`)
  }
}

const startPollingTask = (taskId) => {
  buildTaskId.value = taskId
  pollTaskStatus(taskId)
  pollTimer = setInterval(() => pollTaskStatus(taskId), 2000)
}

// Ask the backend to stop the build. Cancellation is cooperative, so the
// pipeline halts at its next checkpoint - keep polling until it reports back.
const confirmingStop = ref(false)

const handleStopBuild = () => {
  if (!buildTaskId.value || stoppingBuild.value) return
  confirmingStop.value = true
}

const doStopBuild = async () => {
  confirmingStop.value = false
  if (!buildTaskId.value || stoppingBuild.value) return

  stoppingBuild.value = true
  addLog('Stop requested for the graph build...')

  try {
    const res = await cancelTask(buildTaskId.value)
    if (!res.success) {
      addLog(`Stop failed: ${res.error}`)
      stoppingBuild.value = false
    }
  } catch (err) {
    addLog(`Stop failed: ${err.message}`)
    stoppingBuild.value = false
  }
}

const pollTaskStatus = async (taskId) => {
  try {
    const res = await getTaskStatus(taskId)
    if (res.success) {
      const task = res.data
      
      // Log progress message if it changed
      if (task.message && task.message !== buildProgress.value?.message) {
        addLog(task.message)
      }
      
      buildProgress.value = {
        progress: task.progress || 0,
        message: task.message,
        detail: task.progress_detail || {},
        stopping: stoppingBuild.value || task.cancel_requested === true
      }

      if (task.status === 'completed') {
        addLog('Graph build task completed.')
        stopPolling()
        stopGraphPolling() // Stop polling, do final load
        currentPhase.value = 2

        // Final load
        const projRes = await getProject(currentProjectId.value)
        if (projRes.success && projRes.data.graph_id) {
            projectData.value = projRes.data
            await loadGraph(projRes.data.graph_id)
        }
      } else if (task.status === 'cancelled') {
        stopPolling()
        stopGraphPolling()
        stoppingBuild.value = false
        buildProgress.value = null
        addLog(t('step1.buildStopped'))
        // The backend leaves a stopped build in the same recoverable state as a
        // failed one, so reload and let the existing retry path take over.
        await loadProject()
      } else if (task.status === 'failed') {
        stopPolling()
        stoppingBuild.value = false
        buildProgress.value = null
        error.value = task.error
        addLog(`Graph build task failed: ${task.error}`)
      }
    }
  } catch (e) {
    console.error(e)
  }
}

const loadGraph = async (graphId) => {
  graphLoading.value = true
  addLog(`Loading full graph data: ${graphId}`)
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      graphError.value = ''
      addLog('Graph data loaded successfully.')
    } else {
      graphError.value = t('main.graphLoadFailed', { error: res.error || t('common.unknownError') })
      addLog(`Failed to load graph data: ${res.error}`)
    }
  } catch (e) {
    graphError.value = t('main.graphLoadFailed', { error: e.message })
    addLog(`Exception loading graph: ${e.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) {
    addLog('Manual graph refresh triggered.')
    loadGraph(projectData.value.graph_id)
  }
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const stopGraphPolling = () => {
  if (graphPollTimer) {
    clearInterval(graphPollTimer)
    graphPollTimer = null
    addLog('Graph polling stopped.')
  }
}

onMounted(() => {
  initProject()
})

onUnmounted(() => {
  stopPolling()
  stopGraphPolling()
})
</script>

<style scoped>
/* The shell (.main-view / .content-area / .panel-wrapper) lives in App.vue. */

/* Failure banner */
.error-banner,
.warn-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 24px;
  font-size: 14px;
  flex-wrap: wrap;
}

.error-banner {
  background: #FEF2F2;
  border-bottom: 1px solid #FECACA;
  color: #B91C1C;
}

.warn-banner {
  background: #FFF7ED;
  border-bottom: 1px solid #FDBA74;
  color: #9A3412;
}

.error-text,
.warn-text {
  flex: 1;
  min-width: 200px;
}

.error-retry,
.error-back,
.warn-retry {
  flex-shrink: 0;
  border: 1px solid currentColor;
  background: none;
  padding: 7px 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: inherit;
}

.error-retry {
  background: #B91C1C;
  color: var(--white);
}

.error-retry:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-back {
  color: var(--muted);
  border-color: var(--border);
}
</style>
