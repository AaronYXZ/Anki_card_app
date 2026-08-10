if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js");
  });
}

document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName))
  ) {
    return;
  }

  const shortcut = event.code === "Space" ? "Space" : event.key;
  if (!["Space", "1", "2", "3", "4"].includes(shortcut)) {
    return;
  }
  const action = document.querySelector(`[data-shortcut="${shortcut}"]`);
  if (action instanceof HTMLButtonElement && !action.disabled) {
    event.preventDefault();
    action.click();
  }
});
