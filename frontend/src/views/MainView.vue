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
      :projectName="projectData?.name || ''"
      @readonly="readOnly = $event"
    />

    <!-- Failure banner: without it a failed project is a dead end on screen.
         The markup moved into AppBanner so steps 2-5 could get the same
         treatment instead of reporting failures only to the log. -->
    <AppBanner
      :message="error"
      :retryable="canRetry"
      :retryLabel="retryLabel"
      :busy="retrying"
      :dismissible="false"
      @retry="retryProject"
    >
      <template #actions>
        <button class="banner-back" @click="router.push('/')">{{ $t('main.backToProjects') }}</button>
      </template>
    </AppBanner>

    <!-- A stale graph looks identical to a fresh one, so a failed refresh has to
         say so rather than only reaching the console. -->
    <AppBanner
      :message="graphError"
      tone="warn"
      retryable
      @retry="refreshGraph"
      @dismiss="graphError = ''"
    />

    <!-- Main Content Area.
         data-layout="stacked": the brief reads as a centered document with the
         graph beneath it, rather than as a half-width sidebar competing with
         the graph for attention on first open. The panels keep their
         left/right classes so the graph/workbench view modes still collapse
         the right one; only the axis and the order change. -->
    <main id="main-content" class="content-area" :data-mode="viewMode" data-layout="stacked">
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
          :readOnly="readOnly"
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
import { generateOntology, retryOntology, getProject, buildGraph, getTaskStatus, getGraphData, resetProject, cancelTask } from '../api/graph'
import { getPendingUpload, clearPendingUpload } from '../store/pendingUpload'
import AppHeader from '../components/AppHeader.vue'
import AppBanner from '../components/AppBanner.vue'
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

// Set by the header once it knows how far this project got: true when this
// step is not where the project stopped, so the panel shows its result without
// offering to redo it.
const readOnly = ref(false)

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
    // Only reachable by opening /process/new directly - the upload path itself
    // now rewrites the URL to a real project id before anything slow runs.
    error.value = t('main.noPendingUpload')
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

    // Returns as soon as the files are stored; the ontology is still being
    // derived behind `task_id`. The URL is rewritten to the real project id
    // right here, so a reload from this point on reopens the project instead
    // of landing on /process/new with the file list gone.
    const res = await generateOntology(formData)
    if (res.success) {
      clearPendingUpload()
      currentProjectId.value = res.data.project_id
      router.replace({ name: 'Process', params: { projectId: res.data.project_id } })
      addLog(`Files uploaded. Project ${res.data.project_id} created.`)

      const projRes = await getProject(res.data.project_id)
      if (projRes.success) projectData.value = projRes.data

      await waitForOntology(res.data.task_id)
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

// Follows the background ontology task through to the graph build. Used both by
// a fresh upload and by a reload that landed on a project still generating.
let ontologyPollTimer = null

const stopOntologyPolling = () => {
  if (ontologyPollTimer) {
    clearInterval(ontologyPollTimer)
    ontologyPollTimer = null
  }
}

const waitForOntology = async (taskId) => {
  if (!taskId) return
  currentPhase.value = 0
  ontologyProgress.value = { message: t('main.statusGeneratingOntology') }

  // Tasks live in the backend's memory, so a restart mid-generation leaves an
  // id that no longer resolves. Give up after a few misses rather than polling
  // a task that will never answer - the retry button re-runs it from the
  // documents already on disk.
  let misses = 0
  const MAX_MISSES = 3

  const poll = async () => {
    try {
      const res = await getTaskStatus(taskId)
      if (!res.success) return
      misses = 0
      const task = res.data

      if (task.message && task.message !== ontologyProgress.value?.message) {
        ontologyProgress.value = { message: task.message }
        addLog(task.message)
      }

      if (task.status === 'completed') {
        stopOntologyPolling()
        ontologyProgress.value = null
        addLog(`Ontology generated for project ${currentProjectId.value}`)
        const projRes = await getProject(currentProjectId.value)
        if (projRes.success) projectData.value = projRes.data
        await startBuildGraph()
      } else if (task.status === 'failed') {
        stopOntologyPolling()
        ontologyProgress.value = null
        error.value = task.error || 'Ontology generation failed'
        addLog(`Ontology generation failed: ${error.value}`)
      }
    } catch (err) {
      misses += 1
      if (misses >= MAX_MISSES) {
        stopOntologyPolling()
        ontologyProgress.value = null
        error.value = t('main.ontologyTaskLost')
        addLog(`Ontology task ${taskId} is no longer available: ${err.message}`)
      }
    }
  }

  await poll()
  if (!ontologyPollTimer) {
    ontologyPollTimer = setInterval(poll, 2000)
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
      
      // A project still deriving its ontology. This is the state a reload used
      // to be unable to represent, because the id only existed inside the
      // request that was generating it.
      if (res.data.status === 'created' && res.data.ontology_task_id) {
        await waitForOntology(res.data.ontology_task_id)
      } else if (res.data.status === 'ontology_generated' && !res.data.graph_id) {
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

// Two different failures are recoverable here, and they need different repairs.
// A project that has an ontology can rewind and replay the graph build. One
// that never got that far - a failed or lost ontology task - has its documents
// on disk, so generation can simply be re-run. Only a project with neither is
// beyond a retry.
const canRetry = computed(() => {
  if (currentProjectId.value === 'new') return false
  if (projectData.value?.ontology) return true
  return !!projectData.value?.total_text_length
})

const retryLabel = computed(() =>
  projectData.value?.ontology ? t('main.retryBuild') : t('main.retryOntology')
)

const retryProject = async () => {
  if (retrying.value) return
  retrying.value = true
  try {
    if (projectData.value?.ontology) {
      addLog(`Resetting project ${currentProjectId.value}...`)
      await resetProject(currentProjectId.value)
      error.value = ''
      await loadProject()
    } else {
      addLog(`Re-running ontology generation for ${currentProjectId.value}...`)
      const res = await retryOntology(currentProjectId.value)
      error.value = ''
      if (res.success) {
        const projRes = await getProject(currentProjectId.value)
        if (projRes.success) projectData.value = projRes.data
        await waitForOntology(res.data.task_id)
      }
    }
  } catch (err) {
    error.value = err.message
    addLog(`Retry failed: ${err.message}`)
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
  stopOntologyPolling()
})
</script>

<style scoped>
/* The shell (.main-view / .content-area / .panel-wrapper) lives in App.vue. */

/* ---------------------------------------------------------------------------
   Stacked layout: brief on top as a centered column, graph beneath.
   Overrides the shell's side-by-side split for this view only. The rules below
   have to out-specify App.vue's [data-mode] widths, which the extra attribute
   selector plus the scoping attribute achieve.
   --------------------------------------------------------------------------- */
.content-area[data-layout="stacked"] {
  flex-direction: column;
  overflow-y: auto;
}

.content-area[data-layout="stacked"] .panel-wrapper {
  width: 100%;
  height: auto;
  opacity: 1;
  transform: none;
  /* Width is fixed now, so animating it only causes reflow on mode switches. */
  transition: opacity 0.3s ease;
}

/* The brief is second in the DOM - the graph stays first so screen readers and
   the existing mode rules keep their order - so lift it visually. */
.content-area[data-layout="stacked"] .panel-wrapper.right {
  order: 0;
  flex: 0 0 auto;
}

.content-area[data-layout="stacked"] .panel-wrapper.left {
  order: 1;
  flex: 1 1 auto;
  border-right: none;
  border-top: 1px solid var(--border-soft);
  /* Below this the graph is unreadable; it should scroll into view instead. */
  min-height: 420px;
}

/* The view-mode switcher still hides a pane, it just does so vertically. */
.content-area[data-layout="stacked"][data-mode="graph"] .panel-wrapper.right,
.content-area[data-layout="stacked"][data-mode="workbench"] .panel-wrapper.left {
  display: none;
}

.content-area[data-layout="stacked"][data-mode="graph"] .panel-wrapper.left {
  border-top: none;
}

/* The banner itself lives in AppBanner; this is only the extra action this
   view slots into it. `:deep` because the slot content is rendered inside the
   child component's tree. */
:deep(.banner-back) {
  flex-shrink: 0;
  border: 1px solid var(--border);
  background: none;
  padding: 7px 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}
</style>
