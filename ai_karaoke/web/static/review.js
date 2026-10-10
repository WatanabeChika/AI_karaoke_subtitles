import { artifactUrl, assUrl, readJson } from "./api.js";
import { parseAss, segmentProgress } from "./ass_preview.js";
import { assToHex, createElement, formatClock } from "./ui.js";

export class ReviewPanel {
  constructor(jobId, item, container) {
    this.jobId = jobId;
    this.item = item;
    this.container = container;
    this.entries = [];
    this.activeCaptionKey = "";
    this.activeTimelineIndex = -1;
    this.paintTargets = [];
    this.animationFrameId = null;
    this.editDebounce = null;
    this.originalReady = false;
    this.fallbackUsed = false;
    this.originalRetryUsed = false;
    this.build();
    this.loadAss();
  }

  build() {
    this.card = document.createElement("details");
    this.card.className = "review-card";
    this.card.open = this.container.children.length === 0;

    const summary = document.createElement("summary");
    const summaryText = createElement("div");
    summaryText.append(
      createElement("div", "review-title", this.item.title),
      createElement(
        "div",
        "review-meta",
        `${this.item.artifacts.audio_name} · ${this.item.artifacts.ass_name}`,
      ),
    );
    summary.append(summaryText, createElement("span", "review-state", "可审核"));

    const body = createElement("div", "review-body");
    this.audio = document.createElement("audio");
    this.audio.controls = true;
    this.audio.preload = "metadata";
    this.playerStatus = createElement("p", "player-status", "正在载入原曲…");
    this.captionStage = createElement("div", "caption-stage");
    this.captionStage.style.setProperty(
      "--kara-pending",
      assToHex(document.getElementById("kara_primary_color").value) || "#fffdf7",
    );
    this.captionStage.style.setProperty(
      "--kara-sung",
      assToHex(document.getElementById("kara_accent_color").value) || "#ff7356",
    );
    this.showCaptionPlaceholder("播放音频后在此预览双行字幕");

    const reviewGrid = createElement("div", "review-grid");
    const timelinePanel = createElement("section", "timeline-panel");
    const timelineToolbar = createElement("div", "panel-toolbar");
    timelineToolbar.append(
      createElement("h3", "", "歌词与注音"),
      createElement("span", "save-state mono", "点击跳转"),
    );
    this.timelineList = createElement("ol", "timeline-list");
    timelinePanel.append(timelineToolbar, this.timelineList);

    const editorPanel = createElement("section", "editor-panel");
    const editorToolbar = createElement("div", "panel-toolbar");
    const editorActions = createElement("div", "panel-toolbar");
    this.saveState = createElement("span", "save-state", "尚未修改");
    this.saveButton = createElement("button", "button-small", "保存 ASS");
    this.saveButton.type = "button";
    editorActions.append(this.saveState, this.saveButton);
    editorToolbar.append(createElement("h3", "", "ASS 源文件"), editorActions);
    this.editor = document.createElement("textarea");
    this.editor.className = "ass-editor";
    this.editor.spellcheck = false;
    this.editor.setAttribute("aria-label", `${this.item.title} ASS 编辑器`);
    editorPanel.append(editorToolbar, this.editor);

    const links = createElement("div", "review-links");
    const download = createElement("a", "text-link", "下载当前 ASS");
    download.href = artifactUrl(this.jobId, this.item.id, "ass", true);
    const alignmentReport = createElement("a", "text-link", "下载对齐报告");
    alignmentReport.href = artifactUrl(this.jobId, this.item.id, "alignment_report", true);
    links.append(download, alignmentReport);
    if (this.item.artifacts.has_pronunciation_report) {
      const pronunciationReport = createElement("a", "text-link", "下载注音报告");
      pronunciationReport.href = artifactUrl(
        this.jobId,
        this.item.id,
        "pronunciation_report",
        true,
      );
      links.append(pronunciationReport);
    }

    reviewGrid.append(timelinePanel, editorPanel);
    body.append(this.audio, this.playerStatus, this.captionStage, reviewGrid, links);
    this.card.append(summary, body);
    this.container.append(this.card);
    this.bindAudio();
    this.editor.addEventListener("input", () => this.handleEdit());
    this.saveButton.addEventListener("click", () => this.saveAss());
  }

  bindAudio() {
    const originalUrl = artifactUrl(this.jobId, this.item.id, "source");
    const vocalUrl = artifactUrl(this.jobId, this.item.id, "vocal");
    this.audio.addEventListener("loadedmetadata", () => {
      if (!this.fallbackUsed) {
        this.originalReady = true;
        this.playerStatus.textContent = "已载入原曲；字幕预览不会影响音频源。";
        this.playerStatus.className = "player-status";
      }
      this.render(this.audio.currentTime);
    });
    this.audio.addEventListener("playing", () => {
      if (!this.fallbackUsed) this.originalReady = true;
    });
    this.audio.addEventListener("error", () => {
      if (!this.originalReady && !this.fallbackUsed) {
        this.fallbackUsed = true;
        this.playerStatus.textContent = "浏览器无法解码原曲，已改用分离后的人声轨预览。";
        this.playerStatus.className = "player-status error";
        this.audio.src = vocalUrl;
        this.audio.load();
        return;
      }
      if (this.fallbackUsed) {
        this.playerStatus.textContent = "浏览器也无法播放分离后的人声轨。";
        this.playerStatus.className = "player-status error";
        return;
      }
      this.playerStatus.textContent = "原曲播放暂时中断，正在重新载入；音源不会切换为人声轨。";
      this.playerStatus.className = "player-status error";
      if (!this.originalRetryUsed) {
        this.originalRetryUsed = true;
        this.audio.load();
      }
    });
    this.audio.addEventListener("play", () => this.startAnimation());
    this.audio.addEventListener("pause", () => this.stopAnimation(true));
    this.audio.addEventListener("ended", () => this.stopAnimation(true));
    this.audio.addEventListener("seeked", () => this.render(this.audio.currentTime));
    this.audio.addEventListener("timeupdate", () => {
      if (this.audio.paused) this.render(this.audio.currentTime);
    });
    this.audio.src = originalUrl;
  }

  showCaptionPlaceholder(message) {
    this.captionStage.replaceChildren(createElement("div", "caption-placeholder", message));
    this.activeCaptionKey = "";
    this.paintTargets = [];
  }

  activeEntries(time) {
    const latestByStyle = new Map();
    this.entries.forEach((entry, index) => {
      if (time >= entry.start && time <= entry.end) {
        latestByStyle.set(entry.style, { entry, index });
      }
    });
    return ["k1", "k2"].map(style => latestByStyle.get(style)).filter(Boolean);
  }

  buildCaptionRows(active) {
    const key = active.map(({ entry }) => entry.id).join("|");
    if (key === this.activeCaptionKey) return;
    this.activeCaptionKey = key;
    this.paintTargets = [];
    this.captionStage.replaceChildren();
    if (!active.length) {
      this.showCaptionPlaceholder("当前时间没有字幕");
      return;
    }
    active.forEach(({ entry }) => {
      const row = createElement("div", `caption-row ${entry.style}`);
      entry.segments.forEach(segment => {
        if (!segment.text) return;
        const syllable = createElement("span", "kara-syllable");
        const paint = createElement("span", "kara-paint", segment.text);
        if (segment.reading) {
          const ruby = document.createElement("ruby");
          ruby.append(paint, createElement("rt", "", segment.reading));
          syllable.append(ruby);
        } else {
          syllable.append(paint);
        }
        row.append(syllable);
        this.paintTargets.push({ element: syllable, segment });
      });
      this.captionStage.append(row);
    });
  }

  updateTimeline(active) {
    const currentIndex = active.length ? active[active.length - 1].index : -1;
    if (currentIndex === this.activeTimelineIndex) return;
    const oldButton = this.timelineList.children[this.activeTimelineIndex]?.querySelector("button");
    const newButton = this.timelineList.children[currentIndex]?.querySelector("button");
    oldButton?.classList.remove("active");
    newButton?.classList.add("active");
    newButton?.scrollIntoView({ block: "nearest" });
    this.activeTimelineIndex = currentIndex;
  }

  render(time) {
    const active = this.activeEntries(time);
    this.buildCaptionRows(active);
    this.paintTargets.forEach(({ element, segment }) => {
      element.style.setProperty("--kara-progress", `${(segmentProgress(segment, time) * 100).toFixed(2)}%`);
    });
    this.updateTimeline(active);
  }

  startAnimation() {
    this.stopAnimation(false);
    const frame = () => {
      this.render(this.audio.currentTime);
      if (!this.audio.paused && !this.audio.ended) {
        this.animationFrameId = requestAnimationFrame(frame);
      }
    };
    this.animationFrameId = requestAnimationFrame(frame);
  }

  stopAnimation(renderFinal) {
    if (this.animationFrameId !== null) cancelAnimationFrame(this.animationFrameId);
    this.animationFrameId = null;
    if (renderFinal) this.render(this.audio.currentTime);
  }

  renderTimeline() {
    this.timelineList.replaceChildren();
    this.entries.forEach(entry => {
      const listItem = document.createElement("li");
      const button = createElement("button", "timeline-button");
      button.type = "button";
      const copy = createElement("span", "timeline-copy");
      copy.append(createElement("span", "timeline-text", entry.timelineText || "（空白计时）"));
      if (entry.readingText && entry.readingText !== entry.timelineText) {
        copy.append(createElement("span", "timeline-reading", entry.readingText));
      }
      button.append(createElement("span", "timeline-time mono", formatClock(entry.start)), copy);
      button.addEventListener("click", () => {
        this.audio.currentTime = Math.max(0, entry.start + 0.02);
        this.audio.play().catch(() => {});
        this.editor.focus();
        this.editor.setSelectionRange(entry.sourceStart, entry.sourceEnd);
        this.editor.scrollTop = Math.max(0, (entry.sourceIndex - 4) * 19);
      });
      listItem.append(button);
      this.timelineList.append(listItem);
    });
  }

  handleEdit() {
    this.saveState.textContent = "有未保存修改";
    clearTimeout(this.editDebounce);
    this.editDebounce = setTimeout(() => {
      this.entries = parseAss(this.editor.value);
      this.activeCaptionKey = "";
      this.activeTimelineIndex = -1;
      this.renderTimeline();
      this.render(this.audio.currentTime);
    }, 280);
  }

  async saveAss() {
    this.saveButton.disabled = true;
    this.saveState.textContent = "正在保存…";
    try {
      const response = await fetch(assUrl(this.jobId, this.item.id), {
        method: "PUT",
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        body: this.editor.value,
      });
      await readJson(response, "保存失败");
      this.saveState.textContent = "已保存";
    } catch (error) {
      this.saveState.textContent = error.message || "保存失败";
    } finally {
      this.saveButton.disabled = false;
    }
  }

  async loadAss() {
    try {
      const response = await fetch(assUrl(this.jobId, this.item.id));
      const data = await readJson(response, "无法读取 ASS");
      this.editor.value = data.content;
      this.entries = parseAss(data.content);
      this.renderTimeline();
      this.render(0);
    } catch (error) {
      this.saveState.textContent = error.message || "无法读取 ASS";
      this.editor.disabled = true;
      this.saveButton.disabled = true;
    }
  }
}
