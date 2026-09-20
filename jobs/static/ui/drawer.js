(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JobDrawer = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  function createDrawerController({ shell, toggle, drawer, backdrop, isMobile, returnFocus, focusFirst = () => {} }) {
    const setInert = (value) => {
      drawer.inert = value;
      drawer.toggleAttribute("inert", value);
      drawer.setAttribute("aria-hidden", value ? "true" : "false");
    };
    const isOpen = () => shell.classList.contains("drawer-open");
    const setVisible = (visible) => {
      toggle.setAttribute("aria-expanded", visible ? "true" : "false");
      setInert(!visible);
    };
    const open = () => {
      if (!isMobile()) return;
      shell.classList.remove("shell-sidebar-hidden");
      shell.classList.add("drawer-open");
      backdrop.hidden = false;
      setVisible(true);
    };
    const close = (shouldReturnFocus = true) => {
      const wasOpen = isOpen();
      shell.classList.remove("drawer-open");
      backdrop.hidden = true;
      if (isMobile()) setVisible(false);
      if (wasOpen && shouldReturnFocus) returnFocus();
    };
    const toggleMenu = () => {
      if (isMobile()) isOpen() ? close() : open();
      else setVisible(!shell.classList.toggle("shell-sidebar-hidden"));
    };
    const syncViewport = () => {
      if (isMobile()) {
        shell.classList.remove("shell-sidebar-hidden");
        close(false);
      } else {
        shell.classList.remove("drawer-open");
        backdrop.hidden = true;
        setVisible(!shell.classList.contains("shell-sidebar-hidden"));
      }
    };
    syncViewport();
    toggle.addEventListener("click", () => { toggleMenu(); if (isOpen()) focusFirst(); });
    return { open, close, toggleMenu, syncViewport, isOpen };
  }
  return { createDrawerController };
});
