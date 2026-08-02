import { ref, watch } from 'vue'

/**
 * The graph/split/workbench split-pane layout shared by every step view.
 * Each view used to carry its own copy of this, which is how they drifted apart.
 *
 * Panel widths live in CSS (`.content-area[data-mode]` in App.vue) rather than
 * inline styles, so the responsive rules can override them - an inline style
 * wins over any stylesheet rule and made the layout unresponsive.
 */
const MODES = ['graph', 'split', 'workbench']
const STORAGE_KEY = 'layoutMode'

export function useSplitLayout(initialMode = 'split') {
  // A choice made on one step used to be thrown away on the next, because each
  // view hard-coded its own starting mode - so the layout jumped on every
  // navigation and the switcher never held. The per-view default now applies
  // only until the user picks something.
  const stored = localStorage.getItem(STORAGE_KEY)
  const viewMode = ref(MODES.includes(stored) ? stored : initialMode)

  watch(viewMode, (mode) => {
    try {
      localStorage.setItem(STORAGE_KEY, mode)
    } catch {
      // Private-browsing quota. The layout still works, it just stops carrying.
    }
  })

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
