const by = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;"
})[c]);
const stateNames = {
  queued: "排队",
  running: "进行中",
  review: "等候选片",
  done: "已完成",
  failed: "待处理",
  cancelled: "已停止",
  interrupted: "已中断"
};
const stages = [
  ["剧本与导演", /导演|剧本覆盖|提示词/],
  ["渲染", /生成镜头/],
  ["镜头验收", /媒体质检|抽帧/],
  ["后期与配乐", /后期|配乐|混音/],
  ["成片验收", /总审/]
];
let jid = new URLSearchParams(location.search).get("job") || "";
if (!/^[a-f0-9]{32}$/.test(jid)) jid = "";
let olderJob = null;
let shot = null,
  generation = 0,
  previousKey = "",
  batchKey = "",
  token = "";
const fmt = n => "S" + String(n).padStart(2, "0");
const time = t => new Date(t * 1000).toLocaleTimeString();
const elapsed = s => Math.max(0, Math.round((s.ended ?? Date.now() / 1000) - s.started)) + " 秒";
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });
  if (!r.ok) throw Error((await r.json()).error || String(r.status));
  return r.json();
}

function report(error) {
  by("error").textContent = error.message;
}

function renderAttempts(snap) {
  const design = snap.shots[snap.shot - 1];
  const key = JSON.stringify([jid, snap.shot, design, snap.attempts, snap.coverage]);
  if (key === previousKey) return;
  previousKey = key;
  const missing = snap.coverage.filter(r => !r.covered);
  const rows = snap.coverage.filter(r => r.checks.some(c => c.shot === snap.shot));
  by("obligations").innerHTML = design ? '<h3>' + fmt(snap.shot) + ' · ' + esc(design.beat) + '</h3><small>' +
    design.seconds + ' 秒 · 首帧 ' + esc(design.start || "全新画面") + ' · 尾帧 ' + esc(design.end || "自由生成") + '</small>' +
    missing.map(r => '<p class="bad">未覆盖 ' + esc(r.id) + ' · ' + esc(r.text) + ' · ' + esc(r.notes) + '</p>').join('') +
    rows.map(r => '<details><summary>' + esc(r.id) + ' · ' + esc(r.text) + '</summary>' + r.checks.filter(c => c
        .shot === snap.shot)
      .map(c => '<p>' + esc(c.expect) + '</p>').join('') + '</details>').join('') : '分镜生成后显示原文和逐镜验收项。';
  by("attempts").innerHTML = snap.attempts.map(a => {
    const label = a.rejection ? "已退回" : a.selected ? "已采用" : a.code ? (a.eligible ? "候选通过" : "历史版本") : "制作尝试";
    const quality = a.review || {};
    const clip = a.code ? ' · <a href="/api/jobs/' + jid + '/C' + a.code + '.mp4" target="_blank">播放 C' + String(a
      .code).padStart(3, "0") + '</a>' : "";
    return '<div class="entry"><b>' + esc(label) + '</b>' + clip + '<small>' + esc(a.id) + ' · 前一镜采用 ' + esc(a
        .design.previous_code || "—") +
      ' · 原始参考 ' + esc(a.design.root_reference || "—") + '</small><div class="strip">' + a.frames.map(i =>
        '<a style="display:contents" href="/api/frames/' + jid + '/' + a.id + '/' + i +
        '" target="_blank"><img alt="镜内抽帧 ' + (i + 1) + '" src="/api/frames/' + jid + '/' + a.id + '/' + i +
        '"></a>').join("") + '</div>' +
      (a.seam ? '<a href="/api/frames/' + jid + '/' + a.id + '/seam" target="_blank">查看剪辑点前后六帧</a>' : '') +
      '<div class="scores">' + Object.entries(quality.scores || {}).map(([k, v]) => esc(({
        story: "剧情",
        composition: "构图",
        identity: "身份",
        continuity: "衔接",
        artifacts: "画面完整性"
      })[k] || k) + ' ' + esc(v) + '/5').join('　') + '</div>' +
      (a.speech ? '<p>对白回听 · ' + esc(a.speech.status==="unverified" ? "未验收" : "已转录核对") +
        ' ' + esc(a.speech.language || "") + ' ' + esc(a.speech.fallback || "") + '</p>' : '') +
      '<p>' + esc(a.rejection?.reason || quality.notes || "") + '</p><details><summary>提示词与验收依据</summary><p>' + esc(
        a.design.prompt) + '</p>' +
      (quality.aesthetic_checks || []).map(c => '<p>审美 ' + esc(c.index) + ' · ' +
        (c.passed ? "通过" : "退回") + ' · ' + esc(c.evidence) + '</p>').join('') +
      (quality.checks || []).map(c => '<p>' + esc(c.result) + ' · ' + esc(c.evidence) + '</p>').join('') +
      '<small>ComfyUI 任务 ' + esc(a.ticket || "尚无回执") + '</small></details></div>';
  }).join("");
}
async function refresh() {
  const version = ++generation;
  const jobs = await api("/api/jobs");
  if (version !== generation) return;
  if (!jid) jid = jobs[0]?.id || "";
  if (jid && !jobs.some(j => j.id === jid)) {
    if (olderJob?.id !== jid) {
      const detail = await api("/api/jobs/" + jid);
      if (version !== generation) return;
      olderJob = {id: detail.id, spec: detail.spec.title, state: detail.state};
    }
    jobs.unshift(olderJob);
  }
  by("queue").innerHTML = jobs.map(j => '<button data-job="' + j.id + '" class="' + (j.id === jid ? 'active' : '') +
    '">' + esc(j.spec) + '<small>' + stateNames[j.state] + '</small></button>').join("") || "暂无任务。";
  by("clock").textContent = "本机时间 " + new Date().toLocaleTimeString() + " · 每 3 秒更新";
  if (!jid) return;
  const snap = await api("/api/monitor/" + jid + (shot ? "?shot=" + shot : ""));
  if (version !== generation) return;
  by("console").href = "/?job=" + jid;
  by("storyboard").href = "/storyboard?job=" + jid;
  by("film").textContent = jobs.find(j => j.id === jid).spec;
  by("state").textContent = stateNames[snap.state];
  by("detail").textContent = snap.detail;
  const n = Object.keys(snap.selected).length;
  by("meter").style.width = (snap.shots.length ? 100 * n / snap.shots.length : 0) + "%";
  const timing = [[6,8],[7.5,9.75],[9,11.5],[10.5,13.25],[12,15],[15,18.5],[18,22]];
  const remain = snap.shots.filter((s,i) => !snap.selected[i+1]);
  const estimate = remain.reduce(([lo,hi],s) => [lo+timing[s.seconds-2][0],hi+timing[s.seconds-2][1]],[0,0]);
  by("count").textContent = n + " / " + snap.shots.length + " 镜已采用" +
    (remain.length ? " · 剩余正片渲染约 " + Math.round(estimate[0]) + "–" + Math.ceil(estimate[1]) + " 分钟（各一遍，含当前镜全长）" : "");
  const measured = Object.entries({draft:"动作草稿",picture:"正片",score:"配乐"}).flatMap(([key, label]) => {
    const record = snap.production_times?.[key];
    return record ? [label + " " + record.calls + " 次 / " + (record.seconds / 60).toFixed(1) + " 分钟"] : [];
  });
  by("serviceTimes").hidden = !measured.length;
  by("serviceTimes").textContent = "已完成工序耗时（含服务等待）　" + measured.join("　");
  by("steps").innerHTML = stages.map(([name, pattern]) => {
    const ev = snap.stages.find(x => pattern.test(x.title));
    return '<span class="' + (ev?.state || "") + '">' + name + ' · ' + (ev ? stateNames[ev.state] : "—") +
      '</span>';
  }).join("");
  by("shots").innerHTML = snap.shots.map((s, i) => '<button data-shot="' + (i + 1) + '" class="' + (snap.shot === i +
    1 ? 'active' : '') + '">' + (snap.selected[i + 1] ? '✓ ' : '') + fmt(i + 1) + '</button>').join("");
  renderAttempts(snap);
  by("trace").innerHTML = snap.stages.map(s => '<div class="entry">' + time(s.started) + '　' + esc(s.shot ? fmt(s
      .shot) : "全片") + '　' + esc(s.title) + '　' + esc(s.actor) +
    '　' + stateNames[s.state] + ' · ' + elapsed(s) + (s.detail ? '<small>' + esc(s.detail) + '</small>' : '') +
    '</div>').join("") || "等待执行。";
  by("output").hidden = !snap.technical;
  if (snap.technical) by("verdict").innerHTML = '<p>' + snap.technical.width + ' × ' + snap.technical.height + ' · ' +
    snap.technical.fps + ' fps · ' + snap.technical.frames +
    ' 帧　' + (snap.state === 'done' ? '<a href="/api/jobs/' + jid + '/film.mp4" target="_blank">播放成片</a>' : '总审退回') +
    '</p>' + (snap.audio_mix?.encoded ? '<p>成片声音　' + esc(snap.audio_mix.encoded.integrated_lufs ?? "静音") +
    ' LUFS　' + esc(snap.audio_mix.encoded.true_peak_dbtp ?? "静音") + ' dBTP</p>' : '') + snap.source_verdicts.map(v => '<p>' + esc(v.id) + ' · ' + esc(v.notes) + '</p>').join("");
}
by("queue").onclick = e => {
  const b = e.target.closest("[data-job]");
  if (b) {
    jid = b.dataset.job;
    shot = null;
    history.replaceState(null, "", "?job=" + jid);
    refresh().catch(report);
  }
};
by("shots").onclick = e => {
  if (e.target.dataset.shot) {
    shot = Number(e.target.dataset.shot);
    refresh().catch(report);
  }
};
by("follow").onclick = () => {
  shot = null;
  refresh().catch(report);
};
by("batchToggle").onclick = () => {
  by("batch").hidden = !by("batch").hidden;
};
by("files").onchange = () => {
  by("filesNote").textContent = Array.from(by("files").files, f => f.name).join("、");
};
by("submit").onclick = async () => {
  by("submit").disabled = true;
  by("error").textContent = "";
  try {
    const films = [];
    for (const f of by("files").files) {
      const text = (await f.text()).replace(/^\uFEFF/, "");
      if (f.name.toLowerCase().endsWith(".json")) {
        const list = JSON.parse(text);
        if (!Array.isArray(list)) throw Error("JSON 清单需为影片数组");
        films.push(...list);
      } else films.push({
        title: f.name.replace(/\.[^.]+$/, ""),
        script: text,
        requirements: by("brief").value,
        seconds: Number(by("seconds").value),
        mode: "auto",
        music: by("music").checked,
        render_drafts: by("batchDraft").checked
      });
    }
    if (!films.length) throw Error("请先选择剧本文件或 JSON 清单");
    const signature = JSON.stringify(films);
    if (signature !== batchKey) {
      batchKey = signature;
      token = crypto.randomUUID();
    }
    const result = await api("/api/batches", {
      films,
      token
    });
    jid = result.ids[0];
    shot = null;
    by("filesNote").textContent = result.ids.length + " 部影片已进入制作队列";
    by("files").value = "";
    batchKey = "";
    await refresh();
  } catch (e) {
    report(e);
  } finally {
    by("submit").disabled = false;
  }
};
refresh().catch(report);
setInterval(() => refresh().catch(report), 3000);

async function resources() {
  const data = await api("/api/station");
  const devices = data.gpus.map(g => "GPU" + esc(g.index) + " · " + esc(g.name) + " · " + esc(g.used) + "/" + esc(g
      .total) +
    " MiB · " + esc(g.percent) + "% · " + esc(g.watts) + " W");
  const queue = data.queue ? "运行 " + data.queue.running + "，等待 " + data.queue.pending : "未连接";
  by("resources").innerHTML = "<div>" + (devices.join("<br>") || "未取得 nvidia-smi 遥测") +
    "</div><small>工作盘剩余 " + data.disk_free_gib + " GiB" + (data.disk_hold ? "（空间不足，暂停领取新任务）" : "") +
    " · " + esc(data.ownership || "") + " · ComfyUI 队列 " + queue + " · " + time(data.time) + "</small>";
}
resources().catch(report);
setInterval(() => resources().catch(report), 12000);
