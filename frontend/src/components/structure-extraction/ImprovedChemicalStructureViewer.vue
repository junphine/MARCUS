<template>
  <div class="chemical-structure-viewer">
    <div v-if="!structures.length" class="empty-state">
      <p>No structure data available</p>
    </div>

    <div v-else class="structure-content">
      <div v-if="isLoading" class="loading-bar">
        <vue-feather type="loader" class="loading-icon spin"></vue-feather>
        <p>Generating depiction...</p>
      </div>

      <div class="structures-list">
        <div v-for="(structure, index) in structures" :key="index" class="structure-item">
          <div class="structure-display">
            <!-- SVG depiction container -->
            <div v-if="depictions[index] && depictionFormat === 'svg'" class="svg-container"
              v-html="sanitizeCdkSvg(depictions[index])">
            </div>

            <!-- Base64 image container -->
            <img v-else-if="depictions[index] && depictionFormat === 'base64'"
              :src="'data:image/png;base64,' + depictions[index]" class="structure-image"
              alt="Chemical structure" />

            <!-- Fallback container -->
            <div v-else class="fallback-container">
              <p class="smiles-fallback">{{ structure.smiles }}</p>
            </div>
          </div>

          <div v-if="structure.smiles" class="structure-smiles">
            <span class="smiles-label">SMILES:</span>
            <span class="smiles-value">{{ structure.smiles }}</span>
          </div>

          <div class="structure-item-footer">
            <span v-if="structures.length > 1" class="structure-index">{{ index + 1 }}</span>
            <div class="structure-actions">
              <button v-if="structure.smiles" class="btn-action" @click="copySmiles(index)"
                title="Copy SMILES">
                <vue-feather type="copy" size="14"></vue-feather>
              </button>
              <button v-if="editable" class="btn-action edit-btn" @click="openEditor(index)"
                title="Edit Structure">
                <vue-feather type="edit-3" size="14"></vue-feather>
                <span>Edit Structure</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <KetcherEditorModal :visible="showEditor" :initial-smiles="editorSmiles"
        :initial-molfile="editorMolfile" @close="closeEditor" @apply="onEditorApply"
        @error="onEditorError" />
    </div>
  </div>
</template>

<script>
import depictionService from '@/services/depictionService'
import KetcherEditorModal from './KetcherEditorModal.vue'
import { toList } from '@/utils/structure'

export default {
  name: 'ImprovedChemicalStructureViewer',
  components: {
    KetcherEditorModal
  },
  props: {
    smiles: {
      type: [String, Array],
      default: ''
    },
    molfile: {
      type: [String, Array],
      default: ''
    },
    name: {
      type: String,
      default: ''
    },
    // Original source engine (e.g., 'molecode', 'molparser')
    sourceEngine: {
      type: String,
      default: ''
    },
    // Whether to use coordinates from the molfile for depiction
    useCoordinates: {
      type: Boolean,
      default: false
    },
    // SMARTS pattern to highlight in the structure
    highlight: {
      type: String,
      default: ''
    },
    // Whether to show the "Edit Structure" action
    editable: {
      type: Boolean,
      default: false
    }
  },
  emits: ['copied', 'error', 'download-complete', 'depiction-complete', 'structure-edited'],
  data() {
    return {
      isLoading: true,
      depictions: [],
      depictionFormat: 'svg',
      depictionError: null,
      usingMolfile: false,
      initialized: false,
      showEditor: false,
      editingIndex: null
    }
  },
  computed: {
    smilesList() {
      return toList(this.smiles)
    },
    molfileList() {
      return toList(this.molfile)
    },
    // Pair smiles and molfile by index so each structure is rendered separately.
    structures() {
      const smiles = this.smilesList
      const molfiles = this.molfileList
      const count = Math.max(smiles.length, molfiles.length)
      const items = []
      for (let i = 0; i < count; i++) {
        items.push({
          smiles: smiles[i] || '',
          molfile: molfiles[i] || ''
        })
      }
      return items
    },
    shouldUseMolfile() {
      // Use molfile if available AND either:
      // 1. useCoordinates prop is true (explicit request to use coordinates)
      // 2. The source is molecode (its molfile is a mermaid graph rendered by merman)
      // 3. The source is molparser AND there's no explicit request not to use coordinates
      return this.molfileList.length > 0 &&
        (this.useCoordinates === true ||
          this.sourceEngine === 'molecode' ||
          (this.sourceEngine === 'molparser' &&
            this.useCoordinates !== false))
    },
    editorSmiles() {
      if (this.editingIndex == null) return ''
      return (this.structures[this.editingIndex] || {}).smiles || ''
    },
    editorMolfile() {
      if (this.editingIndex == null) return ''
      // MoleCode's "molfile" is actually a mermaid graph, not a real molfile,
      // so Ketcher cannot parse it - only pass real molfiles to the editor.
      if (this.sourceEngine === 'molecode') return ''
      return (this.structures[this.editingIndex] || {}).molfile || ''
    }
  },
  watch: {
    smiles() {
      this.generateDepictions()
    },
    molfile() {
      this.generateDepictions()
    },
    useCoordinates() {
      this.generateDepictions()
    },
    highlight() {
      this.generateDepictions()
    }
  },
  mounted() {
    this.$nextTick(() => {
      this.generateDepictions();
    });
  },
  methods: {
    async generateDepictions() {
      const structures = this.structures

      // Sequence token to guard against stale async results overwriting newer
      // ones when the component is reused across segments (e.g. in a v-for).
      const requestId = (this._depictionSeq = (this._depictionSeq || 0) + 1)

      if (!structures.length) {
        this.depictions = []
        this.isLoading = false
        return
      }

      this.isLoading = true
      this.depictionError = null
      // Clear any previously-rendered depictions immediately so stale images
      // from another structure are never shown while the new ones load.
      this.depictions = []

      const isMolecode = this.sourceEngine === 'molecode'

      try {
        if (!this.initialized) {
          console.log(`[Depiction] Starting depiction for ${this.name || 'unknown structure'} with engine ${this.sourceEngine}`);
          console.log(`[Depiction] Rendering ${structures.length} structure(s)`);
        }

        const results = await Promise.all(
          structures.map((structure) => this.generateOne(structure, isMolecode))
        )

        if (requestId !== this._depictionSeq) return

        this.depictions = results
        this.depictionFormat = 'svg'
        this.initialized = true

        console.log(`[Depiction] Successfully generated ${results.length} depiction(s) for ${this.name || 'structure'}`);
      } catch (error) {
        if (requestId !== this._depictionSeq) return

        console.error(`[Depiction] Error generating depiction for ${this.sourceEngine}:`, error)
        this.depictionError = error.message || 'Failed to generate depiction'
        this.depictions = []

        this.$emit('error', this.depictionError)
      } finally {
        if (requestId === this._depictionSeq) {
            this.isLoading = false;
            this.$emit('depiction-complete', {
              engine: this.sourceEngine,
              success: !this.depictionError,
              error: this.depictionError
            });
        }
      }
    },
    async generateOne(structure, isMolecode) {
      const options = {
        engine: isMolecode ? 'mermaid' : 'cdk',
        width: 400,
        height: 300,
        format: 'svg',
        kekulize: true,
        cip: true,
        unicolor: false
      }

      if (structure.smiles) {
        options.smiles = structure.smiles
      }

      // mermaid renders the MoleCode graph directly; CDK uses molfile
      // coordinates when use_molfile_directly is set.
      if (structure.molfile) {
        options.molfile = structure.molfile
        options.use_molfile_directly = this.shouldUseMolfile
      }

      if (this.highlight) {
        options.highlight = this.highlight
      }

      return await depictionService.generateDepiction(options)
    },
    openEditor(index) {
      this.editingIndex = index
      this.showEditor = true
    },
    closeEditor() {
      this.showEditor = false
      this.editingIndex = null
    },
    onEditorApply(payload) {
      if (!payload || !payload.smiles) {
        this.$emit('error', 'No structure returned from editor')
        this.closeEditor()
        return
      }

      const index = this.editingIndex != null ? this.editingIndex : 0

      // Build the updated lists, replacing the edited structure in place.
      const newSmiles = [...this.smilesList]
      const newMolfiles = [...this.molfileList]
      newSmiles[index] = payload.smiles
      if (index < newMolfiles.length) {
        newMolfiles[index] = payload.molfile || newMolfiles[index]
      } else if (payload.molfile) {
        newMolfiles[index] = payload.molfile
      }

      this.$emit('structure-edited', {
        index,
        smiles: newSmiles,
        molfile: newMolfiles
      })

      this.closeEditor()
    },
    onEditorError(message) {
      this.$emit('error', message)
    },
    sanitizeCdkSvg(svgString) {
      if (!svgString) return '';

      try {
        let cleanedSvg = svgString.replace(/<ns0:/g, '<')
          .replace(/<\/ns0:/g, '</')
          .replace(/<\/ns0:SVG>/g, '</svg>');

        cleanedSvg = cleanedSvg.replace(/<svg/i, '<svg xmlns="http://www.w3.org/2000/svg"');
        cleanedSvg = cleanedSvg.replace(/xmlns:ns0=".*?"/g, '');

        if (!cleanedSvg.includes('viewBox') && cleanedSvg.includes('width') && cleanedSvg.includes('height')) {
          const widthMatch = cleanedSvg.match(/width="(\d+\.?\d*)px?"/);
          const heightMatch = cleanedSvg.match(/height="(\d+\.?\d*)px?"/);

          if (widthMatch && heightMatch) {
            const width = widthMatch[1];
            const height = heightMatch[1];
            cleanedSvg = cleanedSvg.replace(/<svg/, `<svg viewBox="0 0 ${width} ${height}"`);
          }
        }

        return cleanedSvg;
      } catch (error) {
        console.error('Error sanitizing CDK SVG:', error);
        return svgString;
      }
    },
    copySmiles(index = 0) {
      const structure = this.structures[index]
      if (!structure || !structure.smiles) return

      navigator.clipboard.writeText(structure.smiles)
        .then(() => {
          this.$emit('copied', 'SMILES copied to clipboard')
        })
        .catch(err => {
          console.error('Could not copy SMILES:', err)
          this.$emit('error', 'Failed to copy SMILES')
        })
    },
    copyMolfile(index = 0) {
      const structure = this.structures[index]
      if (!structure || !structure.molfile) return

      navigator.clipboard.writeText(structure.molfile)
        .then(() => {
          this.$emit('copied', 'Molfile copied to clipboard')
        })
        .catch(err => {
          console.error('Could not copy Molfile:', err)
          this.$emit('error', 'Failed to copy Molfile')
        })
    },
    downloadImage(index = 0) {
      if (!this.depictions[index]) return

      try {
        let dataUrl = ''

        if (this.depictionFormat === 'svg') {
          const svgElement = document.createElement('div')
          svgElement.innerHTML = this.sanitizeCdkSvg(this.depictions[index])
          const svg = svgElement.firstChild

          if (svg) {
            svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
            dataUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(svg))
          } else {
            throw new Error('Invalid SVG data')
          }
        } else if (this.depictionFormat === 'base64') {
          dataUrl = 'data:image/png;base64,' + this.depictions[index]
        } else {
          throw new Error('Unsupported depiction format')
        }

        const link = document.createElement('a')
        link.download = `${this.name || 'structure'}-${index + 1}.${this.depictionFormat === 'svg' ? 'svg' : 'png'}`
        link.href = dataUrl

        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)

        this.$emit('download-complete', 'Structure image downloaded')
      } catch (error) {
        console.error('Error downloading image:', error)
        this.$emit('error', 'Failed to download structure image')
      }
    }
  }
}
</script>

<style lang="scss" scoped>
.chemical-structure-viewer {
  width: 100%;

  .empty-state {
    text-align: center;
    padding: 1rem;
    color: var(--color-text-light);
    font-size: 0.875rem;
  }

  .structure-content {
    display: flex;
    flex-direction: column;

    .loading-bar {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      justify-content: center;
      padding: 0.5rem;
      color: var(--color-text-light);
      font-size: 0.875rem;

      .loading-icon {
        color: var(--color-primary);
      }

      .spin {
        animation: spin 1s linear infinite;
      }
    }

    .structures-list {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }

    .structure-item {
      display: flex;
      flex-direction: column;

      .structure-display {
        position: relative;
        background-color: var(--color-input-bg);
        border-radius: var(--radius-md);
        padding: 0.75rem;
        min-height: 160px;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 1px solid var(--color-border);
        overflow: hidden;

        .svg-container {
          width: 100%;
          height: 100%;
          display: flex;
          align-items: center;
          justify-content: center;

          :deep(svg) {
            max-width: 100%;
            max-height: 260px;
          }
        }

        .structure-image {
          max-width: 100%;
          max-height: 260px;
        }

        .fallback-container {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 100%;

          .smiles-fallback {
            font-family: monospace;
            font-size: 0.875rem;
            color: var(--color-text-light);
            text-align: center;
            max-width: 90%;
            word-break: break-all;
          }
        }
      }

      .structure-smiles {
        display: flex;
        align-items: baseline;
        gap: 0.5rem;
        padding: 0.5rem 0.25rem 0;
        font-size: 0.8125rem;

        .smiles-label {
          flex-shrink: 0;
          font-weight: 600;
          color: var(--color-text);
        }

        .smiles-value {
          font-family: monospace;
          color: var(--color-text-light);
          word-break: break-all;
          overflow-wrap: anywhere;
        }
      }

      .structure-item-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
        padding: 0.25rem 0.25rem 0;

        .structure-index {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 1.25rem;
          height: 1.25rem;
          padding: 0 0.375rem;
          border-radius: 999px;
          background-color: rgba(74, 77, 231, 0.1);
          color: var(--color-primary);
          font-size: 0.75rem;
          font-weight: 600;
        }

        .structure-actions {
          display: flex;
          align-items: center;
          gap: 0.5rem;

          .btn-action {
            background: none;
            border: none;
            color: var(--color-text-light);
            cursor: pointer;
            padding: 0.25rem;
            border-radius: 4px;
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            font-size: 0.8125rem;
            transition: all 0.2s ease;

            &:hover {
              color: var(--color-primary);
              background-color: rgba(74, 77, 231, 0.1);
            }

            &.edit-btn {
              border: 1px solid var(--color-border);
              padding: 0.25rem 0.5rem;
            }
          }
        }
      }
    }
  }
}

@keyframes spin {
  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

/* Deep selectors for SVG content */
:deep(svg) {
  background-color: white;
}
</style>
