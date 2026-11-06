/* Runs synchronously after extension registration and before the window load event. */
window.PageCapabilities.initialize(JSON.parse(document.getElementById('page-capabilities').textContent));
