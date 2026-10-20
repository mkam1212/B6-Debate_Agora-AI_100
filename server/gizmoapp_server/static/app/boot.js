(function installGizmoAppRuntime() {
  function errorMessage(error) {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return String(error || "Unknown startup error");
  }

  function isBenignResizeObserverNotification(error) {
    const message = errorMessage(error);
    return (
      message === "ResizeObserver loop limit exceeded"
      || message === "ResizeObserver loop completed with undelivered notifications."
    );
  }

  function showFatalError(error) {
    const panel = document.getElementById("app-error");
    if (!panel) {
      console.error("GizmoApp failed to start", error);
      return;
    }
    panel.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = "This app could not start.";
    const detail = document.createElement("span");
    detail.textContent = `${errorMessage(error)} Refresh the preview, or inspect the app logs if the problem continues.`;
    panel.append(title, detail);
    panel.hidden = false;
    document.documentElement.dataset.appState = "error";
    console.error("GizmoApp failed to start", error);
  }

  function readConfig() {
    const raw = document.getElementById("gizmoapp-config");
    if (!raw) {
      throw new Error("The page is missing its runtime configuration.");
    }
    const config = JSON.parse(raw.textContent || "{}");
    if (!config.apiBase || !Number.isFinite(config.requestTimeoutMs)) {
      throw new Error("The page runtime configuration is incomplete.");
    }
    return config;
  }

  function markReady() {
    document.documentElement.dataset.appState = "ready";
  }

  window.GizmoAppRuntime = { markReady, readConfig, showFatalError };
  window.addEventListener("error", (event) => {
    const error = event.error || event.message;
    if (isBenignResizeObserverNotification(error)) {
      event.preventDefault();
      console.debug("GizmoApp ignored a benign browser resize notification.");
      return;
    }
    showFatalError(error);
  });
  window.addEventListener("unhandledrejection", (event) => showFatalError(event.reason));
})();
