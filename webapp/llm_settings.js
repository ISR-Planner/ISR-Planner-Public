// Settings presentation/controller. Construction is inert; the application owns startup.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRLLMSettings = factory();
})(globalThis, function () {
  'use strict';

  function create({ document, storage, http, createModal, appendDebugLine }) {
    return {
      storage,
      http,
      providers: [],
      currentProvider: null,
      currentModel: null,

      // LocalStorage keys
      STORAGE_KEYS: {
        PROVIDER: "isr_llm_provider",
        MODEL: "isr_llm_model",
        OLLAMA_HOST: "isr_ollama_host",
      },

      // Initialize settings
      async init() {
        await this.loadProviders();
        this.setupEventListeners();
        this.loadSavedSettings();
      },

      // Fetch available providers from API
      async loadProviders() {
        try {
          const data = await this.http.json("/api/llm/providers");
          this.providers = data.providers || [];
          this.populateProviderDropdown();
          this.updateProviderStatus();
        } catch (error) {
          console.error("Failed to load LLM providers:", error);
          this.providers = [];
        }
      },

      // Populate provider dropdown
      populateProviderDropdown() {
        const select = document.getElementById("llm-provider");
        if (!select) return;

        select.innerHTML = this.providers
          .map(
            (p) =>
              `<option value="${p.id}">${p.name}${
            p.configured ? " (configured)" : ""
          }</option>`
          )
          .join("");

        // Trigger model update
        this.onProviderChange();
      },

      // Update model dropdown when provider changes
      onProviderChange() {
        const providerSelect = document.getElementById("llm-provider");
        const modelSelect = document.getElementById("llm-model");
        if (!providerSelect || !modelSelect) return;

        const providerId = providerSelect.value;
        const provider = this.providers.find((p) => p.id === providerId);

        const apiKeyGroup = document.getElementById("api-key-group");
        const hostGroup = document.getElementById("ollama-host-group");
        const refreshBtn = document.getElementById("btn-refresh-ollama");

        if (!provider) {
          modelSelect.innerHTML = '<option value="">Select a provider first</option>';
          return;
        }

        const isLocal = !provider.requires_key; // Ollama-style keyless provider

        // Toggle keyless (Ollama) vs key-based UI.
        if (apiKeyGroup) apiKeyGroup.style.display = isLocal ? "none" : "";
        if (hostGroup) hostGroup.style.display = isLocal ? "" : "none";
        if (refreshBtn) refreshBtn.style.display = isLocal ? "" : "none";

        if (isLocal) {
          // Prefill host from saved value or provider default.
          const hostInput = document.getElementById("ollama-host");
          if (hostInput) {
            hostInput.value =
              this.storage.read(this.STORAGE_KEYS.OLLAMA_HOST) ||
              provider.default_base_url ||
              "";
          }
          // Models are discovered from the running server.
          this.loadOllamaModels();
          this.clearConnectionStatus();
          return;
        }

        // Populate models (static list)
        modelSelect.innerHTML = provider.models
          .map((m) => `<option value="${m.id}" title="${m.description}">${m.name}</option>`)
          .join("");

        // Load saved model for this provider
        const savedModel = this.storage.read(
          this.STORAGE_KEYS.MODEL + "_" + providerId
        );
        if (savedModel && provider.models.some((m) => m.id === savedModel)) {
          modelSelect.value = savedModel;
        }

        this.clearConnectionStatus();
      },

      // Get the Ollama host from the input (falling back to saved/default).
      getOllamaHost() {
        const hostInput = document.getElementById("ollama-host");
        const provider = this.providers.find((p) => p.id === "ollama");
        return (
          (hostInput && hostInput.value.trim()) ||
          this.storage.read(this.STORAGE_KEYS.OLLAMA_HOST) ||
          (provider && provider.default_base_url) ||
          "http://localhost:11434"
        );
      },

      // Query the running Ollama server for installed models and fill the dropdown.
      async loadOllamaModels() {
        const modelSelect = document.getElementById("llm-model");
        if (!modelSelect) return;

        modelSelect.innerHTML = '<option value="">Loading models…</option>';
        const host = this.getOllamaHost();

        try {
          const data = await this.http.json(
            "/api/llm/ollama/models?base_url=" + encodeURIComponent(host)
          );

          if (!data.success || !data.models || data.models.length === 0) {
            modelSelect.innerHTML =
              '<option value="">No models found</option>';
            const statusEl = document.getElementById("connection-status");
            if (statusEl) {
              statusEl.textContent =
                data.message ||
                "No models installed. Try `ollama pull llama3.1`.";
              statusEl.className = "connection-status error";
            }
            return;
          }

          modelSelect.innerHTML = data.models
            .map(
              (m) =>
                `<option value="${m.id}" title="${m.description || ""}">${m.name}</option>`
            )
            .join("");

          // Restore saved model choice if still present.
          const savedModel = this.storage.read(
            this.STORAGE_KEYS.MODEL + "_ollama"
          );
          if (savedModel && data.models.some((m) => m.id === savedModel)) {
            modelSelect.value = savedModel;
          }
        } catch (error) {
          modelSelect.innerHTML =
            '<option value="">Could not reach Ollama</option>';
          const statusEl = document.getElementById("connection-status");
          if (statusEl) {
            statusEl.textContent =
              "Could not reach Ollama at " + host + ". Is it running?";
            statusEl.className = "connection-status error";
          }
        }
      },

      // Load saved settings
      loadSavedSettings() {
        // Remove credentials saved by older frontend versions. Hosted-provider keys
        // now live exclusively in server environment variables.
        this.storage.removeMatching("isr_llm_key_");

        const savedProvider = this.storage.read(this.STORAGE_KEYS.PROVIDER);
        const providerSelect = document.getElementById("llm-provider");

        if (savedProvider && providerSelect) {
          const providerExists = this.providers.some((p) => p.id === savedProvider);
          if (providerExists) {
            providerSelect.value = savedProvider;
            this.onProviderChange();
          }
        }
      },

      // Save current settings
      saveSettings() {
        const providerSelect = document.getElementById("llm-provider");
        const modelSelect = document.getElementById("llm-model");
        if (!providerSelect || !modelSelect) return;

        const providerId = providerSelect.value;
        const modelId = modelSelect.value;
        const preferences = [
          [this.STORAGE_KEYS.PROVIDER, providerId],
          [this.STORAGE_KEYS.MODEL + "_" + providerId, modelId],
        ];
        // Local (Ollama) providers persist a host instead of a key.
        if (providerId === "ollama") {
          preferences.push([this.STORAGE_KEYS.OLLAMA_HOST, this.getOllamaHost()]);
        }
        if (!this.storage.write(preferences)) {
          const statusEl = document.getElementById("connection-status");
          if (statusEl) {
            statusEl.textContent = "Settings could not be saved. Browser storage is unavailable.";
            statusEl.className = "connection-status error";
          }
          return;
        }

        this.currentProvider = providerId;
        this.currentModel = modelId;

        this.updateProviderStatus();
        this.closeModal();

        appendDebugLine(`AI Settings saved: ${providerId} / ${modelId}`);
      },

      // Test connection
      async testConnection() {
        const providerSelect = document.getElementById("llm-provider");
        const modelSelect = document.getElementById("llm-model");
        const statusEl = document.getElementById("connection-status");

        if (!providerSelect || !modelSelect || !statusEl) return;

        const providerId = providerSelect.value;
        const modelId = modelSelect.value;
        const provider = this.providers.find((p) => p.id === providerId);
        const isLocal = provider && !provider.requires_key;

        if (!isLocal && !provider?.configured) {
          statusEl.textContent = "Provider is not configured on the server";
          statusEl.className = "connection-status error";
          return;
        }
        if (isLocal && !modelId) {
          statusEl.textContent = "Select a model (Refresh models if empty)";
          statusEl.className = "connection-status error";
          return;
        }

        statusEl.textContent = "Testing...";
        statusEl.className = "connection-status testing";

        try {
          const data = await this.http.json("/api/llm/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              provider: providerId,
              model: modelId,
              base_url: isLocal ? this.getOllamaHost() : null,
            }),
          });

          if (data.success) {
            statusEl.textContent = data.message;
            statusEl.className = "connection-status success";
          } else {
            statusEl.textContent = data.message;
            statusEl.className = "connection-status error";
          }
        } catch (error) {
          statusEl.textContent = "Connection failed: " + error.message;
          statusEl.className = "connection-status error";
        }
      },

      // Clear connection status
      clearConnectionStatus() {
        const statusEl = document.getElementById("connection-status");
        if (statusEl) {
          statusEl.textContent = "";
          statusEl.className = "connection-status";
        }
      },

      // Update provider status display
      updateProviderStatus() {
        const container = document.getElementById("provider-status");
        if (!container) return;

        const providerItems = this.providers
          .map((p) => {
            const configured = !p.requires_key || p.configured;
            const statusClass = configured ? "configured" : "not-configured";
            const statusIcon = configured
              ? "&#10003; Server configured"
              : "&#x2717; Server credential missing";
            return `
          <div class="provider-item">
            <span class="provider-name">${p.name}</span>
            <span class="provider-status-icon ${statusClass}">${statusIcon}</span>
          </div>
        `;
          })
          .join("");

        container.innerHTML = `
      <h4>Provider Status</h4>
      <div class="provider-list">
        ${providerItems}
      </div>
    `;
      },

      modalControl: null,
      getModalControl() {
        if (!this.modalControl) {
          this.modalControl = createModal({
            getModal: () => document.getElementById("settings-modal"),
            getOpenButton: () => document.getElementById("btn-settings"),
            getCloseButton: () => document.getElementById("btn-close-settings"),
            onOpen: () => this.updateProviderStatus(),
          });
        }
        return this.modalControl;
      },

      // Compatibility entrypoints for existing settings callers.
      openModal() {
        this.getModalControl().open();
      },

      closeModal() {
        this.getModalControl().close();
      },

      formListeners: null,

      // Setup is repeatable; dispose before replacing settings form markup.
      setupEventListeners() {
        this.getModalControl().mount();
        if (this.formListeners !== null) return;
        this.formListeners = [];
        const listen = (id, type, handler) => {
          const element = document.getElementById(id);
          if (!element) return;
          element.addEventListener(type, handler);
          this.formListeners.push([element, type, handler]);
        };
        listen("llm-provider", "change", () => this.onProviderChange());
        listen("btn-test-connection", "click", () => this.testConnection());
        listen("btn-save-settings", "click", () => this.saveSettings());
        listen("btn-refresh-ollama", "click", (event) => {
          event.preventDefault();
          this.loadOllamaModels();
        });
        listen("ollama-host", "change", () => this.loadOllamaModels());
      },

      // Removes UI handlers only; pending requests retain their existing behavior.
      disposeEventListeners() {
        if (this.modalControl) this.modalControl.dispose();
        if (this.formListeners === null) return;
        this.formListeners.forEach(([element, type, handler]) => {
          element.removeEventListener(type, handler);
        });
        this.formListeners = null;
      },

      // Get current LLM settings (for API calls)
      getCurrentSettings() {
        const provider = this.storage.read(this.STORAGE_KEYS.PROVIDER);
        if (!provider) return null;

        const model = this.storage.read(this.STORAGE_KEYS.MODEL + "_" + provider);

        // Local keyless providers (Ollama): send provider/model/base_url, no key.
        if (provider === "ollama") {
          if (!model) return null;
          return {
            provider,
            model,
            base_url:
              this.storage.read(this.STORAGE_KEYS.OLLAMA_HOST) ||
              "http://localhost:11434",
          };
        }

        if (!model) return null;
        return { provider, model };
      },
    };
  }

  return { create };
});
