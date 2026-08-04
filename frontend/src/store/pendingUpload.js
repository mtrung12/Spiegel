/**
 * Holds the files waiting to be uploaded.
 * The home page navigates away the moment the engine is started, and the
 * Process page makes the API call.
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  // What the project is called. Chosen on the home page next to the files,
  // because that is the only moment the user has the brief in front of them;
  // the header can still rename it afterwards.
  name: '',
  isPending: false
})

export function setPendingUpload(files, name = '') {
  state.files = files
  state.name = name
  state.isPending = true
}

export function getPendingUpload() {
  return {
    files: state.files,
    name: state.name,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.name = ''
  state.isPending = false
}

export default state
