<template>
  <!-- The rendered report. Step 4 (generation) and step 5 (interaction) showed
       the identical pane from two copies of this markup and CSS; they now share
       one, so a fix to either lands in both. -->
  <div class="left-panel report-style">
    <div v-if="outline" class="report-content-wrapper">
      <div class="report-header-block">
        <div class="report-meta">
          <span class="report-tag">{{ tag }}</span>
          <span v-if="reportId" class="report-id">{{ $t('step4.reportIdLabel') }}: {{ reportId }}</span>
        </div>
        <h1 class="main-title">{{ outline.title }}</h1>
        <p class="sub-title">{{ outline.summary }}</p>
        <div class="header-divider"></div>
      </div>

      <!-- Anything the host view wants between the header and the sections -->
      <slot name="header-extra" />

      <div class="sections-list">
        <div
          v-for="(section, idx) in outline.sections"
          :key="idx"
          class="report-section-item"
          :class="{
            'is-active': currentSectionIndex === idx + 1,
            'is-completed': isSectionCompleted(idx + 1),
            'is-pending': !isSectionCompleted(idx + 1) && currentSectionIndex !== idx + 1
          }"
        >
          <h3 class="section-header-row" :class="{ clickable: isSectionCompleted(idx + 1) }">
            <button
              type="button"
              class="section-header-btn"
              :aria-expanded="!collapsedSections.has(idx)"
              :disabled="!isSectionCompleted(idx + 1)"
              @click="toggleSectionCollapse(idx)"
            >
              <span class="section-number">{{ String(idx + 1).padStart(2, '0') }}</span>
              <span class="section-title">{{ section.title }}</span>
              <!-- Small and quiet: collapsing is a thing the reader may do, not
                   a thing the report is about. It only comes forward on hover,
                   and stays put while collapsed because then it is the only
                   sign the section is still there. -->
              <svg
                v-if="isSectionCompleted(idx + 1)"
                class="collapse-icon"
                :class="{ 'is-collapsed': collapsedSections.has(idx) }"
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                aria-hidden="true"
              >
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </button>
          </h3>

          <div class="section-body" v-show="!collapsedSections.has(idx)">
            <template v-if="generatedSections[idx + 1]">
              <!-- The one-sentence finding, always visible. -->
              <p v-if="split(idx).verdict" class="section-verdict">
                {{ split(idx).verdict }}
              </p>

              <!-- The panel that already renders this section's figures. The
                   host view decides which one, keyed by section id. -->
              <slot :name="`visual-${idx + 1}`" />

              <!-- The evidence opens beside the report rather than inside it.
                   Expanding it inline pushed every later section down the page
                   and buried the verdicts the reader came for - so the working
                   under a finding now sits in its own panel, and the column of
                   findings stays a column of findings. -->
              <button
                v-if="split(idx).body"
                type="button"
                class="evidence-btn"
                :class="{ 'is-open': openEvidence === idx }"
                :aria-expanded="openEvidence === idx"
                @click="toggleEvidence(idx)"
              >
                <span>{{ openEvidence === idx ? $t('step4.hideEvidence') : $t('step4.showEvidence') }}</span>
                <svg
                  viewBox="0 0 24 24"
                  width="13"
                  height="13"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  aria-hidden="true"
                >
                  <path d="M4 4h7"></path>
                  <path d="M4 4v16h16V4h-7"></path>
                  <path d="M14 4v6"></path>
                </svg>
              </button>
            </template>

            <div v-else-if="currentSectionIndex === idx + 1" class="loading-state">
              <div class="loading-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
                  <circle cx="12" cy="12" r="10" stroke-width="4" stroke="var(--border)"></circle>
                  <path d="M12 2a10 10 0 0 1 10 10" stroke-width="4" stroke="var(--ink-3)" stroke-linecap="round"></path>
                </svg>
              </div>
              <span class="loading-text">{{ $t('step4.generatingSection', { title: section.title }) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Whatever comes after the last section. The report is read top to
           bottom, so what to do next belongs at the bottom of it. -->
      <slot name="footer" />
    </div>

    <div v-else class="waiting-placeholder" role="status">
      <div class="waiting-animation" aria-hidden="true">
        <div class="waiting-ring"></div>
        <div class="waiting-ring"></div>
        <div class="waiting-ring"></div>
      </div>
      <span class="waiting-text">{{ $t('step4.waitingForReportAgent') }}</span>
    </div>

    <!-- Teleported to the body: anchored inside this pane it would be trapped
         in the column it is meant to stay out of, and clipped by its scroll
         box. No backdrop - the report has to stay readable and clickable
         beside it, which is the whole point of moving the evidence here. -->
    <Teleport to="body">
      <Transition name="evidence-slide">
        <aside
          v-if="openEvidence !== null"
          class="evidence-drawer"
          role="dialog"
          aria-modal="false"
          :aria-label="$t('step4.evidenceTitle')"
        >
          <header class="evidence-head">
            <div class="evidence-head-text">
              <span class="evidence-eyebrow">{{ $t('step4.evidenceTitle') }}</span>
              <span class="evidence-section">
                {{ String(openEvidence + 1).padStart(2, '0') }} ·
                {{ outline?.sections?.[openEvidence]?.title }}
              </span>
            </div>
            <button
              type="button"
              class="evidence-close"
              :aria-label="$t('common.close')"
              @click="closeEvidence"
            >
              <span aria-hidden="true">×</span>
            </button>
          </header>

          <div
            class="evidence-body generated-content"
            v-html="renderMarkdown(split(openEvidence).body)"
          ></div>
        </aside>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { renderMarkdown, splitVerdict } from '../utils/markdown'

const props = defineProps({
  outline: { type: Object, default: null },
  // Keyed by 1-based section number, matching what the report agent streams.
  generatedSections: { type: Object, default: () => ({}) },
  currentSectionIndex: { type: Number, default: null },
  reportId: { type: String, default: '' },
  tag: { type: String, default: '' }
})

// Collapse state belongs to the pane; nothing outside it reads or sets it.
const collapsedSections = ref(new Set())
// Which section's evidence the side panel is showing, or null. One at a time:
// the panel is a single surface, and reading the working behind two findings
// at once is not something the report is laid out for.
const openEvidence = ref(null)

// Splitting on every render would re-parse each section on any reactive change,
// so the results are cached and only recomputed when the content changes.
const splits = computed(() => {
  const out = {}
  for (const [index, content] of Object.entries(props.generatedSections)) {
    out[index] = splitVerdict(content)
  }
  return out
})

const split = (idx) => splits.value[idx + 1] || { verdict: '', body: '' }

const isSectionCompleted = (sectionNumber) => !!props.generatedSections[sectionNumber]

const closeEvidence = () => {
  openEvidence.value = null
}

const toggleEvidence = (idx) => {
  openEvidence.value = openEvidence.value === idx ? null : idx
}

// Only a finished section can collapse - collapsing a placeholder hides the
// very progress the user is waiting on.
const toggleSectionCollapse = (idx) => {
  if (!isSectionCompleted(idx + 1)) return
  const next = new Set(collapsedSections.value)
  next.has(idx) ? next.delete(idx) : next.add(idx)
  collapsedSections.value = next
}

// Collapsing a section whose evidence is open would leave the panel showing
// working for something no longer on screen.
watch(collapsedSections, (sections) => {
  if (openEvidence.value !== null && sections.has(openEvidence.value)) closeEvidence()
})

// Switching reports (step 4 -> step 5, or another run) must not leave the
// previous report's evidence on screen.
watch(() => props.reportId, closeEvidence)

// Esc closes it, as it would any panel laid over the page. Bound on the
// document because the drawer is teleported out of this component's tree and
// the reader's focus is usually still in the report beside it.
const onKeydown = (event) => {
  if (event.key === 'Escape' && openEvidence.value !== null) closeEvidence()
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.left-panel.report-style {
  width: 45%;
  min-width: 0;
  background: var(--white);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  /* The section rows bleed 12px past the text column on both sides for their
     hover background. Without this that bleed, and any wide table inside a
     section, hand the whole report a horizontal scrollbar. */
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  padding: 30px 50px 60px 50px;
}

.left-panel::-webkit-scrollbar {
  width: 6px;
}

.left-panel::-webkit-scrollbar-track {
  background: transparent;
}

.left-panel::-webkit-scrollbar-thumb {
  background: transparent;
  border-radius: 3px;
  transition: background 0.3s ease;
}

.left-panel:hover::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
}

.left-panel::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.25);
}

/* Report Header */
.report-content-wrapper {
  max-width: 800px;
  margin: 0 auto;
  width: 100%;
}

.report-header-block {
  margin-bottom: 30px;
}

.report-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.report-tag {
  background: var(--black);
  color: var(--white);
  font-size: 12px;
  font-weight: 700;
  padding: 4px 8px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.report-id {
  font-size: 12px;
  color: var(--muted-soft);
  font-weight: 500;
  letter-spacing: 0.02em;
}

.main-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 36px;
  font-weight: 700;
  color: var(--black);
  line-height: 1.2;
  margin: 0 0 16px 0;
  letter-spacing: -0.02em;
}

.sub-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 16px;
  color: var(--muted);
  font-style: italic;
  line-height: 1.6;
  margin: 0 0 30px 0;
  font-weight: 400;
}

.header-divider {
  height: 1px;
  background: var(--border);
  width: 100%;
}

/* Sections List */
.sections-list {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.report-section-item {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header-btn {
  /* Now a <button>: reset the inherited control styling. */
  font: inherit;
  text-align: left;
  appearance: none;
  border: none;
  background: none;
  color: inherit;
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 0;
}

.section-header-row {
  transition: background-color 0.2s ease;
  padding: 8px 12px;
  margin: -8px -12px;
  border-radius: 8px;
  /* The row is one button wide; it used to be a flex container too, which
     stacked two layouts on the same line for no gain. */
  min-width: 0;
}

.section-header-row.clickable {
  cursor: pointer;
}

.section-header-row.clickable:hover {
  background-color: var(--surface);
}

.collapse-icon {
  margin-left: auto;
  color: var(--muted-soft);
  transition: transform 0.3s ease, opacity 0.2s ease;
  flex-shrink: 0;
  align-self: center;
  /* Out of the way until wanted. An arrow next to every heading reads as part
     of the report's typography, which it is not. */
  opacity: 0;
}

.section-header-row:hover .collapse-icon,
.section-header-btn:focus-visible .collapse-icon,
/* While collapsed it is the only thing saying the section is still there, so
   it stays visible regardless. */
.collapse-icon.is-collapsed {
  opacity: 1;
}

.collapse-icon.is-collapsed {
  transform: rotate(-90deg);
}

.section-number {
  font-family: 'JetBrains Mono', monospace;
  font-size: 16px;
  color: var(--border);
  font-weight: 500;
  transition: color 0.3s ease;
}

.section-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 24px;
  font-weight: 600;
  color: var(--black);
  margin: 0;
  transition: color 0.3s ease;
  /* A flex item will not shrink below its content by default, so a long title
     used to push the chevron past the edge of the pane. */
  min-width: 0;
  overflow-wrap: anywhere;
}

/* States */
.report-section-item.is-pending .section-number {
  color: var(--border);
}
.report-section-item.is-pending .section-title {
  color: var(--muted-soft);
}

.report-section-item.is-active .section-number,
.report-section-item.is-completed .section-number {
  color: var(--muted-soft);
}

.report-section-item.is-active .section-title,
.report-section-item.is-completed .section-title {
  color: var(--black);
}

.section-body {
  padding-left: 28px;
  overflow: hidden;
  /* A column so the evidence control can sit itself against the right edge
     without the slotted figure panels losing their full width. */
  display: flex;
  flex-direction: column;
}

/* The one-sentence finding. Sized to be read before anything else in the
   section, and to survive being the only thing on screen. */
.section-verdict {
  font-family: 'Times New Roman', Times, serif;
  font-size: 17px;
  line-height: 1.6;
  color: var(--black);
  margin: 0 0 16px;
}

/* Sits against the right edge of the text column, out of the reading line:
   the verdict and the figures are the section, this is a way to go behind
   them. Its icon is the panel it opens. */
.evidence-btn {
  align-self: flex-end;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 5px 10px;
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
  font-family: 'Inter', system-ui, sans-serif;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
  transition: color 0.15s ease, border-color 0.15s ease, background-color 0.15s ease;
}

.evidence-btn:hover {
  color: var(--ink);
  border-color: var(--border-strong);
  background: var(--surface);
}

/* While its panel is open the control is the panel's handle, so it reads as
   pressed rather than as an invitation. */
.evidence-btn.is-open {
  color: var(--white);
  background: var(--ink);
  border-color: var(--ink);
}

/* ---------- The evidence panel ---------- */
.evidence-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(460px, 100vw);
  z-index: 300;
  background: var(--white);
  border-left: 1px solid var(--border-strong);
  box-shadow: -12px 0 32px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
}

.evidence-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.evidence-head-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.evidence-eyebrow {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted-soft);
}

.evidence-section {
  font-family: 'Times New Roman', Times, serif;
  font-size: 17px;
  font-weight: 600;
  color: var(--black);
  line-height: 1.3;
  overflow-wrap: anywhere;
}

.evidence-close {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 22px;
  line-height: 1;
  padding: 2px 6px;
  color: var(--muted);
  flex-shrink: 0;
  transition: color 0.15s ease;
}

.evidence-close:hover {
  color: var(--ink);
}

.evidence-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 20px;
}

.evidence-slide-enter-active,
.evidence-slide-leave-active {
  transition: transform 0.25s ease, opacity 0.25s ease;
}

.evidence-slide-enter-from,
.evidence-slide-leave-to {
  transform: translateX(16px);
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .evidence-slide-enter-active,
  .evidence-slide-leave-active {
    transition: none;
  }
}

@media (max-width: 900px) {
  .evidence-drawer {
    width: 100vw;
  }
}

/* Generated Content */
.generated-content {
  font-family: 'Inter', 'Noto Sans SC', system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.8;
  color: var(--ink-2);
}

.generated-content :deep(p) {
  margin-bottom: 1em;
}

.generated-content :deep(.md-h2),
.generated-content :deep(.md-h3),
.generated-content :deep(.md-h4) {
  font-family: 'Times New Roman', Times, serif;
  color: var(--black);
  margin-top: 1.5em;
  margin-bottom: 0.8em;
  font-weight: 700;
}

.generated-content :deep(.md-h2) { font-size: 20px; border-bottom: 1px solid var(--surface-2); padding-bottom: 8px; }
.generated-content :deep(.md-h3) { font-size: 18px; }
.generated-content :deep(.md-h4) { font-size: 16px; }

.generated-content :deep(.md-ul),
.generated-content :deep(.md-ol) {
  padding-left: 20px;
  margin-bottom: 1em;
}

.generated-content :deep(.md-li) {
  margin-bottom: 0.5em;
}

/* Tables scroll inside their own box rather than widening the pane. */
.generated-content :deep(.md-table) {
  display: block;
  overflow-x: auto;
  width: 100%;
  border-collapse: collapse;
  margin: 1.5em 0;
  font-size: 13px;
}

.generated-content :deep(.md-table th),
.generated-content :deep(.md-table td) {
  border-bottom: 1px solid var(--border);
  padding: 8px 12px;
  text-align: left;
  white-space: nowrap;
}

.generated-content :deep(.md-table th) {
  color: var(--black);
  font-weight: 600;
  border-bottom-width: 2px;
}

.generated-content :deep(.md-table td:not(:first-child)) {
  font-variant-numeric: tabular-nums;
}

.generated-content :deep(.md-quote) {
  border-left: 3px solid var(--border);
  padding-left: 16px;
  margin: 1.5em 0;
  color: var(--muted);
  font-style: italic;
  font-family: 'Times New Roman', Times, serif;
}

.generated-content :deep(.code-block) {
  background: var(--surface);
  padding: 12px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  overflow-x: auto;
  margin: 1em 0;
  border: 1px solid var(--border);
}

.generated-content :deep(strong) {
  font-weight: 600;
  color: var(--black);
}

/* Loading State */
.loading-state {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
  font-size: 14px;
  margin-top: 4px;
}

.loading-icon {
  width: 18px;
  height: 18px;
  animation: spin 1s linear infinite;
  display: flex;
  align-items: center;
  justify-content: center;
}

.loading-text {
  font-family: 'Times New Roman', Times, serif;
  font-size: 15px;
  color: var(--ink-3);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Content Styles Override */
.generated-content :deep(.md-h2) {
  font-family: 'Times New Roman', Times, serif;
  font-size: 18px;
  margin-top: 0;
}

/* Waiting Placeholder */
.waiting-placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: 40px;
  color: var(--muted-soft);
}

.waiting-animation {
  position: relative;
  width: 48px;
  height: 48px;
}

.waiting-ring {
  position: absolute;
  width: 100%;
  height: 100%;
  border: 2px solid var(--border);
  border-radius: 50%;
  animation: ripple 2s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

.waiting-ring:nth-child(2) {
  animation-delay: 0.4s;
}

.waiting-ring:nth-child(3) {
  animation-delay: 0.8s;
}

@keyframes ripple {
  0% { transform: scale(0.5); opacity: 1; }
  100% { transform: scale(2); opacity: 0; }
}

.waiting-text {
  font-size: 14px;
}
</style>
