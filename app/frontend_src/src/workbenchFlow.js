const PLAN_REVIEW_STATUSES = new Set(["DRAFT", "VALIDATED", "WAITING_FOR_USER_REVIEW"]);

export function getPlanReviewDisplay(planInfo, animation = {}) {
  const plan = planInfo?.plan;
  if (!plan) return { visible: false, phase: "hidden" };

  const version = plan.version || "";
  const status = String(plan.status || "").toUpperCase();

  if (animation.dismissedVersion && animation.dismissedVersion === version) {
    return { visible: false, phase: "hidden" };
  }
  if (animation.exitingVersion && animation.exitingVersion === version) {
    return { visible: true, phase: "approved-exit" };
  }
  if (status === "REVISING") return { visible: true, phase: "revising" };
  if (status === "APPROVED" || status === "FROZEN") return { visible: false, phase: "hidden" };
  if (status === "REJECTED") return { visible: true, phase: "rejected" };
  if (PLAN_REVIEW_STATUSES.has(status)) return { visible: true, phase: "review" };
  return { visible: true, phase: "review" };
}

export function getComposerMode(selectedJob) {
  const status = selectedJob?.status;
  const plan = arguments.length > 1 ? arguments[1]?.plan : null;
  const planStatus = String(plan?.status || "").toUpperCase();
  const waitingForPlanReview = Boolean(plan)
    && ["DRAFT", "VALIDATED", "WAITING_FOR_USER_REVIEW", "REVISING"].includes(planStatus)
    && (selectedJob?.current_checkpoint === "plan_review" || selectedJob?.steering_status === "waiting_user");
  if (waitingForPlanReview) {
    return { mode: "plan-review", guidance: true, planReview: true };
  }
  if (status === "running" || status === "queued" || status === "interrupted") {
    return { mode: "guide-running", guidance: true };
  }
  return { mode: "create", guidance: false };
}

export function buildConfigPayload(configForm) {
  const stallTimeout = Math.max(10, Number(configForm?.stallTimeout || 150));
  return {
    active_profile: "default",
    profiles: {
      default: {
        api_key: String(configForm?.apiKey || "").trim(),
        base_url: String(configForm?.baseUrl || "").trim(),
        model_name: String(configForm?.model || "").trim(),
        video_api_key: String(configForm?.videoApiKey || "").trim(),
        video_base_url: String(configForm?.videoBaseUrl || "").trim(),
        video_model_name: String(configForm?.videoModel || "").trim(),
        tts_api_key: String(configForm?.ttsApiKey || "").trim(),
        tts_base_url: String(configForm?.ttsBaseUrl || "").trim(),
        tts_model_name: String(configForm?.ttsModel || "").trim(),
      },
    },
    enable_phase2_research: Boolean(configForm?.enablePhase2Research),
    enable_plan_review: configForm?.enablePlanReview !== false,
    direct_phase3_execution: Boolean(configForm?.directPhase3Execution),
    prefer_local_materials: Boolean(configForm?.preferLocalMaterials),
    agent_stall_timeout_seconds: stallTimeout,
  };
}

export function shouldRefreshCurrentPlan(selectedJob, planInfo) {
  if (!selectedJob) return false;
  if (planInfo?.plan) return false;
  return selectedJob.current_checkpoint === "plan_review" || selectedJob.steering_status === "waiting_user";
}

export function getTaskHeroVariant(selectedJob) {
  return selectedJob ? "sidebar" : "hero";
}

export function shouldShowInlineLogs(selectedJob, planDisplay) {
  if (!selectedJob) return false;
  return !planDisplay?.visible;
}

export function getInspectorPanelMode(selectedJob) {
  return selectedJob ? "trace" : "empty";
}
