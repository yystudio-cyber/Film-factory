const $ = id => document.getElementById(id);
const clip = $("clip");
const labels = {
  queued: "排队中",
  running: "制作中",
  review: "等待选片",
  done: "已交付",
  failed: "待处理",
  cancelled: "已停止"
};
const shotCode = n => "S" + String(n).padStart(2, "0");
const choices = new Map();
let jid = new URLSearchParams(location.search).get("job") || "";
let board = null;
let frame = 0;
let activeShot = null;
let source = null;
let playing = false;
let epoch = 0;
let timer = 0;
let pendingRequest = 0;
let lastPayload = "";

function node(tag, text) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  return el;
}

async function api(url, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  };
  const response = await fetch(url, options);
  if (!response.ok) {
    const raw = await response.text();
    let reason = "HTTP " + response.status;
    try {
      reason = JSON.parse(raw).error || reason;
    } catch {}
    throw Error(reason);
  }
  return response.json();
}

function message(text = "") {
  $("error").textContent = text;
  $("error").hidden = !text;
}

function tc(frames) {
  const fps = board?.fps || 24;
  return [Math.floor(frames / fps / 60), Math.floor(frames / fps) % 60, frames % fps]
    .map(n => String(n).padStart(2, "0")).join(":");
}

function selectedSource(shot) {
  const choice = choices.get(shot.number);
  if (choice === "still") return null;
  if (choice) {
    const match = shot.movies.find(m => m.url === choice);
    if (match) return match;
  }
  const automatic = shot.movies.filter(m => m.status !== "退回");
  const priority = $("priority").value;
  if (priority === "still") return null;
  if (priority === "draft") {
    const match = automatic.find(m => m.kind === "draft");
    if (match) return match;
  }
  return automatic[0] || null;
}

function stopPlayback() {
  epoch++;
  cancelAnimationFrame(timer);
  playing = false;
  clip.onloadedmetadata = null;
  clip.onended = null;
  clip.onerror = null;
  clip.pause();
  $("play").textContent = frame === board?.frames && frame > 0 ? "重新播放" : "连续播放";
}

function displayStill(shot) {
  const available = shot.references.filter(ref => ref.url && ref.role !== "角色指认帧");
  const first = available.find(r => r.role.includes("首帧")) || available[0];
  const last = available.find(r => r.role === "尾帧");
  const ref = (frame - shot.start_frame >= shot.frames / 2 && last) || first;
  clip.hidden = true;
  $("still").hidden = !ref;
  $("slate").hidden = Boolean(ref);
  if (ref) {
    if ($("still").getAttribute("src") !== ref.url) $("still").src = ref.url;
    $("still").alt = ref.role + " · " + ref.name;
  } else {
    $("slateTitle").textContent = shotCode(shot.number) + " · 分镜占位";
    $("slateText").textContent = shot.beat;
  }
  $("screenTag").hidden = false;
  $("screenTag").textContent = ref ? shotCode(shot.number) + " · " + ref.role + "静帧" : "尚无可用参考画面";
}

function updateClock(value) {
  frame = value;
  $("scrub").value = String(frame);
  $("timecode").textContent = tc(frame) + " / " + tc(board.frames);
  if (!source && activeShot) displayStill(activeShot);
}

function showDepartments(departments) {
  const cards = [];
  const sections = [
    ["camera", "摄影与美术执行单", {
      framing: "景别与运镜",
      blocking: "调度",
      continuity: "连贯性",
      edit_point: "剪辑点"
    }],
    ["sound", "声音与剪辑执行单", {
      sync: "同期声",
      ambience: "环境声",
      bridge: "声音衔接",
      mix_intent: "混音意图"
    }]
  ];
  for (const [role, heading, fields] of sections) {
    const values = departments?.[role];
    if (!values) continue;
    const card = node("div"),
      entries = node("dl");
    for (const [key, label] of Object.entries(fields)) {
      if (values[key]) entries.append(node("dt", label), node("dd", values[key]));
    }
    if (entries.children.length) {
      card.append(node("h3", heading), entries);
      cards.push(card);
    }
  }
  $("departments").replaceChildren(...cards);
  $("departments").hidden = !cards.length;
}

function renderNotes(shot) {
  $("shotTitle").textContent = shotCode(shot.number) + " · 镜头详情";
  $("range").textContent = tc(shot.start_frame) + " – " + tc(shot.end_frame) + " · " + shot.frames + " 帧";
  $("scene").textContent = shot.beat;
  showDepartments(shot.departments);
  const options = [new Option("分镜与参考图", "still")];
  shot.movies.forEach(m => options.push(new Option(m.label, m.url)));
  $("source").replaceChildren(...options);
  $("source").value = source?.url || "still";
  $("source").disabled = false;
  $("approve").hidden = !(source?.kind === "candidate" && board.state === "review" && board.gate === shot.number);
  $("approve").textContent = "采用 " + (source?.label || "当前候选");
  $("references").replaceChildren(...shot.references.map(ref => {
    const box = node("div");
    box.className = "ref";
    if (ref.url) {
      const link = node("a"),
        img = node("img");
      img.src = ref.url;
      img.alt = ref.role;
      link.href = ref.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.append(img);
      box.append(link);
    }
    const missing = ref.role === "角色指认帧" ? "（指认帧缺失）" : "（待就绪）";
    box.append(node("span", ref.role + " · " + ref.name + (ref.url ? "" : missing)));
    return box;
  }));
  const post = [];
  for (const [key, title] of Object.entries({
      sound: "现场声",
      dialogue: "同期对白",
      narration: "旁白",
      title: "定版文字",
      caption: "字幕",
      overlay: "叠加图"
    })) {
    if (shot[key]) post.push(node("dt", title), node("dd", shot[key]));
  }
  if (board.music) post.push(node("dt", "配乐"), node("dd", "完整配乐与混音在交付母版中播放"));
  $("post").replaceChildren(...post);
  $("checks").replaceChildren(...shot.checks.map(c => {
    const li = node("li");
    li.append(node("small", c.id + " · " + c.original), node("span", c.expect));
    return li;
  }));
  if (!shot.checks.length) $("checks").append(node("li", "暂无已写入的验收项"));
  for (const button of $("strip").children) button.setAttribute("aria-current", String(Number(button.dataset.number) === shot.number));
  $("prev").disabled = shot.number < 2;
  $("next").disabled = shot.number >= board.shots.length;
}

// The video clock owns rendered frames. Still-image segments use a monotonic timer.
function seek(value, resume = false) {
  stopPlayback();
  if (!board?.shots.length) return;
  const id = epoch;
  const pos = Math.max(0, Math.min(Math.floor(value), board.frames));
  const shot = board.shots.find(s => pos < s.end_frame) || board.shots.at(-1);
  activeShot = shot;
  source = selectedSource(shot);
  playing = resume && pos < board.frames;
  $("play").textContent = playing ? "暂停" : pos === board.frames ? "重新播放" : "连续播放";
  renderNotes(source?.design ? {...shot, ...source.design} : shot);
  updateClock(pos);

  const next = () => {
    if (id === epoch) seek(shot.end_frame, playing && shot.end_frame < board.frames);
  };
  const fail = () => {
    if (id !== epoch) return;
    stopPlayback();
    message("当前媒体无法播放。可切换本镜预览来源，或从制作任务中查看原始候选。");
  };
  if (source) {
    $("slate").hidden = true;
    $("still").hidden = true;
    clip.hidden = false;
    $("screenTag").hidden = false;
    $("screenTag").textContent = shotCode(shot.number) + " · " + source.label;
    clip.muted = !$("sound").checked;
    const ready = () => {
      if (id !== epoch) return;
      const offset = Math.min(pos - shot.start_frame, shot.frames - 1) / board.fps;
      clip.currentTime = offset;
      if (playing) clip.play().catch(fail);
    };
    clip.onerror = fail;
    clip.onended = next;
    if (clip.getAttribute("src") !== source.url || clip.error) {
      clip.onloadedmetadata = ready;
      clip.src = source.url;
    } else if (clip.readyState) ready();
    else clip.onloadedmetadata = ready;
  }
  if (!playing) return;
  const initial = performance.now();
  const tick = now => {
    if (epoch !== id || !playing) return;
    if (source && (!clip.readyState || clip.seeking)) {
      timer = requestAnimationFrame(tick);
      return;
    }
    const current = source ? shot.start_frame + Math.floor(clip.currentTime * board.fps) :
      pos + Math.floor((now - initial) * board.fps / 1000);
    if (current >= shot.end_frame) {
      next();
      return;
    }
    if (frame !== current) updateClock(current);
    timer = requestAnimationFrame(tick);
  };
  timer = requestAnimationFrame(tick);
}

function rebuild(payload) {
  board = payload;
  const enabled = Boolean(board.shots.length);
  $("filmTitle").textContent = board.title;
  $("status").textContent = (labels[board.state] || board.state) + (board.shots.length && !board.ready ? " · 分镜审核中" : "");
  $("console").href = "/?job=" + jid;
  $("monitor").href = "/monitor?job=" + jid;
  $("master").hidden = !board.master;
  if (board.master) $("master").href = board.master;
  else $("master").removeAttribute("href");
  $("strip").replaceChildren(...board.shots.map(s => {
    const b = node("button", shotCode(s.number));
    b.dataset.number = s.number;
    b.style.flexGrow = String(s.frames);
    b.title = s.beat;
    b.onclick = () => seek(s.start_frame, playing);
    return b;
  }));
  $("play").disabled = !enabled;
  $("scrub").disabled = !enabled;
  $("scrub").max = String(board.frames);
  if (enabled) {
    seek(frame);
    return;
  }
  stopPlayback();
  frame = 0;
  activeShot = null;
  source = null;
  $("timecode").textContent = "00:00:00 / 00:00:00";
  $("source").replaceChildren();
  $("source").disabled = true;
  $("prev").disabled = true;
  $("next").disabled = true;
  $("approve").hidden = true;
  $("clip").hidden = true;
  $("still").hidden = true;
  $("screenTag").hidden = true;
  $("slate").hidden = false;
  $("slateTitle").textContent = "等待分镜";
  $("slateText").textContent = board.detail || "导演产出分镜后可检查节奏。";
  $("shotTitle").textContent = "镜头详情";
  $("range").textContent = "";
  $("scene").textContent = "";
  showDepartments(null);
  for (const id of ["references", "post", "checks"]) $(id).replaceChildren();
}

async function refresh(force = false) {
  if (playing && !force) return;
  const revision = ++pendingRequest;
  const jobs = await api("/api/jobs");
  if (revision !== pendingRequest) return;
  if (!jid) jid = jobs[0]?.id || "";
  $("jobs").replaceChildren(...jobs.map(j => new Option(j.spec + " · " + (labels[j.state] || j.state), j.id)));
  // The selected project's direct link also works beyond the recent-project list.
  if (jid && !jobs.some(j => j.id === jid)) $("jobs").add(new Option("指定项目", jid));
  $("jobs").value = jid;
  if (!jid) {
    $("status").textContent = "暂无制作任务";
    return;
  }
  const payload = await api("/api/storyboard/" + encodeURIComponent(jid));
  if (revision !== pendingRequest || (playing && !force)) return;
  const signature = JSON.stringify(payload);
  if (signature === lastPayload) return;
  lastPayload = signature;
  rebuild(payload);
}

function handleError(err) {
  message(err.message || String(err));
}
$("jobs").onchange = () => {
  stopPlayback();
  choices.clear();
  frame = 0;
  jid = $("jobs").value;
  lastPayload = "";
  rebuild({id: jid, title: "正在读取影片", state: "", detail: "正在读取分镜", frames: 0, shots: []});
  message();
  history.replaceState(null, "", "?job=" + encodeURIComponent(jid));
  refresh(true).catch(handleError);
};
$("play").onclick = () => {
  message();
  if (playing) stopPlayback();
  else seek(frame === board?.frames ? 0 : frame, true);
};
$("prev").onclick = () => seek(board.shots[activeShot.number - 2].start_frame, playing);
$("next").onclick = () => seek(activeShot.end_frame, playing);
$("scrub").oninput = () => seek(Number($("scrub").value));
$("source").onchange = () => {
  message();
  choices.set(activeShot.number, $("source").value);
  seek(frame, playing);
};
$("priority").onchange = () => {
  message();
  choices.clear();
  seek(frame, playing);
};
$("sound").onchange = () => {
  clip.muted = !$("sound").checked;
};
$("approve").onclick = async () => {
  const current = jid,
    number = activeShot?.number,
    code = source?.code;
  if (!code || jid !== board.id || board.gate !== number || source.kind !== "candidate") return;
  stopPlayback();
  $("approve").disabled = true;
  message();
  try {
    await api("/api/jobs/" + current + "/approve", {
      code
    });
    if (current === jid) {
      choices.delete(number);
      lastPayload = "";
      await refresh(true);
    }
  } catch (err) {
    handleError(err);
  } finally {
    $("approve").disabled = false;
  }
};
refresh().catch(handleError);
setInterval(() => refresh().catch(handleError), 4000);
