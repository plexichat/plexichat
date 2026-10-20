// Plexichat client runtime configuration.
// Injected at /config.js by the nginx frontend. The published image ships the
// compiled app and only this file is runtime-configurable, so the API endpoint
// can point at a different host/proxy without rebuilding the client.
window.PLEXICHAT_CONFIG = {
  serverUrl: "http://localhost:8000",
  hideServerField: false,
  defaultTheme: "ocean",
  version: "a.1.0-121"
};