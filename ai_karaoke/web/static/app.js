import { readJson } from "./api.js";
import { ReviewPanel } from "./review.js";
import { bindColor, createElement } from "./ui.js";

const form = document.getElementById("runForm");
const runButton = document.getElementById("runButton");
const progressFill = document.getElementById("progressFill");
const overallLabel = document.getElementById("overallLabel");
const overallNumber = document.getElementById("overallNumber");
const queueEmpty = document.getElementById("queueEmpty");
const taskList = document.getElementById("taskList");
const notice = document.getElementById("notice");
const batchActions = document.getElementById("batchActions");
const downloadAss = document.getElementById("downloadAss");
const downloadEverything = document.getElementById("downloadEverything");
const reviewSection = document.getElementById("reviewSection");
const reviewList = document.getElementById("reviewList");

const reviewPanels = new Map();
let currentJobId = "";
let pollTimer = null;

function setNotice(message, kind = "error") {
  notice.textContent = message;
  notice.className = `notice ${kind}`;
  notice.hidden = !message;
}

async function choosePath(field) {
  setNotice("");
  try {
    const data = await readJson(
      await fetch(`/pick?field=${encodeURIComponent(field)}`),
      "无法打开文件选择器",
    );
    const paths = data.paths || [];
    if (!paths.length) return;
    if (field === "songs") {
      const target = document.getElementById("song_paths");
      const current = target.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
      target.value = [...new Set([...current, ...paths])].join("\n");
    } else {
      document.getElementById(field).value = paths[0];
    }
  } catch (error) {
    setNotice(error.message || "文件选择器调用失败");
  }
}

function ensureReview(jobId, item) {
  const key = `${jobId}:${item.id}`;
  if (reviewPanels.has(key)) return;
  reviewSection.hidden = false;
  reviewPanels.set(key, new ReviewPanel(jobId, item, reviewList));
}

function renderStatus(jobId, data) {
  if (jobId !== currentJobId) return;
  const progress = Number(data.progress || 0);
  progressFill.style.width = `${progress}%`;
  overallNumber.textContent = `${progress}%`;
  const labels = {
    running: `正在处理 ${Math.min((data.current_index || 0) + 1, data.total || 0)} / ${data.total || 0}`,
    completed: `全部完成 · ${data.completed}/${data.total}`,
    partial: `处理结束 · ${data.completed} 成功，${data.failed} 失败`,
    failed: `处理失败 · ${data.failed || 0}/${data.total || 0}`,
  };
  overallLabel.textContent = labels[data.status] || "等待任务";
  queueEmpty.hidden = Boolean(data.items?.length);
  taskList.hidden = !data.items?.length;
  taskList.replaceChildren();

  (data.items || []).forEach(item => {
    const row = createElement("li", `task-item ${item.status}`);
    row.append(
      createElement("span", "task-title", item.title),
      createElement("span", "task-stage", item.error || item.stage),
      createElement(
        "span",
        "task-percent mono",
        item.status === "completed" ? "DONE" : `${item.progress}%`,
      ),
    );
    taskList.append(row);
    if (item.status === "completed" && item.artifacts) ensureReview(jobId, item);
  });

  batchActions.hidden = !(data.completed > 0);
  if (data.completed > 0) {
    const encodedJobId = encodeURIComponent(jobId);
    downloadAss.href = `/download-batch?job_id=${encodedJobId}&scope=ass`;
    downloadEverything.href = `/download-batch?job_id=${encodedJobId}&scope=all`;
  }
  if (!data.running) finishPolling(data);
}

function finishPolling(data) {
  runButton.disabled = false;
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  if (data.status === "completed") {
    setNotice("全部歌曲已处理完成，可以开始试听审核。", "success");
  } else if (data.status === "partial") {
    setNotice("批次已结束；失败歌曲已保留错误信息，其余歌曲可以正常审核。");
  } else if (data.status === "failed") {
    setNotice(data.error || "本批次没有成功生成字幕。");
  }
}

async function pollStatus(jobId) {
  if (!jobId || jobId !== currentJobId) return;
  try {
    const data = await readJson(
      await fetch(`/status?job_id=${encodeURIComponent(jobId)}`),
      "读取任务状态失败",
    );
    renderStatus(jobId, data);
  } catch (error) {
    if (jobId !== currentJobId) return;
    runButton.disabled = false;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    setNotice(error.message || "状态连接中断");
  }
}

function resetReview() {
  reviewPanels.forEach(panel => panel.stopAnimation(false));
  reviewPanels.clear();
  reviewList.replaceChildren();
  reviewSection.hidden = true;
  batchActions.hidden = true;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  setNotice("");
  runButton.disabled = true;
  progressFill.style.width = "0%";
  overallNumber.textContent = "0%";
  overallLabel.textContent = "正在创建任务";
  resetReview();
  try {
    const response = await fetch("/run", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: new URLSearchParams(new FormData(form)).toString(),
    });
    const data = await readJson(response, "任务创建失败");
    currentJobId = data.job_id;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollStatus(currentJobId), 900);
    await pollStatus(currentJobId);
  } catch (error) {
    runButton.disabled = false;
    setNotice(error.message || "无法提交任务");
  }
});

document.querySelectorAll(".picker-button").forEach(button => {
  button.addEventListener("click", () => choosePath(button.dataset.pickField || ""));
});
bindColor("kara_primary_color", "primaryColorPicker");
bindColor("kara_accent_color", "accentColorPicker");
