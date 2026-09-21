(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.JobDraftCopy = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function createCopyController(options) {
    const documentRef = options.document;
    const clipboard = options.clipboard;
    async function handleClick(event) {
      const button = event.target.closest("[data-copy-target]");
      if (!button) return;
      const field = documentRef.getElementById(button.dataset.copyTarget);
      if (!field) return;
      try {
        if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("clipboard unavailable");
        await clipboard.writeText(field.value);
        button.textContent = "Скопировано";
      } catch (_) {
        field.focus();
        field.select();
        button.textContent = "Выделено — нажмите Ctrl+C";
      }
    }
    return {
      handleClick,
      start() { documentRef.addEventListener("click", handleClick); },
    };
  }
  return { createCopyController };
});
