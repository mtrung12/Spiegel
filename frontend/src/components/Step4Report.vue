<template>
  <div class="report-panel">
    <!-- Main Split Layout -->
    <div class="main-split-layout" :class="{ 'progress-collapsed': !showProgress }">
      <!-- LEFT PANEL: the rendered report (shared with step 5) -->
      <ReportPane
        :outline="reportOutline"
        :generatedSections="generatedSections"
        :currentSectionIndex="currentSectionIndex"
        :reportId="reportId"
        :tag="$t('step4.reportTag')"
      >
        <!-- Each panel sits inside the section that explains it, rather than
             stacked above the report where the reader meets forty figures
             before the first sentence. Section order is fixed in
             REPORT_SECTION_IDS, so the slot numbers are stable. -->

        <!-- 01 What happened: exposure, engagement, spread, and the curve -->
        <template #visual-1>
          <CampaignKpiPanel
            v-if="simulationId"
            :simulationId="simulationId"
            :autoLoad="isComplete"
            :only="['reach', 'engagement', 'virality', 'rounds']"
          />
        </template>

        <!-- 02 How they reacted: the text sentiment split, quotes and themes -->
        <template #visual-2>
          <SentimentDigestPanel
            v-if="simulationId"
            :simulationId="simulationId"
            :autoLoad="isComplete"
            bare
          />
        </template>

        <!-- 03 Where the audience split: the per-segment table -->
        <!-- ponytail: this second instance re-fetches /campaign-metrics rather
             than sharing section 01's copy. It is a SQLite read with no LLM in
             it, so two calls are cheaper than threading the payload through the
             pane. Lift the fetch into this view if runs get big enough to
             notice. -->
        <template #visual-3>
          <CampaignKpiPanel
            v-if="simulationId"
            :simulationId="simulationId"
            :autoLoad="isComplete"
            :only="['segments']"
          />
        </template>

        <!-- 04 Signals and limits has no visual, by design: it is the section
             meant to be read rather than scanned. -->
      </ReportPane>

      <!-- RIGHT PANEL: Workflow Timeline.
           While the report is being written this is the only sign of life, so
           it stays open. Once it finishes it is history the reader has to be
           able to push aside for the report itself - so it folds itself at
           that moment, and the rail reopens it on demand. -->
      <div class="right-panel" :class="{ 'is-collapsed': !showProgress }" ref="rightPanel">
        <button
          class="progress-toggle"
          :aria-expanded="showProgress"
          @click="showProgress = !showProgress"
        >
          <span class="progress-toggle-label">{{ $t('step4.progressPanel') }}</span>
          <span class="progress-toggle-status mono">{{ statusText }}</span>
          <svg class="progress-toggle-chevron" :class="{ open: showProgress }" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>

        <!-- Forward navigation must not fold away with the progress detail:
             collapsed or not, a finished report has to offer the next step. -->
        <button v-if="isComplete && !showProgress" class="next-step-btn rail-next" @click="goToInteraction">
          <span>{{ $t('step4.goToInteraction') }}</span>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="5" y1="12" x2="19" y2="12"></line>
            <polyline points="12 5 19 12 12 19"></polyline>
          </svg>
        </button>

        <template v-if="showProgress">
        <div class="panel-header" :class="`panel-header--${activeStep.status}`" v-if="!isComplete">
          <span class="header-dot" v-if="activeStep.status === 'active'"></span>
          <span class="header-index mono">{{ activeStep.noLabel }}</span>
          <span class="header-title">{{ activeStep.title }}</span>
          <span class="header-meta mono" v-if="activeStep.meta">{{ activeStep.meta }}</span>
        </div>

        <!-- Workflow Overview (flat, status-based palette) -->
        <div class="workflow-overview" v-if="agentLogs.length > 0 || reportOutline">
          <div class="workflow-metrics">
            <div class="metric">
              <span class="metric-label">{{ $t('step4.metricSections') }}</span>
              <span class="metric-value mono">{{ completedSections }}/{{ totalSections }}</span>
            </div>
            <div class="metric">
              <span class="metric-label">{{ $t('step4.metricElapsed') }}</span>
              <span class="metric-value mono">{{ formatElapsedTime }}</span>
            </div>
            <div class="metric">
              <span class="metric-label">{{ $t('step4.metricTools') }}</span>
              <span class="metric-value mono">{{ totalToolCalls }}</span>
            </div>
            <div class="metric metric-right">
              <span class="metric-pill" :class="`pill--${statusClass}`">{{ statusText }}</span>
            </div>
          </div>

          <div class="workflow-steps" v-if="workflowSteps.length > 0">
            <div
              v-for="(step, sidx) in workflowSteps"
              :key="step.key"
              class="wf-step"
              :class="`wf-step--${step.status}`"
            >
              <div class="wf-step-connector">
                <div class="wf-step-dot"></div>
                <div class="wf-step-line" v-if="sidx < workflowSteps.length - 1"></div>
              </div>

              <div class="wf-step-content">
                <div class="wf-step-title-row">
                  <span class="wf-step-index mono">{{ step.noLabel }}</span>
                  <span class="wf-step-title">{{ step.title }}</span>
                  <span class="wf-step-meta mono" v-if="step.meta">{{ step.meta }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Next-step button, shown once generation finishes -->
          <button v-if="isComplete" class="next-step-btn" @click="goToInteraction">
            <span>{{ $t('step4.goToInteraction') }}</span>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="5" y1="12" x2="19" y2="12"></line>
              <polyline points="12 5 19 12 12 19"></polyline>
            </svg>
          </button>

          <div class="workflow-divider"></div>
        </div>

        <div class="workflow-timeline">
          <TransitionGroup name="timeline-item">
            <div 
              v-for="(log, idx) in displayLogs" 
              :key="log.timestamp + '-' + idx"
              class="timeline-item"
              :class="getTimelineItemClass(log, idx, displayLogs.length)"
            >
              <!-- Timeline Connector -->
              <div class="timeline-connector">
                <div class="connector-dot" :class="getConnectorClass(log, idx, displayLogs.length)"></div>
                <div class="connector-line" v-if="idx < displayLogs.length - 1"></div>
              </div>
              
              <!-- Timeline Content -->
              <div class="timeline-content">
                <div class="timeline-header">
                  <span class="action-label">{{ getActionLabel(log.action) }}</span>
                  <span class="action-time">{{ formatTime(log.timestamp) }}</span>
                </div>
                
                <!-- Action Body - Different for each type -->
                <!-- Mouse convenience only: the keyboard path to expand is the
                     explicit toggle in .timeline-footer below. -->
                <div class="timeline-body" :class="{ 'collapsed': isLogCollapsed(log) }" @click="toggleLogExpand(log)">
                  
                  <!-- Report Start -->
                  <template v-if="log.action === 'report_start'">
                    <div class="info-row">
                      <span class="info-key">{{ $t('step4.infoSimulation') }}</span>
                      <span class="info-val mono">{{ log.details?.simulation_id }}</span>
                    </div>
                    <div class="info-row" v-if="log.details?.simulation_requirement">
                      <span class="info-key">{{ $t('step4.infoRequirement') }}</span>
                      <span class="info-val">{{ log.details.simulation_requirement }}</span>
                    </div>
                  </template>

                  <!-- Planning -->
                  <template v-if="log.action === 'planning_start'">
                    <div class="status-message planning">{{ log.details?.message }}</div>
                  </template>
                  <template v-if="log.action === 'planning_complete'">
                    <div class="status-message success">{{ log.details?.message }}</div>
                    <div class="outline-badge" v-if="log.details?.outline">
                      {{ log.details.outline.sections?.length || 0 }} sections planned
                    </div>
                  </template>

                  <!-- Section Start -->
                  <template v-if="log.action === 'section_start'">
                    <div class="section-tag">
                      <span class="tag-num">#{{ log.section_index }}</span>
                      <span class="tag-title">{{ log.section_title }}</span>
                    </div>
                  </template>
                  
                  <!-- Section content generated (the body is done; the section may not be) -->
                  <template v-if="log.action === 'section_content'">
                    <div class="section-tag content-ready">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M12 20h9"></path>
                        <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
                      </svg>
                      <span class="tag-title">{{ log.section_title }}</span>
                    </div>
                  </template>

                  <!-- Section complete -->
                  <template v-if="log.action === 'section_complete'">
                    <div class="section-tag completed">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"></polyline>
                      </svg>
                      <span class="tag-title">{{ log.section_title }}</span>
                    </div>
                  </template>

                  <!-- Tool Call -->
                  <template v-if="log.action === 'tool_call'">
                    <div class="tool-badge" :class="'tool-' + getToolColor(log.details?.tool_name)">
                      <!-- Deep Insight - Lightbulb -->
                      <svg v-if="getToolIcon(log.details?.tool_name) === 'lightbulb'" class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.5V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.5A7 7 0 0 0 12 2z"></path>
                      </svg>
                      <!-- Panorama Search - Globe -->
                      <svg v-else-if="getToolIcon(log.details?.tool_name) === 'globe'" class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"></circle>
                        <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                      </svg>
                      <!-- Agent Interview - Users -->
                      <svg v-else-if="getToolIcon(log.details?.tool_name) === 'users'" class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                        <circle cx="9" cy="7" r="4"></circle>
                        <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"></path>
                      </svg>
                      <!-- Quick Search - Zap -->
                      <svg v-else-if="getToolIcon(log.details?.tool_name) === 'zap'" class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                      </svg>
                      <!-- Graph Stats - Chart -->
                      <svg v-else-if="getToolIcon(log.details?.tool_name) === 'chart'" class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="20" x2="18" y2="10"></line>
                        <line x1="12" y1="20" x2="12" y2="4"></line>
                        <line x1="6" y1="20" x2="6" y2="14"></line>
                      </svg>
                      <!-- Entity Query - Database -->
                      <svg v-else-if="getToolIcon(log.details?.tool_name) === 'database'" class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
                        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
                      </svg>
                      <!-- Default - Tool -->
                      <svg v-else class="tool-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
                      </svg>
                      {{ getToolDisplayName(log.details?.tool_name) }}
                    </div>
                    <div v-if="log.details?.parameters && expandedLogs.has(log.timestamp)" class="tool-params">
                      <pre>{{ formatParams(log.details.parameters) }}</pre>
                    </div>
                  </template>

                  <!-- Tool Result -->
                  <template v-if="log.action === 'tool_result'">
                    <div class="result-wrapper" :class="'result-' + log.details?.tool_name">
                      <!-- Hide result-meta for tools that show stats in their own header -->
                      <div v-if="!['interview_agents', 'insight_forge', 'panorama_search', 'quick_search'].includes(log.details?.tool_name)" class="result-meta">
                        <span class="result-tool">{{ getToolDisplayName(log.details?.tool_name) }}</span>
                        <span class="result-size">{{ formatResultSize(log.details?.result_length) }}</span>
                      </div>
                      
                      <!-- Structured Result Display -->
                      <div v-if="!showRawResult[log.timestamp]" class="result-structured">
                        <!-- Interview Agents - Special Display -->
                        <template v-if="log.details?.tool_name === 'interview_agents'">
                          <InterviewDisplay :result="parseInterview(log.details.result)" :result-length="log.details?.result_length" />
                        </template>
                        
                        <!-- Insight Forge -->
                        <template v-else-if="log.details?.tool_name === 'insight_forge'">
                          <InsightDisplay :result="parseInsightForge(log.details.result)" :result-length="log.details?.result_length" />
                        </template>
                        
                        <!-- Panorama Search -->
                        <template v-else-if="log.details?.tool_name === 'panorama_search'">
                          <PanoramaDisplay :result="parsePanorama(log.details.result)" :result-length="log.details?.result_length" />
                        </template>
                        
                        <!-- Quick Search -->
                        <template v-else-if="log.details?.tool_name === 'quick_search'">
                          <QuickSearchDisplay :result="parseQuickSearch(log.details.result)" :result-length="log.details?.result_length" />
                        </template>
                        
                        <!-- Default -->
                        <template v-else>
                          <pre class="raw-preview">{{ truncateText(log.details?.result, 300) }}</pre>
                        </template>
                      </div>
                      
                      <!-- Raw Result -->
                      <div v-else class="result-raw">
                        <pre>{{ log.details?.result }}</pre>
                      </div>
                    </div>
                  </template>

                  <!-- LLM Response -->
                  <template v-if="log.action === 'llm_response'">
                    <div class="llm-meta">
                      <span class="meta-tag">Iteration {{ log.details?.iteration }}</span>
                      <span class="meta-tag" :class="{ active: log.details?.has_tool_calls }">
                        Tools: {{ log.details?.has_tool_calls ? 'Yes' : 'No' }}
                      </span>
                      <span class="meta-tag" :class="{ active: log.details?.has_final_answer, 'final-answer': log.details?.has_final_answer }">
                        Final: {{ log.details?.has_final_answer ? 'Yes' : 'No' }}
                      </span>
                    </div>
                    <!-- A final answer gets its own callout -->
                    <div v-if="log.details?.has_final_answer" class="final-answer-hint">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"></polyline>
                      </svg>
                      <span>Section "{{ log.section_title }}" content generated</span>
                    </div>
                    <div v-if="expandedLogs.has(log.timestamp) && log.details?.response" class="llm-content">
                      <pre>{{ log.details.response }}</pre>
                    </div>
                  </template>

                  <!-- Report Complete -->
                  <template v-if="log.action === 'report_complete'">
                    <div class="complete-banner">
                      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                        <polyline points="22 4 12 14.01 9 11.01"></polyline>
                      </svg>
                      <span>{{ $t('step4.reportGenerationComplete') }}</span>
                    </div>
                  </template>
                </div>

                <!-- Footer: Elapsed Time + Action Buttons -->
                <div class="timeline-footer" v-if="log.elapsed_seconds || (log.action === 'tool_call' && log.details?.parameters) || log.action === 'tool_result' || (log.action === 'llm_response' && log.details?.response)">
                  <span v-if="log.elapsed_seconds" class="elapsed-badge">+{{ log.elapsed_seconds.toFixed(1) }}s</span>
                  <span v-else class="elapsed-placeholder"></span>
                  
                  <div class="footer-actions">
                    <!-- Tool Call: Show/Hide Params -->
                    <button v-if="log.action === 'tool_call' && log.details?.parameters" class="action-btn" @click.stop="toggleLogExpand(log)">
                      {{ expandedLogs.has(log.timestamp) ? 'Hide Params' : 'Show Params' }}
                    </button>
                    
                    <!-- Tool Result: Raw/Structured View -->
                    <button v-if="log.action === 'tool_result'" class="action-btn" @click.stop="toggleRawResult(log.timestamp, $event)">
                      {{ showRawResult[log.timestamp] ? 'Structured View' : 'Raw Output' }}
                    </button>
                    
                    <!-- LLM Response: Show/Hide Response -->
                    <button v-if="log.action === 'llm_response' && log.details?.response" class="action-btn" @click.stop="toggleLogExpand(log)">
                      {{ expandedLogs.has(log.timestamp) ? 'Hide Response' : 'Show Response' }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </TransitionGroup>

          <!-- Empty State -->
          <div v-if="agentLogs.length === 0 && !isComplete" class="workflow-empty">
            <div class="empty-pulse"></div>
            <span>{{ $t('step4.waitingForAgentActivity') }}</span>
          </div>
        </div>
        </template>
      </div>
    </div>

    <!-- Bottom Console Logs -->
    <div class="console-logs">
      <div class="log-header">
        <span class="log-title">{{ $t('step4.consoleOutput') }}</span>
        <span class="log-id">{{ reportId || 'NO_REPORT' }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in consoleLogs" :key="idx">
          <span class="log-msg" :class="getLogLevelClass(log)">{{ log }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick, h, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getAgentLog, getConsoleLog } from '../api/report'
import ReportPane from './ReportPane.vue'
import { renderMarkdown } from '../utils/markdown'
import CampaignKpiPanel from './CampaignKpiPanel.vue'
import SentimentDigestPanel from './SentimentDigestPanel.vue'

const router = useRouter()
const { t } = useI18n()

const props = defineProps({
  reportId: String,
  simulationId: String,
  systemLogs: Array
})

const emit = defineEmits(['add-log', 'update-status'])

// Navigation
const goToInteraction = () => {
  if (props.reportId) {
    router.push({ name: 'Interaction', params: { reportId: props.reportId } })
  }
}

// State
const agentLogs = ref([])
const consoleLogs = ref([])
const agentLogLine = ref(0)
const consoleLogLine = ref(0)
const reportOutline = ref(null)
const currentSectionIndex = ref(null)
const generatedSections = ref({})
const expandedContent = ref(new Set())
const expandedLogs = ref(new Set())
const isComplete = ref(false)
const startTime = ref(null)

// The progress panel is open while there is progress to watch and folds itself
// once there is not. Only on the transition, never on the value: a reader who
// reopens a finished report's progress must not have it snap shut again, and
// opening a report that finished long ago starts folded.
const showProgress = ref(true)
watch(isComplete, (complete) => {
  if (complete) showProgress.value = false
})
const rightPanel = ref(null)
const logContent = ref(null)
const showRawResult = reactive({})

// Toggle functions
const toggleRawResult = (timestamp, event) => {
  // Remember where the button sits in the viewport
  const button = event?.target
  const buttonRect = button?.getBoundingClientRect()
  const buttonTopBeforeToggle = buttonRect?.top
  
  // Toggle the state
  showRawResult[timestamp] = !showRawResult[timestamp]
  
  // Once the DOM settles, adjust the scroll so the button stays put
  if (button && buttonTopBeforeToggle !== undefined && rightPanel.value) {
    nextTick(() => {
      const newButtonRect = button.getBoundingClientRect()
      const buttonTopAfterToggle = newButtonRect.top
      const scrollDelta = buttonTopAfterToggle - buttonTopBeforeToggle
      
      // Adjust the scroll position
      rightPanel.value.scrollTop += scrollDelta
    })
  }
}

const toggleSectionContent = (idx) => {
  if (!generatedSections.value[idx + 1]) return
  const newSet = new Set(expandedContent.value)
  if (newSet.has(idx)) {
    newSet.delete(idx)
  } else {
    newSet.add(idx)
  }
  expandedContent.value = newSet
}

const toggleLogExpand = (log) => {
  const newSet = new Set(expandedLogs.value)
  if (newSet.has(log.timestamp)) {
    newSet.delete(log.timestamp)
  } else {
    newSet.add(log.timestamp)
  }
  expandedLogs.value = newSet
}

const isLogCollapsed = (log) => {
  if (['tool_call', 'tool_result', 'llm_response'].includes(log.action)) {
    return !expandedLogs.value.has(log.timestamp)
  }
  return false
}

// Tool configurations with display names and colors
const toolConfig = {
  'insight_forge': {
    name: 'Deep Insight',
    color: 'purple',
    icon: 'lightbulb' // Insight
  },
  'panorama_search': {
    name: 'Panorama Search',
    color: 'blue',
    icon: 'globe' // Panoramic search
  },
  'interview_agents': {
    name: 'Agent Interview',
    color: 'green',
    icon: 'users' // Conversation
  },
  'quick_search': {
    name: 'Quick Search',
    color: 'orange',
    icon: 'zap' // Fast
  },
  'get_graph_statistics': {
    name: 'Graph Stats',
    color: 'cyan',
    icon: 'chart' // Statistics
  },
  'get_entities_by_type': {
    name: 'Entity Query',
    color: 'pink',
    icon: 'database' // Entities
  }
}

const getToolDisplayName = (toolName) => {
  return toolConfig[toolName]?.name || toolName
}

const getToolColor = (toolName) => {
  return toolConfig[toolName]?.color || 'gray'
}

const getToolIcon = (toolName) => {
  return toolConfig[toolName]?.icon || 'tool'
}

// The backend emits this when an agent gave no answer on a platform.
const PLATFORM_PLACEHOLDERS = [
  '(no reply from this platform)'
]

const isPlatformPlaceholder = (text) => {
  if (!text) return true
  return PLATFORM_PLACEHOLDERS.includes(text.trim())
}

// Parse functions
const parseInsightForge = (text) => {
  const result = {
    query: '',
    simulationRequirement: '',
    stats: { facts: 0, entities: 0, relationships: 0 },
    subQueries: [],
    facts: [],
    entities: [],
    relations: []
  }
  
  try {
    // The analysis question
    const queryMatch = text.match(/Question:\s*(.+?)(?:\n|$)/)
    if (queryMatch) result.query = queryMatch[1].trim()
    
    // The predicted scenario
    const reqMatch = text.match(/Predicted scenario:\s*(.+?)(?:\n|$)/)
    if (reqMatch) result.simulationRequirement = reqMatch[1].trim()
    
    // The statistics line
    const factMatch = text.match(/Relevant predicted facts:\s*(\d+)/)
    const entityMatch = text.match(/Entities involved:\s*(\d+)/)
    const relMatch = text.match(/Relationship chains:\s*(\d+)/)
    if (factMatch) result.stats.facts = parseInt(factMatch[1])
    if (entityMatch) result.stats.entities = parseInt(entityMatch[1])
    if (relMatch) result.stats.relationships = parseInt(relMatch[1])
    
    // Every sub-question, uncapped
    const subQSection = text.match(/### Sub-questions analysed\n([\s\S]*?)(?=\n###|$)/)
    if (subQSection) {
      const lines = subQSection[1].split('\n').filter(l => l.match(/^\d+\./))
      result.subQueries = lines.map(l => l.replace(/^\d+\.\s*/, '').trim()).filter(Boolean)
    }
    
    // Every key fact, uncapped
    const factsSection = text.match(/### \[Key facts\][\s\S]*?\n([\s\S]*?)(?=\n###|$)/)
    if (factsSection) {
      const lines = factsSection[1].split('\n').filter(l => l.match(/^\d+\./))
      result.facts = lines.map(l => {
        const match = l.match(/^\d+\.\s*"?(.+?)"?\s*$/)
        return match ? match[1].replace(/^"|"$/g, '').trim() : l.replace(/^\d+\.\s*/, '').trim()
      }).filter(Boolean)
    }
    
    // Every core entity, with its summary and related-fact count
    const entitySection = text.match(/### \[Core entities\]\n([\s\S]*?)(?=\n###|$)/)
    if (entitySection) {
      const entityText = entitySection[1]
      // Split the entity blocks on "- **"
      const entityBlocks = entityText.split(/\n(?=- \*\*)/).filter(b => b.trim().startsWith('- **'))
      result.entities = entityBlocks.map(block => {
        const nameMatch = block.match(/^-\s*\*\*(.+?)\*\*\s*\((.+?)\)/)
        const summaryMatch = block.match(/Summary:\s*"?(.+?)"?(?:\n|$)/)
        const relatedMatch = block.match(/Related facts:\s*(\d+)/)
        return {
          name: nameMatch ? nameMatch[1].trim() : '',
          type: nameMatch ? nameMatch[2].trim() : '',
          summary: summaryMatch ? summaryMatch[1].trim() : '',
          relatedFactsCount: relatedMatch ? parseInt(relatedMatch[1]) : 0
        }
      }).filter(e => e.name)
    }
    
    // Every relationship chain, uncapped
    const relSection = text.match(/### \[Relationship chains\]\n([\s\S]*?)(?=\n###|$)/)
    if (relSection) {
      const lines = relSection[1].split('\n').filter(l => l.trim().startsWith('-'))
      result.relations = lines.map(l => {
        const match = l.match(/^-\s*(.+?)\s*--\[(.+?)\]-->\s*(.+)$/)
        if (match) {
          return { source: match[1].trim(), relation: match[2].trim(), target: match[3].trim() }
        }
        return null
      }).filter(Boolean)
    }
  } catch (e) {
    console.warn('Parse insight_forge failed:', e)
  }
  
  return result
}

const parsePanorama = (text) => {
  const result = {
    query: '',
    stats: { nodes: 0, edges: 0, activeFacts: 0, historicalFacts: 0 },
    activeFacts: [],
    historicalFacts: [],
    entities: []
  }
  
  try {
    // The query
    const queryMatch = text.match(/Query:\s*(.+?)(?:\n|$)/)
    if (queryMatch) result.query = queryMatch[1].trim()
    
    // The statistics block
    const nodesMatch = text.match(/Total nodes:\s*(\d+)/)
    const edgesMatch = text.match(/Total edges:\s*(\d+)/)
    const activeMatch = text.match(/Currently valid facts:\s*(\d+)/)
    const histMatch = text.match(/Historical\/expired facts:\s*(\d+)/)
    if (nodesMatch) result.stats.nodes = parseInt(nodesMatch[1])
    if (edgesMatch) result.stats.edges = parseInt(edgesMatch[1])
    if (activeMatch) result.stats.activeFacts = parseInt(activeMatch[1])
    if (histMatch) result.stats.historicalFacts = parseInt(histMatch[1])
    
    // Every currently valid fact, uncapped
    const activeSection = text.match(/### \[Currently valid facts\][\s\S]*?\n([\s\S]*?)(?=\n###|$)/)
    if (activeSection) {
      const lines = activeSection[1].split('\n').filter(l => l.match(/^\d+\./))
      result.activeFacts = lines.map(l => {
        // Strip the numbering and the quotes
        const factText = l.replace(/^\d+\.\s*/, '').replace(/^"|"$/g, '').trim()
        return factText
      }).filter(Boolean)
    }
    
    // Every historical or expired fact, uncapped
    const histSection = text.match(/### \[Historical\/expired facts\][\s\S]*?\n([\s\S]*?)(?=\n###|$)/)
    if (histSection) {
      const lines = histSection[1].split('\n').filter(l => l.match(/^\d+\./))
      result.historicalFacts = lines.map(l => {
        const factText = l.replace(/^\d+\.\s*/, '').replace(/^"|"$/g, '').trim()
        return factText
      }).filter(Boolean)
    }
    
    // Every entity involved, uncapped
    const entitySection = text.match(/### \[Entities involved\]\n([\s\S]*?)(?=\n###|$)/)
    if (entitySection) {
      const lines = entitySection[1].split('\n').filter(l => l.trim().startsWith('-'))
      result.entities = lines.map(l => {
        const match = l.match(/^-\s*\*\*(.+?)\*\*\s*\((.+?)\)/)
        if (match) return { name: match[1].trim(), type: match[2].trim() }
        return null
      }).filter(Boolean)
    }
  } catch (e) {
    console.warn('Parse panorama failed:', e)
  }
  
  return result
}

const parseInterview = (text) => {
  const result = {
    topic: '',
    agentCount: '',
    successCount: 0,
    totalCount: 0,
    selectionReason: '',
    interviews: [],
    summary: ''
  }
  
  try {
    // The interview topic
    const topicMatch = text.match(/\*\*Topic:\*\*\s*(.+?)(?:\n|$)/)
    if (topicMatch) result.topic = topicMatch[1].trim()
    
    // How many were interviewed, e.g. "5 / 9 simulation agents"
    const countMatch = text.match(/\*\*Interviewed:\*\*\s*(\d+)\s*\/\s*(\d+)/)
    if (countMatch) {
      result.successCount = parseInt(countMatch[1])
      result.totalCount = parseInt(countMatch[2])
      result.agentCount = `${countMatch[1]} / ${countMatch[2]}`
    }
    
    // Why those interviewees were selected
    const reasonMatch = text.match(/### Why these interviewees were selected\n([\s\S]*?)(?=\n---\n|\n### Interview transcript)/)
    if (reasonMatch) {
      result.selectionReason = reasonMatch[1].trim()
    }
    
    // Break the reasoning down per interviewee
    const parseIndividualReasons = (reasonText) => {
      const reasons = {}
      if (!reasonText) return reasons
      
      const lines = reasonText.split(/\n+/)
      let currentName = null
      let currentReason = []
      
      for (const line of lines) {
        let headerMatch = null
        let name = null
        let reasonStart = null
        
        // Form 1: N. **Name (index=X)**: reasoning
        // e.g. 1. **alumni_345 (index=1)**: as a WHU alumnus...
        headerMatch = line.match(/^\d+\.\s*\*\*([^*(]+)(?:\(index\s*=?\s*\d+\))?\*\*:\s*(.*)/)
        if (headerMatch) {
          name = headerMatch[1].trim()
          reasonStart = headerMatch[2]
        }
        
        // Form 2: - selected Name (index X): reasoning
        // e.g. - selected parent_601 (index 0): as a representative of the parents...
        if (!headerMatch) {
          headerMatch = line.match(/^-\s*Selected\s+([^(]+)(?:\(index\s*=?\s*\d+\))?:\s*(.*)/)
          if (headerMatch) {
            name = headerMatch[1].trim()
            reasonStart = headerMatch[2]
          }
        }
        
        // Form 3: - **Name (index X)**: reasoning
        // e.g. - **parent_601 (index 0)**: as a representative of the parents...
        if (!headerMatch) {
          headerMatch = line.match(/^-\s*\*\*([^*(]+)(?:\(index\s*=?\s*\d+\))?\*\*:\s*(.*)/)
          if (headerMatch) {
            name = headerMatch[1].trim()
            reasonStart = headerMatch[2]
          }
        }
        
        if (name) {
          // Store the previous person's reasoning
          if (currentName && currentReason.length > 0) {
            reasons[currentName] = currentReason.join(' ').trim()
          }
          // Start a new person
          currentName = name
          currentReason = reasonStart ? [reasonStart.trim()] : []
        } else if (currentName && line.trim() && !line.match(/^Not selected|^In summary|^Final selection/)) {
          // A continuation line of the reasoning, excluding the closing summary
          currentReason.push(line.trim())
        }
      }
      
      // Store the last person's reasoning
      if (currentName && currentReason.length > 0) {
        reasons[currentName] = currentReason.join(' ').trim()
      }
      
      return reasons
    }
    
    const individualReasons = parseIndividualReasons(result.selectionReason)
    
    // Split out each interview record
    const interviewBlocks = text.split(/#### Interview #\d+:/).slice(1)
    
    interviewBlocks.forEach((block, index) => {
      const interview = {
        num: index + 1,
        title: '',
        name: '',
        role: '',
        bio: '',
        selectionReason: '',
        questions: [],
        twitterAnswer: '',
        redditAnswer: '',
        quotes: []
      }
      
      // The role label
      const titleMatch = block.match(/^(.+?)\n/)
      if (titleMatch) interview.title = titleMatch[1].trim()
      
      // Name and role
      const nameRoleMatch = block.match(/\*\*(.+?)\*\*\s*\((.+?)\)/)
      if (nameRoleMatch) {
        interview.name = nameRoleMatch[1].trim()
        interview.role = nameRoleMatch[2].trim()
        // Attach this person's selection reasoning
        interview.selectionReason = individualReasons[interview.name] || ''
      }
      
      // The bio
      const bioMatch = block.match(/_Bio:\s*([\s\S]*?)_\n/)
      if (bioMatch) {
        interview.bio = bioMatch[1].trim().replace(/\.\.\.$/, '...')
      }
      
      // The questions
      const qMatch = block.match(/\*\*Q:\*\*\s*([\s\S]*?)(?=\n\n\*\*A:\*\*|\*\*A:\*\*)/)
      if (qMatch) {
        const qText = qMatch[1].trim()
        // Split the questions on their numbering
        const questions = qText.split(/\n\d+\.\s+/).filter(q => q.trim())
        if (questions.length > 0) {
          // A leading "1." on the first question needs special handling
          const firstQ = qText.match(/^1\.\s+(.+)/)
          if (firstQ) {
            interview.questions = [firstQ[1].trim(), ...questions.slice(1).map(q => q.trim())]
          } else {
            interview.questions = questions.map(q => q.trim())
          }
        }
      }
      
      // The answer, split across Twitter and Reddit
      const answerMatch = block.match(/\*\*A:\*\*\s*([\s\S]*?)(?=\*\*Key quotes|$)/)
      if (answerMatch) {
        const answerText = answerMatch[1].trim()
        
        // Separate the Twitter and Reddit answers
        const twitterMatch = answerText.match(/\[Twitter reply\]\n?([\s\S]*?)(?=\[Reddit reply\]|$)/)
        const redditMatch = answerText.match(/\[Reddit reply\]\n?([\s\S]*?)$/)
        
        if (twitterMatch) {
          interview.twitterAnswer = twitterMatch[1].trim()
        }
        if (redditMatch) {
          interview.redditAnswer = redditMatch[1].trim()
        }
        
        // Fallback for the older format, which carried only one platform marker
        if (!twitterMatch && redditMatch) {
          // Reddit only: promote it to the default view unless it is the placeholder
          if (interview.redditAnswer && !isPlatformPlaceholder(interview.redditAnswer)) {
            interview.twitterAnswer = interview.redditAnswer
          }
        } else if (twitterMatch && !redditMatch) {
          if (interview.twitterAnswer && !isPlatformPlaceholder(interview.twitterAnswer)) {
            interview.redditAnswer = interview.twitterAnswer
          }
        } else if (!twitterMatch && !redditMatch) {
          // No platform markers at all (the oldest format): take the whole text
          interview.twitterAnswer = answerText
        }
      }
      
      // The key quotes, in any of the quote styles
      const quotesMatch = block.match(/\*\*Key quotes:\*\*\n([\s\S]*?)(?=\n---|\n####|$)/)
      if (quotesMatch) {
        const quotesText = quotesMatch[1]
        // Prefer the > "text" form
        let quoteMatches = quotesText.match(/> "([^"]+)"/g)
        // Fall back to > "text" or the curly-quoted form
        if (!quoteMatches) {
          quoteMatches = quotesText.match(/> [\u201C""]([^\u201D""]+)[\u201D""]/g)
        }
        if (quoteMatches) {
          interview.quotes = quoteMatches
            .map(q => q.replace(/^> [\u201C""]|[\u201D""]$/g, '').trim())
            .filter(q => q)
        }
      }
      
      if (interview.name || interview.title) {
        result.interviews.push(interview)
      }
    })
    
    // The interview summary
    const summaryMatch = text.match(/### Interview summary and key views\n([\s\S]*?)$/)
    if (summaryMatch) {
      result.summary = summaryMatch[1].trim()
    }
  } catch (e) {
    console.warn('Parse interview failed:', e)
  }
  
  return result
}

const parseQuickSearch = (text) => {
  const result = {
    query: '',
    count: 0,
    facts: [],
    edges: [],
    nodes: []
  }
  
  try {
    // The search query
    const queryMatch = text.match(/Search query:\s*(.+?)(?:\n|$)/)
    if (queryMatch) result.query = queryMatch[1].trim()
    
    // The result count
    const countMatch = text.match(/Found\s*(\d+)\s*matches/)
    if (countMatch) result.count = parseInt(countMatch[1] ?? countMatch[2])
    
    // Every related fact, uncapped
    const factsSection = text.match(/### Related facts:\n([\s\S]*)$/)
    if (factsSection) {
      const lines = factsSection[1].split('\n').filter(l => l.match(/^\d+\./))
      result.facts = lines.map(l => l.replace(/^\d+\.\s*/, '').trim()).filter(Boolean)
    }
    
    // Edge information, when present
    const edgesSection = text.match(/### Related edges:\n([\s\S]*?)(?=\n###|$)/)
    if (edgesSection) {
      const lines = edgesSection[1].split('\n').filter(l => l.trim().startsWith('-'))
      result.edges = lines.map(l => {
        const match = l.match(/^-\s*(.+?)\s*--\[(.+?)\]-->\s*(.+)$/)
        if (match) {
          return { source: match[1].trim(), relation: match[2].trim(), target: match[3].trim() }
        }
        return null
      }).filter(Boolean)
    }
    
    // Node information, when present
    const nodesSection = text.match(/### Related nodes:\n([\s\S]*?)(?=\n###|$)/)
    if (nodesSection) {
      const lines = nodesSection[1].split('\n').filter(l => l.trim().startsWith('-'))
      result.nodes = lines.map(l => {
        const match = l.match(/^-\s*\*\*(.+?)\*\*\s*\((.+?)\)/)
        if (match) return { name: match[1].trim(), type: match[2].trim() }
        const simpleMatch = l.match(/^-\s*(.+)$/)
        if (simpleMatch) return { name: simpleMatch[1].trim(), type: '' }
        return null
      }).filter(Boolean)
    }
  } catch (e) {
    console.warn('Parse quick_search failed:', e)
  }
  
  return result
}

// ========== Sub Components ==========

// Insight Display Component - Enhanced with full data rendering (Interview-like style)
const InsightDisplay = {
  props: ['result', 'resultLength'],
  setup(props) {
    const { t } = useI18n()
    const activeTab = ref('facts') // 'facts', 'entities', 'relations', 'subqueries'
    const expandedFacts = ref(false)
    const expandedEntities = ref(false)
    const expandedRelations = ref(false)
    const INITIAL_SHOW_COUNT = 5
    
    // Format result size for display
    const formatSize = (length) => {
      if (!length) return ''
      if (length >= 1000) {
        return `${(length / 1000).toFixed(1)}k chars`
      }
      return `${length} chars`
    }
    
    return () => h('div', { class: 'insight-display' }, [
      // Header Section - like interview header
      h('div', { class: 'insight-header' }, [
        h('div', { class: 'header-main' }, [
          h('div', { class: 'header-title' }, 'Deep Insight'),
          h('div', { class: 'header-stats' }, [
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.stats.facts || props.result.facts.length),
              h('span', { class: 'stat-label' }, 'Facts')
            ]),
            h('span', { class: 'stat-divider' }, '/'),
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.stats.entities || props.result.entities.length),
              h('span', { class: 'stat-label' }, 'Entities')
            ]),
            h('span', { class: 'stat-divider' }, '/'),
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.stats.relationships || props.result.relations.length),
              h('span', { class: 'stat-label' }, 'Relations')
            ]),
            props.resultLength && h('span', { class: 'stat-divider' }, '·'),
            props.resultLength && h('span', { class: 'stat-size' }, formatSize(props.resultLength))
          ])
        ]),
        props.result.query && h('div', { class: 'header-topic' }, props.result.query),
        props.result.simulationRequirement && h('div', { class: 'header-scenario' }, [
          h('span', { class: 'scenario-label' }, t('step4.scenarioLabel')),
          h('span', { class: 'scenario-text' }, props.result.simulationRequirement)
        ])
      ]),
      
      // Tab Navigation
      h('div', { class: 'insight-tabs' }, [
        h('button', {
          class: ['insight-tab', { active: activeTab.value === 'facts' }],
          onClick: () => { activeTab.value = 'facts' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabKeyFacts', { count: props.result.facts.length }))
        ]),
        h('button', {
          class: ['insight-tab', { active: activeTab.value === 'entities' }],
          onClick: () => { activeTab.value = 'entities' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabCoreEntities', { count: props.result.entities.length }))
        ]),
        h('button', {
          class: ['insight-tab', { active: activeTab.value === 'relations' }],
          onClick: () => { activeTab.value = 'relations' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabRelationChains', { count: props.result.relations.length }))
        ]),
        props.result.subQueries.length > 0 && h('button', {
          class: ['insight-tab', { active: activeTab.value === 'subqueries' }],
          onClick: () => { activeTab.value = 'subqueries' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabSubQueries', { count: props.result.subQueries.length }))
        ])
      ]),
      
      // Tab Content
      h('div', { class: 'insight-content' }, [
        // Facts Tab
        activeTab.value === 'facts' && props.result.facts.length > 0 && h('div', { class: 'facts-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelKeyFacts')),
            h('span', { class: 'panel-count' }, t('step4.totalCount', { count: props.result.facts.length }))
          ]),
          h('div', { class: 'facts-list' },
            (expandedFacts.value ? props.result.facts : props.result.facts.slice(0, INITIAL_SHOW_COUNT)).map((fact, i) => 
              h('div', { class: 'fact-item', key: i }, [
                h('span', { class: 'fact-number' }, i + 1),
                h('div', { class: 'fact-content' }, fact)
              ])
            )
          ),
          props.result.facts.length > INITIAL_SHOW_COUNT && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedFacts.value = !expandedFacts.value }
          }, expandedFacts.value ? t('step4.collapse') : t('step4.expandAll', { count: props.result.facts.length }))
        ]),

        // Entities Tab
        activeTab.value === 'entities' && props.result.entities.length > 0 && h('div', { class: 'entities-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelCoreEntities')),
            h('span', { class: 'panel-count' }, t('step4.totalEntityCount', { count: props.result.entities.length }))
          ]),
          h('div', { class: 'entities-grid' },
            (expandedEntities.value ? props.result.entities : props.result.entities.slice(0, 12)).map((entity, i) => 
              h('div', { class: 'entity-tag', key: i, title: entity.summary || '' }, [
                h('span', { class: 'entity-name' }, entity.name),
                h('span', { class: 'entity-type' }, entity.type),
                entity.relatedFactsCount > 0 && h('span', { class: 'entity-fact-count' }, t('step4.factCount', { count: entity.relatedFactsCount }))
              ])
            )
          ),
          props.result.entities.length > 12 && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedEntities.value = !expandedEntities.value }
          }, expandedEntities.value ? t('step4.collapse') : t('step4.expandAllEntities', { count: props.result.entities.length }))
        ]),

        // Relations Tab
        activeTab.value === 'relations' && props.result.relations.length > 0 && h('div', { class: 'relations-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelRelationChains')),
            h('span', { class: 'panel-count' }, t('step4.totalCount', { count: props.result.relations.length }))
          ]),
          h('div', { class: 'relations-list' },
            (expandedRelations.value ? props.result.relations : props.result.relations.slice(0, INITIAL_SHOW_COUNT)).map((rel, i) => 
              h('div', { class: 'relation-item', key: i }, [
                h('span', { class: 'rel-source' }, rel.source),
                h('span', { class: 'rel-arrow' }, [
                  h('span', { class: 'rel-line' }),
                  h('span', { class: 'rel-label' }, rel.relation),
                  h('span', { class: 'rel-line' })
                ]),
                h('span', { class: 'rel-target' }, rel.target)
              ])
            )
          ),
          props.result.relations.length > INITIAL_SHOW_COUNT && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedRelations.value = !expandedRelations.value }
          }, expandedRelations.value ? t('step4.collapse') : t('step4.expandAll', { count: props.result.relations.length }))
        ]),

        // Sub-queries Tab
        activeTab.value === 'subqueries' && props.result.subQueries.length > 0 && h('div', { class: 'subqueries-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelSubQueries')),
            h('span', { class: 'panel-count' }, t('step4.totalEntityCount', { count: props.result.subQueries.length }))
          ]),
          h('div', { class: 'subqueries-list' },
            props.result.subQueries.map((sq, i) => 
              h('div', { class: 'subquery-item', key: i }, [
                h('span', { class: 'subquery-number' }, `Q${i + 1}`),
                h('div', { class: 'subquery-text' }, sq)
              ])
            )
          )
        ]),
        
        // Empty state
        activeTab.value === 'facts' && props.result.facts.length === 0 && h('div', { class: 'empty-state' }, t('step4.emptyKeyFacts')),
        activeTab.value === 'entities' && props.result.entities.length === 0 && h('div', { class: 'empty-state' }, t('step4.emptyCoreEntities')),
        activeTab.value === 'relations' && props.result.relations.length === 0 && h('div', { class: 'empty-state' }, t('step4.emptyRelationChains'))
      ])
    ])
  }
}

// Panorama Display Component - Enhanced with Active/Historical tabs
const PanoramaDisplay = {
  props: ['result', 'resultLength'],
  setup(props) {
    const { t } = useI18n()
    const activeTab = ref('active') // 'active', 'historical', 'entities'
    const expandedActive = ref(false)
    const expandedHistorical = ref(false)
    const expandedEntities = ref(false)
    const INITIAL_SHOW_COUNT = 5
    
    // Format result size for display
    const formatSize = (length) => {
      if (!length) return ''
      if (length >= 1000) {
        return `${(length / 1000).toFixed(1)}k chars`
      }
      return `${length} chars`
    }
    
    return () => h('div', { class: 'panorama-display' }, [
      // Header Section
      h('div', { class: 'panorama-header' }, [
        h('div', { class: 'header-main' }, [
          h('div', { class: 'header-title' }, 'Panorama Search'),
          h('div', { class: 'header-stats' }, [
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.stats.nodes),
              h('span', { class: 'stat-label' }, 'Nodes')
            ]),
            h('span', { class: 'stat-divider' }, '/'),
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.stats.edges),
              h('span', { class: 'stat-label' }, 'Edges')
            ]),
            props.resultLength && h('span', { class: 'stat-divider' }, '·'),
            props.resultLength && h('span', { class: 'stat-size' }, formatSize(props.resultLength))
          ])
        ]),
        props.result.query && h('div', { class: 'header-topic' }, props.result.query)
      ]),
      
      // Tab Navigation
      h('div', { class: 'panorama-tabs' }, [
        h('button', {
          class: ['panorama-tab', { active: activeTab.value === 'active' }],
          onClick: () => { activeTab.value = 'active' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabActiveFacts', { count: props.result.activeFacts.length }))
        ]),
        h('button', {
          class: ['panorama-tab', { active: activeTab.value === 'historical' }],
          onClick: () => { activeTab.value = 'historical' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabHistoricalFacts', { count: props.result.historicalFacts.length }))
        ]),
        h('button', {
          class: ['panorama-tab', { active: activeTab.value === 'entities' }],
          onClick: () => { activeTab.value = 'entities' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabEntities', { count: props.result.entities.length }))
        ])
      ]),
      
      // Tab Content
      h('div', { class: 'panorama-content' }, [
        // Active Facts Tab
        activeTab.value === 'active' && h('div', { class: 'facts-panel active-facts' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelActiveFacts')),
            h('span', { class: 'panel-count' }, t('step4.totalCount', { count: props.result.activeFacts.length }))
          ]),
          props.result.activeFacts.length > 0 ? h('div', { class: 'facts-list' },
            (expandedActive.value ? props.result.activeFacts : props.result.activeFacts.slice(0, INITIAL_SHOW_COUNT)).map((fact, i) => 
              h('div', { class: 'fact-item active', key: i }, [
                h('span', { class: 'fact-number' }, i + 1),
                h('div', { class: 'fact-content' }, fact)
              ])
            )
          ) : h('div', { class: 'empty-state' }, t('step4.emptyActiveFacts')),
          props.result.activeFacts.length > INITIAL_SHOW_COUNT && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedActive.value = !expandedActive.value }
          }, expandedActive.value ? t('step4.collapse') : t('step4.expandAll', { count: props.result.activeFacts.length }))
        ]),
        
        // Historical Facts Tab
        activeTab.value === 'historical' && h('div', { class: 'facts-panel historical-facts' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelHistoricalFacts')),
            h('span', { class: 'panel-count' }, t('step4.totalCount', { count: props.result.historicalFacts.length }))
          ]),
          props.result.historicalFacts.length > 0 ? h('div', { class: 'facts-list' },
            (expandedHistorical.value ? props.result.historicalFacts : props.result.historicalFacts.slice(0, INITIAL_SHOW_COUNT)).map((fact, i) => 
              h('div', { class: 'fact-item historical', key: i }, [
                h('span', { class: 'fact-number' }, i + 1),
                h('div', { class: 'fact-content' }, [
                  // Pull the [time - time] window out, when present
                  (() => {
                    const timeMatch = fact.match(/^\[(.+?)\]\s*(.*)$/)
                    if (timeMatch) {
                      return [
                        h('span', { class: 'fact-time' }, timeMatch[1]),
                        h('span', { class: 'fact-text' }, timeMatch[2])
                      ]
                    }
                    return h('span', { class: 'fact-text' }, fact)
                  })()
                ])
              ])
            )
          ) : h('div', { class: 'empty-state' }, t('step4.emptyHistoricalFacts')),
          props.result.historicalFacts.length > INITIAL_SHOW_COUNT && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedHistorical.value = !expandedHistorical.value }
          }, expandedHistorical.value ? t('step4.collapse') : t('step4.expandAll', { count: props.result.historicalFacts.length }))
        ]),
        
        // Entities Tab
        activeTab.value === 'entities' && h('div', { class: 'entities-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelEntities')),
            h('span', { class: 'panel-count' }, t('step4.totalEntityCount', { count: props.result.entities.length }))
          ]),
          props.result.entities.length > 0 ? h('div', { class: 'entities-grid' },
            (expandedEntities.value ? props.result.entities : props.result.entities.slice(0, 8)).map((entity, i) => 
              h('div', { class: 'entity-tag', key: i }, [
                h('span', { class: 'entity-name' }, entity.name),
                entity.type && h('span', { class: 'entity-type' }, entity.type)
              ])
            )
          ) : h('div', { class: 'empty-state' }, t('step4.emptyEntities')),
          props.result.entities.length > 8 && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedEntities.value = !expandedEntities.value }
          }, expandedEntities.value ? t('step4.collapse') : t('step4.expandAllEntities', { count: props.result.entities.length }))
        ])
      ])
    ])
  }
}

// Interview Display Component - Conversation Style (Q&A Format)
const InterviewDisplay = {
  props: ['result', 'resultLength'],
  setup(props) {
    // Format result size for display
    const formatSize = (length) => {
      if (!length) return ''
      if (length >= 1000) {
        return `${(length / 1000).toFixed(1)}k chars`
      }
      return `${length} chars`
    }
    
    // Clean quote text - remove leading list numbers to avoid double numbering
    const cleanQuoteText = (text) => {
      if (!text) return ''
      // Remove leading patterns like "1. ", "2. ", "(1)" etc.
      return text.replace(/^\s*\d+[\.\)]\s*/, '').trim()
    }
    
    const activeIndex = ref(0)
    const expandedAnswers = ref(new Set())
    // Each question-answer pair tracks its own platform selection
    const platformTabs = reactive({}) // { 'agentIdx-qIdx': 'twitter' | 'reddit' }
    
    // Which platform is selected for this question?
    const getPlatformTab = (agentIdx, qIdx) => {
      const key = `${agentIdx}-${qIdx}`
      return platformTabs[key] || 'twitter'
    }
    
    // Set the platform for this question
    const setPlatformTab = (agentIdx, qIdx, platform) => {
      const key = `${agentIdx}-${qIdx}`
      platformTabs[key] = platform
    }
    
    const toggleAnswer = (key) => {
      const newSet = new Set(expandedAnswers.value)
      if (newSet.has(key)) {
        newSet.delete(key)
      } else {
        newSet.add(key)
      }
      expandedAnswers.value = newSet
    }
    
    const formatAnswer = (text, expanded) => {
      if (!text) return ''
      if (expanded || text.length <= 400) return text
      return text.substring(0, 400) + '...'
    }
    
    // Is this the "no reply on this platform" placeholder?
    const isPlaceholderText = isPlatformPlaceholder

    // Split an answer on its question numbering
    const splitAnswerByQuestions = (answerText, questionCount) => {
      if (!answerText || questionCount <= 0) return [answerText]
      if (isPlaceholderText(answerText)) return ['']

      // Two numbering styles are supported:
      // 1. "Question X:" - what the interview prompt asks for.
      // 2. "1. " or "\n1. " - the older number-and-dot style.
      let matches = []
      let match

      // Prefer the explicit question marker
      const questionPattern = /(?:^|[\r\n]+)Question\s*(\d+):\s*/g
      while ((match = questionPattern.exec(answerText)) !== null) {
        matches.push({
          num: parseInt(match[1]),
          index: match.index,
          fullMatch: match[0]
        })
      }

      // Nothing matched: fall back to the "1." style
      if (matches.length === 0) {
        const numPattern = /(?:^|[\r\n]+)(\d+)\.\s+/g
        while ((match = numPattern.exec(answerText)) !== null) {
          matches.push({
            num: parseInt(match[1]),
            index: match.index,
            fullMatch: match[0]
          })
        }
      }

      // No numbering, or only one marker: return the whole answer
      if (matches.length <= 1) {
        const cleaned = answerText
          .replace(/^Question\s*\d+:\s*/, '')
          .replace(/^\d+\.\s+/, '')
          .trim()
        return [cleaned || answerText]
      }

      // Slice the answer at each marker
      const parts = []
      for (let i = 0; i < matches.length; i++) {
        const current = matches[i]
        const next = matches[i + 1]

        const startIdx = current.index + current.fullMatch.length
        const endIdx = next ? next.index : answerText.length

        let part = answerText.substring(startIdx, endIdx).trim()
        part = part.replace(/[\r\n]+$/, '').trim()
        parts.push(part)
      }

      if (parts.length > 0 && parts.some(p => p)) {
        return parts
      }

      return [answerText]
    }
    
    // The answer belonging to a question
    const getAnswerForQuestion = (interview, qIdx, platform) => {
      const answer = platform === 'twitter' ? interview.twitterAnswer : (interview.redditAnswer || interview.twitterAnswer)
      if (!answer || isPlaceholderText(answer)) return answer || ''

      const questionCount = interview.questions?.length || 1
      const answers = splitAnswerByQuestions(answer, questionCount)

      // The split worked and the index is in range
      if (answers.length > 1 && qIdx < answers.length) {
        return answers[qIdx] || ''
      }

      // The split failed: give the whole answer to the first question, nothing to the rest
      return qIdx === 0 ? answer : ''
    }
    
    // Does this question have a real answer on both platforms? Placeholders do not count.
    const hasMultiplePlatforms = (interview, qIdx) => {
      if (!interview.twitterAnswer || !interview.redditAnswer) return false
      const twitterAnswer = getAnswerForQuestion(interview, qIdx, 'twitter')
      const redditAnswer = getAnswerForQuestion(interview, qIdx, 'reddit')
      // Both platforms answered for real, and the answers differ
      return !isPlaceholderText(twitterAnswer) && !isPlaceholderText(redditAnswer) && twitterAnswer !== redditAnswer
    }
    
    return () => h('div', { class: 'interview-display' }, [
      // Header Section
      h('div', { class: 'interview-header' }, [
        h('div', { class: 'header-main' }, [
          h('div', { class: 'header-title' }, 'Agent Interview'),
          h('div', { class: 'header-stats' }, [
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.successCount || props.result.interviews.length),
              h('span', { class: 'stat-label' }, 'Interviewed')
            ]),
            props.result.totalCount > 0 && h('span', { class: 'stat-divider' }, '/'),
            props.result.totalCount > 0 && h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.totalCount),
              h('span', { class: 'stat-label' }, 'Total')
            ]),
            props.resultLength && h('span', { class: 'stat-divider' }, '·'),
            props.resultLength && h('span', { class: 'stat-size' }, formatSize(props.resultLength))
          ])
        ]),
        props.result.topic && h('div', { class: 'header-topic' }, props.result.topic)
      ]),
      
      // Agent Selector Tabs
      props.result.interviews.length > 0 && h('div', { class: 'agent-tabs' }, 
        props.result.interviews.map((interview, i) => h('button', {
          class: ['agent-tab', { active: activeIndex.value === i }],
          key: i,
          onClick: () => { activeIndex.value = i }
        }, [
          h('span', { class: 'tab-avatar' }, interview.name ? interview.name.charAt(0) : (i + 1)),
          h('span', { class: 'tab-name' }, interview.title || interview.name || `Agent ${i + 1}`)
        ]))
      ),
      
      // Active Interview Detail
      props.result.interviews.length > 0 && h('div', { class: 'interview-detail' }, [
        // Agent Profile Card
        h('div', { class: 'agent-profile' }, [
          h('div', { class: 'profile-avatar' }, props.result.interviews[activeIndex.value]?.name?.charAt(0) || 'A'),
          h('div', { class: 'profile-info' }, [
            h('div', { class: 'profile-name' }, props.result.interviews[activeIndex.value]?.name || 'Agent'),
            h('div', { class: 'profile-role' }, props.result.interviews[activeIndex.value]?.role || ''),
            props.result.interviews[activeIndex.value]?.bio && h('div', { class: 'profile-bio' }, props.result.interviews[activeIndex.value].bio)
          ])
        ]),
        
        // Selection reason
        props.result.interviews[activeIndex.value]?.selectionReason && h('div', { class: 'selection-reason' }, [
          h('div', { class: 'reason-label' }, t('step4.selectionReason')),
          h('div', { class: 'reason-content' }, props.result.interviews[activeIndex.value].selectionReason)
        ]),
        
        // Q&A conversation thread, one question at a time
        h('div', { class: 'qa-thread' }, 
          (props.result.interviews[activeIndex.value]?.questions?.length > 0 
            ? props.result.interviews[activeIndex.value].questions 
            : [props.result.interviews[activeIndex.value]?.question || 'No question available']
          ).map((question, qIdx) => {
            const interview = props.result.interviews[activeIndex.value]
            const currentPlatform = getPlatformTab(activeIndex.value, qIdx)
            const answerText = getAnswerForQuestion(interview, qIdx, currentPlatform)
            const hasDualPlatform = hasMultiplePlatforms(interview, qIdx)
            const expandKey = `${activeIndex.value}-${qIdx}`
            const isExpanded = expandedAnswers.value.has(expandKey)
            const isPlaceholder = isPlaceholderText(answerText)

            return h('div', { class: 'qa-pair', key: qIdx }, [
              // Question Block
              h('div', { class: 'qa-question' }, [
                h('div', { class: 'qa-badge q-badge' }, `Q${qIdx + 1}`),
                h('div', { class: 'qa-content' }, [
                  h('div', { class: 'qa-sender' }, 'Interviewer'),
                  h('div', { class: 'qa-text' }, question)
                ])
              ]),

              // Answer Block
              answerText && h('div', { class: ['qa-answer', { 'answer-placeholder': isPlaceholder }] }, [
                h('div', { class: 'qa-badge a-badge' }, `A${qIdx + 1}`),
                h('div', { class: 'qa-content' }, [
                  h('div', { class: 'qa-answer-header' }, [
                    h('div', { class: 'qa-sender' }, interview?.name || 'Agent'),
                    // Platform toggle, shown only when both platforms really answered
                    hasDualPlatform && h('div', { class: 'platform-switch' }, [
                      h('button', {
                        class: ['platform-btn', { active: currentPlatform === 'twitter' }],
                        onClick: (e) => { e.stopPropagation(); setPlatformTab(activeIndex.value, qIdx, 'twitter') }
                      }, [
                        h('svg', { class: 'platform-icon', viewBox: '0 0 24 24', width: 12, height: 12, fill: 'none', stroke: 'currentColor', 'stroke-width': 2 }, [
                          h('circle', { cx: '12', cy: '12', r: '10' }),
                          h('line', { x1: '2', y1: '12', x2: '22', y2: '12' }),
                          h('path', { d: 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z' })
                        ]),
                        h('span', {}, t('step4.world1'))
                      ]),
                      h('button', {
                        class: ['platform-btn', { active: currentPlatform === 'reddit' }],
                        onClick: (e) => { e.stopPropagation(); setPlatformTab(activeIndex.value, qIdx, 'reddit') }
                      }, [
                        h('svg', { class: 'platform-icon', viewBox: '0 0 24 24', width: 12, height: 12, fill: 'none', stroke: 'currentColor', 'stroke-width': 2 }, [
                          h('path', { d: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z' })
                        ]),
                        h('span', {}, t('step4.world2'))
                      ])
                    ])
                  ]),
                  h('div', {
                    class: ['qa-text', 'answer-text', { 'placeholder-text': isPlaceholder }],
                    innerHTML: isPlaceholder
                      ? answerText
                      : formatAnswer(answerText, isExpanded)
                          .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                          .replace(/\n/g, '<br>')
                  }),
                  // Expand/collapse button, hidden for placeholder text
                  !isPlaceholder && answerText.length > 400 && h('button', {
                    class: 'expand-answer-btn',
                    onClick: () => toggleAnswer(expandKey)
                  }, isExpanded ? 'Show Less' : 'Show More')
                ])
              ])
            ])
          })
        ),
        
        // Key Quotes Section
        props.result.interviews[activeIndex.value]?.quotes?.length > 0 && h('div', { class: 'quotes-section' }, [
          h('div', { class: 'quotes-header' }, 'Key Quotes'),
          h('div', { class: 'quotes-list' },
            props.result.interviews[activeIndex.value].quotes.slice(0, 3).map((quote, qi) => {
              const cleanedQuote = cleanQuoteText(quote)
              const displayQuote = cleanedQuote.length > 200 ? cleanedQuote.substring(0, 200) + '...' : cleanedQuote
              return h('blockquote', { 
                key: qi, 
                class: 'quote-item',
                innerHTML: renderMarkdown(displayQuote)
              })
            })
          )
        ])
      ]),

      // Summary Section (Collapsible)
      props.result.summary && h('div', { class: 'summary-section' }, [
        h('div', { class: 'summary-header' }, 'Interview Summary'),
        h('div', { 
          class: 'summary-content',
          innerHTML: renderMarkdown(props.result.summary.length > 500 ? props.result.summary.substring(0, 500) + '...' : props.result.summary)
        })
      ])
    ])
  }
}

// Quick Search Display Component - Enhanced with full data rendering
const QuickSearchDisplay = {
  props: ['result', 'resultLength'],
  setup(props) {
    const { t } = useI18n()
    const activeTab = ref('facts') // 'facts', 'edges', 'nodes'
    const expandedFacts = ref(false)
    const INITIAL_SHOW_COUNT = 5
    
    // Check if there are edges or nodes to show tabs
    const hasEdges = computed(() => props.result.edges && props.result.edges.length > 0)
    const hasNodes = computed(() => props.result.nodes && props.result.nodes.length > 0)
    const showTabs = computed(() => hasEdges.value || hasNodes.value)
    
    // Format result size for display
    const formatSize = (length) => {
      if (!length) return ''
      if (length >= 1000) {
        return `${(length / 1000).toFixed(1)}k chars`
      }
      return `${length} chars`
    }
    
    return () => h('div', { class: 'quick-search-display' }, [
      // Header Section
      h('div', { class: 'quicksearch-header' }, [
        h('div', { class: 'header-main' }, [
          h('div', { class: 'header-title' }, 'Quick Search'),
          h('div', { class: 'header-stats' }, [
            h('span', { class: 'stat-item' }, [
              h('span', { class: 'stat-value' }, props.result.count || props.result.facts.length),
              h('span', { class: 'stat-label' }, 'Results')
            ]),
            props.resultLength && h('span', { class: 'stat-divider' }, '·'),
            props.resultLength && h('span', { class: 'stat-size' }, formatSize(props.resultLength))
          ])
        ]),
        props.result.query && h('div', { class: 'header-query' }, [
          h('span', { class: 'query-label' }, t('step4.searchLabel')),
          h('span', { class: 'query-text' }, props.result.query)
        ])
      ]),
      
      // Tab Navigation (only show if there are edges or nodes)
      showTabs.value && h('div', { class: 'quicksearch-tabs' }, [
        h('button', {
          class: ['quicksearch-tab', { active: activeTab.value === 'facts' }],
          onClick: () => { activeTab.value = 'facts' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabFacts', { count: props.result.facts.length }))
        ]),
        hasEdges.value && h('button', {
          class: ['quicksearch-tab', { active: activeTab.value === 'edges' }],
          onClick: () => { activeTab.value = 'edges' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabEdges', { count: props.result.edges.length }))
        ]),
        hasNodes.value && h('button', {
          class: ['quicksearch-tab', { active: activeTab.value === 'nodes' }],
          onClick: () => { activeTab.value = 'nodes' }
        }, [
          h('span', { class: 'tab-label' }, t('step4.tabNodes', { count: props.result.nodes.length }))
        ])
      ]),
      
      // Content Area
      h('div', { class: ['quicksearch-content', { 'no-tabs': !showTabs.value }] }, [
        // Facts (always show if no tabs, or when facts tab is active)
        ((!showTabs.value) || activeTab.value === 'facts') && h('div', { class: 'facts-panel' }, [
          !showTabs.value && h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelSearchResults')),
            h('span', { class: 'panel-count' }, t('step4.totalCount', { count: props.result.facts.length }))
          ]),
          props.result.facts.length > 0 ? h('div', { class: 'facts-list' },
            (expandedFacts.value ? props.result.facts : props.result.facts.slice(0, INITIAL_SHOW_COUNT)).map((fact, i) => 
              h('div', { class: 'fact-item', key: i }, [
                h('span', { class: 'fact-number' }, i + 1),
                h('div', { class: 'fact-content' }, fact)
              ])
            )
          ) : h('div', { class: 'empty-state' }, t('step4.emptySearchResults')),
          props.result.facts.length > INITIAL_SHOW_COUNT && h('button', {
            class: 'expand-btn',
            onClick: () => { expandedFacts.value = !expandedFacts.value }
          }, expandedFacts.value ? t('step4.collapse') : t('step4.expandAll', { count: props.result.facts.length }))
        ]),
        
        // Edges Tab
        activeTab.value === 'edges' && hasEdges.value && h('div', { class: 'edges-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelRelatedEdges')),
            h('span', { class: 'panel-count' }, t('step4.totalCount', { count: props.result.edges.length }))
          ]),
          h('div', { class: 'edges-list' },
            props.result.edges.map((edge, i) => 
              h('div', { class: 'edge-item', key: i }, [
                h('span', { class: 'edge-source' }, edge.source),
                h('span', { class: 'edge-arrow' }, [
                  h('span', { class: 'edge-line' }),
                  h('span', { class: 'edge-label' }, edge.relation),
                  h('span', { class: 'edge-line' })
                ]),
                h('span', { class: 'edge-target' }, edge.target)
              ])
            )
          )
        ]),
        
        // Nodes Tab
        activeTab.value === 'nodes' && hasNodes.value && h('div', { class: 'nodes-panel' }, [
          h('div', { class: 'panel-header' }, [
            h('span', { class: 'panel-title' }, t('step4.panelRelatedNodes')),
            h('span', { class: 'panel-count' }, t('step4.totalEntityCount', { count: props.result.nodes.length }))
          ]),
          h('div', { class: 'nodes-grid' },
            props.result.nodes.map((node, i) => 
              h('div', { class: 'node-tag', key: i }, [
                h('span', { class: 'node-name' }, node.name),
                node.type && h('span', { class: 'node-type' }, node.type)
              ])
            )
          )
        ])
      ])
    ])
  }
}

// Computed
const statusClass = computed(() => {
  if (isComplete.value) return 'completed'
  if (agentLogs.value.length > 0) return 'processing'
  return 'pending'
})

const statusText = computed(() => {
  if (isComplete.value) return 'Completed'
  if (agentLogs.value.length > 0) return 'Generating...'
  return 'Waiting'
})

const totalSections = computed(() => {
  return reportOutline.value?.sections?.length || 0
})

const completedSections = computed(() => {
  return Object.keys(generatedSections.value).length
})

const progressPercent = computed(() => {
  if (totalSections.value === 0) return 0
  return Math.round((completedSections.value / totalSections.value) * 100)
})

const totalToolCalls = computed(() => {
  return agentLogs.value.filter(l => l.action === 'tool_call').length
})

const formatElapsedTime = computed(() => {
  if (!startTime.value) return '0s'
  const lastLog = agentLogs.value[agentLogs.value.length - 1]
  const elapsed = lastLog?.elapsed_seconds || 0
  if (elapsed < 60) return `${Math.round(elapsed)}s`
  const mins = Math.floor(elapsed / 60)
  const secs = Math.round(elapsed % 60)
  return `${mins}m ${secs}s`
})

const displayLogs = computed(() => {
  return agentLogs.value
})

// Workflow steps overview (status-based, no nested cards)
const activeSectionIndex = computed(() => {
  if (isComplete.value) return null
  if (currentSectionIndex.value) return currentSectionIndex.value
  if (totalSections.value > 0 && completedSections.value < totalSections.value) return completedSections.value + 1
  return null
})

const isPlanningDone = computed(() => {
  return !!reportOutline.value?.sections?.length || agentLogs.value.some(l => l.action === 'planning_complete')
})

const isPlanningStarted = computed(() => {
  return agentLogs.value.some(l => l.action === 'planning_start' || l.action === 'report_start')
})

const isFinalizing = computed(() => {
  return !isComplete.value && isPlanningDone.value && totalSections.value > 0 && completedSections.value >= totalSections.value
})

// The step currently active, shown in the header
const activeStep = computed(() => {
  const steps = workflowSteps.value
  // Find the active step
  const active = steps.find(s => s.status === 'active')
  if (active) return active
  
  // None active: fall back to the last completed step
  const doneSteps = steps.filter(s => s.status === 'done')
  if (doneSteps.length > 0) return doneSteps[doneSteps.length - 1]
  
  // Otherwise take the first step
  return steps[0] || { noLabel: '--', title: t('step4.waitingToStart'), status: 'todo', meta: '' }
})

const workflowSteps = computed(() => {
  const steps = []

  // Planning / Outline
  const planningStatus = isPlanningDone.value ? 'done' : (isPlanningStarted.value ? 'active' : 'todo')
  steps.push({
    key: 'planning',
    noLabel: 'PL',
    title: 'Planning / Outline',
    status: planningStatus,
    meta: planningStatus === 'active' ? 'IN PROGRESS' : ''
  })

  // Sections (if outline exists)
  const sections = reportOutline.value?.sections || []
  sections.forEach((section, i) => {
    const idx = i + 1
    const status = (isComplete.value || !!generatedSections.value[idx])
      ? 'done'
      : (activeSectionIndex.value === idx ? 'active' : 'todo')

    steps.push({
      key: `section-${idx}`,
      noLabel: String(idx).padStart(2, '0'),
      title: section.title,
      status,
      meta: status === 'active' ? 'IN PROGRESS' : ''
    })
  })

  // Complete
  const completeStatus = isComplete.value ? 'done' : (isFinalizing.value ? 'active' : 'todo')
  steps.push({
    key: 'complete',
    noLabel: 'OK',
    title: 'Complete',
    status: completeStatus,
    meta: completeStatus === 'active' ? 'FINALIZING' : ''
  })

  return steps
})

// Methods
const addLog = (msg) => {
  emit('add-log', msg)
}

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    return new Date(timestamp).toLocaleTimeString('en-US', { 
      hour12: false, 
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit' 
    })
  } catch {
    return ''
  }
}

const formatParams = (params) => {
  if (!params) return ''
  try {
    return JSON.stringify(params, null, 2)
  } catch {
    return String(params)
  }
}

const formatResultSize = (length) => {
  if (!length) return ''
  if (length < 1000) return `${length} chars`
  return `${(length / 1000).toFixed(1)}k chars`
}

const truncateText = (text, maxLen) => {
  if (!text) return ''
  if (text.length <= maxLen) return text
  return text.substring(0, maxLen) + '...'
}


const getTimelineItemClass = (log, idx, total) => {
  const isLatest = idx === total - 1 && !isComplete.value
  const isMilestone = log.action === 'section_complete' || log.action === 'report_complete'
  return {
    'node--active': isLatest,
    'node--done': !isLatest && isMilestone,
    'node--muted': !isLatest && !isMilestone,
    'node--tool': log.action === 'tool_call' || log.action === 'tool_result'
  }
}

const getConnectorClass = (log, idx, total) => {
  const isLatest = idx === total - 1 && !isComplete.value
  if (isLatest) return 'dot-active'
  if (log.action === 'section_complete' || log.action === 'report_complete') return 'dot-done'
  return 'dot-muted'
}

const getActionLabel = (action) => {
  const labels = {
    'report_start': 'Report Started',
    'planning_start': 'Planning',
    'planning_complete': 'Plan Complete',
    'section_start': 'Section Start',
    'section_content': 'Content Ready',
    'section_complete': 'Section Done',
    'tool_call': 'Tool Call',
    'tool_result': 'Tool Result',
    'llm_response': 'LLM Response',
    'report_complete': 'Complete'
  }
  return labels[action] || action
}

const getLogLevelClass = (log) => {
  if (log.includes('ERROR')) return 'error'
  if (log.includes('WARNING')) return 'warning'
  // INFO keeps the default colour; it is not marked as a success
  return ''
}

// Polling
let agentLogTimer = null
let consoleLogTimer = null

const fetchAgentLog = async () => {
  if (!props.reportId) return
  
  try {
    const res = await getAgentLog(props.reportId, agentLogLine.value)
    
    if (res.success && res.data) {
      const newLogs = res.data.logs || []
      
      if (newLogs.length > 0) {
        newLogs.forEach(log => {
          agentLogs.value.push(log)
          
          if (log.action === 'planning_complete' && log.details?.outline) {
            reportOutline.value = log.details.outline
          }
          
          if (log.action === 'section_start') {
            currentSectionIndex.value = log.section_index
          }

          // section_complete - the section finished
          if (log.action === 'section_complete') {
            if (log.details?.content) {
              generatedSections.value[log.section_index] = log.details.content
              // Expand the section that just finished
              expandedContent.value.add(log.section_index - 1)
              currentSectionIndex.value = null
            }
          }
          
          if (log.action === 'report_complete') {
            isComplete.value = true
            currentSectionIndex.value = null  // Make sure the loading state clears
            emit('update-status', 'completed')
            stopPolling()
            // Scrolling is handled once, in the nextTick after the loop
          }
          
          if (log.action === 'report_start') {
            startTime.value = new Date(log.timestamp)
          }
        })
        
        agentLogLine.value = res.data.from_line + newLogs.length
        
        nextTick(() => {
          if (rightPanel.value) {
            // Finished: scroll to the top. Otherwise follow the newest log at the bottom.
            if (isComplete.value) {
              rightPanel.value.scrollTop = 0
            } else {
              rightPanel.value.scrollTop = rightPanel.value.scrollHeight
            }
          }
        })
      }
    }
  } catch (err) {
    console.warn('Failed to fetch agent log:', err)
  }
}

// Pull the section content out of an LLM response
const extractFinalContent = (response) => {
  if (!response) return null
  
  // Try the contents of a <final_answer> tag
  const finalAnswerTagMatch = response.match(/<final_answer>([\s\S]*?)<\/final_answer>/)
  if (finalAnswerTagMatch) {
    return finalAnswerTagMatch[1].trim()
  }
  
  // Then whatever follows 'Final Answer:', in either layout:
  // 1. Final Answer:\n\n<content>
  // 2. Final Answer: <content>
  const finalAnswerMatch = response.match(/Final\s*Answer:\s*\n*([\s\S]*)$/i)
  if (finalAnswerMatch) {
    return finalAnswerMatch[1].trim()
  }
  
  // Starting with ##, # or > suggests the response is already markdown
  const trimmedResponse = response.trim()
  if (trimmedResponse.match(/^[#>]/)) {
    return trimmedResponse
  }
  
  // Long and markdown-shaped: strip the reasoning preamble and return the rest
  if (response.length > 300 && (response.includes('**') || response.includes('>'))) {
    // Drop the leading 'Thought:' reasoning
    const thoughtMatch = response.match(/^Thought:[\s\S]*?(?=\n\n[^T]|\n\n$)/i)
    if (thoughtMatch) {
      const afterThought = response.substring(thoughtMatch[0].length).trim()
      if (afterThought.length > 100) {
        return afterThought
      }
    }
  }
  
  return null
}

const fetchConsoleLog = async () => {
  if (!props.reportId) return
  
  try {
    const res = await getConsoleLog(props.reportId, consoleLogLine.value)
    
    if (res.success && res.data) {
      const newLogs = res.data.logs || []
      
      if (newLogs.length > 0) {
        consoleLogs.value.push(...newLogs)
        consoleLogLine.value = res.data.from_line + newLogs.length
        
        nextTick(() => {
          if (logContent.value) {
            logContent.value.scrollTop = logContent.value.scrollHeight
          }
        })
      }
    }
  } catch (err) {
    console.warn('Failed to fetch console log:', err)
  }
}

const startPolling = () => {
  if (agentLogTimer || consoleLogTimer) return
  
  fetchAgentLog()
  fetchConsoleLog()
  
  agentLogTimer = setInterval(fetchAgentLog, 2000)
  consoleLogTimer = setInterval(fetchConsoleLog, 1500)
}

const stopPolling = () => {
  if (agentLogTimer) {
    clearInterval(agentLogTimer)
    agentLogTimer = null
  }
  if (consoleLogTimer) {
    clearInterval(consoleLogTimer)
    consoleLogTimer = null
  }
}

// Lifecycle
onMounted(() => {
  if (props.reportId) {
    addLog(`Report Agent initialized: ${props.reportId}`)
    startPolling()
  }
})

onUnmounted(() => {
  stopPolling()
})

watch(() => props.reportId, (newId) => {
  if (newId) {
    agentLogs.value = []
    consoleLogs.value = []
    agentLogLine.value = 0
    consoleLogLine.value = 0
    reportOutline.value = null
    currentSectionIndex.value = null
    generatedSections.value = {}
    expandedContent.value = new Set()
    expandedLogs.value = new Set()
    isComplete.value = false
    startTime.value = null
    showProgress.value = true

    startPolling()
  }
}, { immediate: true })
</script>

<style scoped>
.report-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #F8F9FA;
  font-family: 'Inter', 'Noto Sans SC', system-ui, sans-serif;
  overflow: hidden;
}

/* Main Split Layout */
.main-split-layout {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* Panel Headers */
.panel-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  background: var(--white);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  position: sticky;
  top: 0;
  z-index: 10;
}

.header-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ink);
  box-shadow: 0 0 0 3px rgba(31, 41, 55, 0.15);
  margin-right: 10px;
  flex-shrink: 0;
  animation: pulse-dot 1.5s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% {
    box-shadow: 0 0 0 3px rgba(31, 41, 55, 0.15);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(31, 41, 55, 0.1);
  }
}

.header-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  margin-right: 10px;
  flex-shrink: 0;
}

.header-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-transform: none;
  letter-spacing: 0;
}

.header-meta {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  flex-shrink: 0;
}

/* Panel header status variants */
.panel-header--active {
  background: var(--surface);
  border-color: var(--ink);
}

.panel-header--active .header-index {
  color: var(--ink);
}

.panel-header--active .header-title {
  color: var(--ink);
}

.panel-header--active .header-meta {
  color: var(--ink);
}

.panel-header--done {
  background: var(--surface);
}

.panel-header--done .header-index {
  color: var(--success);
}

.panel-header--todo .header-index,
.panel-header--todo .header-title {
  color: var(--muted-soft);
}

/* Left Panel - Report Style */
/* The report pane's styles moved to ReportPane.vue. */



/* Right Panel */
/* Collapsed: the panel drops to a rail and the report takes the width back.
   The pane's own width lives in ReportPane's scoped styles, so overriding it
   from here needs :deep. */
.main-split-layout.progress-collapsed :deep(.left-panel.report-style) {
  width: auto;
  flex: 1;
}

/* Open: report and progress share the width evenly. ReportPane defaults to
   45%, which is its width in step 5 where there is no progress panel. */
.main-split-layout:not(.progress-collapsed) :deep(.left-panel.report-style) {
  width: 50%;
}

.progress-toggle {
  /* The panel scrolls; the way to fold it must not scroll out of reach. */
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px 16px;
  background: var(--white);
  border: none;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  cursor: pointer;
}

.progress-toggle:hover {
  background: var(--surface);
}

.progress-toggle-status {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
}

.progress-toggle-chevron {
  transform: rotate(-90deg);
  transition: transform 0.2s;
  flex-shrink: 0;
}

.progress-toggle-chevron.open {
  transform: rotate(0deg);
}

.right-panel.is-collapsed {
  flex: 0 0 auto;
  width: 220px;
  overflow: hidden;
}

.right-panel.is-collapsed .rail-next {
  margin: 12px;
  width: calc(100% - 24px);
}

.right-panel {
  flex: 1;
  background: var(--white);
  overflow-y: auto;
  display: flex;
  flex-direction: column;

  /* Functional palette (low saturation, status-based) */
  --wf-border: var(--border);
  --wf-divider: var(--surface-2);

  --wf-active-bg: var(--surface);
  --wf-active-border: var(--ink);
  --wf-active-dot: var(--ink);
  --wf-active-text: var(--ink);

  --wf-done-bg: var(--surface);
  --wf-done-border: var(--border);
  --wf-done-dot: var(--success);

  --wf-muted-dot: var(--border-strong);
  --wf-todo-text: var(--muted-soft);
}

.right-panel::-webkit-scrollbar {
  width: 6px;
}

.right-panel::-webkit-scrollbar-track {
  background: transparent;
}

.right-panel::-webkit-scrollbar-thumb {
  background: transparent;
  border-radius: 3px;
  transition: background 0.3s ease;
}

.right-panel:hover::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
}

.right-panel::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.25);
}

.mono {
  font-family: 'JetBrains Mono', monospace;
}

/* Workflow Overview */
.workflow-overview {
  padding: 16px 20px 0 20px;
}

.workflow-metrics {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.metric {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.metric-right {
  margin-left: auto;
}

.metric-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.metric-value {
  font-size: 12px;
  color: var(--ink-2);
}

.metric-pill {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--wf-border);
  background: var(--surface);
  color: var(--muted);
}

.metric-pill.pill--processing {
  background: var(--wf-active-bg);
  border-color: var(--wf-active-border);
  color: var(--wf-active-text);
}

.metric-pill.pill--completed {
  background: #ECFDF5;
  border-color: #A7F3D0;
  color: #065F46;
}

.metric-pill.pill--pending {
  background: transparent;
  border-style: dashed;
  color: var(--muted);
}

.workflow-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 10px;
}

.wf-step {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--wf-divider);
  border-radius: 8px;
  background: var(--white);
}

.wf-step--active {
  background: var(--wf-active-bg);
  border-color: var(--wf-active-border);
}

.wf-step--done {
  background: var(--wf-done-bg);
  border-color: var(--wf-done-border);
}

.wf-step--todo {
  background: transparent;
  border-color: var(--wf-border);
  border-style: dashed;
}

.wf-step-connector {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 24px;
  flex-shrink: 0;
}

.wf-step-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--wf-muted-dot);
  border: 2px solid var(--white);
  z-index: 1;
}

.wf-step-line {
  width: 2px;
  flex: 1;
  background: var(--wf-divider);
  margin-top: -2px;
}

.wf-step--active .wf-step-dot {
  background: var(--wf-active-dot);
  box-shadow: 0 0 0 3px rgba(31, 41, 55, 0.10);
}

.wf-step--done .wf-step-dot {
  background: var(--wf-done-dot);
}

.wf-step-title-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.wf-step-index {
  font-size: 12px;
  font-weight: 700;
  color: var(--muted-soft);
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.wf-step-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 13px;
  font-weight: 600;
  color: var(--black);
  line-height: 1.35;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wf-step-meta {
  margin-left: auto;
  font-size: 12px;
  font-weight: 700;
  color: var(--wf-active-text);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.wf-step--todo .wf-step-title,
.wf-step--todo .wf-step-index {
  color: var(--wf-todo-text);
}

.workflow-divider {
  height: 1px;
  background: var(--wf-divider);
  margin: 14px 0 0 0;
}

/* Workflow Timeline */
.workflow-timeline {
  padding: 14px 20px 24px;
  flex: 1;
}

.timeline-item {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 12px;
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--wf-divider);
  border-radius: 8px;
  background: var(--white);
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.timeline-item:hover {
  background: var(--surface);
  border-color: var(--wf-border);
}

.timeline-item.node--active {
  background: var(--wf-active-bg);
  border-color: var(--wf-active-border);
}

.timeline-item.node--active:hover {
  background: var(--wf-active-bg);
  border-color: var(--wf-active-border);
}

.timeline-item.node--done {
  background: var(--wf-done-bg);
  border-color: var(--wf-done-border);
}

.timeline-item.node--done:hover {
  background: var(--wf-done-bg);
  border-color: var(--wf-done-border);
}

.timeline-connector {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 24px;
  flex-shrink: 0;
}

.connector-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--wf-muted-dot);
  border: 2px solid var(--white);
  z-index: 1;
}

.connector-line {
  width: 2px;
  flex: 1;
  background: var(--wf-divider);
  margin-top: -2px;
}

/* Connector dot: status only */
.dot-active {
  background: var(--wf-active-dot);
  box-shadow: 0 0 0 3px rgba(31, 41, 55, 0.10);
}

.dot-done {
  background: var(--wf-done-dot);
}

.dot-muted {
  background: var(--wf-muted-dot);
}

.timeline-content {
  min-width: 0;
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  margin: 0;
  transition: none;
}

.timeline-content:hover {
  box-shadow: none;
}

.timeline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.action-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.action-time {
  font-size: 12px;
  color: var(--muted-soft);
  font-family: 'JetBrains Mono', monospace;
}

.timeline-body {
  font-size: 13px;
  color: var(--ink-3);
}

.timeline-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--surface-2);
}

.elapsed-placeholder {
  flex-shrink: 0;
}

.footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.elapsed-badge {
  font-size: 12px;
  color: var(--muted);
  background: var(--surface-2);
  padding: 2px 8px;
  border-radius: 10px;
  font-family: 'JetBrains Mono', monospace;
}

/* Timeline Body Elements */
.info-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.info-key {
  font-size: 12px;
  color: var(--muted-soft);
  min-width: 80px;
}

.info-val {
  color: var(--ink-2);
}

.status-message {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  border: 1px solid transparent;
}

.status-message.planning {
  background: var(--wf-active-bg);
  border-color: var(--wf-active-border);
  color: var(--wf-active-text);
}

.status-message.success {
  background: #ECFDF5;
  border-color: #A7F3D0;
  color: #065F46;
}

.outline-badge {
  display: inline-block;
  margin-top: 8px;
  padding: 4px 10px;
  background: var(--surface);
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.section-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--surface);
  border: 1px solid var(--wf-border);
  border-radius: 6px;
}

.section-tag.content-ready {
  background: var(--wf-active-bg);
  border: 1px dashed var(--wf-active-border);
}

.section-tag.content-ready svg {
  color: var(--wf-active-dot);
}


.section-tag.completed {
  background: #ECFDF5;
  border: 1px solid #A7F3D0;
}

.section-tag.completed svg {
  color: #059669;
}

.tag-num {
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}

.section-tag.completed .tag-num {
  color: #059669;
}

.tag-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-2);
}

.tool-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--surface);
  color: var(--ink-2);
  border: 1px solid var(--wf-border);
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  transition: all 0.2s ease;
}

.tool-icon {
  flex-shrink: 0;
}

/* Tool Colors - Purple (Deep Insight) */
.tool-badge.tool-purple {
  background: linear-gradient(135deg, #F5F3FF 0%, #EDE9FE 100%);
  border-color: #C4B5FD;
  color: #6D28D9;
}
.tool-badge.tool-purple .tool-icon {
  stroke: #7C3AED;
}

/* Tool Colors - Blue (Panorama Search) */
.tool-badge.tool-blue {
  background: linear-gradient(135deg, #EFF6FF 0%, var(--surface-2) 100%);
  border-color: var(--border-strong);
  color: var(--black);
}
.tool-badge.tool-blue .tool-icon {
  stroke: var(--ink);
}

/* Tool Colors - Green (Agent Interview) */
.tool-badge.tool-green {
  background: linear-gradient(135deg, #F0FDF4 0%, #DCFCE7 100%);
  border-color: #86EFAC;
  color: #15803D;
}
.tool-badge.tool-green .tool-icon {
  stroke: #16A34A;
}

/* Tool Colors - Orange (Quick Search) */
.tool-badge.tool-orange {
  background: linear-gradient(135deg, #FFF7ED 0%, #FFEDD5 100%);
  border-color: #FDBA74;
  color: #C2410C;
}
.tool-badge.tool-orange .tool-icon {
  stroke: var(--accent-strong);
}

/* Tool Colors - Cyan (Graph Stats) */
.tool-badge.tool-cyan {
  background: linear-gradient(135deg, #ECFEFF 0%, #CFFAFE 100%);
  border-color: #67E8F9;
  color: #0E7490;
}
.tool-badge.tool-cyan .tool-icon {
  stroke: #0891B2;
}

/* Tool Colors - Pink (Entity Query) */
.tool-badge.tool-pink {
  background: linear-gradient(135deg, #FDF2F8 0%, #FCE7F3 100%);
  border-color: #F9A8D4;
  color: #BE185D;
}
.tool-badge.tool-pink .tool-icon {
  stroke: #DB2777;
}

/* Tool Colors - Gray (Default) */
.tool-badge.tool-gray {
  background: linear-gradient(135deg, var(--surface) 0%, var(--surface-2) 100%);
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.tool-badge.tool-gray .tool-icon {
  stroke: var(--muted);
}

.tool-params {
  margin-top: 10px;
  background: transparent;
  border-radius: 0;
  padding: 10px 0 0 0;
  border-top: 1px dashed var(--wf-divider);
  overflow-x: auto;
}

.tool-params pre {
  margin: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--ink-3);
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
}

/* Unified Action Buttons */
.action-btn {
  background: var(--surface-2);
  border: 1px solid var(--border);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.action-btn:hover {
  background: var(--border);
  color: var(--ink-2);
  border-color: var(--border-strong);
}

/* Result Wrapper */
.result-wrapper {
  background: transparent;
  border: none;
  border-top: 1px solid var(--wf-divider);
  border-radius: 0;
  padding: 12px 0 0 0;
}

.result-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.result-tool {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}

.result-size {
  font-size: 12px;
  color: var(--muted);
  font-family: 'JetBrains Mono', monospace;
}

.result-raw {
  margin-top: 10px;
  max-height: 300px;
  overflow-y: auto;
}

.result-raw pre {
  margin: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink-2);
  background: var(--white);
  border: 1px solid var(--border);
  padding: 10px;
  border-radius: 6px;
}

.raw-preview {
  margin: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--muted);
}

/* Legacy toggle-raw removed - using unified .action-btn */

/* LLM Response */
.llm-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.meta-tag {
  font-size: 12px;
  padding: 3px 8px;
  background: var(--surface-2);
  color: var(--muted);
  border-radius: 4px;
}

.meta-tag.active {
  background: var(--surface-2);
  color: var(--black);
}

.meta-tag.final-answer {
  background: #D1FAE5;
  color: #059669;
  font-weight: 600;
}

.final-answer-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 10px 14px;
  background: #ECFDF5;
  border: 1px solid #A7F3D0;
  border-radius: 6px;
  color: #065F46;
  font-size: 12px;
  font-weight: 500;
}

.final-answer-hint svg {
  flex-shrink: 0;
}

.llm-content {
  margin-top: 10px;
  max-height: 200px;
  overflow-y: auto;
}

.llm-content pre {
  margin: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink-3);
  background: var(--surface-2);
  padding: 10px;
  border-radius: 6px;
}

/* Complete Banner */
.complete-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: #ECFDF5;
  border: 1px solid #A7F3D0;
  border-radius: 8px;
  color: #065F46;
  font-weight: 600;
  font-size: 14px;
}

.next-step-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: calc(100% - 40px);
  margin: 4px 20px 0 20px;
  padding: 14px 20px;
  font-size: 14px;
  font-weight: 600;
  color: var(--white);
  background: var(--ink);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.next-step-btn:hover {
  background: var(--ink-2);
}

.next-step-btn svg {
  transition: transform 0.2s ease;
}

.next-step-btn:hover svg {
  transform: translateX(4px);
}

/* Workflow Empty */
.workflow-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: var(--muted-soft);
  font-size: 13px;
}

.empty-pulse {
  width: 24px;
  height: 24px;
  background: var(--border);
  border-radius: 50%;
  margin-bottom: 16px;
  animation: pulse-ring 1.5s infinite;
}

@keyframes pulse-ring {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.2); opacity: 0.5; }
}

/* Timeline Transitions */
.timeline-item-enter-active {
  transition: all 0.4s ease;
}

.timeline-item-enter-from {
  opacity: 0;
  transform: translateX(-20px);
}

/* ========== Structured Result Display Components ========== */

/* Common Styles - using :deep() for dynamic components */
:deep(.stat-row) {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

:deep(.stat-box) {
  flex: 1;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 8px;
  text-align: center;
}

:deep(.stat-box .stat-num) {
  display: block;
  font-size: 20px;
  font-weight: 700;
  color: var(--black);
  font-family: 'JetBrains Mono', monospace;
}

:deep(.stat-box .stat-label) {
  display: block;
  font-size: 12px;
  color: var(--muted-soft);
  margin-top: 2px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

:deep(.stat-box.highlight) {
  background: #ECFDF5;
  border-color: #A7F3D0;
}

:deep(.stat-box.highlight .stat-num) {
  color: #059669;
}

:deep(.stat-box.muted) {
  background: var(--surface);
  border-color: var(--border);
}

:deep(.stat-box.muted .stat-num) {
  color: var(--muted);
}

:deep(.query-display) {
  background: var(--surface);
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--ink-2);
  margin-bottom: 12px;
  border: 1px solid var(--border);
  line-height: 1.5;
}

:deep(.expand-details) {
  background: var(--white);
  border: 1px solid var(--border);
  padding: 8px 14px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}

:deep(.expand-details:hover) {
  border-color: var(--border-strong);
  color: var(--ink-2);
}

:deep(.detail-content) {
  margin-top: 14px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}

:deep(.section-label) {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--surface-2);
}

/* Facts Section */
:deep(.facts-section) {
  margin-bottom: 14px;
}

:deep(.fact-row) {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--surface-2);
}

:deep(.fact-row:last-child) {
  border-bottom: none;
}

:deep(.fact-row.active) {
  background: #ECFDF5;
  margin: 0 -10px;
  padding: 8px 10px;
  border-radius: 6px;
  border-bottom: none;
}

:deep(.fact-idx) {
  min-width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface-2);
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
  flex-shrink: 0;
}

:deep(.fact-row.active .fact-idx) {
  background: #A7F3D0;
  color: #065F46;
}

:deep(.fact-text) {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.6;
}

/* Entities Section */
:deep(.entities-section) {
  margin-bottom: 14px;
}

:deep(.entity-chips) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

:deep(.entity-chip) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 12px;
}

:deep(.chip-name) {
  font-size: 12px;
  font-weight: 500;
  color: var(--black);
}

:deep(.chip-type) {
  font-size: 12px;
  color: var(--muted-soft);
  background: var(--border);
  padding: 1px 6px;
  border-radius: 3px;
}

/* Relations Section */
:deep(.relations-section) {
  margin-bottom: 14px;
}

:deep(.relation-row) {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--surface-2);
}

:deep(.relation-row:last-child) {
  border-bottom: none;
}

:deep(.rel-node) {
  font-size: 12px;
  font-weight: 500;
  color: var(--black);
  background: var(--surface-2);
  padding: 4px 10px;
  border-radius: 4px;
}

:deep(.rel-edge) {
  font-size: 12px;
  font-weight: 600;
  color: var(--white);
  background: var(--ink);
  padding: 3px 10px;
  border-radius: 10px;
}

/* ========== Interview Display - Conversation Style ========== */
:deep(.interview-display) {
  padding: 0;
}

/* Header */
:deep(.interview-display .interview-header) {
  padding: 0;
  background: transparent;
  border-bottom: none;
  margin-bottom: 16px;
}

:deep(.interview-display .header-main) {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

:deep(.interview-display .header-title) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--black);
  letter-spacing: -0.01em;
}

:deep(.interview-display .header-stats) {
  display: flex;
  align-items: center;
  gap: 6px;
}

:deep(.interview-display .stat-item) {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

:deep(.interview-display .stat-value) {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  font-family: 'JetBrains Mono', monospace;
}

:deep(.interview-display .stat-label) {
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: lowercase;
}

:deep(.interview-display .stat-divider) {
  color: var(--muted-soft);
  font-size: 12px;
}

:deep(.interview-display .stat-size) {
  font-size: 12px;
  color: var(--muted-soft);
  font-family: 'JetBrains Mono', monospace;
}

:deep(.interview-display .header-topic) {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}

/* Agent Tabs - Card Style */
:deep(.interview-display .agent-tabs) {
  display: flex;
  gap: 8px;
  padding: 0 0 14px 0;
  background: transparent;
  border-bottom: 1px solid var(--surface-2);
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

:deep(.interview-display .agent-tabs::-webkit-scrollbar) {
  height: 4px;
}

:deep(.interview-display .agent-tabs::-webkit-scrollbar-track) {
  background: transparent;
}

:deep(.interview-display .agent-tabs::-webkit-scrollbar-thumb) {
  background: var(--border);
  border-radius: 2px;
}

:deep(.interview-display .agent-tabs::-webkit-scrollbar-thumb:hover) {
  background: var(--border-strong);
}

:deep(.interview-display .agent-tab) {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

:deep(.interview-display .agent-tab:hover) {
  background: var(--surface-2);
  border-color: var(--border-strong);
  color: var(--ink-2);
}

:deep(.interview-display .agent-tab.active) {
  background: linear-gradient(135deg, #EEF2FF 0%, #E0E7FF 100%);
  border-color: #A5B4FC;
  color: #4338CA;
  box-shadow: 0 1px 2px rgba(99, 102, 241, 0.1);
}

:deep(.interview-display .tab-avatar) {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--border);
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  border-radius: 50%;
  flex-shrink: 0;
}

:deep(.interview-display .agent-tab:hover .tab-avatar) {
  background: var(--border-strong);
}

:deep(.interview-display .agent-tab.active .tab-avatar) {
  background: var(--muted);
  color: var(--white);
}

:deep(.interview-display .tab-name) {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Interview Detail */
:deep(.interview-display .interview-detail) {
  padding: 12px 0;
  background: transparent;
}

/* Agent Profile - No card */
:deep(.interview-display .agent-profile) {
  display: flex;
  gap: 12px;
  padding: 0;
  background: transparent;
  border: none;
  margin-bottom: 16px;
}

:deep(.interview-display .profile-avatar) {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--border);
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  border-radius: 50%;
  flex-shrink: 0;
}

:deep(.interview-display .profile-info) {
  flex: 1;
  min-width: 0;
}

:deep(.interview-display .profile-name) {
  font-size: 13px;
  font-weight: 600;
  color: var(--black);
  margin-bottom: 2px;
}

:deep(.interview-display .profile-role) {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}

:deep(.interview-display .profile-bio) {
  font-size: 12px;
  color: var(--muted-soft);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Selection reason */
:deep(.interview-display .selection-reason) {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 16px;
}

:deep(.interview-display .reason-label) {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 6px;
}

:deep(.interview-display .reason-content) {
  font-size: 12px;
  color: #475569;
  line-height: 1.6;
}

/* Q&A Thread - Clean list */
:deep(.interview-display .qa-thread) {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

:deep(.interview-display .qa-pair) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 0;
}

:deep(.interview-display .qa-question),
:deep(.interview-display .qa-answer) {
  display: flex;
  gap: 12px;
}

:deep(.interview-display .qa-badge) {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  border-radius: 4px;
  flex-shrink: 0;
}

:deep(.interview-display .q-badge) {
  background: transparent;
  color: var(--muted-soft);
  border: 1px solid var(--border);
}

:deep(.interview-display .a-badge) {
  background: var(--ink);
  color: var(--white);
  border: 1px solid var(--ink);
}

:deep(.interview-display .qa-content) {
  flex: 1;
  min-width: 0;
}

:deep(.interview-display .qa-sender) {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

:deep(.interview-display .qa-text) {
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.6;
}

:deep(.interview-display .qa-answer) {
  background: transparent;
  padding: 0;
  border: none;
  margin-top: 0;
}

:deep(.interview-display .answer-placeholder) {
  opacity: 0.6;
}

:deep(.interview-display .placeholder-text) {
  font-style: italic;
  color: var(--muted-soft);
}

:deep(.interview-display .qa-answer-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

/* Platform Switch */
:deep(.interview-display .platform-switch) {
  display: flex;
  gap: 2px;
  background: transparent;
  padding: 0;
  border-radius: 0;
}

:deep(.interview-display .platform-btn) {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted-soft);
  cursor: pointer;
  transition: all 0.15s ease;
}

:deep(.interview-display .platform-btn:hover) {
  color: var(--muted);
}

:deep(.interview-display .platform-btn.active) {
  background: transparent;
  color: var(--ink);
  border-color: var(--border);
  box-shadow: none;
}

:deep(.interview-display .platform-icon) {
  flex-shrink: 0;
}

:deep(.interview-display .answer-text) {
  font-size: 13px;
  color: var(--black);
  line-height: 1.6;
}

:deep(.interview-display .answer-text strong) {
  color: var(--black);
  font-weight: 600;
}

:deep(.interview-display .expand-answer-btn) {
  display: inline-block;
  margin-top: 8px;
  padding: 0;
  background: transparent;
  border: none;
  border-bottom: 1px dotted var(--border-strong);
  border-radius: 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted-soft);
  cursor: pointer;
  transition: all 0.15s ease;
}

:deep(.interview-display .expand-answer-btn:hover) {
  background: transparent;
  color: var(--muted);
  border-bottom-style: solid;
}

/* Quotes Section - Clean list */
:deep(.interview-display .quotes-section) {
  background: transparent;
  border: none;
  border-top: 1px solid var(--surface-2);
  border-radius: 0;
  padding: 16px 0 0 0;
  margin-top: 16px;
}

:deep(.interview-display .quotes-header) {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 12px;
}

:deep(.interview-display .quotes-list) {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

:deep(.interview-display .quote-item) {
  margin: 0;
  padding: 10px 12px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  font-style: italic;
  color: var(--ink-3);
  line-height: 1.5;
}

/* Summary Section */
:deep(.interview-display .summary-section) {
  margin-top: 20px;
  padding: 16px 0 0 0;
  background: transparent;
  border: none;
  border-top: 1px solid var(--surface-2);
  border-radius: 0;
}

:deep(.interview-display .summary-header) {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
}

:deep(.interview-display .summary-content) {
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.6;
}

/* Markdown styles in summary */
:deep(.interview-display .summary-content h2),
:deep(.interview-display .summary-content h3),
:deep(.interview-display .summary-content h4),
:deep(.interview-display .summary-content h5) {
  margin: 12px 0 8px 0;
  font-weight: 600;
  color: var(--black);
}

:deep(.interview-display .summary-content h2) {
  font-size: 15px;
}

:deep(.interview-display .summary-content h3) {
  font-size: 14px;
}

:deep(.interview-display .summary-content h4),
:deep(.interview-display .summary-content h5) {
  font-size: 13px;
}

:deep(.interview-display .summary-content p) {
  margin: 8px 0;
}

:deep(.interview-display .summary-content strong) {
  font-weight: 600;
  color: var(--black);
}

:deep(.interview-display .summary-content em) {
  font-style: italic;
}

:deep(.interview-display .summary-content ul),
:deep(.interview-display .summary-content ol) {
  margin: 8px 0;
  padding-left: 20px;
}

:deep(.interview-display .summary-content li) {
  margin: 4px 0;
}

:deep(.interview-display .summary-content blockquote) {
  margin: 8px 0;
  padding-left: 12px;
  border-left: 3px solid var(--border);
  color: var(--muted);
  font-style: italic;
}

/* Markdown styles in quotes */
:deep(.interview-display .quote-item strong) {
  font-weight: 600;
  color: var(--ink-2);
}

:deep(.interview-display .quote-item em) {
  font-style: italic;
}

/* ========== Enhanced Insight Display Styles ========== */
:deep(.insight-display) {
  padding: 0;
}

:deep(.insight-header) {
  padding: 12px 16px;
  background: linear-gradient(135deg, #F5F3FF 0%, #EDE9FE 100%);
  border-radius: 8px 8px 0 0;
  border: 1px solid #C4B5FD;
  border-bottom: none;
}

:deep(.insight-header .header-main) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

:deep(.insight-header .header-title) {
  font-size: 14px;
  font-weight: 700;
  color: #6D28D9;
}

:deep(.insight-header .header-stats) {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}

:deep(.insight-header .stat-item) {
  display: flex;
  align-items: baseline;
  gap: 2px;
}

:deep(.insight-header .stat-value) {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: #7C3AED;
}

:deep(.insight-header .stat-label) {
  color: #8B5CF6;
  font-size: 12px;
}

:deep(.insight-header .stat-divider) {
  color: #C4B5FD;
  margin: 0 4px;
}

:deep(.insight-header .stat-size) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--muted-soft);
}

:deep(.insight-header .header-topic) {
  font-size: 13px;
  color: #5B21B6;
  line-height: 1.5;
}

:deep(.insight-header .header-scenario) {
  margin-top: 6px;
  font-size: 12px;
  color: #7C3AED;
}

:deep(.insight-header .scenario-label) {
  font-weight: 600;
}

:deep(.insight-tabs) {
  display: flex;
  gap: 2px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: none;
}

:deep(.insight-tab) {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}

:deep(.insight-tab:hover) {
  background: var(--surface-2);
  color: var(--ink-2);
}

:deep(.insight-tab.active) {
  background: var(--white);
  color: #7C3AED;
  border-color: #C4B5FD;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}


:deep(.insight-content) {
  padding: 12px;
  background: var(--white);
  border: 1px solid var(--border);
  border-top: none;
  border-radius: 0 0 8px 8px;
}

:deep(.insight-display .panel-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--surface-2);
}

:deep(.insight-display .panel-title) {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}

:deep(.insight-display .panel-count) {
  font-size: 12px;
  color: var(--muted-soft);
}

:deep(.insight-display .facts-list),
:deep(.insight-display .relations-list),
:deep(.insight-display .subqueries-list) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

:deep(.insight-display .entities-grid) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

:deep(.insight-display .fact-item) {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.insight-display .fact-number) {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--border);
  border-radius: 50%;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}

:deep(.insight-display .fact-content) {
  flex: 1;
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
}

/* Entity Tag Styles - Compact multi-column layout */
:deep(.insight-display .entity-tag) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: default;
  transition: all 0.15s ease;
}

:deep(.insight-display .entity-tag:hover) {
  background: var(--surface-2);
  border-color: var(--border-strong);
}

:deep(.insight-display .entity-tag .entity-name) {
  font-size: 12px;
  font-weight: 500;
  color: var(--black);
}

:deep(.insight-display .entity-tag .entity-type) {
  font-size: 12px;
  color: #7C3AED;
  background: #EDE9FE;
  padding: 1px 4px;
  border-radius: 3px;
}

:deep(.insight-display .entity-tag .entity-fact-count) {
  font-size: 12px;
  color: var(--muted-soft);
  margin-left: 2px;
}

/* Legacy entity card styles for backwards compatibility */
:deep(.insight-display .entity-card) {
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
}

:deep(.insight-display .entity-header) {
  display: flex;
  align-items: center;
  gap: 10px;
}

:deep(.insight-display .entity-info) {
  flex: 1;
}

:deep(.insight-display .entity-card .entity-name) {
  font-size: 13px;
  font-weight: 600;
  color: var(--black);
}

:deep(.insight-display .entity-card .entity-type) {
  font-size: 12px;
  color: #7C3AED;
  background: #EDE9FE;
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-block;
  margin-top: 2px;
}

:deep(.insight-display .entity-card .entity-fact-count) {
  font-size: 12px;
  color: var(--muted-soft);
  background: var(--surface-2);
  padding: 2px 6px;
  border-radius: 4px;
}

:deep(.insight-display .entity-summary) {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}

/* Relation Item Styles */
:deep(.insight-display .relation-item) {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.insight-display .rel-source),
:deep(.insight-display .rel-target) {
  padding: 4px 8px;
  background: var(--white);
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-2);
}

:deep(.insight-display .rel-arrow) {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
}

:deep(.insight-display .rel-line) {
  flex: 1;
  height: 1px;
  background: var(--border-strong);
}

:deep(.insight-display .rel-label) {
  padding: 2px 6px;
  background: #EDE9FE;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  color: #7C3AED;
  white-space: nowrap;
}

/* Sub-query Styles */
:deep(.insight-display .subquery-item) {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.insight-display .subquery-number) {
  flex-shrink: 0;
  padding: 2px 6px;
  background: #7C3AED;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: var(--white);
}

:deep(.insight-display .subquery-text) {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.5;
}

/* Expand Button */
:deep(.insight-display .expand-btn),
:deep(.panorama-display .expand-btn),
:deep(.quick-search-display .expand-btn) {
  display: block;
  width: 100%;
  margin-top: 12px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
  text-align: center;
}

:deep(.insight-display .expand-btn:hover),
:deep(.panorama-display .expand-btn:hover),
:deep(.quick-search-display .expand-btn:hover) {
  background: var(--surface-2);
  color: var(--ink-2);
  border-color: var(--border-strong);
}

/* Empty State */
:deep(.insight-display .empty-state),
:deep(.panorama-display .empty-state),
:deep(.quick-search-display .empty-state) {
  padding: 24px;
  text-align: center;
  font-size: 12px;
  color: var(--muted-soft);
}

/* ========== Enhanced Panorama Display Styles ========== */
:deep(.panorama-display) {
  padding: 0;
}

:deep(.panorama-header) {
  padding: 12px 16px;
  background: linear-gradient(135deg, #EFF6FF 0%, var(--surface-2) 100%);
  border-radius: 8px 8px 0 0;
  border: 1px solid var(--border-strong);
  border-bottom: none;
}

:deep(.panorama-header .header-main) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

:deep(.panorama-header .header-title) {
  font-size: 14px;
  font-weight: 700;
  color: var(--black);
}

:deep(.panorama-header .header-stats) {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}

:deep(.panorama-header .stat-item) {
  display: flex;
  align-items: baseline;
  gap: 2px;
}

:deep(.panorama-header .stat-value) {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: var(--ink);
}

:deep(.panorama-header .stat-label) {
  color: var(--muted-soft);
  font-size: 12px;
}

:deep(.panorama-header .stat-divider) {
  color: var(--muted-soft);
  margin: 0 4px;
}

:deep(.panorama-header .stat-size) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--muted-soft);
}

:deep(.panorama-header .header-topic) {
  font-size: 13px;
  color: var(--black);
  line-height: 1.5;
}

:deep(.panorama-tabs) {
  display: flex;
  gap: 2px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: none;
}

:deep(.panorama-tab) {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}

:deep(.panorama-tab:hover) {
  background: var(--surface-2);
  color: var(--ink-2);
}

:deep(.panorama-tab.active) {
  background: var(--white);
  color: var(--ink);
  border-color: var(--border-strong);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}


:deep(.panorama-content) {
  padding: 12px;
  background: var(--white);
  border: 1px solid var(--border);
  border-top: none;
  border-radius: 0 0 8px 8px;
}

:deep(.panorama-display .panel-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--surface-2);
}

:deep(.panorama-display .panel-title) {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}

:deep(.panorama-display .panel-count) {
  font-size: 12px;
  color: var(--muted-soft);
}

:deep(.panorama-display .facts-list) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

:deep(.panorama-display .fact-item) {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.panorama-display .fact-item.active) {
  background: var(--surface);
  border-color: var(--border);
}

:deep(.panorama-display .fact-item.historical) {
  background: var(--surface);
  border-color: var(--border);
}

:deep(.panorama-display .fact-number) {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--border);
  border-radius: 50%;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}

:deep(.panorama-display .fact-item.active .fact-number) {
  background: var(--border);
  color: var(--muted);
}

:deep(.panorama-display .fact-item.historical .fact-number) {
  background: var(--muted-soft);
  color: var(--white);
}

:deep(.panorama-display .fact-content) {
  flex: 1;
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
}

:deep(.panorama-display .fact-time) {
  display: block;
  font-size: 12px;
  color: var(--muted-soft);
  margin-bottom: 4px;
  font-family: 'JetBrains Mono', monospace;
}

:deep(.panorama-display .fact-text) {
  display: block;
}

/* Entities Grid */
:deep(.panorama-display .entities-grid) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

:deep(.panorama-display .entity-tag) {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.panorama-display .entity-name) {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-2);
}

:deep(.panorama-display .entity-type) {
  font-size: 12px;
  color: var(--ink);
  background: var(--surface-2);
  padding: 2px 6px;
  border-radius: 4px;
}

/* ========== Enhanced Quick Search Display Styles ========== */
:deep(.quick-search-display) {
  padding: 0;
}

:deep(.quicksearch-header) {
  padding: 12px 16px;
  background: linear-gradient(135deg, #FFF7ED 0%, #FFEDD5 100%);
  border-radius: 8px 8px 0 0;
  border: 1px solid #FDBA74;
  border-bottom: none;
}

:deep(.quicksearch-header .header-main) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

:deep(.quicksearch-header .header-title) {
  font-size: 14px;
  font-weight: 700;
  color: #C2410C;
}

:deep(.quicksearch-header .header-stats) {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}

:deep(.quicksearch-header .stat-item) {
  display: flex;
  align-items: baseline;
  gap: 2px;
}

:deep(.quicksearch-header .stat-value) {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: var(--accent-strong);
}

:deep(.quicksearch-header .stat-label) {
  color: #FB923C;
  font-size: 12px;
}

:deep(.quicksearch-header .stat-divider) {
  color: #FDBA74;
  margin: 0 4px;
}

:deep(.quicksearch-header .stat-size) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--muted-soft);
}

:deep(.quicksearch-header .header-query) {
  font-size: 13px;
  color: #9A3412;
  line-height: 1.5;
}

:deep(.quicksearch-header .query-label) {
  font-weight: 600;
}

:deep(.quicksearch-tabs) {
  display: flex;
  gap: 2px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: none;
}

:deep(.quicksearch-tab) {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}

:deep(.quicksearch-tab:hover) {
  background: var(--surface-2);
  color: var(--ink-2);
}

:deep(.quicksearch-tab.active) {
  background: var(--white);
  color: var(--accent-strong);
  border-color: #FDBA74;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}


:deep(.quicksearch-content) {
  padding: 12px;
  background: var(--white);
  border: 1px solid var(--border);
  border-top: none;
  border-radius: 0 0 8px 8px;
}

/* When there are no tabs, content connects directly to header */
:deep(.quicksearch-content.no-tabs) {
  border-top: none;
}

:deep(.quick-search-display .panel-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--surface-2);
}

:deep(.quick-search-display .panel-title) {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}

:deep(.quick-search-display .panel-count) {
  font-size: 12px;
  color: var(--muted-soft);
}

:deep(.quick-search-display .facts-list) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

:deep(.quick-search-display .fact-item) {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.quick-search-display .fact-item.active) {
  background: var(--surface);
  border-color: var(--border);
}

:deep(.quick-search-display .fact-number) {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--border);
  border-radius: 50%;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}

:deep(.quick-search-display .fact-item.active .fact-number) {
  background: var(--border);
  color: var(--muted);
}

:deep(.quick-search-display .fact-content) {
  flex: 1;
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
}

/* Edges Panel */
:deep(.quick-search-display .edges-list) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

:deep(.quick-search-display .edge-item) {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.quick-search-display .edge-source),
:deep(.quick-search-display .edge-target) {
  padding: 4px 8px;
  background: var(--white);
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-2);
}

:deep(.quick-search-display .edge-arrow) {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
}

:deep(.quick-search-display .edge-line) {
  flex: 1;
  height: 1px;
  background: var(--border-strong);
}

:deep(.quick-search-display .edge-label) {
  padding: 2px 6px;
  background: #FFEDD5;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  color: #C2410C;
  white-space: nowrap;
}

/* Nodes Grid */
:deep(.quick-search-display .nodes-grid) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

:deep(.quick-search-display .node-tag) {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

:deep(.quick-search-display .node-name) {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-2);
}

:deep(.quick-search-display .node-type) {
  font-size: 12px;
  color: var(--accent-strong);
  background: #FFEDD5;
  padding: 2px 6px;
  border-radius: 4px;
}

/* Console logs - kept in step with Step3Simulation.vue */
.console-logs {
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

.log-title {
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 100px;
  overflow-y: auto;
  padding-right: 4px;
}

.log-content::-webkit-scrollbar { width: 4px; }
.log-content::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 2px; }

.log-line {
  font-size: 12px;
  line-height: 1.5;
}

.log-msg {
  color: var(--ink-2);
  word-break: break-all;
}

.log-msg.error { color: #C62828; }
.log-msg.warning { color: var(--accent-strong); }
.log-msg.success { color: #2E7D32; }

/* ---------- Narrow screens ----------
   The two panes stop being readable side by side well before they stop
   fitting, so below this they stack and the shell scrolls as one column. */
@media (max-width: 1024px) {
  .main-split-layout {
    flex-direction: column;
    overflow: visible;
  }

  .left-panel.report-style {
    width: 100%;
    min-width: 0;
    border-right: none;
    border-bottom: 1px solid var(--border);
    padding: 24px 20px 40px;
  }

  .right-panel,
  .right-panel.is-collapsed {
    width: 100%;
    min-width: 0;
  }
}
</style>

<style>
/* English locale: smaller report title */
html[lang="en"] .report-header-block .main-title {
  font-size: 28px;
}
</style>
