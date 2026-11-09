<template>
  <Teleport to="body">
    <div v-if="visible" class="ketcher-modal-overlay">
      <div class="ketcher-modal">
        <div class="modal-header">
          <h3>Edit Structure</h3>
          <button class="close-btn" @click="$emit('close')">
            <span>&times;</span>
          </button>
        </div>
        <div class="modal-body">
          <iframe
            ref="ketcherFrame"
            :src="ketcherUrl"
            class="ketcher-iframe"
            @load="onKetcherLoad"
          ></iframe>
        </div>
        <div class="modal-footer">
          <button class="apply-btn" @click="applyEdits">Apply</button>
          <button class="cancel-btn" @click="$emit('close')">Cancel</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script>
export default {
  name: 'KetcherEditorModal',
  props: {
    visible: { type: Boolean, default: false },
    initialSmiles: { type: String, default: '' },
    initialMolfile: { type: String, default: '' },
    ketcherPath: { type: String, default: '/ketcher/index.html' }
  },
  emits: ['close', 'apply', 'error'],
  data() {
    return {
      ketcherReady: false,
      initCheckInterval: null
    }
  },
  computed: {
    ketcherUrl() {
      // Use a cache-busting param to ensure fresh load
      return this.ketcherPath + '?v=1.0';
    }
  },
  watch: {
    visible(val) {
      if (val) {
        this.ketcherReady = false;
        // Listen for Ketcher's init message before the iframe starts loading
        window.addEventListener('message', this.onKetcherInit);
      } else {
        this.cleanup();
      }
    }
  },
  beforeUnmount() {
    this.cleanup();
  },
  methods: {
    cleanup() {
      window.removeEventListener('message', this.onKetcherInit);
      if (this.initCheckInterval) {
        clearInterval(this.initCheckInterval);
        this.initCheckInterval = null;
      }
    },

    getKetcherApi() {
      const frame = this.$refs.ketcherFrame;
      if (!frame || !frame.contentWindow) return null;
      return frame.contentWindow.ketcher || null;
    },

    onKetcherInit(event) {
      // Ketcher posts { eventType: 'init' } to the parent once its API is ready
      const frame = this.$refs.ketcherFrame;
      if (!frame || event.source !== frame.contentWindow) return;
      if (!event.data || event.data.eventType !== 'init') return;

      console.log('Ketcher init message received');
      this.markReady();
    },

    markReady() {
      if (this.ketcherReady) return;
      this.ketcherReady = true;
      window.removeEventListener('message', this.onKetcherInit);
      if (this.initCheckInterval) {
        clearInterval(this.initCheckInterval);
        this.initCheckInterval = null;
      }
      this.setStructure();
    },

    onKetcherLoad() {
      console.log('Ketcher iframe loaded, initial SMILES:', this.initialSmiles ? this.initialSmiles.substring(0, 30) + '...' : 'none');
      console.log('Initial molfile present:', !!this.initialMolfile);

      // Fallback: poll for the ketcher API in case the init message was missed
      let attempts = 0;
      this.initCheckInterval = setInterval(() => {
        if (this.ketcherReady) {
          clearInterval(this.initCheckInterval);
          this.initCheckInterval = null;
          return;
        }
        attempts++;
        if (this.getKetcherApi()) {
          console.log('Ketcher API detected via polling');
          this.markReady();
        } else if (attempts >= 50) { // ~10 seconds
          clearInterval(this.initCheckInterval);
          this.initCheckInterval = null;
          console.error('Ketcher API did not become available');
          this.$emit('error', 'Ketcher editor failed to initialize');
        }
      }, 200);
    },

    setStructure() {
      const ketcher = this.getKetcherApi();
      if (!ketcher) {
        console.error('Ketcher API not available');
        return;
      }

      // Prefer the molfile (keeps the original coordinates), fall back to SMILES
      const structure = this.initialMolfile || this.initialSmiles;
      if (!structure) {
        console.log('No initial structure to load into Ketcher');
        return;
      }

      console.log('Setting structure in Ketcher via', this.initialMolfile ? 'molfile' : 'SMILES');
      Promise.resolve(ketcher.setMolecule(structure)).catch(error => {
        console.error('Ketcher failed to load the structure:', error);
        // If the molfile failed but we also have SMILES, retry with SMILES
        if (this.initialMolfile && this.initialSmiles) {
          console.log('Retrying with SMILES');
          Promise.resolve(ketcher.setMolecule(this.initialSmiles)).catch(e => {
            console.error('Ketcher also failed to load SMILES:', e);
            this.$emit('error', 'Failed to load structure into the editor');
          });
        } else {
          this.$emit('error', 'Failed to load structure into the editor');
        }
      });
    },

    async applyEdits() {
      const ketcher = this.getKetcherApi();
      if (!ketcher) {
        console.error('Ketcher API not available');
        this.$emit('error', 'Ketcher editor is not ready');
        return;
      }

      try {
        const [smiles, molfile] = await Promise.all([
          ketcher.getSmiles(),
          ketcher.getMolfile()
        ]);
        console.log('Got structure from Ketcher API:', smiles ? smiles.substring(0, 30) + '...' : 'none');
        this.$emit('apply', { smiles, molfile });
      } catch (error) {
        console.error('Error getting structure from Ketcher API:', error);
        this.$emit('error', 'Failed to get structure from the editor');
      }
    }
  }
}
</script>

<style scoped>
.ketcher-modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.35);
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.ketcher-modal {
  background: #fff;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.5rem 0.5rem 1.5rem;
  border-bottom: 1px solid #eee;
}
.close-btn {
  background: none;
  border: none;
  font-size: 2rem;
  color: #888;
  cursor: pointer;
}
.modal-body {
  flex: 1;
  padding: 0;
  overflow: hidden;
  min-height: 0;
}
.ketcher-iframe {
  display: block;
  width: 100%;
  height: 100%;
  border: none;
  background: #f8fafc;
}
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid #eee;
}
.apply-btn {
  background: #4f46e5;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1.25rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
.apply-btn:hover {
  background: #4338ca;
}
.cancel-btn {
  background: #f3f4f6;
  color: #374151;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1.25rem;
  font-weight: 500;
  cursor: pointer;
}
</style>
