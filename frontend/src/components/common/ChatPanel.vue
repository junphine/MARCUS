<template>
  <transition name="chat-slide">
    <div v-if="visible" class="chat-panel" @click.stop>
      <!-- Header -->
      <div class="chat-header">
        <div class="chat-header-info">
          <vue-feather type="message-circle" class="icon"></vue-feather>
          <div class="chat-header-text">
            <span class="chat-title">Chat with MARCUS</span>
            <span class="chat-subtitle" :title="currentPdfName || 'No document loaded'">
              {{ currentPdfName || 'No document loaded' }}
            </span>
          </div>
        </div>
        <button class="chat-close" @click="$emit('close')" title="Close chat">
          <vue-feather type="x" size="18"></vue-feather>
        </button>
      </div>

      <!-- No document hint -->
      <div v-if="!currentPdfName" class="chat-hint">
        <vue-feather type="info" size="14"></vue-feather>
        <span>Upload a PDF document to ask questions about it.</span>
      </div>

      <!-- Messages -->
      <div class="chat-messages" ref="messagesContainer">
        <div v-if="messages.length === 0" class="chat-empty">
          <vue-feather type="message-square" size="32" class="chat-empty-icon"></vue-feather>
          <p>Ask me anything about the current document.</p>
        </div>

        <div
          v-for="(message, index) in messages"
          :key="index"
          class="chat-message"
          :class="message.role"
        >
          <div class="message-bubble">
            <div
              v-if="message.role === 'assistant'"
              class="message-text markdown-body"
              v-html="renderMarkdown(message.text)"
            ></div>
            <div v-else class="message-text">{{ message.text }}</div>
          </div>
        </div>

        <!-- Loading indicator -->
        <div v-if="isLoading" class="chat-message assistant">
          <div class="message-bubble loading-bubble">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
          </div>
        </div>
      </div>

      <!-- Input -->
      <div class="chat-input-area">
        <textarea
          v-model="inputText"
          class="chat-input"
          placeholder="Ask a question about this document..."
          rows="2"
          :disabled="isLoading"
          @keydown.enter.exact.prevent="sendMessage"
        ></textarea>
        <button
          class="chat-send"
          @click="sendMessage"
          :disabled="!canSend"
          title="Send message"
        >
          <vue-feather type="send" size="16"></vue-feather>
        </button>
      </div>

      <!-- Send options -->
      <div class="chat-options">
        <label class="chat-option" title="When enabled, only the document path is sent to the agent">
          <input
            v-model="onlySendPath"
            type="checkbox"
            class="chat-option-checkbox"
          />
          <span class="chat-option-label">Only send path</span>
        </label>
      </div>
    </div>
  </transition>
</template>

<script>
import { marked } from 'marked'
import chatService from '@/services/chatService'

export default {
  name: 'ChatPanel',

  props: {
    visible: {
      type: Boolean,
      default: false
    }
  },

  emits: ['close'],

  data() {
    return {
      messages: [],
      inputText: '',
      isLoading: false,
      // Chat session ID keeps multi-turn conversation context in QwenPaw
      chatSessionId: this.generateSessionId(),
      // When enabled (default), only send the document path to the agent;
      // when disabled, send the document's markdown content via ask_markdown
      onlySendPath: true
    }
  },

  computed: {
    currentPdfName() {
      // Prefer the server-side filename of the processed document
      const metadata = this.$store.state.text.metadata
      if (metadata && metadata.pdfFilename) {
        return metadata.pdfFilename
      }
      const file = this.$store.state.pdf.file
      return file ? file.name : null
    },

    currentPdfId() {
      return this.$store.state.pdf.currentPdfId
    },

    canSend() {
      return this.inputText.trim().length > 0 && !this.isLoading
    }
  },

  watch: {
    // Clear the conversation when a new document is uploaded so the previous
    // document's chat history does not leak into the new document's context.
    currentPdfId(newId, oldId) {
      if (newId && newId !== oldId) {
        this.messages = []
        this.chatSessionId = this.generateSessionId()
      }
    }
  },

  methods: {
    generateSessionId() {
      if (window.crypto && window.crypto.randomUUID) {
        return window.crypto.randomUUID()
      }
      return `chat-${Date.now()}-${Math.random().toString(36).substring(2, 10)}`
    },

    renderMarkdown(text) {
      if (!text) return ''
      return marked.parse(text, { breaks: true })
    },

    async sendMessage() {
      const question = this.inputText.trim()
      if (!question || this.isLoading) return

      this.messages.push({ role: 'user', text: question })
      this.inputText = ''
      this.isLoading = true
      this.scrollToBottom()

      try {
        const result = await chatService.ask(
          question,
          this.currentPdfName,
          this.chatSessionId,
          this.onlySendPath
        )
        this.messages.push({
          role: 'assistant',
          text: result.answer || '(No answer received)'
        })
      } catch (error) {
        const detail = error.response?.data?.detail
        this.messages.push({
          role: 'assistant',
          text: `Sorry, something went wrong: ${detail || error.message}`
        })
      } finally {
        this.isLoading = false
        this.scrollToBottom()
      }
    },

    scrollToBottom() {
      this.$nextTick(() => {
        const container = this.$refs.messagesContainer
        if (container) {
          container.scrollTop = container.scrollHeight
        }
      })
    }
  }
}
</script>

<style scoped>
@import '@/assets/styles/_variables.scss';

.chat-panel {
  position: fixed;
  top: 70px;
  right: 20px;
  width: 480px;
  max-width: calc(100vw - 80px);
  height: min(560px, calc(100vh - 100px));
  display: flex;
  flex-direction: column;
  background: var(--color-card-bg);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  box-shadow: var(--shadow-lg);
  z-index: 1001;
  overflow: hidden;
  font-family: var(--font-family);
}

/* Header */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-panel-bg);
}

.chat-header-info {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-width: 0;
  color: var(--color-primary);
}

.chat-header-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-heading);
}

.chat-subtitle {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 240px;
}

.chat-close {
  background: transparent;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  transition: all 0.2s ease;
}

.chat-close:hover {
  background: var(--color-hover-bg);
  color: var(--color-text);
}

/* No document hint */
.chat-hint {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  background: var(--color-panel-bg);
  border-bottom: 1px solid var(--color-border);
}

/* Messages */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  color: var(--color-text-secondary);
  text-align: center;
  font-size: 0.9rem;
}

.chat-empty-icon {
  opacity: 0.4;
}

.chat-message {
  display: flex;
}

.chat-message.user {
  justify-content: flex-end;
}

.chat-message.assistant {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 95%;
  padding: 0.6rem 0.85rem;
  border-radius: 12px;
  font-size: 0.875rem;
  line-height: 1.5;
  word-break: break-word;
}

.chat-message.user .message-bubble {
  background: var(--color-primary);
  color: #fff;
  border-bottom-right-radius: 4px;
}

.chat-message.assistant .message-bubble {
  background: var(--color-panel-bg);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-bottom-left-radius: 4px;
}

.message-text {
  white-space: pre-wrap;
}

/* Markdown rendering for assistant messages */
.markdown-body {
  white-space: normal;
  line-height: 1.55;
  font-size: 0.875rem;
  word-break: break-word;

  ::v-deep(p) {
    margin: 0 0 0.5rem;

    &:last-child {
      margin-bottom: 0;
    }
  }

  ::v-deep(h1),
  ::v-deep(h2),
  ::v-deep(h3),
  ::v-deep(h4),
  ::v-deep(h5),
  ::v-deep(h6) {
    margin: 0.75rem 0 0.5rem;
    line-height: 1.3;
    font-weight: 600;

    &:first-child {
      margin-top: 0;
    }
  }

  ::v-deep(ul),
  ::v-deep(ol) {
    margin: 0 0 0.5rem;
    padding-left: 1.25rem;
  }

  ::v-deep(li) {
    margin-bottom: 0.15rem;
  }

  ::v-deep(blockquote) {
    margin: 0 0 0.5rem;
    padding-left: 0.75rem;
    border-left: 3px solid var(--color-border);
    color: var(--color-text-secondary);
  }

  ::v-deep(code) {
    background-color: rgba(0, 0, 0, 0.06);
    border-radius: 4px;
    padding: 0.1rem 0.3rem;
    font-size: 0.82em;
    font-family: var(--font-mono, monospace);
  }

  ::v-deep(pre) {
    background-color: rgba(0, 0, 0, 0.06);
    border-radius: 8px;
    padding: 0.75rem;
    overflow-x: auto;
    margin: 0 0 0.5rem;

    code {
      background: none;
      padding: 0;
    }
  }

  ::v-deep(table) {
    border-collapse: collapse;
    width: 100%;
    margin: 0 0 0.5rem;

    th,
    td {
      border: 1px solid var(--color-border);
      padding: 0.35rem 0.5rem;
      text-align: left;
    }

    th {
      background-color: rgba(0, 0, 0, 0.04);
    }
  }

  ::v-deep(img) {
    max-width: 100%;
    height: auto;
    border-radius: 6px;
  }

  ::v-deep(a) {
    color: var(--color-primary);
  }

  ::v-deep(hr) {
    border: none;
    border-top: 1px solid var(--color-border);
    margin: 0.75rem 0;
  }
}

/* Loading indicator */
.loading-bubble {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0.8rem 1rem;
}

.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-secondary);
  animation: typing 1.2s ease-in-out infinite;
}

.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}

/* Input area */
.chat-input-area {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  padding: 0.75rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-panel-bg);
}

.chat-input {
  flex: 1;
  resize: none;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 0.6rem 0.75rem;
  font-size: 0.875rem;
  font-family: var(--font-family);
  color: var(--color-text);
  background: var(--color-card-bg);
  outline: none;
  transition: border-color 0.2s ease;
}

.chat-input:focus {
  border-color: var(--color-primary);
}

.chat-input:disabled {
  opacity: 0.6;
}

.chat-send {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 10px;
  background: var(--color-primary);
  color: #fff;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.chat-send:hover:not(:disabled) {
  background: var(--color-primary-dark);
  transform: translateY(-1px);
}

.chat-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Send options */
.chat-options {
  display: flex;
  align-items: center;
  padding: 0.35rem 0.75rem 0.75rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-panel-bg);
}

.chat-option {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  cursor: pointer;
  user-select: none;
}

.chat-option-checkbox {
  width: 15px;
  height: 15px;
  margin: 0;
  cursor: pointer;
  accent-color: var(--color-primary);
}

.chat-option-label {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}

/* Slide transition */
.chat-slide-enter-active,
.chat-slide-leave-active {
  transition: all 0.25s ease;
}

.chat-slide-enter-from,
.chat-slide-leave-to {
  opacity: 0;
  transform: translateY(-10px) scale(0.98);
}

/* Responsive */
@media (max-width: 768px) {
  .chat-panel {
    top: 10px;
    right: 10px;
    left: 10px;
    width: auto;
    max-width: none;
    height: min(520px, calc(100vh - 40px));
  }
}
</style>
