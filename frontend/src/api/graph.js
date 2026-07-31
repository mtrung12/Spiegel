import service from './index'

/**
 * Generate the ontology by uploading the documents and the simulation requirement.
 * @param {Object} data - files, simulation_requirement, project_name, ...
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return service({
    url: '/api/graph/ontology/generate',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

/**
 * Build the graph.
 * @param {Object} data - project_id, graph_name, ...
 * @returns {Promise}
 */
export function buildGraph(data) {
  return service({
    url: '/api/graph/build',
    method: 'post',
    data
  })
}

/**
 * Query the task status.
 * @param {String} taskId - the task ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * Ask a running task to stop.
 * Cancellation is cooperative: this only requests the stop, so keep polling
 * the task until it reports a terminal status.
 * @param {String} taskId - the task ID
 * @returns {Promise}
 */
export function cancelTask(taskId) {
  return service({
    url: `/api/graph/task/${taskId}/cancel`,
    method: 'post'
  })
}

/**
 * Get the graph data.
 * @param {String} graphId - the graph ID
 * @returns {Promise}
 */
export function getGraphData(graphId) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  })
}

/**
 * Get the project.
 * @param {String} projectId - the project ID
 * @param {Boolean} includeText - also return the extracted document text.
 *   Off by default: it is the whole brief, and only the source-text viewer
 *   needs it.
 * @returns {Promise}
 */
export function getProject(projectId, includeText = false) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get',
    params: includeText ? { include_text: 1 } : undefined
  })
}

/**
 * List the projects for the home page, each with its latest run, report and stage.
 * @param {Number} limit - page size
 * @returns {Promise}
 */
export function listProjects(limit = 20) {
  return service({
    url: '/api/graph/project/list',
    method: 'get',
    params: { limit }
  })
}

/**
 * Which workflow steps this project can navigate to, and the id each one needs.
 * Drives the stepper: a view only knows the ids its own route was given, so
 * step 1 cannot otherwise tell that a finished run and report exist.
 * @param {String} projectId - the project ID
 * @returns {Promise}
 */
export function getProjectProgress(projectId) {
  return service({
    url: `/api/graph/project/${projectId}/progress`,
    method: 'get'
  })
}

/**
 * Delete a project, its knowledge graph and its simulations.
 * @param {String} projectId - the project ID
 * @returns {Promise}
 */
export function deleteProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'delete'
  })
}

/**
 * Reset a failed project back to its last good state so the graph can be rebuilt.
 * @param {String} projectId - the project ID
 * @returns {Promise}
 */
export function resetProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}/reset`,
    method: 'post'
  })
}
