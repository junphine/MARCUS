<template>
  <div class="text-panel">
    <div v-if="!hasText" class="empty-state">
      <vue-feather type="file-text" size="48" class="empty-icon"></vue-feather>
      <h3>No Text Extracted Yet</h3>
      <p>Upload a PDF and click "Extract Text" to view the extracted content here.</p>
    </div>
    
    <div v-else class="text-content">
      <div class="text-actions">
        <button 
          class="btn btn-primary"
          @click="extractAnnotations"
          :disabled="isLoadingAnnotations"
        >
          <span v-if="!isLoadingAnnotations">
            <vue-feather type="tag" class="icon"></vue-feather>
            Extract Annotations
          </span>
          <span v-else class="loading-text">
            <vue-feather type="loader" class="icon spin"></vue-feather>
            Extracting...
          </span>
        </button>
        
        <button class="btn btn-outline" @click="downloadText">
          <vue-feather type="download" class="icon"></vue-feather>
          Download Text
        </button>
      </div>
      
      <div class="text-display">
        <div class="view-toggle">
          <button
            class="toggle-btn"
            :class="{ active: viewMode === 'preview' }"
            @click="viewMode = 'preview'"
          >
            <vue-feather type="eye" class="icon"></vue-feather>
            Preview
          </button>
          <button
            class="toggle-btn"
            :class="{ active: viewMode === 'markdown' }"
            @click="viewMode = 'markdown'"
          >
            <vue-feather type="code" class="icon"></vue-feather>
            Markdown
          </button>
        </div>

        <div
          v-show="viewMode === 'preview'"
          class="markdown-preview"
          v-html="renderedMarkdown"
        ></div>

        <div v-show="viewMode === 'markdown'" class="markdown-raw">
          <HighlightedText
            :text="extractedText"
            :annotations="annotations"
          />
        </div>
      </div>
      
      <div v-if="annotations && annotations.length > 0" class="annotations-section">
        <h3 class="section-title">
          <vue-feather type="list" class="icon"></vue-feather>
          Extracted Annotations
        </h3>
        
        <AnnotationViewer :annotations="annotations" />
        
        <div class="annotation-actions">
          <button class="btn btn-outline" @click="downloadAnnotations">
            <vue-feather type="download" class="icon"></vue-feather>
            Download Annotations
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { marked } from 'marked'
import { getApiImageUrl } from '@/services/api'
import HighlightedText from './HighlightedText.vue'
import AnnotationViewer from './AnnotationViewer.vue'

export default {
  name: 'TextPanel',
  components: {
    HighlightedText,
    AnnotationViewer
  },
  props: {
    extractedText: {
      type: String,
      default: ''
    },
    annotations: {
      type: Array,
      default: () => []
    },
    isLoadingAnnotations: {
      type: Boolean,
      default: false
    },
    pdfFilename: {
      type: String,
      default: ''
    }
  },
  data() {
    return {
      viewMode: 'preview' // 'preview' | 'markdown'
    }
  },
  computed: {
    hasText() {
      return !!this.extractedText
    },
    /**
     * Markdown with docling's <!-- image --> placeholders replaced by
     * real segment image URIs, e.g.
     * http://localhost:9000/latest/decimer/get_segment_image/{pdf}/picture_1_segmented.png
     * The numbering matches the backend segmentation order (picture_1, picture_2, ...).
     */
    markdownWithImages() {
      if (!this.extractedText) return ''
      if (!this.pdfFilename) return this.extractedText

      // Segment directory naming follows the backend decimer segmentation
      // router: re.sub(r"[^\w.-]", "_", filename), without the .pdf extension
      const pdfStem = this.pdfFilename
        .replace(/[^\w.-]/g, '_')
        .replace(/\.pdf$/i, '')

      let figureIndex = 0
      return this.extractedText.replace(/<!--\s*image\s*-->/g, () => {
        figureIndex += 1
        const imageUrl = getApiImageUrl(
          `${pdfStem}/all_segments/picture_${figureIndex}_segmented.png`
        )
        return `![Figure ${figureIndex}](${imageUrl})`
      })
    },
    renderedMarkdown() {
      if (!this.markdownWithImages) return ''
      return marked.parse(this.markdownWithImages)
    }
  },
  methods: {
    extractAnnotations() {
      this.$emit('extract-annotations')
    },
    downloadText() {
      if (!this.markdownWithImages) return

      const blob = new Blob([this.markdownWithImages], { type: 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'extracted_text.md'
      a.click()
      URL.revokeObjectURL(url)
    },
    downloadAnnotations() {
      if (!this.annotations || this.annotations.length === 0) return
      
      // Format annotations as JSON
      const formattedAnnotations = this.annotations.map(ann => {
        return {
          label: ann.label,
          text: ann.text,
          start_offset: ann.start_offset,
          end_offset: ann.end_offset
        }
      })
      
      const blob = new Blob([JSON.stringify(formattedAnnotations, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'annotations.json'
      a.click()
      URL.revokeObjectURL(url)
    }
  }
}
</script>

<style lang="scss" scoped>
.text-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    height: 100%;
    min-height: 300px;
    padding: 2rem;
    
    .empty-icon {
      color: var(--color-text-light);
      margin-bottom: 1rem;
    }
    
    h3 {
      margin-bottom: 0.5rem;
    }
    
    p {
      color: var(--color-text-light);
      max-width: 280px;
    }
  }
  
  .text-content {
    display: flex;
    flex-direction: column;
    height: 100%;
    
    .text-actions {
      display: flex;
      gap: 1rem;
      margin-bottom: 1rem;
      
      .btn {
        flex: 1;
        
        @media (min-width: 768px) {
          flex: 0 0 auto;
        }
      }
      
      .icon {
        width: 16px;
        height: 16px;
      }
      
      .loading-text {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
      }
      
      .spin {
        animation: spin 1s linear infinite;
      }
    }
    
    .text-display {
      flex: 1;
      background-color: var(--color-card-bg);
      border-radius: var(--radius-lg);
      padding: 1.5rem;
      box-shadow: var(--shadow-sm);
      overflow-y: auto;
      margin-bottom: 1.5rem;
      min-height: 200px;
      max-height: calc(100vh - 100px);

      .view-toggle {
        display: flex;
        gap: 0.5rem;
        margin-bottom: 1rem;
        border-bottom: 1px solid var(--color-border, #e2e8f0);
        padding-bottom: 0.75rem;

        .toggle-btn {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          padding: 0.4rem 0.9rem;
          border: 1px solid var(--color-border, #e2e8f0);
          border-radius: var(--radius-md, 8px);
          background: transparent;
          color: var(--color-text-light);
          font-size: 0.875rem;
          cursor: pointer;
          transition: all 0.2s ease;

          .icon {
            width: 14px;
            height: 14px;
          }

          &:hover {
            color: var(--color-primary);
            border-color: var(--color-primary);
          }

          &.active {
            background-color: var(--color-primary);
            border-color: var(--color-primary);
            color: #fff;
          }
        }
      }

      .markdown-preview {
        font-size: 1rem;
        line-height: 1.6;
        color: var(--color-text);

        ::v-deep(h1),
        ::v-deep(h2),
        ::v-deep(h3),
        ::v-deep(h4),
        ::v-deep(h5),
        ::v-deep(h6) {
          margin: 1.25rem 0 0.75rem;
          line-height: 1.3;

          &:first-child {
            margin-top: 0;
          }
        }

        ::v-deep(p) {
          margin-bottom: 1rem;

          &:last-child {
            margin-bottom: 0;
          }
        }

        ::v-deep(ul),
        ::v-deep(ol) {
          margin-bottom: 1rem;
          padding-left: 1.5rem;
        }

        ::v-deep(blockquote) {
          margin: 0 0 1rem;
          padding-left: 1rem;
          border-left: 3px solid var(--color-primary);
          color: var(--color-text-light);
        }

        ::v-deep(code) {
          background-color: rgba(0, 0, 0, 0.06);
          border-radius: 4px;
          padding: 0.1rem 0.35rem;
          font-size: 0.875em;
        }

        ::v-deep(pre) {
          background-color: rgba(0, 0, 0, 0.06);
          border-radius: var(--radius-md, 8px);
          padding: 1rem;
          overflow-x: auto;
          margin-bottom: 1rem;

          code {
            background: none;
            padding: 0;
          }
        }

        ::v-deep(table) {
          border-collapse: collapse;
          width: 100%;
          margin-bottom: 1rem;

          th,
          td {
            border: 1px solid var(--color-border, #e2e8f0);
            padding: 0.5rem 0.75rem;
            text-align: left;
          }

          th {
            background-color: rgba(0, 0, 0, 0.04);
          }
        }

        ::v-deep(img) {
          max-width: 100%;
          height: auto;
        }

        ::v-deep(a) {
          color: var(--color-primary);
        }

        ::v-deep(hr) {
          border: none;
          border-top: 1px solid var(--color-border, #e2e8f0);
          margin: 1.5rem 0;
        }
      }

      .markdown-raw {
        font-family: monospace;
        font-size: 0.9rem;
      }
    }
    
    .annotations-section {
      background-color: var(--color-card-bg);
      border-radius: var(--radius-lg);
      padding: 1.5rem;
      box-shadow: var(--shadow-sm);
      margin-bottom: 1rem;
      
      .section-title {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 1.25rem;
        margin-bottom: 1rem;
        
        .icon {
          color: var(--color-primary);
        }
      }
      
      .annotation-actions {
        display: flex;
        justify-content: flex-end;
        margin-top: 1rem;
        
        .icon {
          width: 16px;
          height: 16px;
        }
      }
    }
  }
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>