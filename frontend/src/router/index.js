import { createRouter, createWebHistory } from 'vue-router'
import { setRouteTitle } from '../utils/pageTitle'
import Home from '../views/Home.vue'
import Process from '../views/MainView.vue'
import SimulationView from '../views/SimulationView.vue'
import SimulationRunView from '../views/SimulationRunView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'
import WorkspaceView from '../views/WorkspaceView.vue'
import NotFound from '../views/NotFound.vue'

// `titleKey` names the message the tab title is built from. Every route used to
// render the same static "Spiegel", so several projects open side by side were
// indistinguishable - and the browser's own history was useless as a way back.
const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home,
    meta: { titleKey: 'home.projectsTitle' }
  },
  {
    path: '/process/:projectId',
    name: 'Process',
    component: Process,
    props: true,
    meta: { titleKey: 'main.stepNames[0]' }
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true,
    meta: { titleKey: 'main.stepNames[1]' }
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true,
    meta: { titleKey: 'main.stepNames[2]' }
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: ReportView,
    props: true,
    meta: { titleKey: 'main.stepNames[3]' }
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: InteractionView,
    props: true,
    meta: { titleKey: 'main.stepNames[4]' }
  },
  {
    path: '/workspace/:projectId',
    name: 'Workspace',
    component: WorkspaceView,
    props: true,
    meta: { titleKey: 'workspace.title' }
  },
  // Anything else. Without this a mistyped or stale link rendered a blank page
  // with no chrome and no way out.
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: NotFound,
    meta: { titleKey: 'notFound.title' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // Step views are their own scroll containers on desktop, but below 1024px the
  // shell scrolls as one column, so a step change used to open mid-page.
  scrollBehavior(to, from, savedPosition) {
    return savedPosition || { top: 0 }
  }
})

// Staying on the same route with different params - switching reports, say -
// keeps the project name the view already reported; a real route change drops
// it so the next view can announce its own.
router.afterEach((to, from) => {
  setRouteTitle(to.meta?.titleKey, { keepProject: to.name === from.name })
})

export default router
