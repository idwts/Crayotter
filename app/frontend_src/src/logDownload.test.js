import assert from "node:assert/strict";
import { test } from "node:test";

import { downloadTextFile } from "./logDownload.js";

test("downloadTextFile keeps the object URL alive until the next timer tick", () => {
  const calls = [];
  const anchor = {
    href: "",
    download: "",
    click() {
      calls.push(["click", this.href]);
    },
    remove() {
      calls.push(["remove"]);
    },
  };
  const document = {
    createElement(tag) {
      assert.equal(tag, "a");
      return anchor;
    },
    body: {
      appendChild(node) {
        calls.push(["append", node]);
      },
    },
  };
  const timers = [];
  const urlApi = {
    createObjectURL(blob) {
      calls.push(["create", blob.type]);
      return "blob:log";
    },
    revokeObjectURL(url) {
      calls.push(["revoke", url]);
    },
  };
  const scheduler = (callback, delay) => {
    timers.push({ callback, delay });
  };

  downloadTextFile({
    text: "line one",
    filename: "job-events.log",
    document,
    urlApi,
    scheduler,
  });

  assert.deepEqual(
    calls.map((item) => item[0]),
    ["create", "append", "click", "remove"],
  );
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 0);

  timers[0].callback();
  assert.deepEqual(calls.at(-1), ["revoke", "blob:log"]);
});
