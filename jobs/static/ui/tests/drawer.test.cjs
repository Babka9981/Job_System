const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createDrawerController } = require("../drawer.js");

function element() {
  const classes = new Set(); const attributes = new Map(); const listeners = new Map();
  return {
    hidden: false, inert: false, focused: false,
    classList: { add: (...v) => v.forEach(x => classes.add(x)), remove: (...v) => v.forEach(x => classes.delete(x)), contains: v => classes.has(v), toggle: v => classes.has(v) ? (classes.delete(v), false) : (classes.add(v), true) },
    setAttribute: (name, value) => attributes.set(name, String(value)),
    getAttribute: name => attributes.get(name),
    toggleAttribute: (name, force) => force ? attributes.set(name, "") : attributes.delete(name),
    hasAttribute: name => attributes.has(name),
    addEventListener: (name, callback) => listeners.set(name, [...(listeners.get(name) || []), callback]),
    listenerCount: name => (listeners.get(name) || []).length,
    click: () => (listeners.get("click") || []).forEach(callback => callback({ type: "click" })),
    focus() { this.focused = true; },
    querySelectorAll: () => [],
  };
}
function fixture() {
  const shell=element(), toggle=element(), drawer=element(), backdrop=element(); let mobile=true, returned=false;
  const controller=createDrawerController({shell,toggle,drawer,backdrop,isMobile:()=>mobile,returnFocus:()=>{returned=true}});
  return {shell,toggle,drawer,backdrop,controller,setMobile:value=>{mobile=value},returned:()=>returned};
}

test("closed mobile drawer is removed from focus order and aria follows open state", () => {
  const f=fixture();
  assert.equal(f.drawer.inert, true); assert.equal(f.drawer.hasAttribute("inert"), true); assert.equal(f.drawer.getAttribute("aria-hidden"), "true"); assert.equal(f.toggle.getAttribute("aria-expanded"), "false");
  f.controller.open();
  assert.equal(f.drawer.inert, false); assert.equal(f.drawer.hasAttribute("inert"), false); assert.equal(f.drawer.getAttribute("aria-hidden"), "false"); assert.equal(f.toggle.getAttribute("aria-expanded"), "true");
  f.controller.close();
  assert.equal(f.drawer.inert, true); assert.equal(f.toggle.getAttribute("aria-expanded"), "false"); assert.equal(f.returned(), true);
});

test("desktop hide does not block drawer after resizing to mobile", () => {
  const f=fixture(); f.setMobile(false); f.controller.syncViewport(); f.controller.toggleMenu();
  assert.equal(f.shell.classList.contains("shell-sidebar-hidden"), true);
  f.setMobile(true); f.controller.syncViewport(); f.controller.open();
  assert.equal(f.shell.classList.contains("shell-sidebar-hidden"), false); assert.equal(f.controller.isOpen(), true); assert.equal(f.drawer.inert, false);
});

test("desktop click toggle synchronizes visibility, tab order, and aria-expanded", () => {
  const f=fixture(); f.setMobile(false); f.controller.syncViewport();
  assert.equal(f.shell.classList.contains("shell-sidebar-hidden"), false);
  assert.equal(f.drawer.inert, false); assert.equal(f.toggle.getAttribute("aria-expanded"), "true");
  f.toggle.click();
  assert.equal(f.shell.classList.contains("shell-sidebar-hidden"), true);
  assert.equal(f.drawer.inert, true); assert.equal(f.drawer.hasAttribute("inert"), true); assert.equal(f.toggle.getAttribute("aria-expanded"), "false");
  f.toggle.click();
  assert.equal(f.shell.classList.contains("shell-sidebar-hidden"), false);
  assert.equal(f.drawer.inert, false); assert.equal(f.drawer.hasAttribute("inert"), false); assert.equal(f.toggle.getAttribute("aria-expanded"), "true");
});

function bootstrapApplication(mobile) {
  const shell=element(), toggle=element(), drawer=element(), backdrop=element();
  const drawerMedia={ matches: mobile, addEventListener() {} };
  const documentElement=element();
  const document={
    documentElement,
    activeElement: null,
    querySelectorAll: () => [],
    querySelector: selector => ({
      "[data-shell]": shell,
      "[data-menu-toggle]": toggle,
      "[data-drawer]": drawer,
      "[data-drawer-backdrop]": backdrop,
    }[selector] || null),
  };
  const context={
    document,
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => drawerMedia,
    navigator: { onLine: true },
    addEventListener() {},
  };
  context.globalThis=context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "drawer.js"), "utf8"), context, { filename: "drawer.js" });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8"), context, { filename: "app.js" });
  return {shell,toggle,drawer};
}

test("application bootstrap registers one drawer transition per click on desktop and mobile", () => {
  const desktop=bootstrapApplication(false);
  assert.equal(desktop.toggle.listenerCount("click"), 1);
  desktop.toggle.click();
  assert.equal(desktop.shell.classList.contains("shell-sidebar-hidden"), true);
  assert.equal(desktop.drawer.inert, true);
  assert.equal(desktop.drawer.hasAttribute("inert"), true);
  assert.equal(desktop.toggle.getAttribute("aria-expanded"), "false");

  const mobile=bootstrapApplication(true);
  assert.equal(mobile.toggle.listenerCount("click"), 1);
  mobile.toggle.click();
  assert.equal(mobile.shell.classList.contains("drawer-open"), true);
  assert.equal(mobile.drawer.inert, false);
  assert.equal(mobile.drawer.hasAttribute("inert"), false);
  assert.equal(mobile.toggle.getAttribute("aria-expanded"), "true");
});
