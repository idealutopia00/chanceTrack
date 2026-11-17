// Run with node tests/check_updates.mjs. Exercise the real update script without a browser dependency.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const source = fs.readFileSync(
  new URL("../chance_track/static/app.js", import.meta.url),
  "utf8",
);
function element() {
  return {
    textContent: "",
    hidden: true,
    disabled: false,
    open: false,
    dataset: {},
    events: {},
    attributes: {},
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
    getBoundingClientRect() {
      return { left: 10, right: 110, top: 10, bottom: 110 };
    },
    classList: { toggle() {} },
    addEventListener(name, handler) {
      this.events[name] = handler;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}
function setup() {
  const ids = Object.fromEntries(
    [
      "refresh-form",
      "refresh-button",
      "refresh-label",
      "update-status",
      "new-report-link",
      "show-notification",
      "notification-dialog",
    ].map((id) => [id, element()]),
  );
  const forms = [element(), element()],
    requests = [],
    navigation = [],
    intervals = [];
  const window = {
    events: {},
    location: {
      assign(url) {
        navigation.push(url);
      },
    },
    addEventListener(name, handler) {
      this.events[name] = handler;
    },
  };
  const document = {
    body: { dataset: { reportId: "2" } },
    hidden: false,
    getElementById(id) {
      return ids[id] || null;
    },
    querySelectorAll(selector) {
      return selector === ".decision-panel form" ? forms : [];
    },
    addEventListener() {},
  };
  let response = {
    ok: true,
    json: async () => ({ report_id: 3, failures: 0 }),
  };
  const context = {
    document,
    window,
    FormData: class {},
    console,
    setInterval(handler) {
      intervals.push(handler);
    },
    fetch: async (url, options) => {
      requests.push([url, options]);
      return response;
    },
  };
  vm.runInNewContext(source, context);
  return {
    ids,
    forms,
    window,
    navigation,
    requests,
    intervals,
    setResponse(value) {
      response = value;
    },
    async submit() {
      await ids["refresh-form"].events.submit({ preventDefault() {} });
    },
  };
}
let app = setup();
await app.submit();
assert.equal(app.requests[0][0], "/refresh");
assert.equal(app.requests[0][1].method, "POST");
assert.deepEqual(app.navigation, ["/reports/3"]);
assert.equal(app.ids["refresh-button"].disabled, false);
assert.equal(app.ids["refresh-form"].attributes["aria-busy"], "false");
app = setup();
app.forms[0].events.input();
await app.submit();
assert.equal(app.navigation.length, 0);
assert.equal(app.ids["new-report-link"].hidden, false);
assert.equal(app.ids["new-report-link"].href, "/reports/3");
assert.match(app.ids["update-status"].textContent, /未保存的判断已保留/);
// Saving one form must not silently discard another form's draft.
app.forms[1].events.input();
app.forms[0].events.submit();
let prevented = false;
app.window.events.beforeunload({
  preventDefault() {
    prevented = true;
  },
});
assert.ok(prevented);
app.setResponse({
  ok: true,
  json: async () => ({ running: true, latest: { id: 2 } }),
});
await app.intervals[0]();
assert.equal(app.requests.at(-1)[0], "/api/update-status");
assert.equal(app.ids["refresh-button"].disabled, true);
app.setResponse({
  ok: true,
  json: async () => ({ running: false, latest: { id: 5 } }),
});
await app.intervals[0]();
assert.equal(app.ids["refresh-button"].disabled, false);
assert.equal(app.ids["new-report-link"].href, "/reports/5");
assert.equal(app.navigation.length, 0);
app = setup();
app.setResponse({
  ok: false,
  json: async () => ({ detail: "已有检查正在运行" }),
});
await app.submit();
assert.match(app.ids["update-status"].textContent, /已有检查/);
assert.equal(app.ids["refresh-button"].disabled, false);
assert.equal(app.navigation.length, 0);
console.log(
  "PASS: manual refresh, draft preservation, status polling, busy recovery",
);

const modalApp = setup();
modalApp.ids["show-notification"].events.click();
assert.equal(modalApp.ids["notification-dialog"].open, true);
assert.equal(modalApp.requests.length, 0);
modalApp.ids["notification-dialog"].events.click({
  target: modalApp.ids["notification-dialog"],
  clientX: 50,
  clientY: 50,
});
assert.equal(modalApp.ids["notification-dialog"].open, true);
modalApp.ids["notification-dialog"].events.click({
  target: modalApp.ids["notification-dialog"],
  clientX: 0,
  clientY: 0,
});
assert.equal(modalApp.ids["notification-dialog"].open, false);
console.log("PASS: notification dialog opens/closes without network requests");
