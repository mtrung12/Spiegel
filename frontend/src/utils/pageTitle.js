/**
 * The browser tab title.
 *
 * Every route used to render the static "Spiegel" from index.html, so several
 * projects open side by side were indistinguishable and the browser's own
 * history was useless as a way back.
 *
 * This lives outside the router because the views that report a project name
 * are the same ones the router imports; putting the state here keeps
 * router -> views -> AppHeader -> router from closing into a cycle.
 */
import i18n from '../i18n'

const SUFFIX = 'Spiegel'

// Set by whichever view knows the campaign. Cleared by the router on every
// navigation: the next view re-announces it, and a stale project name in the
// tab is worse than none.
let projectName = ''
let titleKey = ''

const render = () => {
  const { t } = i18n.global
  const step = titleKey ? t(titleKey) : ''
  document.title = [projectName, step, SUFFIX].filter(Boolean).join(' · ')
}

export const setProjectTitle = (name) => {
  projectName = name || ''
  render()
}

export const setRouteTitle = (key, { keepProject = false } = {}) => {
  if (!keepProject) projectName = ''
  titleKey = key || ''
  render()
}
