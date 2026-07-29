import { ref } from 'vue'

/**
 * The graph/split/workbench split-pane layout shared by every step view.
 * Each view used to carry its own copy of this, which is how they drifted apart.
 *
 * Panel widths live in CSS (`.content-area[data-mode]` in App.vue) rather than
 * inline styles, so the responsive rules can override them - an inline style
 * wins over any stylesheet rule and made the layout unresponsive.
 */
export function useSplitLayout(initialMode = 'split') {
  const viewMode = ref(initialMode) // graph | split | workbench

  // Clicking maximise on an already-maximised panel returns to the split view,
  // so the control is a toggle rather than a dead end.
  const toggleMaximize = (target) => {
    viewMode.value = viewMode.value === target ? 'split' : target
  }

  return { viewMode, toggleMaximize }
}

/**
 * The scrolling system log every step view feeds. Capped so a long-running
 * simulation cannot grow the array without bound.
 */
export function useSystemLog(limit = 200) {
  const systemLogs = ref([])

  const addLog = (msg) => {
    const now = new Date()
    const time = now.toLocaleTimeString('en-US', {
      hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
    }) + '.' + now.getMilliseconds().toString().padStart(3, '0')
    systemLogs.value.push({ time, msg })
    if (systemLogs.value.length > limit) systemLogs.value.shift()
  }

  return { systemLogs, addLog }
}
