const PHASE_ORDER = {
  phase1: 1,
  phase2: 2,
  phase3: 3,
};

export const normalizePhaseCode = (value) => {
  const phase = String(value || "").trim().toLowerCase();
  if (["phase1", "planning", "preparing"].includes(phase)) return "phase1";
  if (["phase2", "researching", "editing_research"].includes(phase)) return "phase2";
  if (["phase3", "react", "react_editing", "done"].includes(phase)) return "phase3";
  return "";
};

export const normalizeRuntimeEvent = (event) => {
  if (!event || typeof event !== "object") return event;
  const normalizedPhase = normalizePhaseCode(event.payload?.phase);
  if (!normalizedPhase) return event;
  return {
    ...event,
    payload: {
      ...event.payload,
      phase: normalizedPhase,
    },
  };
};

const eventKey = (event) => {
  const sequence = Number(event?.sequence || 0);
  if (sequence > 0) return `sequence:${sequence}`;
  return [
    event?.type || "",
    event?.timestamp || "",
    JSON.stringify(event?.payload || {}),
  ].join("|");
};

export const mergeRuntimeEvents = (...eventLists) => {
  const merged = new Map();
  eventLists.flat().filter(Boolean).forEach((rawEvent) => {
    const event = normalizeRuntimeEvent(rawEvent);
    merged.set(eventKey(event), event);
  });
  return [...merged.values()].sort((left, right) => {
    const leftSequence = Number(left?.sequence || 0);
    const rightSequence = Number(right?.sequence || 0);
    if (leftSequence && rightSequence && leftSequence !== rightSequence) {
      return leftSequence - rightSequence;
    }
    return String(left?.timestamp || "").localeCompare(String(right?.timestamp || ""));
  });
};

export const getHighestPhase = (events) => events.reduce((highest, event) => {
  const phase = normalizePhaseCode(event?.payload?.phase);
  return (PHASE_ORDER[phase] || 0) > (PHASE_ORDER[highest] || 0) ? phase : highest;
}, "");
