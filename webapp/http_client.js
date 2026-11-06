(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRHttp = factory();
})(globalThis, function () {
  'use strict';

  // Callers own request options and application-level status/error handling.
  // Decode error-status bodies too, matching the settings UI's fetch contract.
  function create(fetchRequest) {
    return {
      async json(url, options) {
        const response = await fetchRequest(url, options);
        return response.json();
      },
    };
  }

  return { create };
});
