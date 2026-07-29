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
              <svg
                v-if="isSectionCompleted(idx + 1)"
                class="collapse-icon"
                :class="{ 'is-collapsed': collapsedSections.has(idx) }"
                viewBox="0 0 24 24"
                width="20"
                height="20"
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
            <div
              v-if="generatedSections[idx + 1]"
              class="generated-content"
              v-html="renderMarkdown(generatedSections[idx + 1])"
            ></div>

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
    </div>

    <div v-else class="waiting-placeholder" role="status">
      <div class="waiting-animation" aria-hidden="true">
        <div class="waiting-ring"></div>
        <div class="waiting-ring"></div>
        <div class="waiting-ring"></div>
      </div>
      <span class="waiting-text">{{ $t('step4.waitingForReportAgent') }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { renderMarkdown } from '../utils/markdown'

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

const isSectionCompleted = (sectionNumber) => !!props.generatedSections[sectionNumber]

// Only a finished section can collapse - collapsing a placeholder hides the
// very progress the user is waiting on.
const toggleSectionCollapse = (idx) => {
  if (!isSectionCompleted(idx + 1)) return
  const next = new Set(collapsedSections.value)
  next.has(idx) ? next.delete(idx) : next.add(idx)
  collapsedSections.value = next
}
</script>

<style scoped>
.left-panel.report-style {
  width: 45%;
  min-width: 0;
  background: var(--white);
  border-right: 1px solid var(--border);
  overflow-y: auto;
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
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 0;
}

.section-header-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  transition: background-color 0.2s ease;
  padding: 8px 12px;
  margin: -8px -12px;
  border-radius: 8px;
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
  transition: transform 0.3s ease;
  flex-shrink: 0;
  align-self: center;
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
