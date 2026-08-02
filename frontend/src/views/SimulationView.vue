<template>
  <div class="main-view">
    <AppHeader
      :currentStep="2"
      :status="currentStatus"
      :statusText="statusText"
      v-model="viewMode"
      :projectId="projectData?.project_id"
      :simulationId="currentSimulationId"
      :reportId="null"
      :projectName="projectData?.name || ''"
      @readonly="readOnly = $event"
    />

    <!-- Main Content Area -->
    <main id="main-content" class="content-area" :data-mode="viewMode">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left">
        <GraphPanel 
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="2"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right panel: step 2, environment setup -->
      <div class="panel-wrapper right">
        <Step2EnvSetup
          :simulationId="currentSimulationId"
          :projectData="projectData"
          :graphData="graphData"
          :systemLogs="systemLogs"
          :readOnly="readOnly"
          :userConfig="userConfig"
          @next-step="handleNextStep"
          @add-log="addLog"
          @update-status="updateStatus"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import Step2EnvSetup from '../components/Step2EnvSetup.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation, stopSimulation, getEnvStatus, closeSimulationEnv } from '../api/simulation'
import AppHeader from '../components/AppHeader.vue'
import { useSplitLayout, useSystemLog } from '../composables/useWorkbench'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// Props
defineProps({
  simulationId: String
})

const { viewMode, toggleMaximize } = useSplitLayout('split')
const { systemLogs, addLog } = useSystemLog(100)

// Data State
const currentSimulationId = ref(route.params.simulationId)
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const currentStatus = ref('processing') // processing | completed | error

// Set by the header: true when the project stopped past step 2, so the setup
// shows what was chosen without offering to prepare or launch it again.
const readOnly = ref(false)

// The audience size and round count saved by step 2's substeps, so reopening
// the project shows the numbers the run was set up with rather than defaults.
const userConfig = ref(null)

const statusText = computed(() => {
  if (currentStatus.value === 'error') return t('main.statusError')
  if (currentStatus.value === 'completed') return t('main.statusReady')
  return t('main.statusPreparing')
})

const updateStatus = (status) => {
  currentStatus.value = status
}

const handleNextStep = async (params = {}) => {
  addLog(t('log.enterStep3'))

  // A run left over from a previous visit to step 3 has to go before a new one
  // starts. This is the only moment that is true: merely opening step 2 to look
  // at the setup must leave a running simulation alone.
  await checkAndStopRunningSimulation()

  // Record the round count
  if (params.maxRounds) {
    addLog(t('log.customRoundsConfig', { rounds: params.maxRounds }))
  } else {
    addLog(t('log.useAutoRounds'))
  }
  
  // Build the route parameters. start=1 marks this as "launch a new run":
  // step 3 reached any other way (header tab, browser back) attaches to the
  // run already in flight rather than restarting it.
  const routeParams = {
    name: 'SimulationRun',
    params: { simulationId: currentSimulationId.value },
    query: { start: '1' }
  }

  // Pass a custom round count through the query string
  if (params.maxRounds) {
    routeParams.query.maxRounds = params.maxRounds
  }
  
  // Navigate to step 3
  router.push(routeParams)
}

// --- Data Logic ---

/**
 * Check for a running simulation and shut it down.
 * Called just before step 3 starts a fresh run, never on mount: navigating to
 * step 2 to review the setup used to terminate the live run.
 */
const checkAndStopRunningSimulation = async () => {
  if (!currentSimulationId.value) return
  
  try {
    // Is the simulation environment alive?
    const envStatusRes = await getEnvStatus({ simulation_id: currentSimulationId.value })
    
    if (envStatusRes.success && envStatusRes.data?.env_alive) {
      addLog(t('log.detectedSimEnvRunning'))
      
      // Try a graceful shutdown first
      try {
        const closeRes = await closeSimulationEnv({ 
          simulation_id: currentSimulationId.value,
          timeout: 10  // 10 second timeout
        })
        
        if (closeRes.success) {
          addLog(t('log.simEnvClosed'))
        } else {
          addLog(t('log.closeSimEnvFailedWithError', { error: closeRes.error || t('common.unknownError') }))
          // Graceful shutdown failed: force a stop
          await forceStopSimulation()
        }
      } catch (closeErr) {
        addLog(t('log.closeSimEnvException', { error: closeErr.message }))
        // Graceful shutdown errored: force a stop
        await forceStopSimulation()
      }
    } else {
      // The environment is down but the process may linger; check the status
      const simRes = await getSimulation(currentSimulationId.value)
      if (simRes.success && simRes.data?.status === 'running') {
        addLog(t('log.detectedSimRunning'))
        await forceStopSimulation()
      }
    }
  } catch (err) {
    // A failed status check must not block what follows
    console.warn('failed to check the simulation status:', err)
  }
}

/**
 * Force the simulation to stop.
 */
const forceStopSimulation = async () => {
  try {
    const stopRes = await stopSimulation({ simulation_id: currentSimulationId.value })
    if (stopRes.success) {
      addLog(t('log.simForceStopSuccess'))
    } else {
      addLog(t('log.forceStopSimFailed', { error: stopRes.error || t('common.unknownError') }))
    }
  } catch (err) {
    addLog(t('log.forceStopSimException', { error: err.message }))
  }
}

const loadSimulationData = async () => {
  try {
    addLog(t('log.loadingSimData', { id: currentSimulationId.value }))

    // Read the simulation
    const simRes = await getSimulation(currentSimulationId.value)
    if (simRes.success && simRes.data) {
      const simData = simRes.data
      userConfig.value = simData.user_config || null

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
  graphLoading.value = true
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      addLog(t('log.graphDataLoadSuccess'))
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

onMounted(() => {
  addLog(t('log.simViewInit'))
  loadSimulationData()
})
</script>

<!-- The shell (.main-view / .content-area / .panel-wrapper) is defined once in
     App.vue; this view adds nothing on top of it. -->

