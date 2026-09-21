const test = require("node:test");
const assert = require("node:assert/strict");
const { createCopyController } = require("../draft-copy.js");

function fixture(writeText) {
  const field = { value: "Only the draft body", focused: false, selected: false, focus() { this.focused = true; }, select() { this.selected = true; } };
  const button = { dataset: { copyTarget: "draft" }, textContent: "" };
  const document = { getElementById: id => id === "draft" ? field : null, addEventListener() {} };
  const controller = createCopyController({ document, clipboard: { writeText } });
  return { controller, field, button, event: { target: { closest: () => button } } };
}

test("copy sends only editor text and confirms success", async () => {
  const copied = [];
  const item = fixture(async value => copied.push(value));
  await item.controller.handleClick(item.event);
  assert.deepEqual(copied, ["Only the draft body"]);
  assert.equal(item.button.textContent, "Скопировано");
});

test("copy failure selects the editor for keyboard fallback", async () => {
  const item = fixture(async () => { throw new Error("denied"); });
  await item.controller.handleClick(item.event);
  assert.equal(item.field.focused, true);
  assert.equal(item.field.selected, true);
  assert.equal(item.button.textContent.includes("Ctrl+C"), true);
});
