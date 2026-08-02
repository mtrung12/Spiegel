<template>
  <div class="simulation-panel">
    <!-- `startError` was set on every failed launch and never rendered, so a
         run that never started looked like one still warming up. -->
    <AppBanner
      :message="runError"
      :retryable="!readOnly"
      :busy="isStarting || isGeneratingReport"
      @retry="retryRunError"
      @dismiss="dismissRunError"
    />

    <!-- Top Control Bar -->
    <div class="control-bar">
      <!-- One compact progress line; the per-platform detail is behind the chevron -->
      <div class="status-summary">
        <button class="detail-toggle" @click="showPlatformDetail = !showPlatformDetail">
          {{ showPlatformDetail ? '▾' : '▸' }}
        </button>
        <span class="summary-round mono">
          {{ $t('step3.summaryRound') }}
          {{ Math.max(runStatus.twitter_current_round || 0, runStatus.reddit_current_round || 0) }}<span class="stat-total">/{{ runStatus.total_rounds || maxRounds || '-' }}</span>
        </span>
        <span class="summary-acts mono">{{ allActions.length }} {{ $t('step3.summaryEvents') }}</span>
      </div>

      <div class="status-group" v-show="showPlatformDetail">
        <!-- Twitter progress -->
        <div class="platform-status twitter" :class="{ active: runStatus.twitter_running, completed: runStatus.twitter_completed }">
          <div class="platform-header">
            <svg class="platform-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
            </svg>
            <span class="platform-name">{{ $t('step3.platformInfoPlaza') }}</span>
            <span v-if="runStatus.twitter_completed" class="status-badge">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          </div>
          <div class="platform-stats">
            <span class="stat">
              <span class="stat-label">{{ $t('step3.statRound') }}</span>
              <span class="stat-value mono">{{ runStatus.twitter_current_round || 0 }}<span class="stat-total">/{{ runStatus.total_rounds || maxRounds || '-' }}</span></span>
            </span>
            <span class="stat">
              <span class="stat-label">{{ $t('step3.statTime') }}</span>
              <span class="stat-value mono">{{ twitterElapsedTime }}</span>
            </span>
            <span class="stat">
              <span class="stat-label">{{ $t('step3.statActs') }}</span>
              <span class="stat-value mono">{{ runStatus.twitter_actions_count || 0 }}</span>
            </span>
          </div>
          <!-- Available actions -->
          <div class="actions-tooltip">
            <div class="tooltip-title">{{ $t('step3.availableActions') }}</div>
            <div class="tooltip-actions">
              <span class="tooltip-action">{{ $t('step3.actions.post') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.comment') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.like') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.repost') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.quote') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.follow') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.idle') }}</span>
            </div>
          </div>
        </div>
        
        <!-- Reddit progress -->
        <div class="platform-status reddit" :class="{ active: runStatus.reddit_running, completed: runStatus.reddit_completed }">
          <div class="platform-header">
            <svg class="platform-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
            </svg>
            <span class="platform-name">{{ $t('step3.platformTopicCommunity') }}</span>
            <span v-if="runStatus.reddit_completed" class="status-badge">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          </div>
          <div class="platform-stats">
            <span class="stat">
              <span class="stat-label">{{ $t('step3.statRound') }}</span>
              <span class="stat-value mono">{{ runStatus.reddit_current_round || 0 }}<span class="stat-total">/{{ runStatus.total_rounds || maxRounds || '-' }}</span></span>
            </span>
            <span class="stat">
              <span class="stat-label">{{ $t('step3.statTime') }}</span>
              <span class="stat-value mono">{{ redditElapsedTime }}</span>
            </span>
            <span class="stat">
              <span class="stat-label">{{ $t('step3.statActs') }}</span>
              <span class="stat-value mono">{{ runStatus.reddit_actions_count || 0 }}</span>
            </span>
          </div>
          <!-- Available actions -->
          <div class="actions-tooltip">
            <div class="tooltip-title">{{ $t('step3.availableActions') }}</div>
            <div class="tooltip-actions">
              <span class="tooltip-action">{{ $t('step3.actions.post') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.comment') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.like') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.dislike') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.search') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.trend') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.follow') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.mute') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.refresh') }}</span>
              <span class="tooltip-action">{{ $t('step3.actions.idle') }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="action-controls">
        <!-- View toggle: live action stream vs. the feed as posts -->
        <div class="view-toggle">
          <button
            class="view-btn"
            :class="{ active: activeView === 'stream' }"
            @click="activeView = 'stream'"
          >
            {{ $t('step3.viewStream') }}
          </button>
          <button
            class="view-btn"
            :class="{ active: activeView === 'board' }"
            @click="activeView = 'board'"
          >
            {{ $t('step3.viewBoard') }}
          </button>
        </div>

        <!-- Stopping a run has been possible in code but not on screen: the
             handler existed with nothing bound to it, so a run could only be
             ended by letting it finish. Shown while one is live. -->
        <button
          v-if="phase === 1 && !readOnly"
          class="action-btn"
          :disabled="isStopping"
          @click="handleStopSimulation"
        >
          {{ isStopping ? $t('step3.stopping') : $t('step3.stopRun') }}
        </button>

        <button
          v-if="!readOnly"
          class="action-btn primary"
          :disabled="phase !== 2 || isGeneratingReport"
          @click="handleNextStep"
        >
          <span v-if="isGeneratingReport" class="loading-spinner-small"></span>
          {{ isGeneratingReport ? $t('step3.generatingReportBtn') : $t('step3.startGenerateReportBtn') }}
          <span v-if="!isGeneratingReport" class="arrow-icon">→</span>
        </button>
        <p v-else class="view-only-note">{{ $t('common.viewOnly') }}</p>
      </div>
    </div>

    <!-- Feed Board: the posts as stored in the simulation database -->
    <FeedBoard
      v-if="activeView === 'board'"
      :simulation-id="simulationId"
      class="board-view"
    />

    <!-- Main Content: Dual Timeline -->
    <div v-show="activeView === 'stream'" class="main-content-area" ref="scrollContainer">
      <!-- Timeline Header -->
      <div class="timeline-header" v-if="allActions.length > 0">
        <div class="timeline-stats">
          <span class="total-count">{{ $t('step3.totalEvents') }}: <span class="mono">{{ allActions.length }}</span></span>
          <span class="platform-breakdown">
            <span class="breakdown-item twitter">
              <svg class="mini-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
              <span class="mono">{{ twitterActionsCount }}</span>
            </span>
            <span class="breakdown-divider">/</span>
            <span class="breakdown-item reddit">
              <svg class="mini-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
              <span class="mono">{{ redditActionsCount }}</span>
            </span>
          </span>
        </div>
      </div>
      
      <!-- Timeline Feed -->
      <div class="timeline-feed">
        <div class="timeline-axis"></div>
        
        <TransitionGroup name="timeline-item">
          <div 
            v-for="action in chronologicalActions" 
            :key="action._uniqueId || action.id || `${action.timestamp}-${action.agent_id}`" 
            class="timeline-item"
            :class="action.platform"
          >
            <div class="timeline-marker">
              <div class="marker-dot"></div>
            </div>
            
            <div class="timeline-card">
              <div class="card-header">
                <div class="agent-info">
                  <div class="avatar-placeholder">{{ (action.agent_name || 'A')[0] }}</div>
                  <span class="agent-name">{{ action.agent_name }}</span>
                </div>
                
                <div class="header-meta">
                  <div class="platform-indicator">
                    <svg v-if="action.platform === 'twitter'" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
                    <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                  </div>
                  <div class="action-badge" :class="getActionTypeClass(action.action_type)">
                    {{ getActionTypeLabel(action.action_type) }}
                  </div>
                </div>
              </div>
              
              <div class="card-body">
                <!-- CREATE_POST -->
                <div v-if="action.action_type === 'CREATE_POST' && action.action_args?.content" class="content-text main-text">
                  {{ action.action_args.content }}
                </div>

                <!-- QUOTE_POST -->
                <template v-if="action.action_type === 'QUOTE_POST'">
                  <div v-if="action.action_args?.quote_content" class="content-text">
                    {{ action.action_args.quote_content }}
                  </div>
                  <div v-if="action.action_args?.original_content" class="quoted-block">
                    <div class="quote-header">
                      <svg class="icon-small" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
                      <span class="quote-label">@{{ action.action_args.original_author_name || 'User' }}</span>
                    </div>
                    <div class="quote-text">
                      {{ truncateContent(action.action_args.original_content, 150) }}
                    </div>
                  </div>
                </template>

                <!-- REPOST -->
                <template v-if="action.action_type === 'REPOST'">
                  <div class="repost-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
                    <span class="repost-label">Reposted from @{{ action.action_args?.original_author_name || 'User' }}</span>
                  </div>
                  <div v-if="action.action_args?.original_content" class="repost-content">
                    {{ truncateContent(action.action_args.original_content, 200) }}
                  </div>
                </template>

                <!-- LIKE_POST -->
                <template v-if="action.action_type === 'LIKE_POST'">
                  <div class="like-info">
                    <svg class="icon-small filled" viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"></path></svg>
                    <span class="like-label">Liked @{{ action.action_args?.post_author_name || 'User' }}'s post</span>
                  </div>
                  <div v-if="action.action_args?.post_content" class="liked-content">
                    "{{ truncateContent(action.action_args.post_content, 120) }}"
                  </div>
                </template>

                <!-- CREATE_COMMENT -->
                <template v-if="action.action_type === 'CREATE_COMMENT'">
                  <div v-if="action.action_args?.content" class="content-text">
                    {{ action.action_args.content }}
                  </div>
                  <div v-if="action.action_args?.post_id" class="comment-context">
                    <svg class="icon-small" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                    <span>Reply to post #{{ action.action_args.post_id }}</span>
                  </div>
                </template>

                <!-- SEARCH_POSTS -->
                <template v-if="action.action_type === 'SEARCH_POSTS'">
                  <div class="search-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                    <span class="search-label">{{ $t('step3.searchQuery') }}:</span>
                    <span class="search-query">"{{ action.action_args?.query || '' }}"</span>
                  </div>
                </template>

                <!-- FOLLOW -->
                <template v-if="action.action_type === 'FOLLOW'">
                  <div class="follow-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="8.5" cy="7" r="4"></circle><line x1="20" y1="8" x2="20" y2="14"></line><line x1="23" y1="11" x2="17" y2="11"></line></svg>
                    <span class="follow-label">Followed @{{ action.action_args?.target_user || action.action_args?.user_id || 'User' }}</span>
                  </div>
                </template>

                <!-- UPVOTE / DOWNVOTE -->
                <template v-if="action.action_type === 'UPVOTE_POST' || action.action_type === 'DOWNVOTE_POST'">
                  <div class="vote-info">
                    <svg v-if="action.action_type === 'UPVOTE_POST'" class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="18 15 12 9 6 15"></polyline></svg>
                    <svg v-else class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
                    <span class="vote-label">{{ action.action_type === 'UPVOTE_POST' ? 'Upvoted' : 'Downvoted' }} Post</span>
                  </div>
                  <div v-if="action.action_args?.post_content" class="voted-content">
                    "{{ truncateContent(action.action_args.post_content, 120) }}"
                  </div>
                </template>

                <!-- DO_NOTHING (silent) -->
                <template v-if="action.action_type === 'DO_NOTHING'">
                  <div class="idle-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                    <span class="idle-label">{{ $t('step3.actionSkipped') }}</span>
                  </div>
                </template>

                <!-- Fallback: an unknown type, or content none of the above matched -->
                <div v-if="!['CREATE_POST', 'QUOTE_POST', 'REPOST', 'LIKE_POST', 'CREATE_COMMENT', 'SEARCH_POSTS', 'FOLLOW', 'UPVOTE_POST', 'DOWNVOTE_POST', 'DO_NOTHING'].includes(action.action_type) && action.action_args?.content" class="content-text">
                  {{ action.action_args.content }}
                </div>
              </div>

              <div class="card-footer">
                <span class="time-tag">R{{ action.round_num }} • {{ formatActionTime(action.timestamp) }}</span>
                <!-- Platform tag removed as it is in header now -->
              </div>
            </div>
          </div>
        </TransitionGroup>

        <div v-if="allActions.length === 0" class="waiting-state">
          <div class="pulse-ring"></div>
          <span>{{ $t('step3.waitingForActions') }}</span>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <button type="button" class="log-header" :aria-expanded="showLogs" @click="showLogs = !showLogs">
        <span class="log-title">
          <span aria-hidden="true">{{ showLogs ? '▾' : '▸' }}</span>
          {{ $t('step3.simulationMonitor') }}
        </span>
        <span class="log-id">{{ simulationId || $t('step3.noSimulation') }}</span>
      </button>
      <div v-show="showLogs" class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  startSimulation,
  stopSimulation,
  getRunStatus,
  getRunStatusDetail
} from '../api/simulation'
import { generateReport, getReportBySimulation } from '../api/report'
import FeedBoard from './FeedBoard.vue'
import AppBanner from './AppBanner.vue'

const { t } = useI18n()

const props = defineProps({
  simulationId: String,
  // True only when step 2's launch button sent us here. Otherwise this panel
  // attaches to whatever run already exists instead of force-restarting it.
  startNewRun: { type: Boolean, default: false },
  maxRounds: Number, // Round cap, passed down from step 2
  minutesPerRound: {
    type: Number,
    default: 30 // 30 simulated minutes per round by default
  },
  projectData: Object,
  graphData: Object,
  systemLogs: Array,
  // The project moved past this step: show the run, offer nothing that would
  // generate another report from it.
  readOnly: { type: Boolean, default: false }
})

const emit = defineEmits(['next-step', 'add-log', 'update-status'])

const router = useRouter()

// State
// Which view the main area shows: 'stream' is the live action timeline,
// 'board' is the feed read back out of the simulation database.
const activeView = ref('stream')
// Both collapsed by default: the run screen showed too much at once
const showPlatformDetail = ref(false)
const showLogs = ref(false)
const isGeneratingReport = ref(false)
const phase = ref(0) // 0: not started, 1: running, 2: finished
const isStarting = ref(false)
const isStopping = ref(false)
const startError = ref(null)
const reportError = ref(null)
const runStatus = ref({})
const allActions = ref([]) // Every action, accumulated incrementally
const actionIds = ref(new Set()) // Action IDs, used to deduplicate
const scrollContainer = ref(null)

// Computed
// Actions are shown oldest first, so the newest sits at the bottom
const chronologicalActions = computed(() => {
  return allActions.value
})

// Action count per platform
const twitterActionsCount = computed(() => {
  return allActions.value.filter(a => a.platform === 'twitter').length
})

const redditActionsCount = computed(() => {
  return allActions.value.filter(a => a.platform === 'reddit').length
})

// Format the elapsed simulated time, from the round and minutes-per-round
const formatElapsedTime = (currentRound) => {
  if (!currentRound || currentRound <= 0) return '0h 0m'
  const totalMinutes = currentRound * props.minutesPerRound
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours}h ${minutes}m`
}

// Elapsed simulated time on Twitter
const twitterElapsedTime = computed(() => {
  return formatElapsedTime(runStatus.value.twitter_current_round || 0)
})

// Elapsed simulated time on Reddit
const redditElapsedTime = computed(() => {
  return formatElapsedTime(runStatus.value.reddit_current_round || 0)
})

// Methods
const addLog = (msg) => {
  emit('add-log', msg)
}

// Reset every piece of state, for a restart
const resetAllState = () => {
  phase.value = 0
  runStatus.value = {}
  allActions.value = []
  actionIds.value = new Set()
  prevTwitterRound.value = 0
  prevRedditRound.value = 0
  startError.value = null
  isStarting.value = false
  isStopping.value = false
  stopPolling()  // Stop any polling left over
}

// The two ways this step fails, sharing one banner. Whichever is set decides
// what Retry does, so the button never re-runs the wrong half of the step.
const runError = computed(() => {
  if (startError.value) return t('step3.startFailedBanner', { error: startError.value })
  if (reportError.value) return t('step3.reportFailedBanner', { error: reportError.value })
  return ''
})

const dismissRunError = () => {
  startError.value = null
  reportError.value = null
}

const retryRunError = () => {
  if (startError.value) return doStartSimulation()
  if (reportError.value) return handleNextStep()
}

// Start the simulation
const doStartSimulation = async () => {
  if (!props.simulationId) {
    addLog(t('log.errorMissingSimId'))
    return
  }

  // Reset everything first, so the previous run cannot bleed through
  resetAllState()
  
  isStarting.value = true
  startError.value = null
  addLog(t('log.startingDualSim'))
  emit('update-status', 'processing')
  
  try {
    const params = {
      simulation_id: props.simulationId,
      platform: 'parallel',
      force: true,  // Force a restart
      enable_graph_memory_update: true  // Stream activity into the graph
    }
    
    if (props.maxRounds) {
      params.max_rounds = props.maxRounds
      addLog(t('log.setMaxRounds', { rounds: props.maxRounds }))
    }
    
    addLog(t('log.graphMemoryUpdateEnabled'))
    
    const res = await startSimulation(params)
    
    if (res.success && res.data) {
      if (res.data.force_restarted) {
        addLog(t('log.oldSimCleared'))
      }
      addLog(t('log.engineStarted'))
      addLog(`  ├─ PID: ${res.data.process_pid || '-'}`)
      
      phase.value = 1
      runStatus.value = res.data
      
      startStatusPolling()
      startDetailPolling()
    } else {
      startError.value = res.error || 'failed to start'
      addLog(t('log.startFailed', { error: res.error || t('common.unknownError') }))
      emit('update-status', 'error')
    }
  } catch (err) {
    startError.value = err.message
    addLog(t('log.startException', { error: err.message }))
    emit('update-status', 'error')
  } finally {
    isStarting.value = false
  }
}

// Stop the simulation
const handleStopSimulation = async () => {
  if (!props.simulationId) return
  
  isStopping.value = true
  addLog(t('log.stoppingSim'))
  
  try {
    const res = await stopSimulation({ simulation_id: props.simulationId })
    
    if (res.success) {
      addLog(t('log.simStoppedSuccess'))
      phase.value = 2
      stopPolling()
      emit('update-status', 'completed')
    } else {
      addLog(t('log.stopFailed', { error: res.error || t('common.unknownError') }))
    }
  } catch (err) {
    addLog(t('log.stopException', { error: err.message }))
  } finally {
    isStopping.value = false
  }
}

// Poll the status
let statusTimer = null
let detailTimer = null

const startStatusPolling = () => {
  statusTimer = setInterval(fetchRunStatus, 2000)
}

const startDetailPolling = () => {
  detailTimer = setInterval(fetchRunStatusDetail, 3000)
}

const stopPolling = () => {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
  if (detailTimer) {
    clearInterval(detailTimer)
    detailTimer = null
  }
}

// Previous round per platform, used to detect a change and log it
const prevTwitterRound = ref(0)
const prevRedditRound = ref(0)

const fetchRunStatus = async () => {
  if (!props.simulationId) return
  
  try {
    const res = await getRunStatus(props.simulationId)
    
    if (res.success && res.data) {
      const data = res.data
      
      runStatus.value = data
      
      // Detect and log the round change on each platform
      if (data.twitter_current_round > prevTwitterRound.value) {
        addLog(`[Plaza] R${data.twitter_current_round}/${data.total_rounds} | T:${data.twitter_simulated_hours || 0}h | A:${data.twitter_actions_count}`)
        prevTwitterRound.value = data.twitter_current_round
      }
      
      if (data.reddit_current_round > prevRedditRound.value) {
        addLog(`[Community] R${data.reddit_current_round}/${data.total_rounds} | T:${data.reddit_simulated_hours || 0}h | A:${data.reddit_actions_count}`)
        prevRedditRound.value = data.reddit_current_round
      }
      
      // Is the simulation finished? runner_status or the platform flags say so
      const isCompleted = data.runner_status === 'completed' || data.runner_status === 'stopped'
      const isFailed = data.runner_status === 'failed'
      
      // runner_status is authoritative because the backend only publishes a
      // terminal state after the Zep ingestion barrier has completed.
      if (isFailed) {
        addLog(t('log.simFailed') + (data.error ? `: ${data.error}` : ''))
        phase.value = 2
        stopPolling()
        emit('update-status', 'error')
      } else if (isCompleted) {
        addLog(t('log.simCompleted'))
        phase.value = 2
        stopPolling()
        emit('update-status', 'completed')
      }
    }
  } catch (err) {
    console.warn('failed to fetch the run status:', err)
  }
}

// Have all enabled platforms finished?
const checkPlatformsCompleted = (data) => {
  // No platform data at all: false
  if (!data) return false
  
  // Check each platform's completion flag
  const twitterCompleted = data.twitter_completed === true
  const redditCompleted = data.reddit_completed === true
  
  // At least one finished: check that every enabled platform did.
  // A platform counts as enabled when actions_count > 0, or running was ever true.
  const twitterEnabled = (data.twitter_actions_count > 0) || data.twitter_running || twitterCompleted
  const redditEnabled = (data.reddit_actions_count > 0) || data.reddit_running || redditCompleted
  
  // No platform enabled: false
  if (!twitterEnabled && !redditEnabled) return false
  
  // Did every enabled platform finish?
  if (twitterEnabled && !twitterCompleted) return false
  if (redditEnabled && !redditCompleted) return false
  
  return true
}

const fetchRunStatusDetail = async () => {
  if (!props.simulationId) return
  
  try {
    const res = await getRunStatusDetail(props.simulationId)
    
    if (res.success && res.data) {
      // all_actions carries the complete list
      const serverActions = res.data.all_actions || []
      
      // Append the new actions, deduplicated
      let newActionsAdded = 0
      serverActions.forEach(action => {
        // Build a unique ID
        const actionId = action.id || `${action.timestamp}-${action.platform}-${action.agent_id}-${action.action_type}`
        
        if (!actionIds.value.has(actionId)) {
          actionIds.value.add(actionId)
          allActions.value.push({
            ...action,
            _uniqueId: actionId
          })
          newActionsAdded++
        }
      })
      
      // No auto-scroll: the user is free to browse the timeline.
      // New actions are appended at the bottom.
    }
  } catch (err) {
    console.warn('failed to fetch the detailed status:', err)
  }
}

// Helpers
const getActionTypeLabel = (type) => {
  const labels = {
    'CREATE_POST': 'POST',
    'REPOST': 'REPOST',
    'LIKE_POST': 'LIKE',
    'CREATE_COMMENT': 'COMMENT',
    'LIKE_COMMENT': 'LIKE',
    'DO_NOTHING': 'IDLE',
    'FOLLOW': 'FOLLOW',
    'SEARCH_POSTS': 'SEARCH',
    'QUOTE_POST': 'QUOTE',
    'UPVOTE_POST': 'UPVOTE',
    'DOWNVOTE_POST': 'DOWNVOTE'
  }
  return labels[type] || type || 'UNKNOWN'
}

const getActionTypeClass = (type) => {
  const classes = {
    'CREATE_POST': 'badge-post',
    'REPOST': 'badge-action',
    'LIKE_POST': 'badge-action',
    'CREATE_COMMENT': 'badge-comment',
    'LIKE_COMMENT': 'badge-action',
    'QUOTE_POST': 'badge-post',
    'FOLLOW': 'badge-meta',
    'SEARCH_POSTS': 'badge-meta',
    'UPVOTE_POST': 'badge-action',
    'DOWNVOTE_POST': 'badge-action',
    'DO_NOTHING': 'badge-idle'
  }
  return classes[type] || 'badge-default'
}

const truncateContent = (content, maxLength = 100) => {
  if (!content) return ''
  if (content.length > maxLength) return content.substring(0, maxLength) + '...'
  return content
}

const formatActionTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    return new Date(timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}

// The report_id of a completed report that already covers the current run, or
// null when there is none and one has to be generated. A 404 here is the
// normal "no report yet" answer.
const findReusableReport = async () => {
  try {
    const res = await getReportBySimulation(props.simulationId)
    const report = res?.data
    if (!report || report.status !== 'completed') return null

    const runFinishedAt = runStatus.value.completed_at
    // No run timestamp to compare against: treat the report as stale rather
    // than risk showing one that predates this run.
    if (!runFinishedAt) return null
    if (new Date(report.created_at) < new Date(runFinishedAt)) return null

    return report.report_id
  } catch {
    return null
  }
}

const handleNextStep = async () => {
  if (!props.simulationId) {
    addLog(t('log.errorMissingSimId'))
    return
  }

  if (isGeneratingReport.value) {
    addLog(t('log.reportRequestSent'))
    return
  }
  
  isGeneratingReport.value = true
  reportError.value = null

  try {
    // Coming back to step 3 from step 4 must not cost another full report
    // pipeline run. A report generated after this run finished still describes
    // it, so reuse it; only a report older than the run is actually stale.
    const reusableId = await findReusableReport()
    if (reusableId) {
      addLog(t('log.reportReused', { reportId: reusableId }))
      router.push({ name: 'Report', params: { reportId: reusableId } })
      return
    }

    addLog(t('log.startingReportGen'))
    const res = await generateReport({
      simulation_id: props.simulationId,
      force_regenerate: true
    })

    if (res.success && res.data) {
      const reportId = res.data.report_id
      addLog(t('log.reportGenTaskStarted', { reportId }))
      
      // Navigate to the report page
      router.push({ name: 'Report', params: { reportId } })
    } else {
      const reason = res.error || t('common.unknownError')
      addLog(t('log.reportGenFailed', { error: reason }))
      reportError.value = reason
      isGeneratingReport.value = false
    }
  } catch (err) {
    addLog(t('log.reportGenException', { error: err.message }))
    reportError.value = err.message
    isGeneratingReport.value = false
  }
}

// Scroll log to bottom
const logContent = ref(null)
watch([() => props.systemLogs?.length, showLogs], () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})

// Runner states that mean a run already owns this simulation. Re-entering step
// 3 (header tab, browser back) must attach to it, not force-restart it.
const LIVE_RUNNER_STATUSES = ['starting', 'running', 'paused', 'stopping']
const TERMINAL_RUNNER_STATUSES = ['stopped', 'completed', 'failed']

const attachOrStart = async () => {
  try {
    const res = await getRunStatus(props.simulationId)
    const status = res?.success ? res.data?.runner_status : null

    if (LIVE_RUNNER_STATUSES.includes(status)) {
      addLog(t('log.attachedToRun'))
      phase.value = 1
      runStatus.value = res.data
      prevTwitterRound.value = res.data.twitter_current_round || 0
      prevRedditRound.value = res.data.reddit_current_round || 0
      emit('update-status', 'processing')
      // Pull the backlog of actions once, then follow along.
      fetchRunStatusDetail()
      startStatusPolling()
      startDetailPolling()
      return
    }

    if (TERMINAL_RUNNER_STATUSES.includes(status)) {
      addLog(t('log.showingFinishedRun'))
      phase.value = 2
      runStatus.value = res.data
      emit('update-status', status === 'failed' ? 'error' : 'completed')
      fetchRunStatusDetail()
      return
    }
  } catch (err) {
    // No status to read: fall through and start a run.
    console.warn('failed to read the run status on mount:', err)
  }

  doStartSimulation()
}

onMounted(() => {
  addLog(t('log.step3Init'))
  if (!props.simulationId) return

  if (props.startNewRun) {
    doStartSimulation()
  } else {
    attachOrStart()
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.simulation-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--white);
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  overflow: hidden;
}

/* --- Control Bar --- */
.control-bar {
  background: var(--white);
  padding: 12px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-soft);
  z-index: 10;
  height: 64px;
  position: relative;
}

.status-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--ink-2);
}

.detail-toggle {
  border: 1px solid var(--border-soft);
  background: var(--white);
  border-radius: 4px;
  width: 22px;
  height: 22px;
  cursor: pointer;
  color: var(--muted);
  font-size: 12px;
  line-height: 1;
}

.summary-acts { color: var(--muted-soft); }

/* Detail drops below the bar so the 64px control bar keeps its height */
.status-group {
  display: flex;
  gap: 12px;
  position: absolute;
  top: 64px;
  left: 24px;
  background: var(--white);
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  padding: 8px;
  z-index: 20;
  box-shadow: 0 4px 12px rgba(0,0,0,0.06);
}

/* Platform Status Cards */
.platform-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 12px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border-soft);
  opacity: 0.7;
  transition: all 0.3s;
  min-width: 140px;
  position: relative;
  cursor: pointer;
}

.platform-status.active {
  opacity: 1;
  border-color: var(--ink-2);
  background: var(--white);
}

.platform-status.completed {
  opacity: 1;
  border-color: #1A936F;
  background: #F2FAF6;
}

/* Actions Tooltip */
.actions-tooltip {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: 8px;
  padding: 10px 14px;
  background: var(--accent);
  color: var(--white);
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  opacity: 0;
  visibility: hidden;
  transition: all 0.2s ease;
  z-index: 100;
  min-width: 180px;
  pointer-events: none;
}

.actions-tooltip::before {
  content: '';
  position: absolute;
  top: -6px;
  left: 50%;
  transform: translateX(-50%);
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-bottom: 6px solid var(--black);
}

.platform-status:hover .actions-tooltip {
  opacity: 1;
  visibility: visible;
}

.tooltip-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}

.tooltip-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tooltip-action {
  font-size: 12px;
  font-weight: 600;
  padding: 3px 8px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
  color: var(--white);
  letter-spacing: 0.03em;
}

.platform-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.platform-name {
  font-size: 12px;
  font-weight: 700;
  color: var(--black);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.platform-status.twitter .platform-icon { color: var(--black); }
.platform-status.reddit .platform-icon { color: var(--black); }

.platform-stats {
  display: flex;
  gap: 10px;
}

.stat {
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.stat-label {
  font-size: 12px;
  color: var(--muted-soft);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.stat-value {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}

.stat-total, .stat-unit {
  font-size: 12px;
  color: var(--muted-soft);
  font-weight: 400;
}

.status-badge {
  margin-left: auto;
  color: #1A936F;
  display: flex;
  align-items: center;
}

/* View Toggle: action stream vs. feed board */
.action-controls {
  display: flex;
  align-items: center;
  gap: 14px;
}

.view-toggle {
  display: flex;
  gap: 4px;
}

.view-btn {
  padding: 6px 14px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--white);
  color: var(--muted-soft);
  font-size: 12px;
  font-family: inherit;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.view-btn:hover { border-color: var(--border-strong); color: var(--ink-3); }

.view-btn.active {
  border-color: var(--ink-2);
  background: var(--ink-2);
  color: var(--white);
}

/* The board replaces the timeline, so it takes the same flex slot */
.board-view {
  flex: 1;
  min-height: 0;
}

/* Action Button */
.view-only-note {
  font-size: 12px;
  color: var(--muted);
  padding: 10px 0;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  font-size: 13px;
  font-weight: 600;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.action-btn.primary {
  background: var(--accent);
  color: var(--white);
}

.action-btn.primary:hover:not(:disabled) {
  background: var(--accent-strong);
}

.action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* --- Main Content Area --- */
.main-content-area {
  flex: 1;
  overflow-y: auto;
  position: relative;
  background: var(--white);
}

/* Timeline Header */
.timeline-header {
  position: sticky;
  top: 0;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  padding: 12px 24px;
  border-bottom: 1px solid var(--border-soft);
  z-index: 5;
  display: flex;
  justify-content: center;
}

.timeline-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 12px;
  color: var(--muted);
  background: var(--surface-2);
  padding: 4px 12px;
  border-radius: 20px;
}

.total-count {
  font-weight: 600;
  color: var(--ink-2);
}

.platform-breakdown {
  display: flex;
  align-items: center;
  gap: 8px;
}

.breakdown-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.breakdown-divider { color: var(--muted-soft); }
.breakdown-item.twitter { color: var(--black); }
.breakdown-item.reddit { color: var(--black); }

/* --- Timeline Feed --- */
.timeline-feed {
  padding: 24px 0;
  position: relative;
  min-height: 100%;
  max-width: 900px;
  margin: 0 auto;
}

.timeline-axis {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--border-soft); /* Cleaner line */
  transform: translateX(-50%);
}

.timeline-item {
  display: flex;
  justify-content: center;
  margin-bottom: 32px;
  position: relative;
  width: 100%;
}

.timeline-marker {
  position: absolute;
  left: 50%;
  top: 24px;
  width: 10px;
  height: 10px;
  background: var(--white);
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  transform: translateX(-50%);
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
}

.marker-dot {
  width: 4px;
  height: 4px;
  background: var(--border-strong);
  border-radius: 50%;
}

.timeline-item.twitter .marker-dot { background: var(--black); }
.timeline-item.reddit .marker-dot { background: var(--black); }
.timeline-item.twitter .timeline-marker { border-color: var(--black); }
.timeline-item.reddit .timeline-marker { border-color: var(--black); }

/* Card Layout */
.timeline-card {
  width: calc(100% - 48px);
  background: var(--white);
  border-radius: 2px;
  padding: 16px 20px;
  border: 1px solid var(--border-soft);
  box-shadow: 0 2px 10px rgba(0,0,0,0.02);
  position: relative;
  transition: all 0.2s;
}

.timeline-card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
  border-color: var(--border-strong);
}

/* Left side (Twitter) */
.timeline-item.twitter {
  justify-content: flex-start;
  padding-right: 50%;
}
.timeline-item.twitter .timeline-card {
  margin-left: auto;
  margin-right: 32px; /* Gap from axis */
}

/* Right side (Reddit) */
.timeline-item.reddit {
  justify-content: flex-end;
  padding-left: 50%;
}
.timeline-item.reddit .timeline-card {
  margin-right: auto;
  margin-left: 32px; /* Gap from axis */
}

/* Card Content Styles */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--surface-2);
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar-placeholder {
  width: 24px;
  height: 24px;
  background: var(--black);
  color: var(--white);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.agent-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--black);
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.platform-indicator {
  color: var(--muted-soft);
  display: flex;
  align-items: center;
}

.action-badge {
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 2px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 1px solid transparent;
}

/* Monochromatic Badges */
.badge-post { background: var(--surface-3); color: var(--ink-2); border-color: var(--border-soft); }
.badge-comment { background: var(--surface-3); color: var(--muted); border-color: var(--border-soft); }
.badge-action { background: var(--white); color: var(--muted); border: 1px solid var(--border-soft); }
.badge-meta { background: var(--surface); color: var(--muted-soft); border: 1px dashed var(--border-strong); }
.badge-idle { opacity: 0.5; }

.content-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-2);
  margin-bottom: 10px;
}

.content-text.main-text {
  font-size: 14px;
  color: var(--black);
}

/* Info Blocks (Quote, Repost, etc) */
.quoted-block, .repost-content {
  background: var(--surface);
  border: 1px solid var(--border-soft);
  padding: 10px 12px;
  border-radius: 2px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-3);
}

.quote-header, .repost-info, .like-info, .search-info, .follow-info, .vote-info, .idle-info, .comment-context {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--muted);
}

.icon-small {
  color: var(--muted-soft);
}
.icon-small.filled {
  color: var(--muted-soft); /* Keep icons neutral unless highlighted */
}

.search-query {
  font-family: 'JetBrains Mono', monospace;
  background: var(--surface-3);
  padding: 0 4px;
  border-radius: 2px;
}

.card-footer {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
  font-size: 12px;
  color: var(--muted-soft);
  font-family: 'JetBrains Mono', monospace;
}

/* Waiting State */
.waiting-state {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  color: var(--muted-soft);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.pulse-ring {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px solid var(--border-soft);
  animation: ripple 2s infinite;
}

@keyframes ripple {
  0% { transform: scale(0.8); opacity: 1; border-color: var(--border-strong); }
  100% { transform: scale(2.5); opacity: 0; border-color: var(--border-soft); }
}

/* Animation */
.timeline-item-enter-active,
.timeline-item-leave-active {
  transition: all 0.4s cubic-bezier(0.165, 0.84, 0.44, 1);
}

.timeline-item-enter-from {
  opacity: 0;
  transform: translateY(20px);
}

.timeline-item-leave-to {
  opacity: 0;
}

/* Logs */
.system-logs {
  background: var(--surface);
  color: var(--ink-2);
  padding: 16px;
  font-family: 'JetBrains Mono', monospace;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.log-header {
  /* Now a <button>: reset the inherited control styling. */
  font: inherit;
  text-align: left;
  appearance: none;
  border: none;
  background: none;
  color: inherit;
  width: 100%;
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--muted-soft);
  cursor: pointer;
  user-select: none;
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
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time { color: var(--muted-soft); min-width: 75px; }
.log-msg { color: var(--ink-2); word-break: break-all; }
.mono { font-family: 'JetBrains Mono', monospace; }

/* Loading spinner for button */
.loading-spinner-small {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: var(--white);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 6px;
}
</style>
