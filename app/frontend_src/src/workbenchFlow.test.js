import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildConfigPayload,
  getComposerMode,
  getInspectorPanelMode,
  getPlanReviewDisplay,
  getTaskHeroVariant,
  shouldShowInlineLogs,
  shouldRefreshCurrentPlan,
} from "./workbenchFlow.js";

test("getPlanReviewDisplay hides absent plans and shows review checkpoints", () => {
  assert.deepEqual(getPlanReviewDisplay(null), { visible: false, phase: "hidden" });

  assert.deepEqual(
    getPlanReviewDisplay({ plan: { version: "v001", status: "WAITING_FOR_USER_REVIEW" } }),
    { visible: true, phase: "review" },
  );

  assert.deepEqual(
    getPlanReviewDisplay({ plan: { version: "v002", status: "REVISING" } }),
    { visible: true, phase: "revising" },
  );
});

test("getPlanReviewDisplay keeps approved plans mounted only during exit animation", () => {
  assert.deepEqual(
    getPlanReviewDisplay({ plan: { version: "v001", status: "APPROVED" } }, { exitingVersion: "v001" }),
    { visible: true, phase: "approved-exit" },
  );

  assert.deepEqual(
    getPlanReviewDisplay({ plan: { version: "v001", status: "APPROVED" } }),
    { visible: false, phase: "hidden" },
  );

  assert.deepEqual(
    getPlanReviewDisplay({ plan: { version: "v001", status: "APPROVED" } }, { dismissedVersion: "v001" }),
    { visible: false, phase: "hidden" },
  );
});

test("getComposerMode maps task lifecycle to create, guidance, or plan review state", () => {
  assert.deepEqual(getComposerMode(null), { mode: "create", guidance: false });
  assert.deepEqual(getComposerMode({ status: "running" }), { mode: "guide-running", guidance: true });
  assert.deepEqual(
    getComposerMode(
      { status: "running", current_checkpoint: "plan_review", steering_status: "waiting_user" },
      { plan: { version: "v001", status: "WAITING_FOR_USER_REVIEW" } },
    ),
    { mode: "plan-review", guidance: true, planReview: true },
  );
  assert.deepEqual(getComposerMode({ status: "completed" }), { mode: "create", guidance: false });
  assert.deepEqual(getComposerMode({ status: "failed" }), { mode: "create", guidance: false });
  assert.deepEqual(getComposerMode({ status: "cancelled" }), { mode: "create", guidance: false });
});

test("buildConfigPayload does not write resource pool sizes from the UI", () => {
  const payload = buildConfigPayload({
    apiKey: "k",
    baseUrl: "https://example.test/v1",
    model: "qwen-plus",
    videoApiKey: "",
    videoBaseUrl: "",
    videoModel: "qwen-vl-max-latest",
    ttsApiKey: "",
    ttsBaseUrl: "",
    ttsModel: "qwen-tts-latest",
    stallTimeout: "1200",
    enablePhase2Research: true,
    enablePlanReview: true,
    directPhase3Execution: false,
    preferLocalMaterials: false,
    searchPoolSize: "99",
    downloadPoolSize: "99",
    videoAnalysisPoolSize: "99",
    llmPoolSize: "99",
    ffmpegPoolSize: "99",
    ttsPoolSize: "99",
    exportPoolSize: "99",
  });

  assert.equal(payload.agent_stall_timeout_seconds, 1200);
  assert.equal(payload.search_pool_size, undefined);
  assert.equal(payload.download_pool_size, undefined);
  assert.equal(payload.video_analysis_pool_size, undefined);
  assert.equal(payload.llm_pool_size, undefined);
  assert.equal(payload.ffmpeg_pool_size, undefined);
  assert.equal(payload.tts_pool_size, undefined);
  assert.equal(payload.export_pool_size, undefined);
});

test("shouldRefreshCurrentPlan treats plan review checkpoint as recoverable state", () => {
  assert.equal(shouldRefreshCurrentPlan({ current_checkpoint: "plan_review" }, null), true);
  assert.equal(shouldRefreshCurrentPlan({ steering_status: "waiting_user" }, null), true);
  assert.equal(
    shouldRefreshCurrentPlan({ current_checkpoint: "plan_review" }, { plan: { version: "v001", status: "WAITING_FOR_USER_REVIEW" } }),
    false,
  );
  assert.equal(shouldRefreshCurrentPlan({ current_checkpoint: "phase1" }, null), false);
});

test("getTaskHeroVariant keeps the large prompt only before a job starts", () => {
  assert.equal(getTaskHeroVariant(null), "hero");
  assert.equal(getTaskHeroVariant({ status: "queued" }), "sidebar");
  assert.equal(getTaskHeroVariant({ status: "running" }), "sidebar");
  assert.equal(getTaskHeroVariant({ status: "completed" }), "sidebar");
  assert.equal(getTaskHeroVariant({ status: "cancelled" }), "sidebar");
});

test("shouldShowInlineLogs hides live logs while the plan review card is visible", () => {
  assert.equal(shouldShowInlineLogs(null, { visible: false, phase: "hidden" }), false);
  assert.equal(shouldShowInlineLogs({ status: "running" }, { visible: true, phase: "review" }), false);
  assert.equal(shouldShowInlineLogs({ status: "running" }, { visible: false, phase: "hidden" }), true);
  assert.equal(shouldShowInlineLogs({ status: "completed" }, { visible: false, phase: "hidden" }), true);
});

test("getInspectorPanelMode keeps Agent trace visible and removes runtime parameters", () => {
  assert.equal(getInspectorPanelMode(null), "empty");
  assert.equal(getInspectorPanelMode({ status: "running" }), "trace");
  assert.equal(getInspectorPanelMode({ status: "completed" }), "trace");
});
