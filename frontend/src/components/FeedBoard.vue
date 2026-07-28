<template>
  <div class="feed-board">
    <!-- Toolbar: platform, sort, refresh -->
    <div class="board-toolbar">
      <div class="platform-switch">
        <button
          v-for="p in platforms"
          :key="p.value"
          class="switch-btn"
          :class="{ active: platform === p.value }"
          @click="selectPlatform(p.value)"
        >
          {{ p.label }}
        </button>
      </div>

      <div class="toolbar-right">
        <span class="total-label">
          {{ $t('feedBoard.postCount') }}
          <span class="mono">{{ total }}</span>
        </span>
        <button class="refresh-btn" :disabled="loading" @click="loadPosts">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" :class="{ spinning: loading }">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
          </svg>
          {{ $t('feedBoard.refresh') }}
        </button>
      </div>
    </div>

    <div class="board-body">
      <!-- Master: post list -->
      <div class="post-list">
        <div class="list-header">
          <span class="col-author">{{ $t('feedBoard.author') }}</span>
          <button
            v-for="col in sortableColumns"
            :key="col.key"
            class="col-sort"
            :class="{ active: sortBy === col.key }"
            :title="col.title"
            @click="setSort(col.key)"
          >
            {{ col.label }}
            <span v-if="sortBy === col.key" class="sort-arrow">{{ order === 'desc' ? '↓' : '↑' }}</span>
          </button>
        </div>

        <div class="list-scroll">
          <div
            v-for="post in posts"
            :key="post.post_id"
            class="post-row"
            :class="{ selected: selectedPost && selectedPost.post_id === post.post_id }"
            @click="selectPost(post)"
          >
            <div class="row-top">
              <span class="post-id mono">#{{ post.post_id }}</span>
              <span class="post-author">{{ authorLabel(post) }}</span>
              <span v-if="post.original_post_id" class="repost-tag">{{ $t('feedBoard.repost') }}</span>
            </div>

            <div class="row-content">{{ preview(post) }}</div>

            <div class="row-metrics">
              <span class="metric up" :title="$t('feedBoard.likes')">▲ <span class="mono">{{ post.num_likes || 0 }}</span></span>
              <span class="metric down" :title="$t('feedBoard.dislikes')">▼ <span class="mono">{{ post.num_dislikes || 0 }}</span></span>
              <span class="metric" :title="$t('feedBoard.comments')">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                <span class="mono">{{ post.num_comments || 0 }}</span>
              </span>
              <span class="metric" :title="$t('feedBoard.shares')">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
                <span class="mono">{{ post.num_shares || 0 }}</span>
              </span>
              <span class="metric time mono">{{ formatTime(post.created_at) }}</span>
            </div>
          </div>

          <div v-if="!loading && posts.length === 0" class="empty-state">
            <div class="pulse-ring"></div>
            <span>{{ error || $t('feedBoard.noPosts') }}</span>
          </div>
        </div>

        <!-- Pagination -->
        <div v-if="totalPages > 1" class="pagination">
          <button class="page-btn" :disabled="page === 0 || loading" @click="goToPage(page - 1)">‹</button>
          <span class="page-info mono">{{ page + 1 }} / {{ totalPages }}</span>
          <button class="page-btn" :disabled="page >= totalPages - 1 || loading" @click="goToPage(page + 1)">›</button>
        </div>
      </div>

      <!-- Detail: selected post + its comments -->
      <div class="post-detail">
        <div v-if="!selectedPost" class="detail-placeholder">
          <span>{{ $t('feedBoard.selectPrompt') }}</span>
        </div>

        <template v-else>
          <div class="detail-header">
            <div class="detail-author">
              <div class="avatar-placeholder">{{ authorInitial(selectedPost) }}</div>
              <div class="author-meta">
                <span class="author-name">{{ authorLabel(selectedPost) }}</span>
                <span class="author-sub mono">
                  #{{ selectedPost.post_id }} • agent {{ selectedPost.user_id }} • {{ formatTime(selectedPost.created_at) }}
                </span>
              </div>
            </div>
            <button class="close-detail" :title="$t('feedBoard.close')" @click="selectedPost = null">×</button>
          </div>

          <div class="detail-scroll">
            <div class="detail-content">{{ selectedPost.content || $t('feedBoard.emptyContent') }}</div>

            <div v-if="selectedPost.quote_content" class="quoted-block">
              <div class="quote-label">{{ $t('feedBoard.quoted') }}</div>
              <div class="quote-text">{{ selectedPost.quote_content }}</div>
            </div>

            <div class="detail-metrics">
              <span class="detail-metric up">▲ <span class="mono">{{ selectedPost.num_likes || 0 }}</span></span>
              <span class="detail-metric down">▼ <span class="mono">{{ selectedPost.num_dislikes || 0 }}</span></span>
              <span class="detail-metric">{{ $t('feedBoard.shares') }} <span class="mono">{{ selectedPost.num_shares || 0 }}</span></span>
            </div>

            <div class="comments-section">
              <div class="comments-header">
                {{ $t('feedBoard.comments') }}
                <span class="mono">{{ comments.length }}</span>
                <span v-if="commentsLoading" class="loading-dot">…</span>
              </div>

              <div v-for="c in comments" :key="c.comment_id" class="comment-item">
                <div class="comment-head">
                  <span class="comment-author">{{ authorLabel(c) }}</span>
                  <span class="comment-metrics mono">▲ {{ c.num_likes || 0 }} ▼ {{ c.num_dislikes || 0 }}</span>
                </div>
                <div class="comment-body">{{ c.content }}</div>
              </div>

              <div v-if="!commentsLoading && comments.length === 0" class="no-comments">
                {{ $t('feedBoard.noComments') }}
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getSimulationPosts, getSimulationComments } from '../api/simulation'

const { t } = useI18n()

const props = defineProps({
  simulationId: String,
  // Which platform's database to read. Omitted, the backend picks the
  // simulation's default platform on the first load.
  defaultPlatform: {
    type: String,
    default: null
  }
})

const PAGE_SIZE = 25

const platforms = [
  { value: 'reddit', label: 'Topic Community' },
  { value: 'twitter', label: 'Info Plaza' }
]

const sortableColumns = [
  { key: 'created_at', label: t('feedBoard.sortNewest'), title: t('feedBoard.sortNewestHint') },
  { key: 'num_likes', label: '▲', title: t('feedBoard.likes') },
  { key: 'num_dislikes', label: '▼', title: t('feedBoard.dislikes') },
  { key: 'num_comments', label: t('feedBoard.sortComments'), title: t('feedBoard.comments') },
  { key: 'num_shares', label: t('feedBoard.sortShares'), title: t('feedBoard.shares') }
]

// State
const posts = ref([])
const total = ref(0)
const page = ref(0)
const sortBy = ref('created_at')
const order = ref('desc')
const platform = ref(props.defaultPlatform || 'reddit')
const loading = ref(false)
const error = ref(null)

const selectedPost = ref(null)
const comments = ref([])
const commentsLoading = ref(false)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

async function loadPosts() {
  if (!props.simulationId) {
    error.value = t('feedBoard.noSimulation')
    return
  }

  loading.value = true
  error.value = null

  try {
    const res = await getSimulationPosts(
      props.simulationId,
      platform.value,
      PAGE_SIZE,
      page.value * PAGE_SIZE,
      sortBy.value,
      order.value
    )

    if (res?.success) {
      posts.value = res.data?.posts || []
      total.value = res.data?.total || 0

      // A post kept open across a refresh should show the newer counts.
      if (selectedPost.value) {
        const fresh = posts.value.find(p => p.post_id === selectedPost.value.post_id)
        if (fresh) selectedPost.value = fresh
      }
    } else {
      error.value = res?.error || t('feedBoard.loadFailed')
      posts.value = []
    }
  } catch (e) {
    error.value = e.message || t('feedBoard.loadFailed')
    posts.value = []
  } finally {
    loading.value = false
  }
}

async function selectPost(post) {
  selectedPost.value = post
  comments.value = []
  commentsLoading.value = true

  try {
    const res = await getSimulationComments(
      props.simulationId,
      post.post_id,
      platform.value
    )
    if (res?.success) {
      comments.value = res.data?.comments || []
    }
  } catch (e) {
    comments.value = []
  } finally {
    commentsLoading.value = false
  }
}

function setSort(key) {
  if (sortBy.value === key) {
    // Same column clicked again: flip the direction.
    order.value = order.value === 'desc' ? 'asc' : 'desc'
  } else {
    sortBy.value = key
    order.value = 'desc'
  }
  page.value = 0
  loadPosts()
}

function selectPlatform(value) {
  if (platform.value === value) return
  platform.value = value
  page.value = 0
  selectedPost.value = null
  comments.value = []
  loadPosts()
}

function goToPage(next) {
  page.value = next
  loadPosts()
}

function authorLabel(row) {
  return row.author_user_name || row.author_name || `agent_${row.user_id}`
}

function authorInitial(row) {
  return (authorLabel(row) || 'A')[0].toUpperCase()
}

function preview(post) {
  const text = post.content || post.quote_content || ''
  if (!text) return t('feedBoard.emptyContent')
  return text.length > 180 ? `${text.slice(0, 180)}…` : text
}

function formatTime(value) {
  if (!value) return '-'
  // The simulation writes a sandbox timestamp, not a real one, so it is shown
  // as-is rather than being localised.
  const str = String(value)
  const match = str.match(/(\d{2}:\d{2}:\d{2})/)
  return match ? match[1] : str.slice(0, 19)
}

// Load on mount and whenever the simulation changes.
watch(
  () => props.simulationId,
  (id) => {
    if (!id) return
    page.value = 0
    selectedPost.value = null
    comments.value = []
    loadPosts()
  },
  { immediate: true }
)

defineExpose({ loadPosts })
</script>

<style scoped>
.feed-board {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #FFF;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  overflow: hidden;
}

.mono { font-family: 'JetBrains Mono', monospace; }

/* --- Toolbar --- */
.board-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  border-bottom: 1px solid #EAEAEA;
  background: #FAFAFA;
  flex-shrink: 0;
}

.platform-switch {
  display: flex;
  gap: 4px;
}

.switch-btn {
  padding: 5px 12px;
  border: 1px solid #EAEAEA;
  border-radius: 4px;
  background: #FFF;
  color: #888;
  font-size: 11px;
  font-family: inherit;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all 0.2s;
}

.switch-btn:hover { border-color: #CCC; color: #555; }

.switch-btn.active {
  border-color: #333;
  background: #333;
  color: #FFF;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.total-label {
  font-size: 11px;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.total-label .mono { color: #333; margin-left: 4px; }

.refresh-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid #EAEAEA;
  border-radius: 4px;
  background: #FFF;
  color: #555;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover:not(:disabled) { border-color: #333; color: #111; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* --- Body: master / detail --- */
.board-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.post-list {
  display: flex;
  flex-direction: column;
  width: 46%;
  min-width: 320px;
  border-right: 1px solid #EAEAEA;
  min-height: 0;
}

.list-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 16px;
  border-bottom: 1px solid #EAEAEA;
  background: #FFF;
  flex-shrink: 0;
}

.col-author {
  flex: 1;
  font-size: 10px;
  color: #AAA;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.col-sort {
  padding: 3px 8px;
  border: 1px solid transparent;
  border-radius: 3px;
  background: transparent;
  color: #888;
  font-size: 10px;
  font-family: inherit;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.col-sort:hover { background: #F5F5F5; color: #333; }

.col-sort.active {
  border-color: #EAEAEA;
  background: #F2FAF6;
  color: #1A936F;
}

.sort-arrow { margin-left: 2px; }

.list-scroll {
  flex: 1;
  overflow-y: auto;
  position: relative;
  min-height: 0;
}

.post-row {
  padding: 12px 16px;
  border-bottom: 1px solid #F5F5F5;
  cursor: pointer;
  transition: background 0.15s;
}

.post-row:hover { background: #FAFAFA; }

.post-row.selected {
  background: #F2FAF6;
  box-shadow: inset 2px 0 0 #1A936F;
}

.row-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
}

.post-id { font-size: 10px; color: #BBB; }

.post-author {
  font-size: 12px;
  font-weight: 500;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repost-tag {
  padding: 1px 5px;
  border-radius: 2px;
  background: #F5F5F5;
  color: #888;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.row-content {
  font-size: 12px;
  line-height: 1.55;
  color: #555;
  margin-bottom: 8px;
  word-break: break-word;
}

.row-metrics {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: #999;
}

.metric {
  display: flex;
  align-items: center;
  gap: 3px;
}

.metric.up { color: #1A936F; }
.metric.down { color: #C1666B; }
.metric.time { margin-left: auto; color: #CCC; font-size: 10px; }

/* --- Empty state --- */
.empty-state {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  color: #CCC;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  text-align: center;
  width: 80%;
}

.pulse-ring {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px solid #EAEAEA;
  animation: ripple 2s infinite;
}

@keyframes ripple {
  0% { transform: scale(0.8); opacity: 1; border-color: #CCC; }
  100% { transform: scale(2.5); opacity: 0; border-color: #EAEAEA; }
}

/* --- Pagination --- */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 10px;
  border-top: 1px solid #EAEAEA;
  background: #FAFAFA;
  flex-shrink: 0;
}

.page-btn {
  width: 26px;
  height: 26px;
  border: 1px solid #EAEAEA;
  border-radius: 4px;
  background: #FFF;
  color: #555;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}

.page-btn:hover:not(:disabled) { border-color: #333; color: #111; }
.page-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.page-info { font-size: 11px; color: #888; }

/* --- Detail --- */
.post-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.detail-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #CCC;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid #EAEAEA;
  flex-shrink: 0;
}

.detail-author {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.avatar-placeholder {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: #F5F5F5;
  border: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #666;
  flex-shrink: 0;
}

.author-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.author-name {
  font-size: 13px;
  font-weight: 500;
  color: #222;
}

.author-sub { font-size: 10px; color: #AAA; }

.close-detail {
  border: none;
  background: transparent;
  color: #CCC;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
  transition: color 0.15s;
}

.close-detail:hover { color: #333; }

.detail-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
  min-height: 0;
}

.detail-content {
  font-size: 13px;
  line-height: 1.7;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
}

.quoted-block {
  margin-top: 14px;
  padding: 10px 12px;
  border-left: 2px solid #EAEAEA;
  background: #FAFAFA;
  border-radius: 0 4px 4px 0;
}

.quote-label {
  font-size: 9px;
  color: #AAA;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 5px;
}

.quote-text { font-size: 12px; line-height: 1.6; color: #666; }

.detail-metrics {
  display: flex;
  gap: 16px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #F5F5F5;
  font-size: 11px;
  color: #999;
}

.detail-metric.up { color: #1A936F; }
.detail-metric.down { color: #C1666B; }

/* --- Comments --- */
.comments-section { margin-top: 22px; }

.comments-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding-bottom: 8px;
  border-bottom: 1px solid #EAEAEA;
  margin-bottom: 12px;
}

.loading-dot { color: #CCC; }

.comment-item {
  padding: 10px 0;
  border-bottom: 1px solid #F8F8F8;
}

.comment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}

.comment-author { font-size: 12px; font-weight: 500; color: #444; }
.comment-metrics { font-size: 10px; color: #BBB; }

.comment-body {
  font-size: 12px;
  line-height: 1.6;
  color: #666;
  word-break: break-word;
}

.no-comments {
  padding: 16px 0;
  color: #CCC;
  font-size: 11px;
  text-align: center;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

/* Scrollbars, matching the rest of the app */
.list-scroll::-webkit-scrollbar,
.detail-scroll::-webkit-scrollbar { width: 5px; }

.list-scroll::-webkit-scrollbar-track,
.detail-scroll::-webkit-scrollbar-track { background: transparent; }

.list-scroll::-webkit-scrollbar-thumb,
.detail-scroll::-webkit-scrollbar-thumb { background: #EAEAEA; border-radius: 3px; }

.list-scroll::-webkit-scrollbar-thumb:hover,
.detail-scroll::-webkit-scrollbar-thumb:hover { background: #CCC; }

/* Narrow viewports: stack master over detail */
@media (max-width: 900px) {
  .board-body { flex-direction: column; }

  .post-list {
    width: 100%;
    min-width: 0;
    border-right: none;
    border-bottom: 1px solid #EAEAEA;
    max-height: 50%;
  }
}
</style>
