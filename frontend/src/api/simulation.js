import service from './index'

/**
 * Create a simulation.
 * @param {Object} data - { project_id, graph_id?, enable_twitter?, enable_reddit? }
 */
export const createSimulation = (data) => {
  return service.post('/api/simulation/create', data)
}

/**
 * Get the graph entities (segments) that agents get built from.
 * @param {string} graphId
 * @param {boolean} [enrich] - also fetch related edges; false is much faster
 */
export const getGraphEntities = (graphId, enrich = false) => {
  return service.get(`/api/simulation/entities/${graphId}`, { params: { enrich } })
}

/**
 * Prepare the simulation environment (background task).
 * @param {Object} data - { simulation_id, entity_types?, use_llm_for_profiles?, parallel_profile_count?, force_regenerate? }
 */
export const prepareSimulation = (data) => {
  return service.post('/api/simulation/prepare', data)
}

/**
 * Query the preparation progress.
 * @param {Object} data - { task_id?, simulation_id? }
 */
export const getPrepareStatus = (data) => {
  return service.post('/api/simulation/prepare/status', data)
}

/**
 * Get the simulation status.
 * @param {string} simulationId
 */
export const getSimulation = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}`)
}

/**
 * Get the agent profiles live, while they are being generated.
 * @param {string} simulationId
 * @param {string} [platform] - 'reddit' | 'twitter'; omitted, the backend picks from the simulation config
 */
export const getSimulationProfilesRealtime = (simulationId, platform) => {
  const params = platform ? { platform } : {}
  return service.get(`/api/simulation/${simulationId}/profiles/realtime`, { params })
}

/**
 * Get the simulation config.
 * @param {string} simulationId
 */
export const getSimulationConfig = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config`)
}

/**
 * Get the simulation config live, while it is being generated.
 * @param {string} simulationId
 * @returns {Promise} the config, with its metadata and body
 */
export const getSimulationConfigRealtime = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config/realtime`)
}

/**
 * List every simulation.
 * @param {string} projectId - optional, filter by project
 */
export const listSimulations = (projectId) => {
  const params = projectId ? { project_id: projectId } : {}
  return service.get('/api/simulation/list', { params })
}

/**
 * Start the simulation.
 * @param {Object} data - { simulation_id, platform?, max_rounds?, enable_graph_memory_update? }
 */
export const startSimulation = (data) => {
  return service.post('/api/simulation/start', data)
}

/**
 * Stop the simulation.
 * @param {Object} data - { simulation_id }
 */
export const stopSimulation = (data) => {
  return service.post('/api/simulation/stop', data)
}

/**
 * Get the live run status.
 * @param {string} simulationId
 */
export const getRunStatus = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status`)
}

/**
 * Get the detailed run status, including the recent actions.
 * @param {string} simulationId
 */
export const getRunStatusDetail = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status/detail`)
}

/**
 * Get the posts from the simulation.
 * @param {string} simulationId
 * @param {string} [platform] - 'reddit' | 'twitter'; omitted, the backend picks from the simulation config
 * @param {number} limit - page size
 * @param {number} offset - page offset
 * @param {string} sortBy - 'created_at' | 'num_likes' | 'num_dislikes' | 'num_shares' | 'num_comments' | 'post_id'
 * @param {string} order - 'desc' | 'asc'
 */
export const getSimulationPosts = (
  simulationId,
  platform,
  limit = 50,
  offset = 0,
  sortBy = 'created_at',
  order = 'desc'
) => {
  const params = { limit, offset, sort_by: sortBy, order }
  if (platform) params.platform = platform
  return service.get(`/api/simulation/${simulationId}/posts`, { params })
}

/**
 * Get the comments from the simulation, optionally for a single post.
 * @param {string} simulationId
 * @param {number|string} [postId] - omitted, returns the newest comments across all posts
 * @param {string} [platform] - 'reddit' | 'twitter'; omitted, the backend picks from the simulation config
 * @param {number} limit - page size
 * @param {number} offset - page offset
 */
export const getSimulationComments = (simulationId, postId = null, platform = null, limit = 100, offset = 0) => {
  const params = { limit, offset }
  if (postId !== null && postId !== undefined) params.post_id = postId
  if (platform) params.platform = platform
  return service.get(`/api/simulation/${simulationId}/comments`, { params })
}

/**
 * Shut the simulation environment down gracefully.
 * @param {Object} data - { simulation_id, timeout? }
 */
export const closeSimulationEnv = (data) => {
  return service.post('/api/simulation/close-env', data)
}

/**
 * Get the simulation environment status.
 * @param {Object} data - { simulation_id }
 */
export const getEnvStatus = (data) => {
  return service.post('/api/simulation/env-status', data)
}

/**
 * Interview agents in a batch.
 * @param {Object} data - { simulation_id, interviews: [{ agent_id, prompt }] }
 */
export const interviewAgents = (data) => {
  return service.post('/api/simulation/interview/batch', data)
}

/**
 * Get the measured marketing KPIs for a campaign test.
 * Counted from the action log — reach, engagement, virality, sentiment,
 * share of voice, per-segment breakdown and the round-by-round curve.
 * @param {string} simulationId
 */
export const getCampaignMetrics = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/campaign-metrics`)
}

/**
 * Get the sentiment digest for what the audience actually wrote.
 * Positive/neutral/negative split of posts and comments, the loudest post on
 * each side, recurring objections and recurring hooks. LLM-classified and
 * cached server-side; pass force to reclassify.
 * @param {string} simulationId
 * @param {boolean} force - reclassify instead of reusing the cached digest
 */
export const getSentimentDigest = (simulationId, force = false) => {
  return service.get(`/api/simulation/${simulationId}/sentiment-digest`, {
    params: force ? { force: true } : {}
  })
}
