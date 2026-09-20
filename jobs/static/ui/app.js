(() => {
  const root = document.documentElement;
  const storedTheme = () => { try { return localStorage.getItem("theme"); } catch (_) { return null; } };
  const effectiveTheme = () => storedTheme() || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const applyTheme = (theme) => { root.dataset.theme = theme; };
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => button.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", next); } catch (_) {}
    applyTheme(next);
  }));
  addEventListener("storage", (event) => { if (event.key === "theme") event.newValue ? applyTheme(event.newValue) : root.removeAttribute("data-theme"); });
  const shell = document.querySelector("[data-shell]"); const toggle = document.querySelector("[data-menu-toggle]"); const drawer = document.querySelector("[data-drawer]"); const backdrop = document.querySelector("[data-drawer-backdrop]");
  const drawerMedia = matchMedia("(max-width: 62rem)"); const mobile = () => drawerMedia.matches;
  const focusable = () => drawer ? [...drawer.querySelectorAll('a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])')] : [];
  const drawerController = shell ? JobDrawer.createDrawerController({shell,toggle,drawer,backdrop,isMobile:mobile,returnFocus:()=>toggle.focus(),focusFirst:()=>focusable()[0]?.focus()}) : null;
  backdrop?.addEventListener("click", () => drawerController.close()); drawer?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => { if (mobile()) drawerController.close(); }));
  drawerMedia.addEventListener("change", () => drawerController?.syncViewport());
  addEventListener("keydown", (event) => { if (event.key === "Escape" && drawerController?.isOpen()) drawerController.close(); if (event.key === "Tab" && drawerController?.isOpen()) { const items=focusable(); if(!items.length)return; const first=items[0],last=items.at(-1); if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()} } });
  const connection=document.querySelector("[data-connection]"); const updateConnection=()=>{if(connection){connection.textContent=navigator.onLine?"Онлайн":"Нет сети";connection.classList.toggle("tone-warning",!navigator.onLine)}}; addEventListener("online",updateConnection);addEventListener("offline",updateConnection);updateConnection();
  document.querySelectorAll("[data-submit-lock]").forEach((form)=>form.addEventListener("submit",()=>{const button=form.querySelector("[data-submit-button]");if(button){button.disabled=true;button.textContent="Входим…"}}));
})();
