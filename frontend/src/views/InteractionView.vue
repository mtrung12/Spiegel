<template>
  <div class="main-view">
    <AppHeader
      :currentStep="5"
      :status="currentStatus"
      :statusText="statusText"
      v-model="viewMode"
      :projectId="projectData?.project_id"
      :simulationId="simulationId"
      :reportId="currentReportId"
    >
      <template #actions>
        <button class="workspace-btn" :disabled="!projectData?.project_id" @click="goToWorkspace">
          {{ $t('workspace.openWorkspace') }}
        </button>
      </template>
    </AppHeader>

    <!-- Main Content Area -->
    <main id="main-content" class="content-area" :data-mode="viewMode">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left">
        <GraphPanel 
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="5"
          :isSimulating="false"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right panel: step 5, deep interaction -->
      <div class="panel-wrapper right">
        <Step5Interaction
          :reportId="currentReportId"
          :simulationId="simulationId"
          :systemLogs="systemLogs"
          @add-log="addLog"
          @update-status="updateStatus"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import GraphPanel from '../components/GraphPanel.vue'
import Step5Interaction from '../components/Step5Interaction.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation } from '../api/simulation'
import { getReport } from '../api/report'
import AppHeader from '../components/AppHeader.vue'
import { useSplitLayout, useSystemLog } from '../composables/useWorkbench'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

// Props
const props = defineProps({
  reportId: String
})

// Layout state - defaults to the workbench view
const { viewMode, toggleMaximize } = useSplitLayout('workbench')

// Data State
const currentReportId = ref(route.params.reportId)
const simulationId = ref(null)
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const { systemLogs, addLog } = useSystemLog(200)
const currentStatus = ref('ready') // ready | processing | completed | error

const statusText = computed(() => {
  if (currentStatus.value === 'error') return t('main.statusError')
  if (currentStatus.value === 'completed') return t('main.statusCompleted')
  if (currentStatus.value === 'processing') return t('main.statusProcessing')
  return t('main.statusReady')
})

const updateStatus = (status) => {
  currentStatus.value = status
}

const goToWorkspace = () => {
  if (projectData.value?.project_id) {
    router.push({ name: 'Workspace', params: { projectId: projectData.value.project_id } })
  }
}

// --- Data Logic ---
const loadReportData = async () => {
  try {
    addLog(t('log.loadReportData', { id: currentReportId.value }))

    // Read the report to find its simulation_id
    const reportRes = await getReport(currentReportId.value)
    if (reportRes.success && reportRes.data) {
      const reportData = reportRes.data
      simulationId.value = reportData.simulation_id

      if (simulationId.value) {
        // Read the simulation
        const simRes = await getSimulation(simulationId.value)
        if (simRes.success && simRes.data) {
          const simData = simRes.data

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
        }
      }
    } else {
      addLog(t('log.getReportInfoFailed', { error: reportRes.error || t('common.unknownError') }))
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

// Watch route params
watch(() => route.params.reportId, (newId) => {
  if (newId && newId !== currentReportId.value) {
    currentReportId.value = newId
    loadReportData()
  }
}, { immediate: true })

onMounted(() => {
  addLog(t('log.interactionViewInit'))
  loadReportData()
})
</script>

<style scoped>
.workspace-btn {
  border: 1px solid var(--border);
  background: var(--white);
  padding: 7px 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  transition: background 0.2s, border-color 0.2s;
}

.workspace-btn:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--border-strong);
}

.workspace-btn:disabled {
  color: var(--muted-soft);
  cursor: not-allowed;
}
</style>
