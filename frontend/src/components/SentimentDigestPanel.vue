<template>
  <section class="sd-panel">
    <header class="sd-header">
      <div class="sd-heading">
        <h2 class="sd-title">{{ $t('sentiment.panelTitle') }}</h2>
        <p class="sd-subtitle">{{ $t('sentiment.panelSubtitle') }}</p>
      </div>
      <div class="sd-actions">
        <button class="sd-btn" :disabled="loading" @click="load(false)">
          {{ $t('sentiment.refresh') }}
        </button>
        <button
          class="sd-btn"
          :disabled="loading"
          :title="$t('sentiment.reclassifyHint')"
          @click="load(true)"
        >
          {{ $t('sentiment.reclassify') }}
        </button>
      </div>
    </header>

    <div v-if="loading" class="sd-state">{{ $t('sentiment.loading') }}</div>
    <div v-else-if="error" class="sd-state sd-state--error">
      {{ $t('sentiment.error', { error }) }}
    </div>
    <div v-else-if="!hasData" class="sd-state">{{ $t('sentiment.empty') }}</div>

    <template v-else>
      <!-- ── The headline split ─────────────────────────────── -->
      <div class="sd-group">
        <div class="sd-group-label">
          {{ $t('sentiment.groupSplit') }}
          <span class="sd-scope">
            <button
              v-for="s in scopes"
              :key="s.key"
              class="sd-scope-btn"
              :class="{ active: scope === s.key }"
              @click="scope = s.key"
            >
              {{ $t(s.label) }}
              <span class="mono sd-scope-count">{{ digest[s.key]?.total ?? 0 }}</span>
            </button>
          </span>
        </div>

        <div class="sd-bar" role="img" :aria-label="barAriaLabel">
          <div
            v-for="seg in barSegments"
            :key="seg.key"
            class="sd-bar-seg"
            :class="`is-${seg.key}`"
            :style="{ width: seg.pct + '%' }"
            :title="`${$t(seg.label)} ${seg.pct}% (${seg.count})`"
          >
            <span v-if="seg.pct >= 8" class="sd-bar-text mono">{{ seg.pct }}%</span>
          </div>
        </div>

        <div class="sd-legend">
          <span v-for="seg in barSegments" :key="seg.key" class="sd-legend-item">
            <i class="sd-dot" :class="`is-${seg.key}`"></i>
            {{ $t(seg.label) }}
            <span class="mono">{{ seg.count }}</span>
          </span>
          <span class="sd-legend-item sd-legend-net">
            {{ $t('sentiment.netSentiment') }}
            <span class="mono">{{ net }}</span>
          </span>
        </div>
      </div>

      <!-- ── Loudest voice on each side ─────────────────────── -->
      <div class="sd-group">
        <div class="sd-group-label">{{ $t('sentiment.groupHighlights') }}</div>
        <div v-if="!highlightCards.length" class="sd-state">{{ $t('sentiment.noHighlights') }}</div>
        <div v-else class="sd-highlights">
          <article
            v-for="card in highlightCards"
            :key="card.key"
            class="sd-card"
            :class="`is-${card.item.sentiment}`"
          >
            <div class="sd-card-head">
              <span class="sd-card-label">{{ $t(card.label) }}</span>
              <span class="sd-card-stats mono">
                ♥ {{ card.item.likes }}
                <template v-if="card.item.dislikes"> · ✕ {{ card.item.dislikes }}</template>
              </span>
            </div>
            <p class="sd-card-body">
              {{ card.item.content }}<span v-if="card.item.truncated">…</span>
            </p>
            <div class="sd-card-foot">
              <span class="sd-card-author">@{{ card.item.author_name }}</span>
              <span class="sd-card-meta mono">{{ card.item.platform }} · #{{ card.item.item_id }}</span>
              <span v-if="card.item.theme" class="sd-tag">{{ card.item.theme }}</span>
            </div>
          </article>
        </div>
      </div>

      <!-- ── Recurring objections / recurring hooks ─────────── -->
      <div class="sd-themes">
        <div class="sd-group">
          <div class="sd-group-label">{{ $t('sentiment.groupProblems') }}</div>
          <p class="sd-group-hint">{{ $t('sentiment.problemsHint') }}</p>
          <ThemeList
            :themes="digest.problems"
            variant="negative"
            :empty-text="$t('sentiment.noProblems')"
            :expanded="expanded"
            @toggle="toggleTheme"
          />
        </div>

        <div class="sd-group">
          <div class="sd-group-label">{{ $t('sentiment.groupQuirks') }}</div>
          <p class="sd-group-hint">{{ $t('sentiment.quirksHint') }}</p>
          <ThemeList
            :themes="digest.quirks"
            variant="positive"
            :empty-text="$t('sentiment.noQuirks')"
            :expanded="expanded"
            @toggle="toggleTheme"
          />
        </div>
      </div>

      <footer class="sd-foot">
        <span>{{ $t('sentiment.classifiedBy', { model: digest.model }) }}</span>
        <span v-if="digest.totals?.seed_posts_excluded">
          · {{ $t('sentiment.seedsExcluded', { count: digest.totals.seed_posts_excluded }) }}
        </span>
        <span v-if="digest.cached"> · {{ $t('sentiment.fromCache') }}</span>
      </footer>
    </template>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted, h } from 'vue'
import { getSentimentDigest } from '../api/simulation'

const props = defineProps({
  simulationId: String,
  // The parent flips this once the run has finished, so the panel does not
  // classify a feed that is still being written.
  autoLoad: { type: Boolean, default: true }
})

const digest = ref(null)
const loading = ref(false)
const error = ref('')
const scope = ref('overall')
const expanded = ref(new Set())

const scopes = [
  { key: 'overall', label: 'sentiment.scopeAll' },
  { key: 'posts', label: 'sentiment.scopePosts' },
  { key: 'comments', label: 'sentiment.scopeComments' }
]

const hasData = computed(() => Boolean(digest.value?.totals?.classified))

const split = computed(() => digest.value?.[scope.value] || {})

const net = computed(() => {
  const n = split.value.net_sentiment
  return typeof n === 'number' ? (n > 0 ? `+${n}` : `${n}`) : '—'
})

const barSegments = computed(() => {
  const s = split.value
  return [
    { key: 'positive', label: 'sentiment.positive', pct: s.positive_pct || 0, count: s.positive || 0 },
    { key: 'neutral', label: 'sentiment.neutral', pct: s.neutral_pct || 0, count: s.neutral || 0 },
    { key: 'negative', label: 'sentiment.negative', pct: s.negative_pct || 0, count: s.negative || 0 }
  ]
})

const barAriaLabel = computed(() =>
  barSegments.value.map((s) => `${s.key} ${s.pct}%`).join(', ')
)

// Only the cards the backend actually produced - a run with no negative
// comments simply has one card fewer.
const HIGHLIGHT_ORDER = [
  { key: 'top_positive_post', label: 'sentiment.topPositivePost' },
  { key: 'top_negative_post', label: 'sentiment.topNegativePost' },
  { key: 'top_positive_comment', label: 'sentiment.topPositiveComment' },
  { key: 'top_negative_comment', label: 'sentiment.topNegativeComment' }
]

const highlightCards = computed(() => {
  const h = digest.value?.highlights || {}
  return HIGHLIGHT_ORDER
    .filter((c) => h[c.key])
    .map((c) => ({ ...c, item: h[c.key] }))
})

const toggleTheme = (theme) => {
  const next = new Set(expanded.value)
  next.has(theme) ? next.delete(theme) : next.add(theme)
  expanded.value = next
}

const load = async (force = false) => {
  if (!props.simulationId) return
  loading.value = true
  error.value = ''
  try {
    const res = await getSentimentDigest(props.simulationId, force)
    digest.value = res.data || null
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

watch(() => [props.simulationId, props.autoLoad], () => {
  if (props.autoLoad) load(false)
})

onMounted(() => {
  if (props.autoLoad) load(false)
})

defineExpose({ load })

// A theme row plus its collapsible examples. Small enough to live here rather
// than in its own file, and it is not used anywhere else.
const ThemeList = (props, { emit }) => {
  if (!props.themes || !props.themes.length) {
    return h('div', { class: 'sd-state' }, props.emptyText)
  }

  const max = props.themes.reduce((m, t) => Math.max(m, t.count), 0) || 1

  return h('ul', { class: 'sd-theme-list' }, props.themes.map((t) =>
    h('li', { class: 'sd-theme', key: t.theme }, [
      h('button', {
        class: 'sd-theme-row',
        onClick: () => emit('toggle', t.theme)
      }, [
        h('span', { class: 'sd-theme-name' }, t.theme),
        h('span', { class: 'sd-theme-bar' }, [
          h('span', {
            class: `sd-theme-fill is-${props.variant}`,
            style: { width: `${Math.max(4, Math.round((t.count / max) * 100))}%` }
          })
        ]),
        h('span', { class: 'sd-theme-count mono' }, `${t.count}×`),
        h('span', { class: 'sd-theme-share mono' }, `${t.share_pct}%`)
      ]),
      props.expanded.has(t.theme)
        ? h('ul', { class: 'sd-example-list' }, (t.examples || []).map((ex) =>
            h('li', { class: 'sd-example', key: ex.uid }, [
              h('p', { class: 'sd-example-text' }, ex.content + (ex.truncated ? '…' : '')),
              h('span', { class: 'sd-example-meta mono' }, `@${ex.author_name} · ${ex.platform} · ♥ ${ex.likes}`)
            ])
          ))
        : null
    ])
  ))
}
ThemeList.props = ['themes', 'variant', 'emptyText', 'expanded']
ThemeList.emits = ['toggle']
</script>

<style scoped>
.sd-panel {
  border: 1px solid var(--border);
  background: var(--white);
  padding: 24px;
  margin-bottom: 32px;
}

.mono {
  font-family: 'JetBrains Mono', monospace;
}

.sd-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.sd-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--ink);
  margin: 0;
}

.sd-subtitle {
  font-size: 0.78rem;
  color: var(--muted-soft);
  margin: 4px 0 0;
}

.sd-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.sd-btn {
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  font-size: 0.75rem;
  padding: 6px 12px;
  cursor: pointer;
}

.sd-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.sd-state {
  font-size: 0.82rem;
  color: var(--muted-soft);
  padding: 12px 0;
}

.sd-state--error {
  color: var(--danger);
}

.sd-group {
  margin-bottom: 24px;
}

.sd-group-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 0.75rem;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--muted-soft);
  margin-bottom: 10px;
}

.sd-group-hint {
  font-size: 0.75rem;
  color: var(--muted-soft);
  margin: -4px 0 10px;
}

/* ── Scope switch ─────────────────────────────────────── */

.sd-scope {
  display: flex;
  gap: 4px;
}

.sd-scope-btn {
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  font-size: 0.75rem;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  padding: 4px 10px;
  cursor: pointer;
}

.sd-scope-btn.active {
  border-color: var(--ink);
  color: var(--ink);
}

.sd-scope-count {
  color: var(--muted-soft);
  margin-left: 4px;
}

/* ── Split bar ────────────────────────────────────────── */

.sd-bar {
  display: flex;
  height: 34px;
  border: 1px solid var(--border);
  overflow: hidden;
}

.sd-bar-seg {
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.2s ease;
  min-width: 0;
}

.sd-bar-seg.is-positive { background: var(--ink); }
.sd-bar-seg.is-neutral { background: var(--border); }
.sd-bar-seg.is-negative { background: var(--danger); }

.sd-bar-text {
  font-size: 0.75rem;
  color: var(--white);
}

.sd-bar-seg.is-neutral .sd-bar-text {
  color: var(--muted);
}

.sd-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 10px;
  font-size: 0.75rem;
  color: var(--muted);
}

.sd-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.sd-legend-net {
  margin-left: auto;
  color: var(--ink);
}

.sd-dot {
  width: 8px;
  height: 8px;
  display: inline-block;
}

.sd-dot.is-positive { background: var(--ink); }
.sd-dot.is-neutral { background: var(--border); }
.sd-dot.is-negative { background: var(--danger); }

/* ── Highlight cards ──────────────────────────────────── */

.sd-highlights {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.sd-card {
  border: 1px solid var(--border);
  border-left-width: 3px;
  background: var(--surface);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sd-card.is-positive { border-left-color: var(--ink); }
.sd-card.is-negative { border-left-color: var(--danger); }

.sd-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.sd-card-label {
  font-size: 0.75rem;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: var(--muted-soft);
}

.sd-card-stats {
  font-size: 0.75rem;
  color: var(--muted);
  white-space: nowrap;
}

.sd-card-body {
  font-size: 0.85rem;
  line-height: 1.5;
  color: var(--ink);
  margin: 0;
}

.sd-card-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 0.75rem;
  color: var(--muted);
}

.sd-card-meta {
  color: var(--muted-soft);
}

.sd-tag {
  border: 1px solid var(--border);
  background: var(--white);
  padding: 1px 7px;
  font-size: 0.75rem;
  color: var(--muted);
}

/* ── Theme lists ──────────────────────────────────────── */

.sd-themes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 24px;
}

.sd-theme-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.sd-theme {
  border-bottom: 1px solid var(--surface-2);
}

.sd-theme-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 70px 42px 44px;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: none;
  background: transparent;
  padding: 9px 0;
  cursor: pointer;
  text-align: left;
  font-size: 0.8rem;
  color: var(--ink);
}

.sd-theme-row:hover {
  color: var(--danger);
}

.sd-theme-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sd-theme-bar {
  height: 6px;
  background: var(--surface-2);
  display: block;
}

.sd-theme-fill {
  display: block;
  height: 100%;
}

.sd-theme-fill.is-negative { background: var(--danger); }
.sd-theme-fill.is-positive { background: var(--ink); }

.sd-theme-count,
.sd-theme-share {
  font-size: 0.75rem;
  color: var(--muted);
  text-align: right;
}

.sd-theme-share {
  color: var(--muted-soft);
}

.sd-example-list {
  list-style: none;
  margin: 0 0 10px;
  padding: 0 0 0 12px;
  border-left: 1px solid var(--border);
}

.sd-example {
  margin-bottom: 8px;
}

.sd-example-text {
  font-size: 0.78rem;
  line-height: 1.5;
  color: var(--muted);
  margin: 0 0 2px;
}

.sd-example-meta {
  font-size: 0.75rem;
  color: var(--muted-soft);
}

.sd-foot {
  font-size: 0.75rem;
  color: var(--muted-soft);
  border-top: 1px solid var(--surface-2);
  padding-top: 12px;
}
</style>
