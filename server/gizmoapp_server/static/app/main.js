import { SceneRenderer } from "./scene.js";


function bootstrap() {
  const runtime = window.GizmoAppRuntime;
  if (!runtime) {
    throw new Error("The shared app runtime did not load.");
  }
  runtime.readConfig();
  const canvas = document.getElementById("scene-canvas");
  if (!canvas) {
    throw new Error("The graphical canvas is missing.");
  }
  const renderer = new SceneRenderer(canvas);
  renderer.setNodes([]);
  runtime.markReady();
}


try {
  bootstrap();
} catch (error) {
  window.GizmoAppRuntime?.showFatalError(error);
}
