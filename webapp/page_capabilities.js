/* Optional page extensions register without side effects; startup selects them. */
(function (root) {
  'use strict';
  const names = ['weatherPreview', 'weatherDiagnostics'];
  const initializers = new Map();
  let initialized = false;
  let capabilities;
  root.PageCapabilities = Object.freeze({
    register(name, initialize) {
      if (initialized) throw new Error('Page capabilities are already initialized.');
      if (!names.includes(name) || typeof initialize !== 'function' || initializers.has(name)) {
        throw new Error(`Invalid or duplicate page extension: ${name}`);
      }
      initializers.set(name, initialize);
    },
    initialize(options) {
      if (initialized) throw new Error('Page capabilities are already initialized.');
      if (!options || Object.keys(options).sort().join(',') !== [...names].sort().join(',') ||
          names.some(name => typeof options[name] !== 'boolean') ||
          (options.weatherDiagnostics && !options.weatherPreview)) {
        throw new Error('Invalid page capabilities.');
      }
      // Validate all selected extensions before any extension installs listeners/hooks.
      for (const name of names) {
        if (options[name] && !initializers.has(name)) throw new Error(`Missing page extension: ${name}`);
      }
      if (options.weatherPreview && typeof root.weatherCheckpoints?.configure !== 'function') {
        throw new Error('Missing weather checkpoint controller.');
      }
      capabilities = Object.freeze({ ...options });
      initialized = true;
      for (const name of names) if (capabilities[name]) initializers.get(name)();
    },
    get capabilities() { return capabilities; },
  });
})(window);
