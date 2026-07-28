import service from './index'

/**
 * Start report generation.
 * @param {Object} data - { simulation_id, force_regenerate? }
 */
export const generateReport = (data) => {
  return service.post('/api/report/generate', data)
}

/**
 * Get the agent log, incrementally.
 * @param {string} reportId
 * @param {number} fromLine - line to start from
 */
export const getAgentLog = (reportId, fromLine = 0) => {
  return service.get(`/api/report/${reportId}/agent-log`, { params: { from_line: fromLine } })
}

/**
 * Get the console log, incrementally.
 * @param {string} reportId
 * @param {number} fromLine - line to start from
 */
export const getConsoleLog = (reportId, fromLine = 0) => {
  return service.get(`/api/report/${reportId}/console-log`, { params: { from_line: fromLine } })
}

/**
 * Get the report detail.
 * @param {string} reportId
 */
export const getReport = (reportId) => {
  return service.get(`/api/report/${reportId}`)
}

/**
 * List reports, newest first.
 * @param {string} [simulationId] - optional, filter by simulation
 * @param {number} limit - page size
 */
export const listReports = (simulationId = null, limit = 50) => {
  const params = { limit }
  if (simulationId) params.simulation_id = simulationId
  return service.get('/api/report/list', { params })
}

/**
 * Get the sections generated so far, complete report or not.
 * @param {string} reportId
 */
export const getReportSections = (reportId) => {
  return service.get(`/api/report/${reportId}/sections`)
}

/**
 * Chat with the report agent.
 * @param {Object} data - { simulation_id, message, chat_history? }
 */
export const chatWithReport = (data) => {
  return service.post('/api/report/chat', data)
}
