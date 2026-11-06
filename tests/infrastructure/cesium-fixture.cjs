// Neutral engine double for lifecycle tests, not a renderer or domain simulation.
function createCesiumFixture() {
  const viewers = [];
  const events = [];
  class Viewer {
    constructor(containerId) {
      this.containerId = containerId;
      this.destroyed = false;
      this.id = viewers.length;
      this.entities = {
        values: [],
        add: entity => { this.entities.values.push(entity); return entity; },
        remove: entity => {
          events.push(`remove:${this.id}`);
          const index = this.entities.values.indexOf(entity);
          if (index >= 0) this.entities.values.splice(index, 1);
        },
        removeAll: () => { events.push(`clear:${this.id}`); this.entities.values.length = 0; },
      };
      this.imageryLayers = { removeAll() {}, addImageryProvider() {} };
      viewers.push(this);
    }
    zoomTo() { events.push(`zoom:${this.id}`); }
    destroy() { this.destroyed = true; this.entities.removeAll(); }
    isDestroyed() { events.push(`guard:${this.id}`); return this.destroyed; }
  }
  return {
    viewers, events,
    engine: {
      Viewer,
      TileMapServiceImageryProvider: class {},
      UrlTemplateImageryProvider: class {},
      buildModuleUrl: value => value,
      HeadingPitchRange: class {},
      Math: { toRadians: value => value * Math.PI / 180 },
    },
  };
}
module.exports = { createCesiumFixture };
