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
          <button
            v-for="post in posts"
            :key="post.post_id"
            type="button"
            class="post-row"
            :class="[isX ? 'x-row' : 'r-row', { selected: selectedPost && selectedPost.post_id === post.post_id }]"
            :aria-pressed="!!selectedPost && selectedPost.post_id === post.post_id"
            @click="selectPost(post)"
          >
            <!-- Info Plaza / X: repost banner, avatar left, name · @handle ·
                 time on one line, then the text, then reply/repost/like. -->
            <template v-if="isX">
              <div v-if="post.original_post_id" class="x-repost-banner">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
                {{ $t('feedBoard.repostedBy', { name: handle(post) }) }}
              </div>

              <div class="x-body">
                <div class="x-avatar">{{ authorInitial(post) }}</div>
                <div class="x-main">
                  <div class="x-head">
                    <span class="x-name">{{ displayName(post) }}</span>
                    <span class="x-handle mono">@{{ handle(post) }}</span>
                    <span class="x-dot">·</span>
                    <span class="x-time mono">{{ formatTime(post.created_at) }}</span>
                  </div>

                  <div class="x-text">{{ preview(post) }}</div>

                  <div class="x-actions">
                    <span class="x-action reply" :title="$t('feedBoard.replies')">
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                      <span class="mono">{{ post.num_comments || 0 }}</span>
                    </span>
                    <span class="x-action repost" :title="$t('feedBoard.reposts')">
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
                      <span class="mono">{{ post.num_shares || 0 }}</span>
                    </span>
                    <span class="x-action like" :title="$t('feedBoard.likes')">
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"></path></svg>
                      <span class="mono">{{ post.num_likes || 0 }}</span>
                    </span>
                  </div>
                </div>
              </div>
            </template>

            <!-- Topic Community / Reddit: vote stack left, one score, then the
                 u/author line and a comment count. -->
            <template v-else>
              <div class="r-body">
                <div class="r-votes">
                  <span class="vote-arrow up">▲</span>
                  <span class="vote-score mono">{{ voteScore(post) }}</span>
                  <span class="vote-arrow down">▼</span>
                </div>

                <div class="r-main">
                  <div class="r-head">
                    <span class="post-id mono">#{{ post.post_id }}</span>
                    <span class="r-author">u/{{ handle(post) }}</span>
                    <span class="r-time mono">{{ formatTime(post.created_at) }}</span>
                    <span v-if="post.original_post_id" class="repost-tag">{{ $t('feedBoard.repost') }}</span>
                  </div>

                  <div class="row-content">{{ preview(post) }}</div>

                  <div class="r-actions">
                    <span class="metric" :title="$t('feedBoard.comments')">
                      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                      <span class="mono">{{ post.num_comments || 0 }}</span>
                      {{ $t('feedBoard.comments') }}
                    </span>
                    <span class="metric" :title="$t('feedBoard.shares')">
                      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
                      <span class="mono">{{ post.num_shares || 0 }}</span>
                    </span>
                  </div>
                </div>
              </div>
            </template>
          </button>

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
                <span class="author-name">{{ isX ? displayName(selectedPost) : authorLabel(selectedPost) }}</span>
                <span class="author-sub mono">
                  {{ isX ? `@${handle(selectedPost)}` : `u/${handle(selectedPost)}` }}
                  • #{{ selectedPost.post_id }} • {{ formatTime(selectedPost.created_at) }}
                </span>
              </div>
            </div>
            <button class="close-detail" :title="$t('feedBoard.close')" @click="selectedPost = null">×</button>
          </div>

          <div class="detail-scroll">
            <div class="detail-content">
              {{ selectedPost.content || selectedPost.original_content || $t('feedBoard.emptyContent') }}
            </div>

            <div v-if="selectedPost.quote_content" class="quoted-block">
              <div class="quote-label">{{ $t('feedBoard.quoted') }}</div>
              <div class="quote-text">{{ selectedPost.quote_content }}</div>
            </div>

            <div class="detail-metrics">
              <template v-if="isX">
                <span class="detail-metric">{{ $t('feedBoard.replies') }} <span class="mono">{{ comments.length }}</span></span>
                <span class="detail-metric">{{ $t('feedBoard.reposts') }} <span class="mono">{{ selectedPost.num_shares || 0 }}</span></span>
                <span class="detail-metric like">♥ <span class="mono">{{ selectedPost.num_likes || 0 }}</span></span>
              </template>
              <template v-else>
                <span class="detail-metric up">▲ <span class="mono">{{ selectedPost.num_likes || 0 }}</span></span>
                <span class="detail-metric down">▼ <span class="mono">{{ selectedPost.num_dislikes || 0 }}</span></span>
                <span class="detail-metric">{{ $t('feedBoard.shares') }} <span class="mono">{{ selectedPost.num_shares || 0 }}</span></span>
              </template>
            </div>

            <div class="comments-section">
              <div class="comments-header">
                {{ isX ? $t('feedBoard.replies') : $t('feedBoard.comments') }}
                <span class="mono">{{ comments.length }}</span>
                <span v-if="commentsLoading" class="loading-dot">…</span>
              </div>

              <div v-for="c in comments" :key="c.comment_id" class="comment-item">
                <div class="comment-head">
                  <span class="comment-author">{{ isX ? `@${handle(c)}` : `u/${handle(c)}` }}</span>
                  <span class="comment-metrics mono">
                    <template v-if="isX">♥ {{ c.num_likes || 0 }}</template>
                    <template v-else>▲ {{ c.num_likes || 0 }} ▼ {{ c.num_dislikes || 0 }}</template>
                  </span>
                </div>
                <div class="comment-body">{{ c.content }}</div>
              </div>

              <div v-if="!commentsLoading && comments.length === 0" class="no-comments">
                {{ isX ? $t('feedBoard.noReplies') : $t('feedBoard.noComments') }}
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

// Sort columns are named after the platform's own vocabulary: Topic Community
// votes on posts, Info Plaza likes and reposts them.
const REDDIT_COLUMNS = [
  { key: 'created_at', label: t('feedBoard.sortNewest'), title: t('feedBoard.sortNewestHint') },
  { key: 'num_likes', label: '▲', title: t('feedBoard.upvotes') },
  { key: 'num_dislikes', label: '▼', title: t('feedBoard.downvotes') },
  { key: 'num_comments', label: t('feedBoard.sortComments'), title: t('feedBoard.comments') },
  { key: 'num_shares', label: t('feedBoard.sortShares'), title: t('feedBoard.shares') }
]

const X_COLUMNS = [
  { key: 'created_at', label: t('feedBoard.sortNewest'), title: t('feedBoard.sortNewestHint') },
  { key: 'num_likes', label: '♥', title: t('feedBoard.likes') },
  { key: 'num_comments', label: t('feedBoard.sortReplies'), title: t('feedBoard.replies') },
  { key: 'num_shares', label: t('feedBoard.sortReposts'), title: t('feedBoard.reposts') }
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

// Info Plaza follows X: avatar-left single column, reply/repost/like bar, no
// downvote at all. Topic Community follows Reddit: a vote stack on the left.
const isX = computed(() => platform.value === 'twitter')
const sortableColumns = computed(() => (isX.value ? X_COLUMNS : REDDIT_COLUMNS))

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
  // The sort can point at a column the new platform does not show.
  if (!sortableColumns.value.some(c => c.key === sortBy.value)) {
    sortBy.value = 'created_at'
    order.value = 'desc'
  }
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

// X shows the display name and the @handle side by side, so they are needed
// apart rather than as one label.
function displayName(row) {
  return row.author_name || row.author_user_name || `agent_${row.user_id}`
}

function handle(row) {
  return row.author_user_name || `agent_${row.user_id}`
}

// Reddit shows one score, not two counters.
function voteScore(row) {
  return (row.num_likes || 0) - (row.num_dislikes || 0)
}

function authorInitial(row) {
  return (authorLabel(row) || 'A')[0].toUpperCase()
}

function preview(post) {
  // A repost is stored with no content of its own, only a pointer at the
  // original, so the original's text is what it actually shows.
  const text = post.content || post.quote_content || post.original_content || ''
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
  background: var(--white);
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
  border-bottom: 1px solid var(--border-soft);
  background: var(--surface);
  flex-shrink: 0;
}

.platform-switch {
  display: flex;
  gap: 4px;
}

.switch-btn {
  padding: 5px 12px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--white);
  color: var(--muted-soft);
  font-size: 12px;
  font-family: inherit;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all 0.2s;
}

.switch-btn:hover { border-color: var(--border-strong); color: var(--ink-3); }

.switch-btn.active {
  border-color: var(--ink-2);
  background: var(--ink-2);
  color: var(--white);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.total-label {
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.total-label .mono { color: var(--ink-2); margin-left: 4px; }

.refresh-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--white);
  color: var(--ink-3);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover:not(:disabled) { border-color: var(--ink-2); color: var(--ink); }
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
  min-width: 0;
  border-right: 1px solid var(--border-soft);
  min-height: 0;
}

.list-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border-soft);
  background: var(--white);
  flex-shrink: 0;
}

.col-author {
  flex: 1;
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.col-sort {
  padding: 3px 8px;
  border: 1px solid transparent;
  border-radius: 3px;
  background: transparent;
  color: var(--muted-soft);
  font-size: 12px;
  font-family: inherit;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.col-sort:hover { background: var(--surface-2); color: var(--ink-2); }

.col-sort.active {
  border-color: var(--border-soft);
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
  /* A <button> now; reset the inherited control styling. */
  font: inherit;
  text-align: left;
  appearance: none;
  color: inherit;
  width: 100%;
  display: block;
  padding: 12px 16px;
  border-bottom: 1px solid var(--surface-2);
  cursor: pointer;
  transition: background 0.15s;
}

.post-row:hover { background: var(--surface); }

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

.post-id { font-size: 12px; color: var(--muted-soft); }

.post-author {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repost-tag {
  padding: 1px 5px;
  border-radius: 2px;
  background: var(--surface-2);
  color: var(--muted-soft);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.row-content {
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink-3);
  margin-bottom: 8px;
  word-break: break-word;
}

.row-metrics {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--muted-soft);
}

.metric {
  display: flex;
  align-items: center;
  gap: 3px;
}

.metric.up { color: #1A936F; }
.metric.down { color: #C1666B; }
.metric.time { margin-left: auto; color: var(--muted-soft); font-size: 12px; }

/* --- Info Plaza rows: X layout --- */
.x-repost-banner {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 5px;
  padding-left: 40px;
  font-size: 12px;
  color: var(--muted-soft);
}

.x-body { display: flex; gap: 10px; }

.x-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--muted);
  flex-shrink: 0;
}

.x-main { flex: 1; min-width: 0; }

.x-head {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  margin-bottom: 3px;
  min-width: 0;
}

.x-name {
  font-weight: 600;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.x-handle, .x-dot, .x-time { color: var(--muted-soft); }
.x-handle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.x-text {
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink-3);
  margin-bottom: 8px;
  word-break: break-word;
}

/* X spreads the action bar across the post width rather than bunching it. */
.x-actions {
  display: flex;
  gap: 8px;
  max-width: 320px;
  font-size: 12px;
  color: var(--muted-soft);
}

.x-action {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
}

.x-action.reply { color: #55707F; }
.x-action.repost { color: #1A936F; }
.x-action.like { color: #C1666B; }

/* --- Topic Community rows: Reddit layout --- */
.r-body { display: flex; gap: 10px; }

.r-votes {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  flex-shrink: 0;
  width: 26px;
  font-size: 12px;
  color: var(--muted-soft);
}

.vote-arrow.up { color: #1A936F; }
.vote-arrow.down { color: #C1666B; }
.vote-score { color: var(--ink-2); font-weight: 600; }

.r-main { flex: 1; min-width: 0; }

.r-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  font-size: 12px;
  color: var(--muted-soft);
}

.r-author {
  font-weight: 500;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.r-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

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
  color: var(--muted-soft);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  text-align: center;
  width: 80%;
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

/* --- Pagination --- */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 10px;
  border-top: 1px solid var(--border-soft);
  background: var(--surface);
  flex-shrink: 0;
}

.page-btn {
  width: 26px;
  height: 26px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--white);
  color: var(--ink-3);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}

.page-btn:hover:not(:disabled) { border-color: var(--ink-2); color: var(--ink); }
.page-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.page-info { font-size: 12px; color: var(--muted-soft); }

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
  color: var(--muted-soft);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-soft);
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
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--muted);
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

.author-sub { font-size: 12px; color: var(--muted-soft); }

.close-detail {
  border: none;
  background: transparent;
  color: var(--muted-soft);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
  transition: color 0.15s;
}

.close-detail:hover { color: var(--ink-2); }

.detail-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
  min-height: 0;
}

.detail-content {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.quoted-block {
  margin-top: 14px;
  padding: 10px 12px;
  border-left: 2px solid var(--border-soft);
  background: var(--surface);
  border-radius: 0 4px 4px 0;
}

.quote-label {
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 5px;
}

.quote-text { font-size: 12px; line-height: 1.6; color: var(--muted); }

.detail-metrics {
  display: flex;
  gap: 16px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--surface-2);
  font-size: 12px;
  color: var(--muted-soft);
}

.detail-metric.up { color: #1A936F; }
.detail-metric.down { color: #C1666B; }
.detail-metric.like { color: #C1666B; }

/* --- Comments --- */
.comments-section { margin-top: 22px; }

.comments-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 12px;
}

.loading-dot { color: var(--muted-soft); }

.comment-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--surface);
}

.comment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}

.comment-author { font-size: 12px; font-weight: 500; color: var(--ink-3); }
.comment-metrics { font-size: 12px; color: var(--muted-soft); }

.comment-body {
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
  word-break: break-word;
}

.no-comments {
  padding: 16px 0;
  color: var(--muted-soft);
  font-size: 12px;
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
.detail-scroll::-webkit-scrollbar-thumb { background: var(--border-soft); border-radius: 3px; }

.list-scroll::-webkit-scrollbar-thumb:hover,
.detail-scroll::-webkit-scrollbar-thumb:hover { background: var(--border-strong); }

/* Narrow viewports: stack master over detail */
@media (max-width: 900px) {
  .board-body { flex-direction: column; }

  .post-list {
    width: 100%;
    min-width: 0;
    border-right: none;
    border-bottom: 1px solid var(--border-soft);
    max-height: 50%;
  }
}
</style>
