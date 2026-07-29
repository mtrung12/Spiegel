<template>
  <div class="workbench-panel">
    <div class="scroll-container">
      <!-- Step 01: Ontology -->
      <div class="step-card" :class="{ 'active': currentPhase === 0, 'completed': currentPhase > 0 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">01</span>
            <span class="step-title">{{ $t('step1.ontologyGeneration') }}</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase > 0" class="badge success">{{ $t('step1.ontologyCompleted') }}</span>
            <span v-else-if="currentPhase === 0" class="badge processing">{{ $t('step1.ontologyGenerating') }}</span>
            <span v-else class="badge pending">{{ $t('step1.ontologyPending') }}</span>
          </div>
        </div>
        
        <div class="card-content">
          <p class="description">
            {{ $t('step1.ontologyDesc') }}
          </p>

          <!-- Loading / Progress -->
          <div v-if="currentPhase === 0 && ontologyProgress" class="progress-section">
            <div class="spinner-sm"></div>
            <span>{{ ontologyProgress.message || $t('step1.analyzingDocs') }}</span>
          </div>

          <!-- Detail Overlay -->
          <div v-if="selectedOntologyItem" class="ontology-detail-overlay">
            <div class="detail-header">
               <div class="detail-title-group">
                  <span class="detail-type-badge">{{ selectedOntologyItem.itemType === 'entity' ? $t('step1.entityBadge') : $t('step1.relationBadge') }}</span>
                  <span class="detail-name">{{ selectedOntologyItem.name }}</span>
               </div>
               <button class="close-btn" @click="selectedOntologyItem = null">×</button>
            </div>
            <div class="detail-body">
               <div class="detail-desc">{{ selectedOntologyItem.description }}</div>
               
               <!-- Attributes -->
               <div class="detail-section" v-if="selectedOntologyItem.attributes?.length">
                  <span class="section-label">{{ $t('step1.attributes') }}</span>
                  <div class="attr-list">
                     <div v-for="attr in selectedOntologyItem.attributes" :key="attr.name" class="attr-item">
                        <span class="attr-name">{{ attr.name }}</span>
                        <span class="attr-type">({{ attr.type }})</span>
                        <span class="attr-desc">{{ attr.description }}</span>
                     </div>
                  </div>
               </div>

               <!-- Examples (Entity) -->
               <div class="detail-section" v-if="selectedOntologyItem.examples?.length">
                  <span class="section-label">{{ $t('step1.examples') }}</span>
                  <div class="example-list">
                     <span v-for="ex in selectedOntologyItem.examples" :key="ex" class="example-tag">{{ ex }}</span>
                  </div>
               </div>

               <!-- Source/Target (Relation) -->
               <div class="detail-section" v-if="selectedOntologyItem.source_targets?.length">
                  <span class="section-label">{{ $t('step1.connections') }}</span>
                  <div class="conn-list">
                     <div v-for="(conn, idx) in selectedOntologyItem.source_targets" :key="idx" class="conn-item">
                        <span class="conn-node">{{ conn.source }}</span>
                        <span class="conn-arrow">→</span>
                        <span class="conn-node">{{ conn.target }}</span>
                     </div>
                  </div>
               </div>
            </div>
          </div>

          <!-- Generated Entity Tags -->
          <div v-if="projectData?.ontology?.entity_types" class="tags-container" :class="{ 'dimmed': selectedOntologyItem }">
            <span class="tag-label">{{ $t('step1.generatedEntityTypes') }}</span>
            <div class="tags-list">
              <button
                v-for="entity in projectData.ontology.entity_types"
                :key="entity.name"
                type="button"
                class="entity-tag clickable"
                @click="selectOntologyItem(entity, 'entity')"
              >
                {{ entity.name }}
              </button>
            </div>
          </div>

          <!-- Generated Relation Tags -->
          <div v-if="projectData?.ontology?.edge_types" class="tags-container" :class="{ 'dimmed': selectedOntologyItem }">
            <span class="tag-label">{{ $t('step1.generatedRelationTypes') }}</span>
            <div class="tags-list">
              <button
                v-for="rel in projectData.ontology.edge_types"
                :key="rel.name"
                type="button"
                class="entity-tag clickable"
                @click="selectOntologyItem(rel, 'relation')"
              >
                {{ rel.name }}
              </button>
            </div>
          </div>

          <!-- Extracted source text. Collapsed by default and fetched only on
               first open - it is the whole brief, and for an image-only deck it
               is the vision model's read of it, which is the one place you can
               check what the pipeline actually saw. -->
          <details
            v-if="projectData?.project_id && currentPhase > 0"
            class="source-text"
            @toggle="onSourceTextToggle"
          >
            <summary class="source-text-summary">
              {{ $t('step1.viewSourceText') }}
              <span v-if="projectData?.total_text_length" class="source-text-size">
                {{ $t('step1.sourceTextChars', { count: projectData.total_text_length.toLocaleString() }) }}
              </span>
            </summary>
            <p v-if="sourceTextLoading" class="source-text-status">{{ $t('step1.sourceTextLoading') }}</p>
            <p v-else-if="sourceTextError" class="source-text-status error">{{ sourceTextError }}</p>
            <pre v-else-if="sourceText" class="source-text-body">{{ sourceText }}</pre>
          </details>
        </div>
      </div>

      <!-- Step 02: Graph Build -->
      <div class="step-card" :class="{ 'active': currentPhase === 1, 'completed': currentPhase > 1 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">02</span>
            <span class="step-title">{{ $t('step1.graphRagBuild') }}</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase > 1" class="badge success">{{ $t('step1.ontologyCompleted') }}</span>
            <span v-else-if="currentPhase === 1" class="badge processing">{{ buildProgress?.progress || 0 }}%</span>
            <span v-else class="badge pending">{{ $t('step1.ontologyPending') }}</span>
          </div>
        </div>

        <div class="card-content">
          <p class="description">
            {{ $t('step1.graphRagDesc') }}
          </p>

          <!-- Audience research: status only, the themes themselves live in the
               simulation rather than being reported back here -->
          <div v-if="crawlerStatus" class="crawler-row">
            <span v-if="crawlerStatus.busy" class="spinner-sm"></span>
            <span v-else class="crawler-dot" :class="crawlerStatus.tone"></span>
            <span class="crawler-label">{{ $t('step1.crawlerLabel') }}</span>
            <span class="crawler-text">{{ crawlerStatus.text }}</span>
          </div>

          <!-- Stop: available for the whole build, not just the crawl. Gated on
               a live task rather than the phase, which stays at 1 after a build
               stops or fails. -->
          <button
            v-if="currentPhase === 1 && buildProgress"
            class="stop-btn"
            :disabled="stopping"
            @click="$emit('stop-build')"
          >
            {{ stopping ? $t('step1.stopping') : $t('step1.stopBuild') }}
          </button>

          <!-- Stats Cards -->
          <div class="stats-grid">
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.nodes }}</span>
              <span class="stat-label">{{ $t('step1.entityNodes') }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.edges }}</span>
              <span class="stat-label">{{ $t('step1.relationEdges') }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.types }}</span>
              <span class="stat-label">{{ $t('step1.schemaTypes') }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 03: Complete -->
      <div class="step-card" :class="{ 'active': currentPhase === 2, 'completed': currentPhase >= 2 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">03</span>
            <span class="step-title">{{ $t('step1.buildComplete') }}</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase >= 2" class="badge accent">{{ $t('step1.inProgress') }}</span>
          </div>
        </div>
        
        <div class="card-content">
          <p class="description">{{ $t('step1.buildCompleteDesc') }}</p>
          <button 
            class="action-btn" 
            :disabled="currentPhase < 2 || creatingSimulation"
            @click="handleEnterEnvSetup"
          >
            <span v-if="creatingSimulation" class="spinner-sm"></span>
            {{ creatingSimulation ? $t('step1.creating') : $t('step1.enterEnvSetup') + ' ➝' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <div class="log-header">
        <span class="log-title">{{ $t('step1.systemDashboard') }}</span>
        <span class="log-id">{{ projectData?.project_id || 'NO_PROJECT' }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>

    <ConfirmDialog
      :open="!!createError"
      alertOnly
      :title="$t('step1.createSimulationFailedTitle')"
      :message="createError"
      :confirmLabel="$t('common.close')"
      @confirm="createError = ''"
      @cancel="createError = ''"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { createSimulation } from '../api/simulation'
import { getProject } from '../api/graph'
import ConfirmDialog from './ConfirmDialog.vue'

const router = useRouter()
const { t } = useI18n()

const props = defineProps({
  currentPhase: { type: Number, default: 0 },
  projectData: Object,
  ontologyProgress: Object,
  buildProgress: Object,
  graphData: Object,
  systemLogs: { type: Array, default: () => [] },
  stopping: { type: Boolean, default: false }
})

defineEmits(['next-step', 'stop-build'])

// Extracted source text, fetched on first open of the disclosure rather than
// with the project - it is the whole brief, and nothing else on this screen
// needs it.
const sourceText = ref('')
const sourceTextLoading = ref(false)
const sourceTextError = ref('')

const onSourceTextToggle = async (event) => {
  if (!event.target.open || sourceText.value || sourceTextLoading.value) return

  sourceTextLoading.value = true
  sourceTextError.value = ''
  try {
    const res = await getProject(props.projectData.project_id, true)
    sourceText.value = res.data?.extracted_text || ''
    if (!sourceText.value) sourceTextError.value = t('step1.sourceTextEmpty')
  } catch (e) {
    sourceTextError.value = e?.message || t('step1.sourceTextFailed')
  } finally {
    sourceTextLoading.value = false
  }
}

// Coarse crawler state for the build card. Deliberately a one-line status: the
// harvested themes shape the personas downstream, they are not a report here.
const crawlerStatus = computed(() => {
  const corpus = props.buildProgress?.detail?.corpus
  if (!corpus) return null

  switch (corpus.state) {
    case 'starting':
      return { busy: true, text: t('step1.crawlerStarting') }
    case 'searching':
      return { busy: true, text: t('step1.crawlerSearching') }
    case 'coding':
      return { busy: true, text: t('step1.crawlerCoding') }
    case 'done':
      return {
        busy: false,
        tone: 'ok',
        text: t('step1.crawlerDone', { themes: corpus.themes ?? 0 })
      }
    case 'skipped':
      return { busy: false, tone: 'muted', text: t('step1.crawlerSkipped') }
    default:
      return null
  }
})

const selectedOntologyItem = ref(null)
const logContent = ref(null)
const creatingSimulation = ref(false)
// A failed create is reported in-app; window.alert blocks the tab and looks
// like a browser warning rather than part of the product.
const createError = ref('')

// Move on to environment setup: create the simulation and navigate
const handleEnterEnvSetup = async () => {
  if (!props.projectData?.project_id || !props.projectData?.graph_id) {
    console.error('missing project or graph information')
    return
  }
  
  creatingSimulation.value = true
  
  try {
    const res = await createSimulation({
      project_id: props.projectData.project_id,
      graph_id: props.projectData.graph_id,
      enable_twitter: true,
      enable_reddit: true
    })
    
    if (res.success && res.data?.simulation_id) {
      // Navigate to the simulation page
      router.push({
        name: 'Simulation',
        params: { simulationId: res.data.simulation_id }
      })
    } else {
      console.error('failed to create the simulation:', res.error)
      createError.value = t('step1.createSimulationFailed', { error: res.error || t('common.unknownError') })
    }
  } catch (err) {
    console.error('error creating the simulation:', err)
    createError.value = t('step1.createSimulationException', { error: err.message })
  } finally {
    creatingSimulation.value = false
  }
}

const selectOntologyItem = (item, type) => {
  selectedOntologyItem.value = { ...item, itemType: type }
}

const graphStats = computed(() => {
  const nodes = props.graphData?.node_count || props.graphData?.nodes?.length || 0
  const edges = props.graphData?.edge_count || props.graphData?.edges?.length || 0
  const types = props.projectData?.ontology?.entity_types?.length || 0
  return { nodes, edges, types }
})

const formatDate = (dateStr) => {
  if (!dateStr) return '--:--:--'
  const d = new Date(dateStr)
  return d.toLocaleTimeString('en-US', { hour12: false }) + '.' + d.getMilliseconds()
}

// Auto-scroll logs
watch(() => props.systemLogs.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})
</script>

<style scoped>
.workbench-panel {
  height: 100%;
  background-color: var(--surface);
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.scroll-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.step-card {
  background: var(--white);
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  border: 1px solid var(--border-soft);
  transition: all 0.3s ease;
  position: relative; /* For absolute overlay */
}

.step-card.active {
  border-color: #FF5722;
  box-shadow: 0 4px 12px rgba(255, 87, 34, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 20px;
  font-weight: 700;
  color: var(--border-soft);
}

.step-card.active .step-num,
.step-card.completed .step-num {
  color: var(--black);
}

.step-title {
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.badge {
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 4px;
  font-weight: 600;
  text-transform: uppercase;
}

.badge.success { background: #E8F5E9; color: #2E7D32; }
.badge.processing { background: #FF5722; color: var(--white); }
.badge.accent { background: #FF5722; color: var(--white); }
.badge.pending { background: var(--surface-2); color: var(--muted-soft); }

.api-note {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--muted-soft);
  margin-bottom: 8px;
}

.description {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
  margin-bottom: 16px;
}

/* Step 01 Tags */
.tags-container {
  margin-top: 12px;
  transition: opacity 0.3s;
}

.tags-container.dimmed {
    opacity: 0.3;
    pointer-events: none;
}

.tag-label {
  display: block;
  font-size: 12px;
  color: var(--muted-soft);
  margin-bottom: 8px;
  font-weight: 600;
}

.source-text {
  margin-top: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 12px;
}

.source-text-summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  list-style: none;
  user-select: none;
}

.source-text-summary::before {
  content: '▸';
  display: inline-block;
  margin-right: 6px;
  transition: transform 0.15s;
}

.source-text[open] > .source-text-summary::before {
  transform: rotate(90deg);
}

.source-text-summary:hover {
  color: var(--muted-soft);
}

.source-text-size {
  margin-left: 8px;
  font-weight: 400;
  opacity: 0.6;
}

.source-text-status {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted-soft);
}

.source-text-status.error {
  color: #E06C5A;
}

.source-text-body {
  margin: 10px 0 0;
  padding: 12px;
  max-height: 360px;
  overflow: auto;
  background: rgba(0, 0, 0, 0.25);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted-soft);
  /* The brief is prose with hard-wrapped lines; wrap rather than scroll sideways. */
  white-space: pre-wrap;
  word-break: break-word;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.entity-tag {
  font: inherit;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--ink-2);
  font-family: 'JetBrains Mono', monospace;
  transition: all 0.2s;
}

.entity-tag.clickable {
    cursor: pointer;
}

.entity-tag.clickable:hover {
    background: var(--border-soft);
    border-color: var(--border-strong);
}

/* Ontology Detail Overlay */
.ontology-detail-overlay {
    position: absolute;
    top: 60px; /* Below header roughly */
    left: 20px;
    right: 20px;
    bottom: 20px;
    background: rgba(255, 255, 255, 0.98);
    backdrop-filter: blur(4px);
    z-index: 10;
    border: 1px solid var(--border-soft);
    box-shadow: 0 4px 20px rgba(0,0,0,0.05);
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    animation: fadeIn 0.2s ease-out;
}

@keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

.detail-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-soft);
    background: var(--surface);
}

.detail-title-group {
    display: flex;
    align-items: center;
    gap: 8px;
}

.detail-type-badge {
    font-size: 12px;
    font-weight: 700;
    color: var(--white);
    background: var(--black);
    padding: 2px 6px;
    border-radius: 2px;
    text-transform: uppercase;
}

.detail-name {
    font-size: 14px;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
}

.close-btn {
    background: none;
    border: none;
    font-size: 18px;
    color: var(--muted-soft);
    cursor: pointer;
    line-height: 1;
}

.close-btn:hover {
    color: var(--ink-2);
}

.detail-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
}

.detail-desc {
    font-size: 12px;
    color: var(--ink-3);
    line-height: 1.5;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px dashed var(--border-soft);
}

.detail-section {
    margin-bottom: 16px;
}

.section-label {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted-soft);
    margin-bottom: 8px;
}

.attr-list, .conn-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.attr-item {
    font-size: 12px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: baseline;
    padding: 4px;
    background: var(--surface);
    border-radius: 4px;
}

.attr-name {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    color: var(--black);
}

.attr-type {
    color: var(--muted-soft);
    font-size: 12px;
}

.attr-desc {
    color: var(--ink-3);
    flex: 1;
    min-width: 150px;
}

.example-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

.example-tag {
    font-size: 12px;
    background: var(--white);
    border: 1px solid var(--border-soft);
    padding: 3px 8px;
    border-radius: 12px;
    color: var(--ink-3);
}

.conn-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    padding: 6px;
    background: var(--surface-2);
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
}

.conn-node {
    font-weight: 600;
    color: var(--ink-2);
}

.conn-arrow {
    color: var(--muted-soft);
}

/* Step 02 Crawler status */
.crawler-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--surface);
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  font-size: 12px;
}

.crawler-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.crawler-dot.ok { background: #2E7D32; }
.crawler-dot.muted { background: var(--muted-soft); }

.crawler-label {
  font-weight: 600;
  color: var(--ink-2);
}

.crawler-text {
  color: var(--muted);
}

.stop-btn {
  width: 100%;
  background: var(--white);
  color: #C62828;
  border: 1px solid #FFCDD2;
  padding: 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  margin-bottom: 12px;
  transition: all 0.2s;
}

.stop-btn:hover:not(:disabled) {
  background: #FFEBEE;
  border-color: #EF9A9A;
}

.stop-btn:disabled {
  color: var(--muted-soft);
  border-color: var(--border-soft);
  cursor: not-allowed;
}

/* Step 02 Stats */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
  background: var(--surface);
  padding: 16px;
  border-radius: 6px;
}

.stat-card {
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 20px;
  font-weight: 700;
  color: var(--black);
  font-family: 'JetBrains Mono', monospace;
}

.stat-label {
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: uppercase;
  margin-top: 4px;
  display: block;
}

/* Step 03 Button */
.action-btn {
  width: 100%;
  background: var(--accent);
  color: var(--white);
  border: none;
  padding: 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
}

.action-btn:hover:not(:disabled) {
  opacity: 0.8;
}

.action-btn:disabled {
  background: var(--border-strong);
  cursor: not-allowed;
}

.progress-section {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #FF5722;
  margin-bottom: 12px;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid #FFCCBC;
  border-top-color: #FF5722;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* System Logs */
.system-logs {
  background: var(--surface);
  color: var(--ink-2);
  padding: 16px;
  font-family: 'JetBrains Mono', monospace;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.log-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--muted-soft);
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 80px; /* Approx 4 lines visible */
  overflow-y: auto;
  padding-right: 4px;
}

.log-content::-webkit-scrollbar {
  width: 4px;
}

.log-content::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 2px;
}

.log-line {
  font-size: 12px;
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time {
  color: var(--muted-soft);
  min-width: 75px;
}

.log-msg {
  color: var(--ink-2);
  word-break: break-all;
}

/* ---------- Narrow screens ---------- */
@media (max-width: 720px) {
  .env-setup-panel .scroll-container,
  .workbench-panel .scroll-container {
    padding-left: 14px;
    padding-right: 14px;
  }

  /* Multi-column stat and config grids stop being legible well before they
     stop fitting, so they drop to a single column. */
  [class*="-grid"],
  .info-card,
  .stats-row {
    grid-template-columns: 1fr !important;
  }
}
</style>
