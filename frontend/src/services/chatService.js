import api from './api'
import sessionService from './sessionService'

/**
 * Service for chatting with the MARCUS assistant (backed by the QwenPaw agent)
 * about the currently loaded document.
 */
const chatService = {
  /**
   * Ask a question about the current document
   * @param {string} question - The user's question
   * @param {string|null} pdfFilename - Filename of the current PDF (as stored on the server)
   * @param {string|null} chatSessionId - Chat session ID for multi-turn conversation context
   * @param {boolean} onlySendPath - When true (default), only send the path and let the
   *   agent resolve files itself; when false, send the document's markdown content
   * @returns {Promise} - Promise with the answer data ({ answer, session_id, document_dir })
   */
  ask: async (question, pdfFilename = null, chatSessionId = null, onlySendPath = true) => {
    const config = {
      headers: {},
      // The agent may need to read/process the whole document, allow long requests
      timeout: 600000 // 10 minutes
    }

    // Session middleware requires an active session ID for /v1//latest/ endpoints
    const sessionId = sessionService.getSessionId()
    if (sessionId) {
      config.headers['X-Session-ID'] = sessionId
    }

    try {
      const response = await api.post('/chat/ask', {
        question,
        pdf_filename: pdfFilename || null,
        session_id: chatSessionId || null,
        only_path: onlySendPath
      }, config)
      return response.data
    } catch (error) {
      console.error('Error asking document question:', error)
      throw error
    }
  }
}

export default chatService
