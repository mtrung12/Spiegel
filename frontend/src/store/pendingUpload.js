/**
 * Holds the files waiting to be uploaded.
 * The home page navigates away the moment the engine is started, and the
 * Process page makes the API call.
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  isPending: false
})

export function setPendingUpload(files) {
  state.files = files
  state.isPending = true
}

export function getPendingUpload() {
  return {
    files: state.files,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.isPending = false
}

export default state
