<template>
  <div class="main-view">
    <AppHeader
      :currentStep="3"
      :status="currentStatus"
      :statusText="statusText"
      v-model="viewMode"
      :projectId="projectData?.project_id"
      :simulationId="currentSimulationId"
      :reportId="existingReportId"
    ></AppHeader>

    <!-- Main Content Area -->
    <main id="main-content" class="content-area" :data-mode="viewMode">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left">
        <GraphPanel 
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="3"
          :isSimulating="isSimulating"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right panel: step 3, run the simulation -->
      <div class="panel-wrapper right">
        <Step3Simulation
          :simulationId="currentSimulationId"
          :startNewRun="startNewRun"
          :maxRounds="maxRounds"
          :minutesPerRound="minutesPerRound"
          :projectData="projectData"
          :graphData="graphData"
          :systemLogs="systemLogs"
          @go-back="handleGoBack"
          @next-step="handleNextStep"
          @add-log="addLog"
          @update-status="updateStatus"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import Step3Simulation from '../components/Step3Simulation.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation, getSimulationConfig } from '../api/simulation'
import { getReportBySimulation } from '../api/report'
import AppHeader from '../components/AppHeader.vue'
import { useSplitLayout, useSystemLog } from '../composables/useWorkbench'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// Props
const props = defineProps({
  simulationId: String
})

const { viewMode, toggleMaximize } = useSplitLayout('workbench')

// Data State
const currentSimulationId = ref(route.params.simulationId)
// Read maxRounds from the query string during setup, so child components see it right away
const maxRounds = ref(route.query.maxRounds ? parseInt(route.query.maxRounds) : null)
// start=1 is only set by step 2's launch button. Read once during setup, then
// dropped from the URL below so a reload of this page cannot restart the run.
const startNewRun = ref(route.query.start === '1')
const minutesPerRound = ref(30) // 30 simulated minutes per round by default
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const { systemLogs, addLog } = useSystemLog(200)
const currentStatus = ref('processing') // processing | completed | error
// A report this simulation already produced. Without it the stepper's step 4
// stays disabled after coming back here, and the only way forward is the
// generate button - which re-runs the whole report pipeline.
const existingReportId = ref(null)

const statusText = computed(() => {
  if (currentStatus.value === 'error') return t('main.statusError')
  if (currentStatus.value === 'completed') return t('main.statusCompleted')
  return t('main.statusRunning')
})

const isSimulating = computed(() => currentStatus.value === 'processing')

const updateStatus = (status) => {
  currentStatus.value = status
}

const handleGoBack = () => {
  // Navigating away leaves the run alone: it used to be torn down here, which
  // meant a glance at step 2 cost the whole run. Step 3's Stop button is the
  // one way to end a run, and starting a new one from step 2 clears the old.
  stopGraphRefresh()
  router.push({ name: 'Simulation', params: { simulationId: currentSimulationId.value } })
}

const handleNextStep = () => {
  // Step3Simulation handles report generation and navigation itself;
  // this handler is only a fallback
  addLog(t('log.enterStep4'))
}

// --- Data Logic ---
const loadSimulationData = async () => {
  try {
    addLog(t('log.loadingSimData', { id: currentSimulationId.value }))
    
    // Read the simulation
    const simRes = await getSimulation(currentSimulationId.value)
    if (simRes.success && simRes.data) {
      const simData = simRes.data
      
      // Read the simulation config for minutes_per_round
      try {
        const configRes = await getSimulationConfig(currentSimulationId.value)
        if (configRes.success && configRes.data?.time_config?.minutes_per_round) {
          minutesPerRound.value = configRes.data.time_config.minutes_per_round
          addLog(t('log.timeConfig', { minutes: minutesPerRound.value }))
        }
      } catch (configErr) {
        addLog(t('log.timeConfigFetchFailed', { minutes: minutesPerRound.value }))
      }
      
      // Read the project
      if (simData.project_id) {
        const projRes = await getProject(simData.project_id)
        if (projRes.success && projRes.data) {
          projectData.value = projRes.data
          addLog(t('log.projectLoadSuccess', { id: projRes.data.project_id }))
          
          // Read the graph data
          if (projRes.data.graph_id) {
            await loadGraph(projRes.data.graph_id)
          }
        }
      }
    } else {
      addLog(t('log.loadSimDataFailed', { error: simRes.error || t('common.unknownError') }))
    }
  } catch (err) {
    addLog(t('log.loadException', { error: err.message }))
  }
}

const loadGraph = async (graphId) => {
  // While the simulation runs, an auto-refresh skips the full-screen loader to avoid flicker.
  // A manual refresh or the first load does show it.
  if (!isSimulating.value) {
    graphLoading.value = true
  }
  
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      if (!isSimulating.value) {
        addLog(t('log.graphDataLoadSuccess'))
      }
    }
  } catch (err) {
    addLog(t('log.graphLoadFailed', { error: err.message }))
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) {
    loadGraph(projectData.value.graph_id)
  }
}

// 404 here is the normal "no report yet" answer, not a failure worth logging.
const loadExistingReport = async () => {
  if (!currentSimulationId.value) return
  try {
    const res = await getReportBySimulation(currentSimulationId.value)
    existingReportId.value = res?.data?.report_id || null
  } catch {
    existingReportId.value = null
  }
}

// --- Auto Refresh Logic ---
let graphRefreshTimer = null

const startGraphRefresh = () => {
  if (graphRefreshTimer) return
  addLog(t('log.graphRealtimeRefreshStart'))
  // Refresh once immediately, then every 30 seconds
  graphRefreshTimer = setInterval(refreshGraph, 30000)
}

const stopGraphRefresh = () => {
  if (graphRefreshTimer) {
    clearInterval(graphRefreshTimer)
    graphRefreshTimer = null
    addLog(t('log.graphRealtimeRefreshStop'))
  }
}

watch(isSimulating, (newValue) => {
  if (newValue) {
    startGraphRefresh()
  } else {
    stopGraphRefresh()
  }
}, { immediate: true })

onMounted(() => {
  addLog(t('log.simRunViewInit'))

  if (startNewRun.value) {
    // Strip start=1 so a reload attaches to the run instead of relaunching it.
    const { start, ...rest } = route.query
    router.replace({ query: rest })
  }
  
  // Record maxRounds (already read from the query string during setup)
  if (maxRounds.value) {
    addLog(t('log.customRounds', { rounds: maxRounds.value }))
  }
  
  loadSimulationData()
  loadExistingReport()
})

onUnmounted(() => {
  stopGraphRefresh()
})
</script>


