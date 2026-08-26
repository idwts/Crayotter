import { useEffect, useMemo, useState } from "react";
import { BookOpenText, Check, Clapperboard, Save, ShieldCheck, Sparkles } from "lucide-react";
import {
  approveStory,
  composeStoryVideo,
  fetchCurrentStory,
  reviseStory,
} from "../storyApi";

const cx = (...classes) => classes.filter(Boolean).join(" ");

export function StoryReviewPanel({ selectedJob, onVideoJobCreated, notify, t }) {
  const [document, setDocument] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedSceneId, setSelectedSceneId] = useState("");
  const [draftAction, setDraftAction] = useState("");
  const [episodeNumber, setEpisodeNumber] = useState(1);
  const [targetDuration, setTargetDuration] = useState(60);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    if (!selectedJob?.job_id || selectedJob.job_kind !== "story_development") {
      setDocument(null);
      return;
    }
    if (!["completed", "failed", "cancelled"].includes(selectedJob.status)) return;
    let alive = true;
    setLoading(true);
    fetchCurrentStory(selectedJob.job_id)
      .then((result) => alive && setDocument(result.document || null))
      .catch((error) => notify("error", t("operationFailed", { message: error.message })))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [notify, selectedJob?.events_count, selectedJob?.job_id, selectedJob?.job_kind, selectedJob?.status, t]);

  const scenes = useMemo(
    () =>
      (document?.package?.episodes || []).flatMap((episode) =>
        (episode.scenes || []).map((scene) => ({
          ...scene,
          episodeNumber: episode.number,
          episodeTitle: episode.title,
        })),
      ),
    [document],
  );
  const selectedScene = scenes.find((scene) => scene.scene_id === selectedSceneId) || scenes[0];

  useEffect(() => {
    if (!selectedScene) return;
    setSelectedSceneId(selectedScene.scene_id);
    setDraftAction(selectedScene.action || "");
  }, [document?.version, selectedScene]);

  useEffect(() => {
    const episode = document?.package?.episodes?.[0];
    if (!episode) return;
    setEpisodeNumber(episode.number || 1);
    setTargetDuration(episode.target_duration_seconds || 60);
  }, [document?.package?.episodes, document?.version]);

  if (loading && !document) return <section className="soft-section story-review-panel">{t("processing")}</section>;
  if (!document) {
    return (
      <section className="soft-section story-review-panel story-review-empty">
        <BookOpenText size={24} />
        <strong>{t("storyWaitingTitle")}</strong>
        <p>{t("storyWaitingBody")}</p>
      </section>
    );
  }

  const saveScene = async () => {
    if (!selectedScene || saving) return;
    setSaving(true);
    try {
      const packageCopy = JSON.parse(JSON.stringify(document.package));
      for (const episode of packageCopy.episodes || []) {
        const scene = (episode.scenes || []).find((item) => item.scene_id === selectedScene.scene_id);
        if (scene) scene.action = draftAction;
      }
      const result = await reviseStory(selectedJob.job_id, document.version, { package: packageCopy });
      setDocument(result.document);
      notify("success", t("storyRevisionSaved", { version: result.document.version }));
    } catch (error) {
      notify("error", t("operationFailed", { message: error.message }));
    } finally {
      setSaving(false);
    }
  };

  const approveCurrent = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const result = await approveStory(selectedJob.job_id, document.version);
      setDocument(result.document);
      notify("success", t("storyApproved"));
    } catch (error) {
      notify("error", t("operationFailed", { message: error.message }));
    } finally {
      setSaving(false);
    }
  };

  const startVideo = async () => {
    if (composing || document.status !== "APPROVED") return;
    setComposing(true);
    try {
      const result = await composeStoryVideo(selectedJob.job_id, document.version, {
        episode_number: Number(episodeNumber),
        target_duration_seconds: Number(targetDuration),
        enable_plan_review: true,
      });
      notify("success", t("storyComposeCreated", { title: result.job?.title || t("videoWorkflow") }));
      onVideoJobCreated?.(result);
    } catch (error) {
      notify("error", t("operationFailed", { message: error.message }));
    } finally {
      setComposing(false);
    }
  };

  const report = document.similarity_report;
  return (
    <section className="soft-section story-review-panel motion-enter">
      <div className="section-heading compact">
        <div>
          <div className="eyebrow">{t("storyWorkbenchEyebrow")}</div>
          <h3>{document.package?.title || document.dna?.title || t("storyWorkflow")}</h3>
        </div>
        <div className="story-review-actions">
          <span className="soft-chip">{document.version}</span>
          <span className={cx("story-risk-chip", `risk-${report?.overall_risk || "low"}`)}>
            <ShieldCheck size={13} />
            {t("storyRisk", { risk: report?.overall_risk || "low" })}
          </span>
          <button className="primary-button" disabled={saving || document.status === "APPROVED"} onClick={approveCurrent} type="button">
            <Check size={15} />
            {document.status === "APPROVED" ? t("storyApproved") : t("approveStory")}
          </button>
        </div>
      </div>

      {document.status === "APPROVED" && (
        <div className="story-compose-card">
          <div className="story-compose-copy">
            <span><Clapperboard size={20} /></span>
            <div><strong>{t("storyComposeTitle")}</strong><p>{t("storyComposeBody")}</p></div>
          </div>
          <div className="story-compose-controls">
            <label><small>{t("storyComposeEpisode")}</small><select value={episodeNumber} onChange={(event) => {
              const next = Number(event.target.value);
              setEpisodeNumber(next);
              const episode = document.package?.episodes?.find((item) => item.number === next);
              if (episode) setTargetDuration(episode.target_duration_seconds || 60);
            }}>{(document.package?.episodes || []).map((episode) => <option key={episode.episode_id} value={episode.number}>EP{episode.number} · {episode.title}</option>)}</select></label>
            <label><small>{t("storyComposeDuration")}</small><input type="number" min="15" max="3600" value={targetDuration} onChange={(event) => setTargetDuration(event.target.value)} /></label>
            <button disabled={composing || !Number(targetDuration)} onClick={startVideo} type="button"><Sparkles size={16} />{composing ? t("storyComposeStarting") : t("storyComposeButton")}</button>
          </div>
        </div>
      )}

      <div className="story-editor-grid">
        <nav className="story-scene-list" aria-label={t("storyScenes")}>{scenes.map((scene) => (
          <button className={cx("story-scene-button", selectedScene?.scene_id === scene.scene_id && "selected")} key={scene.scene_id} onClick={() => setSelectedSceneId(scene.scene_id)} type="button">
            <small>EP{scene.episodeNumber} · {scene.number}</small><strong>{scene.heading}</strong><span>{scene.purpose}</span>
          </button>
        ))}</nav>
        {selectedScene && <div className="story-scene-editor"><div><small>{selectedScene.episodeTitle}</small><strong>{selectedScene.heading}</strong><button className="text-action" disabled={saving} onClick={saveScene} type="button"><Save size={14} />{t("saveStoryRevision")}</button></div><textarea value={draftAction} onChange={(event) => setDraftAction(event.target.value)} />{(selectedScene.dialogue || []).map((line, index) => <p className="story-dialogue-line" key={`${selectedScene.scene_id}-${index}`}><strong>{line.character}</strong><span>{line.text}</span></p>)}</div>}
      </div>
      <p className="story-risk-disclaimer">{report?.disclaimer}</p>
    </section>
  );
}
