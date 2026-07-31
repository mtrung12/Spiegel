<template>
  <section class="kpi-panel">
    <div v-if="loading" class="kpi-state">{{ $t('campaign.loading') }}</div>
    <div v-else-if="error" class="kpi-state kpi-state--error">
      {{ $t('campaign.error', { error }) }}
    </div>
    <div v-else-if="!hasData" class="kpi-state">{{ $t('campaign.empty') }}</div>

    <template v-else>
      <div v-for="group in groups" :key="group.key" class="kpi-group">
        <div class="kpi-group-label">{{ $t(group.label) }}</div>
        <div class="kpi-grid">
          <div v-for="cell in group.cells" :key="cell.label" class="kpi-card">
            <div class="kpi-value">{{ cell.value }}</div>
            <div class="kpi-label">{{ $t(cell.label) }}</div>
          </div>
        </div>
      </div>

      <!-- Per-segment breakdown -->
      <div v-if="shows('segments')" class="kpi-group">
        <div class="kpi-group-label">{{ $t('campaign.segments') }}</div>
        <div v-if="!segments.length" class="kpi-state">{{ $t('campaign.noSegments') }}</div>
        <div v-else class="kpi-table-wrap">
          <table class="kpi-table">
            <thead>
              <tr>
                <th>{{ $t('campaign.segmentName') }}</th>
                <th>{{ $t('campaign.audienceSize') }}</th>
                <th>{{ $t('campaign.reachRate') }}</th>
                <th>{{ $t('campaign.engagementRate') }}</th>
                <th>{{ $t('campaign.netSentiment') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="seg in segments" :key="seg.segment">
                <td class="kpi-td-name">{{ seg.segment }}</td>
                <td>{{ seg.audience_size }}</td>
                <td>{{ pct(seg.reach_rate_pct) }}</td>
                <td>{{ pct(seg.engagement_rate_pct) }}</td>
                <td>{{ num(seg.net_sentiment) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Round-by-round curve, as a bar strip -->
      <div v-if="shows('rounds')" class="kpi-group">
        <div class="kpi-group-label">{{ $t('campaign.perRound') }}</div>
        <div v-if="!perRound.length" class="kpi-state">{{ $t('campaign.noRounds') }}</div>
        <div v-else class="kpi-chart">
          <!-- y axis: the strip is normalised to the loudest round, so the
               only two labels that carry information are that peak and 0. -->
          <div class="kpi-yaxis mono">
            <span>{{ maxRoundActions }}</span>
            <span>0</span>
          </div>
          <div class="kpi-plot">
            <div class="kpi-rounds">
              <div
                v-for="row in perRound"
                :key="row.round_num"
                class="kpi-round-bar"
                :title="`${$t('campaign.roundLabel')} ${row.round_num} · ${row.total_actions} ${$t('campaign.roundActions')}`"
              >
                <div class="kpi-round-fill" :style="{ height: barHeight(row.total_actions) }"></div>
              </div>
            </div>
            <div class="kpi-xaxis mono">
              <span>{{ $t('campaign.roundLabel') }} {{ perRound[0].round_num }}</span>
              <span>{{ $t('campaign.roundActions') }}</span>
              <span>{{ $t('campaign.roundLabel') }} {{ perRound[perRound.length - 1].round_num }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getCampaignMetrics } from '../api/simulation'

const props = defineProps({
  simulationId: String,
  // The parent flips this once the report/simulation has finished, so the
  // panel does not query an action log that is still being written.
  autoLoad: { type: Boolean, default: true },
  // Which blocks to render: any of reach, engagement, virality, sentiment,
  // segments, rounds. Empty means all of them. The report pane uses this to
  // show each block beside the section that explains it, instead of dumping
  // every figure above the report.
  only: { type: Array, default: () => [] }
})

const shows = (key) => !props.only.length || props.only.includes(key)

const metrics = ref(null)
const loading = ref(false)
const error = ref('')

const num = (v) => (typeof v === 'number' ? Math.round(v * 100) / 100 : v ? v : '—')
const pct = (v) => (typeof v === 'number' ? `${Math.round(v * 10) / 10}%` : '—')

// share_of_voice is a per-topic map, not a scalar; the headline figure is the
// campaign's own share when the backend reports one.
const shareOfVoice = computed(() => {
  const sov = metrics.value?.share_of_voice
  if (typeof sov === 'number') return pct(sov)
  const share = sov?.campaign_share_pct ?? sov?.campaign_pct
  return typeof share === 'number' ? pct(share) : '—'
})

// The endpoint answers 200 with an all-zero payload for a run that has not
// produced an action log yet, so emptiness has to be judged from the numbers.
const hasData = computed(() => {
  const m = metrics.value
  if (!m) return false
  return Boolean(m.reach?.audience_size || m.reach?.rounds_observed || m.engagement?.engagement_actions)
})

const groups = computed(() => {
  const m = metrics.value
  if (!m) return []
  return allGroups(m).filter((g) => shows(g.key))
})

const allGroups = (m) => {
  return [
    {
      key: 'reach',
      label: 'campaign.groupReach',
      cells: [
        { label: 'campaign.audienceSize', value: num(m.reach?.audience_size) },
        { label: 'campaign.reachedAgents', value: num(m.reach?.reached_agents) },
        { label: 'campaign.reachRate', value: pct(m.reach?.reach_rate_pct) },
        { label: 'campaign.impressions', value: num(m.reach?.total_impressions) }
      ]
    },
    {
      key: 'engagement',
      label: 'campaign.groupEngagement',
      cells: [
        { label: 'campaign.engagedAgents', value: num(m.engagement?.engaged_agents) },
        { label: 'campaign.engagementRate', value: pct(m.engagement?.engagement_rate_pct) },
        { label: 'campaign.engagementActions', value: num(m.engagement?.engagement_actions) },
        { label: 'campaign.passiveShare', value: pct(m.engagement?.passive_share_pct) }
      ]
    },
    {
      key: 'virality',
      label: 'campaign.groupVirality',
      cells: [
        { label: 'campaign.amplifications', value: num(m.virality?.amplifications) },
        { label: 'campaign.viralityRatio', value: num(m.virality?.virality_ratio) },
        { label: 'campaign.cascadeDepth', value: num(m.virality?.cascade_depth_rounds) },
        { label: 'campaign.peakRound', value: num(m.virality?.peak_round) }
      ]
    },
    {
      key: 'sentiment',
      label: 'campaign.groupSentiment',
      cells: [
        { label: 'campaign.positiveShare', value: pct(m.sentiment?.positive_share_pct) },
        { label: 'campaign.negativeShare', value: pct(m.sentiment?.negative_share_pct) },
        { label: 'campaign.netSentiment', value: num(m.sentiment?.net_sentiment) },
        { label: 'campaign.shareOfVoice', value: shareOfVoice.value }
      ]
    }
  ]
}

const segments = computed(() => metrics.value?.segments || [])
const perRound = computed(() => metrics.value?.per_round || [])

const maxRoundActions = computed(() =>
  perRound.value.reduce((max, r) => Math.max(max, r.total_actions || 0), 0)
)

const barHeight = (value) => {
  if (!maxRoundActions.value) return '2px'
  return `${Math.max(2, Math.round((value / maxRoundActions.value) * 100))}%`
}

const load = async () => {
  if (!props.simulationId) return
  loading.value = true
  error.value = ''
  try {
    const res = await getCampaignMetrics(props.simulationId)
    metrics.value = res.data || null
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

watch(() => [props.simulationId, props.autoLoad], () => {
  if (props.autoLoad) load()
})

onMounted(() => {
  if (props.autoLoad) load()
})

defineExpose({ load })
</script>

<style scoped>
.kpi-panel {
  border: 1px solid var(--border);
  background: var(--white);
  padding: 24px;
  margin-bottom: 32px;
}

.kpi-state {
  font-size: 0.82rem;
  color: var(--muted-soft);
  padding: 12px 0;
}

.kpi-state--error {
  color: var(--danger);
}

.kpi-group {
  margin-bottom: 24px;
}

.kpi-group-label {
  font-size: 0.75rem;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--muted-soft);
  margin-bottom: 10px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}

.kpi-card {
  border: 1px solid var(--border);
  background: var(--surface);
  padding: 14px 16px;
}

.kpi-value {
  font-size: 1.4rem;
  font-weight: 600;
  color: var(--ink);
  font-family: 'JetBrains Mono', monospace;
}

.kpi-label {
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 4px;
}

.kpi-table-wrap {
  overflow-x: auto;
}

.kpi-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}

.kpi-table th,
.kpi-table td {
  text-align: right;
  padding: 8px 12px;
  border-bottom: 1px solid var(--surface-2);
  white-space: nowrap;
}

.kpi-table th {
  color: var(--muted-soft);
  font-weight: 500;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.kpi-table th:first-child,
.kpi-td-name {
  text-align: left;
  color: var(--ink);
}

.kpi-chart {
  display: flex;
  gap: 8px;
}

.kpi-plot {
  flex: 1 1 auto;
  min-width: 0;
}

.kpi-yaxis,
.kpi-xaxis {
  font-family: 'JetBrains Mono', monospace;
}

.kpi-yaxis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: flex-end;
  height: 90px;
  font-size: 0.68rem;
  color: var(--muted-soft);
}

.kpi-xaxis {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  font-size: 0.68rem;
  color: var(--muted-soft);
}

.kpi-rounds {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 90px;
  border-bottom: 1px solid var(--border);
}

.kpi-round-bar {
  flex: 1 1 auto;
  min-width: 3px;
  height: 100%;
  display: flex;
  align-items: flex-end;
}

.kpi-round-fill {
  width: 100%;
  background: var(--ink);
}
</style>
