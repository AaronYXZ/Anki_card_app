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

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("[data-unlike-form]")) {
    return;
  }

  event.preventDefault();
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = true;
  }

  try {
    const response = await fetch(form.action, {
      method: form.method,
      body: new FormData(form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (response.status !== 204) {
      throw new Error(`Unlike failed with status ${response.status}`);
    }

    form.closest("article")?.remove();
    const favoritesList = document.querySelector("[data-favorites-list]");
    if (favoritesList instanceof HTMLElement && !favoritesList.querySelector("article")) {
      favoritesList.outerHTML =
        '<div class="empty-state"><h2>No favorite cards</h2><p>Tap the heart on a revealed review card to save it here.</p><a class="button" href="/review">Open review</a></div>';
    }
  } catch (_error) {
    form.submit();
  }
});
