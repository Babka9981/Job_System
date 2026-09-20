(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JobForms = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  function bindDisableOnSubmit(root) {
    root.querySelectorAll("form[data-disable-on-submit]").forEach((form) => {
      form.addEventListener("submit", () => {
        if (!form.checkValidity()) return;
        form.querySelectorAll('button[type="submit"]').forEach((button) => {
          button.disabled = true;
          button.setAttribute("aria-disabled", "true");
        });
      });
    });
  }
  return { bindDisableOnSubmit };
});
