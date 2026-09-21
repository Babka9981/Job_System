const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const { createIsolation, reservePort, validateProtectedEvidence } = require("./browser-acceptance.cjs");

test("protected evidence rejects a successful login redirect", () => {
  assert.throws(() => validateProtectedEvidence({
    route: "/profile/", status: 200,
    expectedURL: "http://127.0.0.1:1234/profile/",
    finalURL: "http://127.0.0.1:1234/accounts/login/?next=/profile/",
    hasShell: false, hasMain: true, username: "", hasLoginForm: true,
    heading: "Вход", expectedHeading: "Мой профиль",
  }), /redirected to/);
});

test("parallel harness isolation uses distinct workspaces databases and reserved ports", async () => {
  const first = createIsolation();
  const second = createIsolation();
  const [firstPort, secondPort] = await Promise.all([reservePort(), reservePort()]);
  try {
    assert.notEqual(first.work, second.work);
    assert.notEqual(first.env.JOB_DATABASE_PATH, second.env.JOB_DATABASE_PATH);
    assert.notEqual(first.env.JOB_ACCEPTANCE_META, second.env.JOB_ACCEPTANCE_META);
    assert.notEqual(firstPort.port, secondPort.port);
  } finally {
    await Promise.all([firstPort.release(), secondPort.release()]);
    fs.rmSync(first.work, { recursive: true, force: true });
    fs.rmSync(second.work, { recursive: true, force: true });
  }
});
