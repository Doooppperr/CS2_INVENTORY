(() => {
  "use strict";

  const STORAGE_KEY = "cs2-inventory-theme";
  const VALID_PREFERENCES = new Set(["system", "light", "dark"]);
  const mediaQuery = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : { matches: false };
  const root = document.documentElement;

  const normalize = (value) => VALID_PREFERENCES.has(value) ? value : "system";
  const resolve = (preference) => preference === "system"
    ? (mediaQuery.matches ? "dark" : "light")
    : preference;

  function apply(preference, persist = false) {
    const normalized = normalize(preference);
    const effective = resolve(normalized);
    root.dataset.themePreference = normalized;
    root.dataset.theme = effective;
    root.style.colorScheme = effective;

    document.querySelectorAll("[data-theme-selector]").forEach((selector) => {
      selector.value = normalized;
    });

    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, normalized);
      } catch (_) {
        // The selected theme still applies for the current page when storage is unavailable.
      }
    }
  }

  function initializeSelectors() {
    document.querySelectorAll("[data-theme-selector]").forEach((selector) => {
      selector.value = normalize(root.dataset.themePreference);
      selector.addEventListener("change", () => apply(selector.value, true));
    });
  }

  const handleSystemChange = () => {
    if (root.dataset.themePreference === "system") apply("system");
  };
  if (typeof mediaQuery.addEventListener === "function") {
    mediaQuery.addEventListener("change", handleSystemChange);
  } else if (typeof mediaQuery.addListener === "function") {
    mediaQuery.addListener(handleSystemChange);
  }

  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY) apply(event.newValue || "system");
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeSelectors, { once: true });
  } else {
    initializeSelectors();
  }

  window.cs2Theme = { apply, storageKey: STORAGE_KEY };
})();
