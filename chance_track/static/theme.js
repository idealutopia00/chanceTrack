(() => {
  const root = document.documentElement;
  const system = matchMedia("(prefers-color-scheme: dark)");
  const key = "chanceTrackTheme";
  let preference = "system";
  function readPreference() {
    try {
      const saved = localStorage.getItem(key);
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return preference;
    }
  }
  function applyTheme() {
    const theme = preference === "system"
      ? (system.matches ? "dark" : "light")
      : preference;
    if (root.dataset.theme === theme) return;
    root.dataset.theme = theme;
    window.dispatchEvent(new Event("chancetrack:theme"));
  }

  preference = readPreference();
  applyTheme();
  system.addEventListener("change", applyTheme);
  document.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("workspace-theme");
    select.value = preference;
    select.hidden = false;
    select.addEventListener("change", () => {
      preference = select.value;
      try {
        localStorage.setItem(key, preference);
      } catch {}
      applyTheme();
    });
    window.addEventListener("pageshow", (event) => {
      if (!event.persisted) return;
      preference = readPreference();
      select.value = preference;
      applyTheme();
    });
  });
})();
