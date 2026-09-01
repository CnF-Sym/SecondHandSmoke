const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function bytes(n) {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + " " + u[i];
}

function duration(seconds) {
  if (seconds == null || !isFinite(seconds)) return "—";
  const total = Math.max(0, Math.round(Number(seconds)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map(n => String(n).padStart(2, "0")).join(":");
}

let toastTimer;
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

async function api(url, opts = {}) {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...opts
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { toast(j.error || "Request failed", true); throw new Error(j.error); }
  return j;
}

// -- actions ---------------------------------------------------------------
async function createDay() {
  await api("/api/day", { method: "POST", body: JSON.stringify({
    project: $("dayProject").value,
    name: $("dayName").value,
    shoot_date: $("dayDate").value
  })});
  $("dayName").value = "";
  refresh();
}

async function activateDay(id) { await api(`/api/day/${id}/activate`, { method: "POST" }); refresh(); }

async function addDest() {
  await api("/api/destination", { method: "POST", body: JSON.stringify({
    path: $("destPath").value, label: $("destLabel").value, role: $("destRole").value
  })});
  $("destPath").value = ""; $("destLabel").value = "";
  refresh();
}

async function delDest(id) {
  await api(`/api/destination/${id}`, { method: "DELETE" });
  refresh();
}

async function scan() {
  const j = await api("/api/scan");
  $("scanRoot").textContent = j.scan_root;
  const box = $("scanList");
  const problems = (j.problems || []).map(p =>
    `<div class="scan-problem">${esc(p)}</div>`).join("");
  if (!j.cards.length) {
    box.innerHTML = problems || '<p class="empty">No cards found. Check the card is mounted.</p>';
    return;
  }
  box.innerHTML = problems + j.cards.map(c => {
    const label = !c.known ? "Ingest card"
      : (c.status === "verified" ? "Re-verify card" : "Resume ingest");
    return `
    <div class="found">
      <div class="grow">
        <div><span class="roll-sm">${esc(c.roll || c.label)}</span> <span class="tag">${esc(c.kind)}</span>
        ${c.known ? '<span class="tag known">registered</span>' : ""}</div>
        <div class="path">${esc(c.path)} · ${c.media_count} media of ${c.file_count} files · ${bytes(c.total_bytes)}</div>
        ${(c.scan_errors || []).length ? '<div class="scan-problem">Card has read errors — ingest blocked</div>' : ''}
      </div>
      ${(c.scan_errors || []).length ? "" : `
      <button class="btn go" onclick="ingestCard('${c.fingerprint}')">${label}</button>
      <button class="btn sm ghost" onclick="register('${c.fingerprint}')">Register only</button>`}
    </div>`;
  }).join("");
}

async function register(fp) {
  const result = await api("/api/register", { method: "POST", body: JSON.stringify({ fingerprint: fp }) });
  toast(result.reused ? "Card re-linked and identity checked" : "Card registered to this day");
  scan(); refresh();
}

async function ingestCard(fp) {
  toast("Card in — copying to every destination and verifying every byte…");
  try {
    await api("/api/ingest", { method: "POST", body: JSON.stringify({ fingerprint: fp }) });
  } catch { return; }
  scan(); refresh();
}

async function offload(id, mode) {
  await api(`/api/offload/${id}` + (mode ? `?mode=${mode}` : ""), { method: "POST" });
  refresh();
}

async function cancelJob() {
  proxyQueueActive = false;
  $("proxyStopButton").hidden = true;
  await api("/api/cancel", { method: "POST" });
}

async function manifest(id) {
  const j = await api(`/api/card/${id}/manifest`, { method: "POST" });
  toast(`Wrote ${j.paths.length} manifest file(s)`);
  // A chain warning means a manifest already on that drive has been altered
  // since it was written. It is the whole reason the chain exists, so it gets
  // its own message rather than being folded into the count above.
  (j.warnings || []).forEach(w => toast(w, true));
}

async function analyze(id) {
  toast("Reading media metadata and sound reports…");
  const result = await api(`/api/card/${id}/analyze`, { method: "POST" });
  const matched = result.sound_rows_matched
    ? ` · ${result.sound_rows_matched} sound-report row(s) matched` : "";
  toast(`${result.ready} of ${result.total} media files analyzed${matched}`,
    result.errors && result.errors.length);
  refresh();
}

async function buildProxies(id, rebuild) {
  toast(rebuild ? "Rebuilding ProRes proxies…" : "Building ProRes proxies from verified masters…");
  await api(`/api/proxy/${id}` + (rebuild ? "?rebuild=1" : ""), { method: "POST" });
  refresh();
}

let activeDayId = null;
let proxyQueueActive = false;
let proxyQueueLastFinished = null;

async function syncDay() {
  if (!activeDayId) return;
  toast("Matching camera and sound by embedded timecode…");
  const result = await api(`/api/day/${activeDayId}/sync`, { method: "POST" });
  const synced = (result.matched || 0) + (result.multiple || 0);
  const attention = (result.ambiguous || 0) + (result.unmatched || 0) + (result.invalid || 0);
  toast(`${synced} of ${result.total} camera clips matched` +
    (attention ? ` · ${attention} need attention` : "") +
    ((result.warnings || []).length ? ` · ${result.warnings.length} warning(s)` : ""),
    Boolean(attention));
  refresh();
}

async function waveformDay() {
  if (!activeDayId) return;
  toast("Starting waveform fallback from verified masters…");
  await api(`/api/day/${activeDayId}/waveform`, { method: "POST" });
  refresh();
}

async function buildAllProxies() {
  if (!activeDayId) return;
  proxyQueueActive = true;
  $("proxyStopButton").hidden = false;
  toast("Building proxies for every eligible roll, one at a time…");
  await advanceProxyQueue();
}

function stopProxyQueue() {
  proxyQueueActive = false;
  $("proxyStopButton").hidden = true;
  toast("Will stop once the current roll finishes.");
}

async function advanceProxyQueue() {
  if (!proxyQueueActive || !activeDayId) return;
  try {
    const j = await api(`/api/day/${activeDayId}/proxies/next`, { method: "POST" });
    if (j.done) {
      proxyQueueActive = false;
      $("proxyStopButton").hidden = true;
      toast("All eligible proxies are built.");
    }
  } catch {
    proxyQueueActive = false;
    $("proxyStopButton").hidden = true;
  }
  refresh();
}

async function buildHandoff(rebuild) {
  if (!activeDayId) return;
  toast(rebuild ? "Rebuilding editorial handoff…" : "Building Resolve handoff and review dailies…");
  await api(`/api/day/${activeDayId}/handoff` + (rebuild ? "?rebuild=1" : ""),
    { method: "POST" });
  refresh();
}

function openHandoffNotes() {
  if (activeDayId) window.open(`/api/day/${activeDayId}/handoff/notes`, "_blank");
}

function openReport(id) { window.open(`/api/day/${id}/report`, "_blank"); }

function openPdf(layout) {
  if (!activeDayId) return toast("Start a shoot day first.");
  if (layout === "thumbnail") {
    // The first thumbnail report decodes one frame per clip off the verified
    // primary drive. Say so, rather than letting a long quiet wait look like
    // a hang. After that the frames are cached and it is instant.
    toast("Pulling a frame from each clip — the first run takes a moment.");
  }
  window.open(`/api/day/${activeDayId}/report.pdf?layout=${layout}`, "_blank");
}

// The logo can be picked, dropped, or typed. All three end at the same place:
// the server decodes it, checks it really is an image, re-encodes it as PNG and
// keeps its own copy — so a logo cannot vanish mid-shoot when the drive it came
// from is unmounted.
const MAX_LOGO_BYTES = 12 * 1024 * 1024;
let logoPreviewKey = null;

function pickLogo() { $("logoFile").click(); }

function logoChosen(input) {
  const file = input.files && input.files[0];
  input.value = "";
  if (file) sendLogo(file);
}

async function sendLogo(file) {
  if (!file.type.startsWith("image/")) {
    return toast(`${file.name} is not an image.`, true);
  }
  if (file.size > MAX_LOGO_BYTES) {
    return toast("That image is larger than 12 MB. A report logo does not need to be.", true);
  }
  toast(`Reading ${file.name}…`);
  let dataUrl;
  try {
    dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("The file could not be read."));
      reader.readAsDataURL(file);
    });
  } catch (err) {
    return toast(String(err.message || err), true);
  }
  try {
    const result = await api("/api/report-branding", {
      method: "POST",
      body: JSON.stringify({
        production: $("reportProduction").value.trim(),
        logo_data: dataUrl,
      }),
    });
    $("reportLogo").value = result.logo || "";
    logoPreviewKey = null;
    toast("Logo saved.");
  } catch (err) {
    // api() has already shown the reason.
  }
  refresh();
}

async function removeLogo() {
  try {
    await api("/api/report-branding", {
      method: "POST",
      body: JSON.stringify({
        production: $("reportProduction").value.trim(), clear_logo: true }),
    });
    $("reportLogo").value = "";
    logoPreviewKey = null;
    toast("Logo removed. Nothing of yours was deleted — only this program's copy.");
  } catch (err) {
    // api() has already shown the reason.
  }
  refresh();
}

function wireLogoDrop() {
  const zone = $("logoDrop");
  if (!zone) return;
  const stop = event => { event.preventDefault(); event.stopPropagation(); };
  ["dragenter", "dragover"].forEach(name => zone.addEventListener(name, event => {
    stop(event);
    zone.classList.add("over");
  }));
  ["dragleave", "dragend"].forEach(name => zone.addEventListener(name, event => {
    stop(event);
    zone.classList.remove("over");
  }));
  zone.addEventListener("drop", event => {
    stop(event);
    zone.classList.remove("over");
    const file = event.dataTransfer && event.dataTransfer.files
      && event.dataTransfer.files[0];
    if (file) sendLogo(file);
  });
  // A file dropped anywhere else would otherwise navigate the whole interface
  // away to that file, which mid-offload is alarming even though it is safe.
  ["dragover", "drop"].forEach(name =>
    window.addEventListener(name, event => {
      if (!zone.contains(event.target)) event.preventDefault();
    }));
}

async function saveBranding() {
  const production = $("reportProduction").value.trim();
  const logo = $("reportLogo").value.trim();
  try {
    await api("/api/report-branding",
              { method: "POST", body: JSON.stringify({ production, logo }) });
    toast("Report branding saved.");
  } catch (err) {
    toast(String(err.message || err));
  }
  refresh();
}

// The PDF is an addition, never a replacement: if reportlab is absent the HTML
// day report is still there and still authoritative. Say that plainly instead
// of leaving two dead buttons.
function renderReports(state) {
  const note = $("pdfNote");
  const pdf = state.pdf || {};
  const brand = state.branding || {};
  const focused = document.activeElement;
  if (focused !== $("reportProduction")) $("reportProduction").value = brand.production || "";
  if (focused !== $("reportLogo")) $("reportLogo").value = brand.logo || "";

  const preview = $("logoPreview");
  const dropText = $("logoDropText");
  const removeButton = $("logoRemove");
  if (brand.logo) {
    if (logoPreviewKey !== brand.logo) {
      logoPreviewKey = brand.logo;
      preview.src = `/api/report-branding/logo?v=${Date.now()}`;
    }
    preview.hidden = false;
    removeButton.hidden = false;
    dropText.textContent = "Drop another image to replace it";
  } else {
    logoPreviewKey = null;
    preview.hidden = true;
    preview.removeAttribute("src");
    removeButton.hidden = true;
    dropText.textContent = "Drop a logo image here, or choose one above";
  }

  const ready = Boolean(pdf.available && activeDayId);
  $("pdfTableButton").disabled = !ready;
  $("pdfMetaButton").disabled = !ready;
  $("pdfThumbButton").disabled = !ready;

  if (!pdf.available) {
    note.className = "note warn";
    note.textContent = pdf.reason || "PDF reports are unavailable on this machine.";
  } else if (!activeDayId) {
    note.className = "note";
    note.textContent = "Start a shoot day to produce a report.";
  } else {
    note.className = "note";
    note.textContent = "The PDF and the HTML report are generated from one reading of "
      + "the catalog, so they cannot disagree about whether a card is safe to format.";
  }
}

// -- render ----------------------------------------------------------------
// ffmpeg is optional and external, and its absence is easy to misread as the
// tool being broken. Say plainly which half still works, because the half that
// still works is the half that decides whether a card can be formatted.
// Presets — saved drives and report branding, grouped into folders.
// Applying one is reported item by item rather than as a single yes or no:
// three drives where two are mounted is an ordinary morning, and refusing the
// two that are there because the third is not would be actively unhelpful.
let presetFolderChoice = "0";

function renderPresets(state) {
  const groups = state.presets || [];
  const box = $("presetList");
  const rows = [];
  for (const folder of groups) {
    if (folder.presets.length === 0 && folder.id === 0) continue;
    rows.push(`<div class="preset-folder">
      <span>${esc(folder.name)}</span>
      ${folder.id ? `<button class="btn ghost sm" title="Delete this folder; its presets are kept"
        onclick="deletePresetFolder(${folder.id})">Remove folder</button>` : ""}
    </div>`);
    if (!folder.presets.length) {
      rows.push(`<div class="row"><span class="grow empty">Empty</span></div>`);
      continue;
    }
    for (const preset of folder.presets) {
      const drives = preset.destinations.length;
      const detail = preset.destinations
        .map(d => `${esc(d.label)} (${esc(d.role)})`).join(" · ");
      rows.push(`<div class="row">
        <span class="grow">
          <b>${esc(preset.name)}</b>
          <span class="path">${drives} drive${drives === 1 ? "" : "s"} · ${detail}</span>
        </span>
        <button class="btn ghost sm" onclick="applyPreset(${preset.id})">Apply</button>
        <button class="btn ghost sm" onclick="deletePreset(${preset.id})">Delete</button>
      </div>`);
    }
  }
  box.innerHTML = rows.length
    ? `<div class="list">${rows.join("")}</div>`
    : `<p class="empty">No presets yet. Set up a day's drives, then save it here.</p>`;

  const select = $("presetFolder");
  if (document.activeElement !== select) {
    select.innerHTML = groups.map(f =>
      `<option value="${f.id}">${esc(f.id ? f.name : "No folder")}</option>`).join("");
    select.value = groups.some(f => String(f.id) === presetFolderChoice)
      ? presetFolderChoice : "0";
  }
}

async function savePreset() {
  const name = $("presetName").value.trim();
  if (!name) return toast("Name the preset first.", true);
  presetFolderChoice = $("presetFolder").value;
  try {
    await api("/api/preset", { method: "POST", body: JSON.stringify({
      name, folder_id: Number(presetFolderChoice) }) });
    $("presetName").value = "";
    toast(`Saved "${name}".`);
  } catch (err) { /* api() has already said why */ }
  refresh();
}

async function addPresetFolder() {
  const name = $("presetFolderName").value.trim();
  if (!name) return toast("Name the folder first.", true);
  try {
    await api("/api/preset-folder", { method: "POST",
                                      body: JSON.stringify({ name }) });
    $("presetFolderName").value = "";
    toast(`Folder "${name}" added.`);
  } catch (err) { /* api() has already said why */ }
  refresh();
}

async function deletePresetFolder(id) {
  await api(`/api/preset-folder/${id}`, { method: "DELETE" });
  toast("Folder removed. Its presets were kept.");
  refresh();
}

async function deletePreset(id) {
  await api(`/api/preset/${id}`, { method: "DELETE" });
  refresh();
}

async function applyPreset(id) {
  let result;
  try {
    result = await api(`/api/preset/${id}/apply`, { method: "POST" });
  } catch (err) {
    return;
  }
  const lines = [];
  if (result.attached.length) {
    lines.push(`<b>Attached:</b> ${result.attached.map(d => esc(d.label)).join(", ")}.`);
  }
  if (result.skipped.length) {
    lines.push(`Already on this day: ${result.skipped.map(d => esc(d.label)).join(", ")}.`);
  }
  for (const failure of result.failed) {
    lines.push(`<b>${esc(failure.label)} was not added:</b> ${esc(failure.reason)}`);
  }
  for (const warning of result.warnings) lines.push(esc(warning));
  if (result.project) {
    lines.push(`This preset was saved on project <b>${esc(result.project)}</b>. `
      + "The day you are on keeps its own project name.");
  }
  const note = $("presetResult");
  note.hidden = lines.length === 0;
  note.className = (result.failed.length || result.warnings.length)
    ? "note warn" : "note";
  note.innerHTML = lines.join("<br>");
  toast(result.attached.length
    ? `Applied "${esc(result.preset)}".`
    : `Nothing new to attach from "${esc(result.preset)}".`);
  refresh();
}

// Naming. {original} is the default, so nothing is renamed unless someone asks
// for it, and the preview exists because a pattern nobody looked at is a
// pattern nobody understood.
// The checksum belongs to the shoot day, because a manifest covers a day and
// every hash in it has to be the same kind of thing.
function renderChecksum(state) {
  const checksum = state.checksum || {};
  const select = $("checksumChoice");
  const choices = checksum.choices || [];
  const signature = choices.map(c => c.id).join(",");
  if (select.dataset.signature !== signature) {
    select.dataset.signature = signature;
    select.innerHTML = choices.map(c =>
      `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("");
  }
  if (document.activeElement !== select) select.value = checksum.current || "xxh64";
  const chosen = choices.find(c => c.id === (checksum.current || "xxh64"));
  // Say which manifests a choice actually produces. XXH3, XXH128 and C4 have
  // no element name in ASC MHL 1.1, so a day using one gets the 2.0 ascmhl
  // folder and no 1.1 sidecar — which is a thing to know before the shoot, not
  // after it.
  $("checksumNote").textContent = chosen
    ? `${chosen.note} Written as <${chosen.mhl2_element}> in `
      + `${(chosen.manifests || []).join(" and ")}.`
    : "";
}

async function saveChecksum() {
  if (!activeDayId) return toast("Start a shoot day first.", true);
  try {
    await api(`/api/day/${activeDayId}/checksum`, { method: "POST",
      body: JSON.stringify({ hash_algo: $("checksumChoice").value }) });
    toast("Checksum saved for this day.");
  } catch (err) { /* api() has already said why, including why it was refused */ }
  refresh();
}

function renderNaming(state) {
  const naming = state.naming || {};
  if (document.activeElement !== $("patternCamera")) {
    $("patternCamera").value = naming.pattern_camera || "";
  }
  if (document.activeElement !== $("patternSound")) {
    $("patternSound").value = naming.pattern_sound || "";
  }
  if (document.activeElement !== $("filterCamera")) {
    $("filterCamera").value = naming.filter_camera || "";
  }
  if (document.activeElement !== $("filterSound")) {
    $("filterSound").value = naming.filter_sound || "";
  }
  const filtering = [naming.filter_camera, naming.filter_sound]
    .filter(f => (f || "").trim());
  const warn = $("filterWarning");
  warn.hidden = filtering.length === 0;
  warn.textContent = filtering.length
    ? "A filter is active. Files it skips are not copied, are listed on the card "
      + "and in the report, and the card will read CLEARED WITH EXCLUSIONS rather "
      + "than SAFE TO FORMAT — because it will not be fully copied."
    : "";
  $("namingTokens").innerHTML = "Tokens: "
    + (naming.tokens || []).map(t => `<code>{${esc(t)}}</code>`).join(" ")
    + `. <code>${esc(naming.default || "{original}")}</code> keeps camera names.`;
}

async function saveNaming() {
  if (!activeDayId) return toast("Start a shoot day first.", true);
  try {
    await api(`/api/day/${activeDayId}/naming`, { method: "POST",
      body: JSON.stringify({
        pattern_camera: $("patternCamera").value.trim(),
        pattern_sound: $("patternSound").value.trim(),
        filter_camera: $("filterCamera").value.trim(),
        filter_sound: $("filterSound").value.trim(),
      }) });
    toast("Naming saved.");
    previewNaming();
  } catch (err) { /* api() has already said why */ }
  refresh();
}

async function previewNaming() {
  const box = $("namingPreview");
  if (!activeDayId) return toast("Start a shoot day first.", true);
  let result;
  try {
    result = await api(`/api/day/${activeDayId}/naming/preview`);
  } catch (err) { return; }
  if (!result.cards.length) {
    box.innerHTML = '<p class="empty">Register a card to see what it would be called.</p>';
    return;
  }
  box.innerHTML = result.cards.map(card => {
    const clashes = card.collisions.map(clash =>
      `<div class="scan-problem">${esc(clash.name)} would be written twice —
        from ${clash.sources.map(esc).join(" and ")}. Change the pattern so it
        tells them apart; the offload will not start like this.</div>`).join("");
    const rows = card.sample.map(item => `<div class="row">
        <span class="grow path">${esc(item.from)}</span>
        <span class="path">${item.renamed ? "→ " + esc(item.to) : "unchanged"}</span>
      </div>`).join("");
    const skipped = card.skipped_total ? `<div class="scan-problem">
        ${card.skipped_total} file(s) would not be copied:
        ${card.skipped.map(esc).join(", ")}${card.skipped_total > card.skipped.length
          ? " …" : ""}. This card will read CLEARED WITH EXCLUSIONS, not SAFE TO
        FORMAT.</div>` : "";
    const more = card.total > card.sample.length
      ? `<div class="row"><span class="grow empty">…and ${card.total - card.sample.length} more</span></div>`
      : "";
    return `<div class="preset-folder"><span>${esc(card.label)} ·
        ${card.renamed} of ${card.total} renamed</span></div>
      ${clashes}${skipped}<div class="list">${rows}${more}</div>`;
  }).join("");
}

// Drive tools. The speed number is the point: every competitor leads with
// MB/s and this program could not previously answer the question at all.
function renderDrives(state) {
  const drives = state.drives || [];
  const box = $("driveList");
  box.innerHTML = drives.length ? `<div class="list">${drives.map(d => {
    const seen = d.indexed_at
      ? `${d.indexed_files} files · ${humanBytes(d.indexed_bytes)} · indexed ${esc(d.indexed_at.slice(0, 10))}`
      : "never indexed";
    const rolls = (d.manifests || []).slice(0, 6)
      .map(m => esc([m.day_name, m.roll].filter(Boolean).join(" ") || m.rel_path))
      .join(", ");
    return `<div class="row">
      <span class="grow">
        <b>${esc(d.label)}</b>
        <span class="tag ${d.online ? "known" : ""}">${d.online ? "online" : "offline"}</span>
        <span class="path">${seen}${rolls ? " · holds " + rolls : ""}</span>
      </span>
      <button class="btn ghost sm" onclick="forgetDrive(${d.id})">Forget</button>
    </div>`;
  }).join("")}</div>`
    : '<p class="empty">No drives indexed yet. Index one to remember what it holds.</p>';

  const job = state.job;
  const note = $("driveResult");
  if (job && job.job_type === "drive" && job.result && job.state === "done") {
    const r = job.result;
    if (job.mode === "speed") {
      note.hidden = false;
      note.className = r.cache_bypassed ? "note" : "note warn";
      note.innerHTML = `<b>Write ${r.write_mb_s} MB/s · read ${r.read_mb_s} MB/s</b>
        over ${humanBytes(r.bytes)}. ${r.verified ? "Read back and checksum-matched."
          : "<b>The read-back did not match what was written.</b>"}
        ${esc(r.note)}`;
    } else {
      note.hidden = false;
      note.className = "note";
      note.innerHTML = `Indexed <b>${esc(r.label || "")}</b>: ${r.files} files,
        ${humanBytes(r.bytes)}${(r.manifests || []).length
          ? `, ${r.manifests.length} manifest(s) read` : ""}.`;
    }
  }
}

function humanBytes(n) {
  n = Number(n) || 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i += 1; }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

async function driveSpeed() {
  const path = $("drivePath").value.trim();
  if (!path) return toast("Give it a drive to test.", true);
  try {
    await api("/api/drive/speed", { method: "POST",
                                    body: JSON.stringify({ path }) });
    toast("Speed test running — writing, then reading it back.");
  } catch (err) { /* api() has already said why */ }
  refresh();
}

async function driveIndex() {
  const path = $("drivePath").value.trim();
  if (!path) return toast("Give it a drive to index.", true);
  try {
    await api("/api/drive/index", { method: "POST", body: JSON.stringify({
      path, label: $("driveLabel").value.trim() }) });
    toast("Indexing — reading the drive, and any manifests already on it.");
  } catch (err) { /* api() has already said why */ }
  refresh();
}

async function forgetDrive(id) {
  await api(`/api/drive/${id}`, { method: "DELETE" });
  toast("Forgotten from the registry. Nothing on the drive was touched.");
  refresh();
}

function renderTools(tools) {
  const box = $("toolStatus");
  if (!box) return;
  const t = tools || {};
  if (t.ffmpeg && t.ffprobe) {
    box.className = "foot-line foot-tools";
    box.innerHTML = `ffmpeg found at <b>${esc(t.ffmpeg)}</b> · metadata, proxies,
      timecode sync, waveform fallback and dailies are available`;
    return;
  }
  const missing = [!t.ffmpeg && "ffmpeg", !t.ffprobe && "ffprobe"]
    .filter(Boolean).join(" and ");
  box.className = "foot-line foot-tools missing";
  box.innerHTML = `<b>${esc(missing)} not found.</b>&nbsp; Offload, verification,
    checksums, MHL manifests and the format gate are unaffected — a card that
    clears is still safe to format. Metadata, proxies, timecode sync, waveform
    fallback and dailies are unavailable until it is installed
    (<code>brew install ffmpeg</code>); it is picked up without restarting.`;
}

function renderJob(job) {
  const p = $("jobPanel");
  if (!job) { p.hidden = true; return; }
  p.hidden = false;
  const active = job.state === "queued" || job.state === "running";
  const isProxy = job.job_type === "proxy";
  const isWaveform = job.job_type === "waveform";
  const isHandoff = job.job_type === "handoff";
  const jobName = isProxy ? "proxy job" : (isWaveform ? "waveform sync" :
    (isHandoff ? "editorial handoff" : "offload job"));
  $("jobPanel").querySelector("h2").textContent = (active ? "Active " : "Last ") + jobName;
  $("cancelButton").hidden = !active;
  $("jobFile").textContent = job.current_file || job.message || "";
  $("jobFill").style.width = job.percent + "%";
  $("jobPhase").textContent = (job.mode === "verify" ? "verify · " : "") + (job.phase || job.state);
  $("jobPct").textContent = job.percent + "%";
  $("jobBytes").textContent = isWaveform ? "verified masters · metadata only" : ((isProxy || isHandoff)
    ? duration(job.seconds_done) + " / " + duration(job.seconds_total)
    : bytes(job.bytes_done) + " / " + bytes(job.bytes_total));
  $("jobFiles").textContent = job.files_done + " / " + job.files_total + " files";
  $("jobErrors").innerHTML =
    (job.errors || []).map(e => `<div>${esc(e)}</div>`).join("") +
    (job.warnings || []).map(w => `<div class="warn">${esc(w)}</div>`).join("");
}

function statusLine(elId, cls, text) {
  const el = $(elId);
  el.className = "status-line " + cls;
  el.querySelector(".text").textContent = text;
}

function renderHandoff(summary, syncSummary, day, job) {
  const detail = $("handoffDetail");
  const button = $("handoffButton");
  const rebuild = $("handoffRebuild");
  const notes = $("handoffNotes");
  const jobActive = job && ["queued", "running"].includes(job.state);
  rebuild.hidden = true;
  notes.hidden = true;
  if (!day) {
    statusLine("handoffState", "idle", "Start a shoot day to build a handoff.");
    detail.textContent = "Resolve timeline · ALE · relink manifest · review dailies";
    button.disabled = true;
    return;
  }
  const total = Number(summary?.total || 0);
  const ready = Number(summary?.ready || 0);
  const unresolved = Number(summary?.unresolved || 0);
  const synced = Number(syncSummary?.analyzed || 0);
  button.disabled = !summary?.available || !total || synced !== total || jobActive;
  button.textContent = summary?.package_ready ? "Check handoff" : "Build Resolve handoff";
  if (!summary?.available) {
    statusLine("handoffState", "bad", "Handoff unavailable on this machine");
    detail.textContent = summary?.unavailable_reason || "Required dailies tools are unavailable";
  } else if (!total) {
    statusLine("handoffState", "idle", "No camera clips catalogued yet.");
  } else if (synced !== total) {
    statusLine("handoffState", "warn", "Sync day's footage before handoff");
    detail.textContent = `${synced} / ${total} camera clips analyzed · unresolved clips may still be packaged after sync`;
  } else if (summary?.package_ready) {
    statusLine("handoffState", unresolved ? "warn" : "ok",
      unresolved
        ? `Package ready · ${unresolved} sync review item${unresolved === 1 ? "" : "s"}`
        : `Package ready · ${ready} / ${total} review dailies`);
    detail.textContent = `${summary.fcpxml_version} FCPXML · ${bytes(summary.output_bytes || 0)}` +
      (summary.bundle_path ? ` · ${summary.bundle_path}` : "");
    rebuild.hidden = false;
    rebuild.disabled = jobActive;
    notes.hidden = false;
  } else if (summary?.state === "failed" || summary?.problems) {
    statusLine("handoffState", "bad", `Handoff incomplete · ${summary.problems || 0} review problem(s)`);
    detail.textContent = summary.error || "Run the handoff again after correcting the problem";
  } else if (summary?.state === "stale") {
    statusLine("handoffState", "warn", "Handoff is stale · rebuild after media or sync changes");
    detail.textContent = summary.bundle_path || "The earlier package no longer matches the catalog";
    rebuild.hidden = false;
    rebuild.disabled = jobActive;
  } else {
    statusLine("handoffState", "idle", `Not built · ${total} camera clip${total === 1 ? "" : "s"}`);
    detail.textContent = "Resolve timeline · ALE · JSON relink manifest · checksummed review dailies";
  }
}

function renderSync(summary, waveform, day, job) {
  const detail = $("syncDetail");
  const button = $("syncButton");
  const waveformButton = $("waveformButton");
  const jobActive = job && ["queued", "running"].includes(job.state);
  button.disabled = !day || jobActive;
  button.textContent = summary?.waveform
    ? "Reset with timecode sync"
    : (summary && summary.analyzed ? "Re-sync day's footage" : "Sync day's footage");
  detail.textContent = "Relationships only · originals and proxies untouched";
  if (!day) {
    waveformButton.disabled = true;
    statusLine("syncState", "idle", "Start a shoot day to calculate sync.");
    return;
  }
  const total = Number(summary?.total || 0);
  const analyzed = Number(summary?.analyzed || 0);
  const synced = Number(summary?.matched || 0) + Number(summary?.multiple || 0);
  const attention = Number(summary?.ambiguous || 0) + Number(summary?.unmatched || 0) +
    Number(summary?.invalid || 0) + Number(summary?.review || 0);
  const waveformPending = Number(waveform?.pending || 0);
  waveformButton.disabled = !waveform?.available || !analyzed || !waveformPending || jobActive;
  waveformButton.textContent = !waveform?.available ? "Waveform unavailable" :
    (!waveformPending && analyzed ? "Waveforms checked" :
      (attention ? "Waveform fallback" : "Check TC waveforms"));
  if (!total) {
    statusLine("syncState", "idle", "No camera clips catalogued yet.");
  } else if (!analyzed) {
    statusLine("syncState", "idle", `Not run · ${total} camera clip${total === 1 ? "" : "s"}`);
  } else if (!attention && analyzed === total) {
    statusLine("syncState", "ok", `${synced} / ${total} camera clips matched`);
    const methods = [summary.timecode ? `${summary.timecode} TC` : "",
      summary.waveform ? `${summary.waveform} waveform` : "",
      waveform?.confirmed ? `${waveform.confirmed} TC waveform-confirmed` : "",
      waveform?.problems ? `${waveform.problems} waveform warning(s)` : ""
    ].filter(Boolean).join(" · ");
    detail.textContent = `${methods || `${summary.links || 0} links`} · relationships only · originals and proxies untouched`;
  } else {
    statusLine("syncState", "warn", `${synced} / ${total} matched · ${attention} need attention`);
    const issues = [
      summary.ambiguous ? `${summary.ambiguous} ambiguous` : "",
      summary.review ? `${summary.review} review` : "",
      summary.unmatched ? `${summary.unmatched} unmatched` : "",
      summary.invalid ? `${summary.invalid} invalid TC` : ""
    ].filter(Boolean).join(" · ");
    const unavailable = !waveform?.available ? ` · ${waveform?.unavailable_reason || "waveform unavailable"}` : "";
    detail.textContent = `${issues || "Sync is incomplete"}${unavailable} · originals and proxies untouched`;
  }
}

function renderProxyDay(cards, day, job) {
  const button = $("proxyAllButton");
  const detail = $("proxyDayDetail");
  const jobActive = job && ["queued", "running"].includes(job.state);
  if (!day) {
    statusLine("proxyDayState", "idle", "Start a shoot day to build proxies.");
    button.disabled = true;
    return;
  }
  const eligible = (cards || []).filter(c => ["camera", "mixed"].includes(c.kind) && c.proxies?.total);
  const total = eligible.reduce((n, c) => n + (c.proxies.total || 0), 0);
  const ready = eligible.reduce((n, c) => n + (c.proxies.ready || 0), 0);
  const problems = eligible.reduce((n, c) => n + (c.proxies.problems || 0), 0);
  const unavailable = eligible.some(c => !c.proxies.available);
  button.disabled = !eligible.length || (ready === total && total > 0) || jobActive || proxyQueueActive || unavailable;
  if (proxyQueueActive) {
    statusLine("proxyDayState", "warn", `Building… ${ready} / ${total} clips ready`);
    detail.textContent = "Queuing every eligible roll automatically, one drive-safe job at a time";
  } else if (!eligible.length) {
    statusLine("proxyDayState", "idle", "No camera clips ready for proxies yet.");
    detail.textContent = "A card needs to clear SAFE TO FORMAT before it can proxy";
  } else if (unavailable) {
    statusLine("proxyDayState", "bad", "Proxies unavailable on this machine");
    detail.textContent = "Install ffmpeg with ProRes support to enable this phase";
  } else if (total && ready === total) {
    statusLine("proxyDayState", problems ? "warn" : "ok",
      problems ? `${ready} / ${total} ready · ${problems} warning(s)` : `${ready} / ${total} clips ready`);
    detail.textContent = "ProRes Proxy 1080p · relink-safe · originals untouched";
  } else {
    statusLine("proxyDayState", "warn", `${ready} / ${total} clips ready`);
    detail.textContent = "ProRes Proxy 1080p · relink-safe · originals untouched";
  }
}

function renderCards(cards, dayId) {
  const box = $("cardList");
  if (!cards.length) {
    box.innerHTML = '<p class="empty">Nothing registered yet. Scan for cards.</p>';
    return;
  }
  box.innerHTML = cards.map(c => {
    const g = c.gate;
    const m = c.metadata || {};
    const p = c.proxies || {};
    const y = c.sync || {};
    const metadataDone = (m.ready || 0) + (m.partial || 0);
    const mediaDuration = c.kind === "sound" ? m.audio_seconds : m.video_seconds;
    const metadataLine = m.total
      ? ` · metadata ${metadataDone}/${m.total}` +
        (m.updated_at ? ` · ${duration(mediaDuration)}` : "") +
        (m.partial ? ` · <b>${m.partial} partial</b>` : "") +
        (m.problems ? ` · <b>${m.problems} metadata warning(s)</b>` : "")
      : "";
    const proxyLine = p.total
      ? ` · proxy ${p.ready || 0}/${p.total}` +
        (p.output_bytes ? ` · ${bytes(p.output_bytes)}` : "") +
        (p.problems ? ` · <b>${p.problems} proxy warning(s)</b>` : "") +
        (!p.available ? " · <b>proxy unavailable</b>" : "")
      : "";
    const syncLine = y.total
      ? (y.updated_at
          ? ` · sync ${y.synced || 0}/${y.total}` +
            (y.ambiguous ? ` · <b>${y.ambiguous} ambiguous</b>` : "") +
            (y.review ? ` · <b>${y.review} review</b>` : "") +
            (y.unmatched ? ` · <b>${y.unmatched} unmatched</b>` : "") +
            (y.invalid ? ` · <b>${y.invalid} invalid TC</b>` : "") +
            (y.waveform ? ` · ${y.waveform} waveform` : "")
          : " · sync not run")
      : "";
    const canProxy = ["camera", "mixed"].includes(c.kind) && p.total && g.safe && p.available;
    const canAnalyze = c.mounted || g.safe;
    return `
    <div class="slate">
      <div class="slate-head">
        <span class="roll">${esc(c.roll || c.label)}</span>
        <span class="slate-meta">${esc(c.kind)} · ${c.file_count} files · ${bytes(c.total_bytes)}
          ${metadataLine}${proxyLine}${syncLine}${c.mounted ? "" : " · <b>card not mounted</b>"}</span>
      </div>
      <div class="verdict ${g.stamp ? esc(g.stamp.tone) : (g.safe ? "ok" : "bad")}">
        <span class="lamp"></span>
        <span class="verdict-text">${esc(g.stamp ? g.stamp.label
          : (g.safe ? "SAFE TO FORMAT" : "DO NOT FORMAT"))}
        <small>${esc(g.reason)}</small></span>
      </div>
      ${(g.excluded || []).length ? `<div class="excluded-list">
        Deliberately not copied: ${g.excluded.map(esc).join(", ")}</div>` : ""}
      <div class="slate-actions">
        <button class="btn sm ${g.safe ? "" : "go"}" onclick="offload(${c.id})"
          ${c.mounted ? "" : "disabled"}>
          ${c.status === "verified" ? "Resume offload" : "Offload"}</button>
        <button class="btn sm ghost" onclick="offload(${c.id},'verify')">Re-read drives</button>
        <button class="btn sm ghost" onclick="analyze(${c.id})" ${canAnalyze ? "" : "disabled"}>Read metadata</button>
        ${p.total ? `<button class="btn sm proxy" onclick="buildProxies(${c.id},false)"
          ${canProxy ? "" : "disabled"}>${p.ready === p.total ? "Check proxies" : "Build proxies"}</button>` : ""}
        ${p.ready ? `<button class="btn sm ghost" onclick="buildProxies(${c.id},true)"
          ${canProxy ? "" : "disabled"}>Rebuild proxies</button>` : ""}
        <button class="btn sm ghost" onclick="manifest(${c.id})" ${g.safe ? "" : "disabled"}>Write manifest</button>
        <button class="btn sm ghost" onclick="openReport(${dayId})">Day report</button>
        <span class="count">${g.verified} / ${g.total} verified</span>
      </div>
    </div>`;
  }).join("");
}

const ROLE_INFO = {
  primary: { title: "Drive A · primary", hint: "First physical drive — required" },
  backup: { title: "Drive B · backup", hint: "Second physical drive — required" },
  offsite: { title: "Cloud / offsite", hint: "A synced cloud folder, or a drive that leaves with you" },
};

function renderDestSlots(destinations) {
  const byRole = { primary: null, backup: null, offsite: null };
  for (const d of destinations) if (!byRole[d.role]) byRole[d.role] = d;
  $("destSlots").innerHTML = Object.entries(ROLE_INFO).map(([role, info]) => {
    const d = byRole[role];
    if (d) {
      return `
      <div class="slot filled role-${role}">
        <div class="slot-label"><span class="led"></span>${esc(info.title)}</div>
        <div class="slot-path">${esc(d.label)}</div>
        <div class="slot-path">${esc(d.path)}</div>
        <div class="slot-btns">
          <button class="btn ghost sm" onclick="openBrowse('${role}')">Change</button>
          <button class="btn ghost sm" onclick="delDest(${d.id})">Remove</button>
        </div>
      </div>`;
    }
    return `
    <div class="slot clickable role-${role}" onclick="openBrowse('${role}')">
      <div class="slot-label"><span class="led"></span>${esc(info.title)}</div>
      <div class="slot-hint">${esc(info.hint)}</div>
      <div class="slot-cta">Click to choose a drive or folder →</div>
    </div>`;
  }).join("");
}

// -- folder picker -----------------------------------------------------------
let browseRole = null;
let browsePath = null;
let browseParent = null;

async function openBrowse(role) {
  browseRole = role;
  $("browseTitle").textContent = "Choose a folder — " + ROLE_INFO[role].title;
  $("browseModal").hidden = false;
  await browseTo(role === "offsite" ? "home" : "volumes");
}

function closeBrowse() {
  $("browseModal").hidden = true;
  browseRole = null;
}

// Escape closes the folder picker, the way every other dialog on the machine
// behaves. The picker is never opened on its own, only by clicking a slot.
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape" && !$("browseModal").hidden) closeBrowse();
});

async function browseTo(path) {
  let j;
  try { j = await api(`/api/browse?path=${encodeURIComponent(path)}`); }
  catch { return; }
  browsePath = j.path;
  browseParent = j.parent;
  $("browsePath").textContent = j.path;
  $("browseUp").disabled = !j.parent;
  $("browseList").innerHTML = j.entries.length
    ? j.entries.map(e => `<div class="browse-row" data-path="${esc(e.path)}">
        <span class="browse-icon">▸</span>${esc(e.name)}</div>`).join("")
    : '<p class="empty">No subfolders here.</p>';
}

// Delegated once, not rebuilt with every list render — paths can contain
// characters (quotes, apostrophes) that are not safe to inline into an
// onclick="" attribute, so folder names travel through a data attribute
// instead of a generated JS call.
$("browseList").addEventListener("click", ev => {
  const row = ev.target.closest(".browse-row");
  if (row) browseTo(row.dataset.path);
});

function browseUp() {
  if (browseParent) browseTo(browseParent);
}

async function chooseCurrentFolder() {
  if (!browsePath || !browseRole) return;
  let state;
  try { state = await api("/api/state"); } catch { return; }
  const existing = (state.destinations || []).find(d => d.role === browseRole);
  if (existing) {
    try { await api(`/api/destination/${existing.id}`, { method: "DELETE" }); }
    catch { return; } // error toast already shown by api(); leave the modal open
  }
  const label = browsePath.split("/").filter(Boolean).pop() || browseRole;
  try {
    await api("/api/destination", { method: "POST", body: JSON.stringify({
      path: browsePath, label, role: browseRole
    })});
  } catch { return; }
  closeBrowse();
  refresh();
}

function computeSteps(s) {
  const day = s.day;
  const dests = s.destinations || [];
  const cards = s.cards || [];
  // Count physical drives, not rows: two folders on one disk are one place
  // for a copy to die, and the gate will refuse them at job time. An
  // unidentified destination falls back to counting as its own device so a
  // fresh catalog is never shown as worse than it is.
  const deviceKeys = new Set(dests.map((d, i) => d.device_id || `unidentified:${i}`));
  const drives = deviceKeys.size;
  const need = s.min_copies;
  const destsReady = drives >= need;
  const anyCards = cards.length > 0;
  const clearedCards = cards.filter(c => c.gate.safe).length;
  const allSafe = anyCards && clearedCards === cards.length;
  const camCards = cards.filter(c => ["camera", "mixed"].includes(c.kind));
  const syncTotal = s.sync?.total || 0;
  const syncAnalyzed = s.sync?.analyzed || 0;
  const syncAttention = (s.sync?.ambiguous || 0) + (s.sync?.unmatched || 0)
    + (s.sync?.invalid || 0) + (s.sync?.review || 0);
  const syncDone = syncTotal > 0 && syncAnalyzed === syncTotal && !syncAttention;
  const proxyTotal = camCards.reduce((n, c) => n + (c.proxies?.total || 0), 0);
  const proxyReady = camCards.reduce((n, c) => n + (c.proxies?.ready || 0), 0);
  const proxiesDone = proxyTotal > 0 && proxyReady === proxyTotal;
  const handoffDone = Boolean(s.handoff?.package_ready) && !s.handoff?.unresolved;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  const steps = [
    {
      key: "day", label: "Day", anchor: null, done: Boolean(day),
      hint: "Name the project and the day. Everything else hangs off it.",
    },
    {
      key: "dest", label: "Destinations", anchor: "destSlots", done: destsReady,
      blocked: Boolean(day) && !destsReady,
      count: `${drives}/${need}`,
      hint: destsReady
        ? `${plural(drives, "drive")} ready.`
        : `Add ${plural(need - drives, "more drive")}. A card cannot clear until every `
          + `file is verified on ${need} physically separate drives.`,
    },
    {
      key: "cards", label: "Ingest cards", anchor: "scanList", done: allSafe,
      count: anyCards ? `${clearedCards}/${cards.length}` : "",
      hint: !anyCards
        ? "Put a card in the reader and scan for it."
        : allSafe
          ? "Every card on this day has cleared. They are safe to format."
          : `${plural(cards.length - clearedCards, "card")} still to clear. `
            + "Nothing is safe to format until it does.",
    },
    {
      key: "sync", label: "Sync", anchor: "syncPanel", done: syncDone,
      count: syncTotal ? `${syncAnalyzed}/${syncTotal}` : "",
      hint: !syncTotal
        ? "Nothing to sync yet — sync runs on camera clips once a card has cleared."
        : syncAttention
          ? `${plural(syncAttention, "clip")} need a look before the handoff.`
          : "Match camera clips to sound by timecode. Originals are untouched.",
    },
    {
      key: "proxy", label: "Proxies", anchor: "proxyPanel", done: proxiesDone,
      count: proxyTotal ? `${proxyReady}/${proxyTotal}` : "",
      hint: proxyTotal
        ? "Build relink-safe ProRes proxies from the verified copies."
        : "Proxies build from camera clips once a card has cleared.",
    },
    {
      key: "handoff", label: "Handoff", anchor: "handoffPanel", done: handoffDone,
      count: s.handoff?.total ? `${s.handoff.ready}/${s.handoff.total}` : "",
      hint: s.handoff?.unresolved
        ? `${plural(s.handoff.unresolved, "sync exception")} to resolve before editorial.`
        : "Build the Resolve timeline, the ALE and the review dailies.",
    },
  ];
  const nowIndex = steps.findIndex(st => !st.done);
  return steps.map((st, i) => ({ ...st, now: i === nowIndex }));
}

// The rail under the header. One run of steps joined by a line, the current one
// filled so it reads from across a cart, and a plain sentence saying what to do
// next — because "which of these six things is my problem right now" is the
// question at 4am, and a row of lamps does not answer it.
// A phase that cannot run yet is dimmed, not hidden. Hiding controls mid-shoot
// makes people think the tool broke; leaving them looking live makes them think
// the tool is ignoring them. Dimmed, with the panel's own status line saying
// what unlocks it, is the honest middle.
function setWaiting(id, waiting) {
  const panel = $(id);
  if (panel) panel.classList.toggle("waiting", Boolean(waiting));
}

function renderSteps(s) {
  const steps = computeSteps(s);
  const parts = [];
  steps.forEach((st, i) => {
    if (i > 0) {
      parts.push(`<li class="track-link ${steps[i - 1].done ? "done" : ""}"
        aria-hidden="true"></li>`);
    }
    const classes = ["step", st.done ? "done" : "", st.now ? "now" : "",
                     st.blocked ? "blocked" : ""].filter(Boolean).join(" ");
    const jump = st.anchor
      ? `onclick="document.getElementById('${st.anchor}')?.scrollIntoView({behavior:'smooth',block:'center'})"`
      : "";
    const state = st.blocked ? "blocked" : st.done ? "done" : st.now ? "current" : "not started";
    parts.push(`
      <li>
        <button type="button" class="${classes}" ${jump}
          ${st.now ? 'aria-current="step"' : ""}
          aria-label="${esc(st.label)}${st.count ? `, ${esc(st.count)}` : ""}, ${state}">
          <span class="led" aria-hidden="true"></span>${esc(st.label)}
          ${st.count ? `<span class="step-count">${esc(st.count)}</span>` : ""}
        </button>
      </li>`);
  });
  $("stepTracker").innerHTML = parts.join("");

  const current = steps.find(st => st.now);
  const hint = $("trackHint");
  if (!current) {
    hint.className = "track-hint";
    hint.innerHTML = "<b>The day is complete.</b> Every card cleared, synced, "
      + "proxied and handed off.";
  } else {
    hint.className = `track-hint ${current.blocked ? "blocked" : ""}`;
    hint.innerHTML = `<b>Next — ${esc(current.label)}.</b> ${esc(current.hint)}`;
  }
}


async function refresh() {
  let s;
  try { s = await api("/api/state"); } catch { return false; }
  activeDayId = s.day ? s.day.id : null;

  $("dayRule").innerHTML = s.day
    ? `<b>${esc(s.day.project)}</b> / <b>${esc(s.day.name)}</b> · ${esc(s.day.shoot_date)}`
    : "No shoot day selected";

  $("daySwitch").innerHTML = s.days.map(d =>
    `<span class="chip ${s.day && d.id === s.day.id ? "on" : ""}"
       onclick="activateDay(${d.id})">${esc(d.name)}</span>`).join("");

  const need = s.min_copies;
  $("destNote").innerHTML = s.destinations.length >= need
    ? `${s.destinations.length} destinations. A card clears once every file verifies on ${need}.`
    : `<b style="color:var(--work)">${s.destinations.length} of ${need} required destinations.</b>
       Ingest is blocked until you add ${need - s.destinations.length} more.`;

  renderDestSlots(s.destinations, need);

  renderJob(s.job);
  renderSync(s.sync, s.waveform, s.day, s.job);
  renderProxyDay(s.cards, s.day, s.job);
  renderHandoff(s.handoff, s.sync, s.day, s.job);
  renderCards(s.cards, s.day ? s.day.id : 0);
  renderSteps(s);
  renderTools(s.tools);
  renderReports(s);
  renderPresets(s);
  renderNaming(s);
  renderChecksum(s);
  renderDrives(s);

  // Sync, proxies and dailies all read from verified copies, so none of them
  // can do anything until at least one card has cleared the gate.
  const anyCleared = (s.cards || []).some(c => c.gate && c.gate.safe);
  setWaiting("syncPanel", !anyCleared);
  setWaiting("proxyPanel", !anyCleared);
  setWaiting("handoffPanel", !anyCleared);
  setWaiting("reportPanel", !s.day);
  setWaiting("presetPanel", !s.day);
  setWaiting("namingPanel", !s.day);
  $("firstRun").hidden = (s.days || []).length > 0;

  if (proxyQueueActive && s.job && s.job.job_type === "proxy" &&
      ["done", "failed", "cancelled"].includes(s.job.state) && s.job.finished_epoch &&
      s.job.finished_epoch !== proxyQueueLastFinished) {
    proxyQueueLastFinished = s.job.finished_epoch;
    advanceProxyQueue();
  }

  const busy = Boolean(s.job && ["queued", "running"].includes(s.job.state));
  if (busy && pollTimer !== null) schedulePoll(POLL_ACTIVE);
  return busy;
}

const localNow = new Date();
localNow.setMinutes(localNow.getMinutes() - localNow.getTimezoneOffset());
$("dayDate").value = localNow.toISOString().slice(0, 10);
// Polling cadence. /api/state stats every verified copy on every connected
// drive, so a fixed 1.2s poll hammered the same disks an offload was writing
// to. Poll fast only while a job is genuinely running.
const POLL_ACTIVE = 1200;
const POLL_IDLE = 5000;
let pollTimer = null;

function schedulePoll(delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollOnce, delay);
}

async function pollOnce() {
  const busy = await refresh();
  schedulePoll(busy ? POLL_ACTIVE : POLL_IDLE);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { clearTimeout(pollTimer); pollOnce(); }
});

wireLogoDrop();
refresh().then(busy => schedulePoll(busy ? POLL_ACTIVE : POLL_IDLE));
