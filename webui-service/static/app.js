/* fnmusic-ext WebUI — 原生 JS，无框架无外部资产。 */
"use strict";

// 飞牛桌面用 HTTPS 打开管理窗，页面必须挂在同源路径 /app/fnmusic-ext 下。
// WebUI 自己也会剥掉这个前缀。管理接口只认飞牛网关注入的管理员头。
const APP_BASE = "/app/fnmusic-ext";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const PROVIDER_LABEL = { musicdl: "musicdl 聚合音源", musicbox: "网易云音乐盒子", lxmusic: "洛雪自定义源", none: "未配置" };
const PROC_LABEL = { musicdl: "musicdl", musicbox: "musicbox", lxmusic: "lxmusic", webui: "WebUI" };
const PLATFORM_LABEL = { tx: "QQ音乐", wy: "网易云", kw: "酷我", kg: "酷狗", mg: "咪咕" };
const ALL_PLATFORMS = ["tx", "wy", "kw", "kg", "mg"];
const CATEGORY_PLATFORMS = ["tx", "wy", "kw", "kg"];  // 咪咕无分类体系

// 平台歌单设置的多选状态（与 .env 的 csv 字段双向映射）
const state = {
  playlistSource: [],     // 选中的平台 code，如 ["tx","kg"]；空 = 不注入任何平台
  playlistCategory: [],   // 选中的分类，形如 ["tx:11","kg:12"]
  categoryData: null,     // /api/playlist-categories 的返回（懒加载一次）
};

let configValues = {};   // GET /api/config 的 values
let platforms = { enabled: [], registered: [] };
// 当前洛雪源实际支持的平台 code 列表（来自 /api/status 的 lx_source.source.platforms）。
// null = 尚未拿到源信息（非 lx 提供方 / 状态未加载）——此时推荐平台勾选框回退全量展示。
let lxSupported = null;
let dirty = false;
let qrTimer = null;
let lxVerifiedUrl = null; // 已通过测试的 lx URL（保存时免二次校验提示用）
let lxSourceList = [];    // 洛雪源列表 [{ name, url }]

async function api(path, options) {
  const resp = await fetch(APP_BASE + path, options);
  let body = {};
  try { body = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
  return body;
}

function toast(message, kind) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast " + (kind || "");
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 3800);
}

function markDirty(note) {
  dirty = true;
  const bar = $("#save-bar");
  if (bar) bar.classList.add("show");
  const noteEl = $("#save-note");
  if (noteEl) noteEl.textContent = note || "有未保存的修改";
}

function clearDirty() {
  dirty = false;
  const bar = $("#save-bar");
  if (bar) bar.classList.remove("show");
  const noteEl = $("#save-note");
  if (noteEl) noteEl.textContent = "";
}

/* -------------------------------------------------------------- 导航 */
function switchPage(page) {
  $$(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + page));
  $$("[data-page]").forEach((el) => el.classList.toggle("active", el.dataset.page === page));
}
$$("[data-page]").forEach((btn) => btn.addEventListener("click", () => switchPage(btn.dataset.page)));

/* -------------------------------------------------------------- 概览 */
async function loadStatus() {
  try {
    const st = await api("/api/status");
    $("#brand-version").textContent = `v${st.version}`;
    $("#sidebar-foot").textContent = `${PROVIDER_LABEL[st.current_provider] || st.current_provider}`;
    $("#ov-provider-body").innerHTML =
      `<span class="state-line"><span class="dot ok"></span>${PROVIDER_LABEL[st.current_provider] || st.current_provider}</span>`;
    $("#ov-processes").innerHTML = Object.entries(st.processes).map(([name, p]) => {
      const ok = p.state === "RUNNING";
      const cls = ok ? "ok" : p.state === "FATAL" ? "err" : "";
      return `<span class="chip"><span class="dot ${cls}"></span>${PROC_LABEL[name] || name} · ${p.state}</span>`;
    }).join("");
    $("#ov-services").innerHTML = Object.entries(st.services).map(([name, s]) => {
      if (!s.reachable && s.note) return `<span class="state-line"><span class="dot"></span>${PROC_LABEL[name]}：${s.note}</span>`;
      return `<span class="state-line"><span class="dot ${s.reachable ? "ok" : "err"}"></span>${PROC_LABEL[name]}：${s.reachable ? "正常" : "不可达"}</span>`;
    }).join("");
    const lxCard = $("#ov-lx-card");
    if (st.current_provider === "lxmusic" && st.lx_source) {
      lxCard.hidden = false;
      const s = st.lx_source.source;
      lxSupported = s ? Object.keys(s.platforms || {}) : [];
      const rows = [];
      rows.push(`<div class="kv"><b>状态</b>${st.lx_source.initialized ? "已加载" : "未加载"}</div>`);
      if (s) {
        rows.push(`<div class="kv"><b>源名称</b>${s.name || "-"} ${s.version ? "v" + s.version : ""}</div>`);
        rows.push(`<div class="kv"><b>平台</b>${Object.keys(s.platforms || {}).join("、") || "-"}</div>`);
        rows.push(`<div class="kv"><b>运行</b>${s.running ? "是" : "否"}</div>`);
      }
      if (st.lx_source.last_error) rows.push(`<div class="kv"><b>错误</b>${st.lx_source.last_error}</div>`);
      $("#ov-lx").innerHTML = rows.join("");
    } else {
      lxCard.hidden = true;
      lxSupported = null;
    }
    renderSourceChips();  // 用最新源支持信息重绘推荐平台勾选框（隐藏源不支持的平台）
  } catch (exc) {
    $("#ov-provider-body").textContent = "状态加载失败：" + exc.message;
  }
}

/* -------------------------------------------------------------- 配置读写 */
async function loadConfig() {
  const cfg = await api("/api/config");
  configValues = cfg.values;
  applyConfigToForm();
  clearDirty();
}

function applyConfigToForm() {
  const v = configValues;
  const provider = v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
  $$("input[name=provider]").forEach((el) => { el.checked = el.value === provider; });
  syncProviderPanels(provider);
  if (provider === "musicbox") syncNeteaseAccount();
  const quality = v.FNMUSIC_QUALITY_MODE || "high";
  $$("input[name=quality]").forEach((el) => { el.checked = el.value === quality; });
  $("#recommend-hot").checked = v.FNMUSIC_RECOMMEND_HOT === "true";
  $("#recommend-daily").checked = v.FNMUSIC_RECOMMEND_DAILY === "true";
  $("#recommend-playlist").checked = v.FNMUSIC_RECOMMEND_PLAYLIST === "true";
  state.playlistSource = splitCsv(v.FNMUSIC_RECOMMEND_PLAYLIST_SOURCE);
  state.playlistCategory = splitCsv(v.FNMUSIC_RECOMMEND_PLAYLIST_CATEGORY);
  renderSourceChips();
  ensureCategoryData().then(renderCategoryChips);
  updatePlaylistDetailVisibility();
  $("#recommend-playlist-size").value = v.FNMUSIC_RECOMMEND_PLAYLIST_PAGE_SIZE || "12";
  $("#tee-enabled").checked = v.FNMUSIC_TEE_SAVE_ENABLED === "true";
  $("#auto-cover").checked = v.FNMUSIC_AUTO_COVER !== "false";
  $("#cover-proxy").checked = v.FNMUSIC_COVER_PROXY !== "false";
  $("#lyric-auto-dl").checked = v.FNMUSIC_LYRIC_AUTO_DL === "true";
  $("#fav-autobind").checked = v.FNMUSIC_FAV_AUTO_BIND === "true";
  $("#tee-dir").value = v.FNMUSIC_TEE_SAVE_DIR || "";
  $("#tee-max").value = v.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#bind-timeout").value = v.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#handoff-max").value = v.FNMUSIC_TEE_HANDOFF_MAX != null ? v.FNMUSIC_TEE_HANDOFF_MAX : "3";
  $("#scan-path").value = v.FNMUSIC_LIBRARY_SCAN_PATH || "";
  updateTeeCountLabel();
  updateBindTimeoutLabel();
  $("#llm-base").value = v.FNMUSIC_LLM_BASE_URL || "";
  $("#llm-key").value = v.FNMUSIC_LLM_API_KEY || "";
  $("#llm-model").value = v.FNMUSIC_LLM_MODEL || "";
  $("#search-timeout").value = v.FNMUSIC_SEARCH_TIMEOUT || "15";
  $("#search-probe").checked = v.FNMUSIC_SEARCH_PROBE === "true";
  $("#search-deep").checked = v.FNMUSIC_SEARCH_DEEP_PAGE !== "false";
  $("#search-deep-max").value = v.FNMUSIC_SEARCH_DEEP_MAX_PAGES || "10";
  $("#netease-my-playlists").checked = v.FNMUSIC_NETEASE_MY_PLAYLISTS === "true";
  $("#lx-url").value = v.LX_SOURCE_URL || "";
  lxVerifiedUrl = v.LX_SOURCE_URL || null;
  try {
    const rawList = v.LX_SOURCE_LIST;
    lxSourceList = rawList ? JSON.parse(rawList) : [];
    if (!Array.isArray(lxSourceList)) lxSourceList = [];
  } catch (_) {
    lxSourceList = [];
  }
  const currentUrl = (v.LX_SOURCE_URL || "").trim();
  if (currentUrl && !lxSourceList.some((item) => item.url === currentUrl)) {
    lxSourceList.unshift({
      name: lxDefaultName(currentUrl),
      url: currentUrl,
    });
  }
  if ($("#lx-name")) {
    const matched = lxSourceList.find((item) => item.url === currentUrl);
    $("#lx-name").value = (matched && matched.name) || "";
  }
  renderLxSourceList();
  syncLxAddButton();
  renderPlatformChips();
}

function collectConfig() {
  const provider = ($$("input[name=provider]").find((el) => el.checked) || {}).value || "";
  const values = {
    FNMUSIC_MUSICDL_ENABLED: provider === "musicdl",
    FNMUSIC_NETEASE_ENABLED: provider === "musicbox",
    FNMUSIC_LX_ENABLED: provider === "lxmusic",
    FNMUSIC_QUALITY_MODE: ($$("input[name=quality]").find((el) => el.checked) || {}).value || "high",
    FNMUSIC_RECOMMEND_HOT: $("#recommend-hot").checked,
    FNMUSIC_RECOMMEND_DAILY: $("#recommend-daily").checked,
    FNMUSIC_RECOMMEND_PLAYLIST: $("#recommend-playlist").checked,
    FNMUSIC_RECOMMEND_PLAYLIST_SOURCE: state.playlistSource.join(","),
    FNMUSIC_RECOMMEND_PLAYLIST_CATEGORY: state.playlistCategory.join(","),
    FNMUSIC_RECOMMEND_PLAYLIST_PAGE_SIZE: parseInt($("#recommend-playlist-size").value || "12", 10) || 12,
    FNMUSIC_TEE_SAVE_ENABLED: $("#tee-enabled").checked,
    FNMUSIC_AUTO_COVER: $("#auto-cover").checked,
    FNMUSIC_COVER_PROXY: $("#cover-proxy").checked,
    FNMUSIC_LYRIC_AUTO_DL: $("#lyric-auto-dl").checked,
    FNMUSIC_FAV_AUTO_BIND: $("#fav-autobind").checked,
    FNMUSIC_TEE_SAVE_DIR: $("#tee-dir").value.trim(),
    FNMUSIC_TEE_CACHE_MAX: parseInt($("#tee-max").value || "2", 10),
    FNMUSIC_OFFICIAL_BIND_TIMEOUT_S: parseInt($("#bind-timeout").value || "120", 10) || 120,
    FNMUSIC_TEE_HANDOFF_MAX: parseInt($("#handoff-max").value || "3", 10) || 0,
    FNMUSIC_LIBRARY_SCAN_PATH: $("#scan-path").value.trim(),
    FNMUSIC_LLM_BASE_URL: $("#llm-base").value.trim(),
    FNMUSIC_LLM_API_KEY: $("#llm-key").value.trim(),
    FNMUSIC_LLM_MODEL: $("#llm-model").value.trim(),
    FNMUSIC_SEARCH_TIMEOUT: parseInt($("#search-timeout").value || "15", 10) || 15,
    FNMUSIC_SEARCH_PROBE: $("#search-probe").checked,
    FNMUSIC_SEARCH_DEEP_PAGE: $("#search-deep").checked,
    FNMUSIC_SEARCH_DEEP_MAX_PAGES: parseInt($("#search-deep-max").value || "10", 10) || 10,
    FNMUSIC_NETEASE_MY_PLAYLISTS: $("#netease-my-playlists").checked,
    LX_SOURCE_LIST: JSON.stringify(lxSourceList),
  };
  if (provider === "musicdl") {
    values.FNMUSIC_ONLINE_SOURCES = platforms.enabled.join(",");
    values.MUSICDL_SOURCES = platforms.enabled.join(",");
  }
  if (provider === "lxmusic") {
    const url = $("#lx-url").value.trim();
    if (!url) throw new Error("洛雪源需要填写脚本 URL");
    values.LX_SOURCE_URL = url;
  }
  return values;
}

async function saveConfig() {
  let values;
  try { values = collectConfig(); } catch (exc) { toast(exc.message, "fail"); return; }
  const btn = $("#save-btn");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    const result = await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    const failed = (result.actions || []).filter((a) => !a.ok);
    const parts = [];
    if (result.changed && result.changed.length) parts.push(`已保存 ${result.changed.length} 项`);
    (result.actions || []).forEach((a) => {
      if (a.kind === "process") parts.push(`进程 ${a.program} ${a.op} ${a.ok ? "成功" : "失败"}`);
      if (a.kind === "lx_activate") parts.push(`洛雪源激活${a.ok ? "成功" : "失败"}`);
    });
    if (failed.length) {
      toast((parts.join("；") || "") + ` —— ${failed.map((f) => f.error).join("；")}`, "fail");
    } else {
      toast(parts.join("；") || "配置无变化", "ok");
    }
    clearDirty();
    await loadConfig();
    await loadStatus();
  } catch (exc) {
    toast("保存失败：" + exc.message, "fail");
  } finally {
    btn.disabled = false;
    btn.textContent = "保存并生效";
  }
}
$("#save-btn").addEventListener("click", saveConfig);

/* -------------------------------------------------------------- 音源选择 */
function savedProvider() {
  const v = configValues;
  return v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
}

// 点选未启用的音源：临时拉起其进程供预览（不写配置；5 分钟内未保存自动停止）
async function startPreview(provider) {
  try {
    const r = await api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    if (r.preview) toast(`已临时启动 ${PROVIDER_LABEL[provider]}（预览）：5 分钟内未保存将自动停止`);
    return true;
  } catch (exc) {
    toast(`临时启动 ${PROVIDER_LABEL[provider]} 失败：${exc.message}`, "fail");
    return false;
  }
}

function syncProviderPanels(provider) {
  $$(".provider-card").forEach((el) => el.classList.toggle("selected", el.dataset.provider === provider));
  $("#panel-musicbox").hidden = provider !== "musicbox";
  $("#panel-musicdl").hidden = provider !== "musicdl";
  $("#panel-lxmusic").hidden = provider !== "lxmusic";
}
$$("input[name=provider]").forEach((el) =>
  el.addEventListener("change", async () => {
    syncProviderPanels(el.value);
    if (el.value === "musicbox") syncNeteaseAccount();
    markDirty("音源切换需保存后生效");
    if (el.value && el.value !== savedProvider() && await startPreview(el.value)) {
      if (el.value === "musicdl") await loadPlatforms(false);
    }
  }));

/* -------------------------------------------------------------- musicdl 平台 */
function setPlatformFallback() {
  platforms = { enabled: (configValues.FNMUSIC_ONLINE_SOURCES || "").split(",").filter(Boolean), registered: [] };
  $("#platform-note").textContent = "musicdl 进程未运行，暂无法获取平台列表（点选 musicdl 音源可临时启动预览）";
  renderPlatformChips();
}

async function fetchPlatforms() {
  const data = await api("/api/platforms");
  platforms = { enabled: data.enabled || [], registered: data.registered || [] };
  $("#platform-note").textContent = `共 ${platforms.registered.length} 个注册平台，已启用 ${platforms.enabled.length} 个`;
  renderPlatformChips();
}

async function loadPlatforms(autoPreview = true) {
  try {
    await fetchPlatforms();
  } catch (_) {
    // 进程未运行：autoPreview（用户点选/刷新触发）时临时拉起后重试；页面加载不自动拉起
    if (!autoPreview) { setPlatformFallback(); return; }
    try {
      if (await startPreview("musicdl")) await fetchPlatforms();
      else setPlatformFallback();
    } catch (_) {
      setPlatformFallback();
    }
  }
}

/* ------------------------------------------- 平台歌单：平台 / 分类多选 chip */

function splitCsv(raw) {
  return String(raw || "")
    .replace(/，/g, ",")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function updatePlaylistDetailVisibility() {
  // 总开关（平台推荐歌单）关闭时，隐藏平台/分类/数量这些细节设置，
  // 避免用户误以为「取消所有平台勾选」就能关闭功能。
  const wrap = $("#playlist-detail-wrap");
  const sw = $("#recommend-playlist");
  if (!wrap || !wrap.style || !sw) return;
  wrap.style.display = sw.checked ? "" : "none";
}

function visiblePlaylistPlatforms() {
  // 仅展示当前洛雪源实际支持的平台（来自 /api/status 的 lx_source.source.platforms）；
  // null/空 表示尚未拿到源信息（非 lx 提供方 / 状态未加载）——回退全量展示。
  if (lxSupported && lxSupported.length) {
    return ALL_PLATFORMS.filter((p) => lxSupported.includes(p));
  }
  return ALL_PLATFORMS;
}

function renderSourceChips() {
  const box = $("#playlist-source-chips");
  if (!box) return;
  const on = new Set(state.playlistSource);
  const visible = visiblePlaylistPlatforms();
  box.innerHTML = visible
    .map((p) => `<span class="chip ${on.has(p) ? "on" : ""}" data-src="${p}">${PLATFORM_LABEL[p]}<small>${p}</small></span>`)
    .join("");
  box.querySelectorAll(".chip[data-src]").forEach((chip) =>
    chip.addEventListener("click", () => {
      const p = chip.dataset.src;
      const set = new Set(state.playlistSource);
      if (set.has(p)) set.delete(p); else set.add(p);
      state.playlistSource = visible.filter((x) => set.has(x));
      // 取消平台时，顺手清掉该平台已选分类，避免残留无效配置
      if (!set.has(p)) {
        state.playlistCategory = state.playlistCategory.filter((c) => !c.startsWith(p + ":"));
      }
      renderSourceChips();
      renderCategoryChips();
      markDirty("平台歌单已修改");
    }));
  // 提示：被当前音源不支持而隐藏的平台（如 mg），避免用户误勾导致静默失败
  const hintEl = $("#playlist-source-hint");
  if (hintEl) {
    const hidden = ALL_PLATFORMS.filter((p) => !visible.includes(p));
    hintEl.textContent = hidden.length
      ? `当前洛雪源不支持 ${hidden.map((p) => PLATFORM_LABEL[p] || p).join("、")}，已自动隐藏（勾了也不会注入）。`
      : "勾选要注入的平台（至少选 1 个）。一个都不勾选 = 不注入任何推荐歌单。";
  }
}

async function ensureCategoryData() {
  if (state.categoryData) return state.categoryData;
  try {
    state.categoryData = await api("/api/playlist-categories");
  } catch (err) {
    state.categoryData = { ok: false, categories: {}, error: String(err && err.message || err) };
  }
  return state.categoryData;
}

function renderCategoryChips() {
  const body = $("#playlist-category-body");
  if (!body) return;
  const data = state.categoryData;
  if (!data) { body.innerHTML = `<p class="hint">正在加载分类…</p>`; return; }
  if (data.ok === false && !Object.keys(data.categories || {}).length) {
    body.innerHTML = `<p class="hint">分类加载失败：${data.error || "未知错误"}（不影响保存，留空即该平台「全部」）</p>`;
    return;
  }
  // 只展示「已勾选平台」的分类；未勾选任何平台 = 不注入，不展示分类
  if (!state.playlistSource.length) {
    body.innerHTML = `<p class="hint">未勾选任何平台 —— 不会注入任何推荐歌单，无需设置分类。</p>`;
    return;
  }
  const active = state.playlistSource.filter((p) => CATEGORY_PLATFORMS.includes(p));
  if (!active.length) {
    body.innerHTML = `<p class="hint">所选平台均无分类体系（咪咕不支持分类），无需设置。</p>`;
    return;
  }
  const on = new Set(state.playlistCategory);
  const blocks = active.map((p) => {
    const cats = (data.categories || {})[p] || [];
    if (!cats.length) {
      return `<div class="cat-block"><div class="cat-head">${PLATFORM_LABEL[p] || p}<small>${p}</small></div>
        <p class="hint">该平台分类暂不可用</p></div>`;
    }
    // 按 group 分组渲染，保留平台原生分类体系
    const groups = [];
    cats.forEach((c) => {
      const g = c.group || "分类";
      let slot = groups.find((x) => x.name === g);
      if (!slot) { slot = { name: g, items: [] }; groups.push(slot); }
      slot.items.push(c);
    });
    const inner = groups.map((g) => `
      <div class="cat-group"><span class="cat-group-name">${g.name}</span>
        <div class="chip-grid">${g.items.map((c) => {
          const key = `${p}:${c.id}`;
          return `<span class="chip ${on.has(key) ? "on" : ""}" data-cat="${key}">${c.name}</span>`;
        }).join("")}</div>
      </div>`).join("");
    return `<div class="cat-block"><div class="cat-head">${PLATFORM_LABEL[p] || p}<small>${p}</small></div>${inner}</div>`;
  });
  body.innerHTML = blocks.join("");
  body.querySelectorAll(".chip[data-cat]").forEach((chip) =>
    chip.addEventListener("click", () => {
      const key = chip.dataset.cat;
      const set = new Set(state.playlistCategory);
      if (set.has(key)) set.delete(key); else set.add(key);
      state.playlistCategory = Array.from(set);
      chip.classList.toggle("on");
      markDirty("歌单分类已修改");
    }));
}

function renderPlatformChips() {
  const keyword = $("#platform-search").value.trim().toLowerCase();
  const container = $("#platform-list");
  const enabledSet = new Set(platforms.enabled);
  const items = platforms.registered.filter((p) => !keyword || p.toLowerCase().includes(keyword));
  container.innerHTML = items.length
    ? items.map((p) => `<span class="chip ${enabledSet.has(p) ? "on" : ""}" data-platform="${p}">${p}</span>`).join("")
    : `<span class="muted">无匹配平台</span>`;
  container.querySelectorAll(".chip[data-platform]").forEach((chip) =>
    chip.addEventListener("click", () => {
      const p = chip.dataset.platform;
      const set = new Set(platforms.enabled);
      if (set.has(p)) { set.delete(p); chip.classList.remove("on"); }
      else { set.add(p); chip.classList.add("on"); }
      platforms.enabled = Array.from(set);
      markDirty("musicdl 平台已修改");
    }));
}
$("#platform-search").addEventListener("input", renderPlatformChips);
$("#platform-reload").addEventListener("click", loadPlatforms);

/* -------------------------------------------------------------- 网易扫码 */
async function syncNeteaseAccount(retries = 0) {
  // 把网易账号态同步到 #qr-check（已登录显示昵称；未登录/失败清空）
  for (let i = 0; ; i++) {
    try {
      const st = await api("/api/netease/auth/status");
      const d = st.data || st;
      if (d.logged_in) {
        $("#qr-check").textContent = `当前登录：${d.nickname || d.user_id || "已登录用户"}`;
        return;
      }
    } catch (_) { /* status 不可达视为未登录 */ }
    if (i >= retries) { $("#qr-check").textContent = ""; return; }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function startQrLogin() {
  stopQrPolling();
  $("#qr-status").textContent = "正在生成二维码…";
  $("#qr-img").hidden = true;
  syncNeteaseAccount(); // 生成前同步当前账号态（非致命，不阻塞出码）
  const callLogin = () => api("/api/netease/auth/login", { method: "POST" });
  try {
    let data;
    try {
      data = await callLogin();
    } catch (exc) {
      // musicbox 进程未运行（未点选/预览过期）：临时拉起后重试一次
      if (!await startPreview("musicbox")) throw exc;
      data = await callLogin();
    }
    const unikey = data.unikey || data.codekey || (data.data && (data.data.unikey || data.data.codekey)) || "";
    if (!unikey) throw new Error("未获取到 unikey");
    $("#qr-img").src = `${APP_BASE}/api/netease/qr?unikey=${encodeURIComponent(unikey)}`;
    $("#qr-img").hidden = false;
    $("#qr-status").textContent = "请用手机网易云音乐 App 扫码";
    pollQr(unikey);
  } catch (exc) {
    $("#qr-status").textContent = "生成失败：" + exc.message;
  }
}

async function checkQrStatus(unikey) {
  const st = await api(`/api/netease/auth/login/check?unikey=${encodeURIComponent(unikey)}`);
  // musicbox CLI 返回 {ok, data:{code}} 信封结构；兼容扁平 {code}
  const code = st?.data?.code ?? st?.code;
  if (code === 803) {
    stopQrPolling();
    $("#qr-status").textContent = "登录成功 ✓";
    syncNeteaseAccount(2); // 803 后 cookie 落盘需要一点时间，带重试取昵称
    toast("网易账号登录成功", "ok");
  } else if (code === 802) {
    $("#qr-status").textContent = "已扫码，请在手机上确认";
  } else if (code === 800) {
    stopQrPolling();
    $("#qr-status").textContent = "二维码已过期，请重新生成";
  } else {
    $("#qr-status").textContent = "等待扫码…";
  }
}

function pollQr(unikey, intervalMs = 2000) {
  stopQrPolling();
  qrTimer = setInterval(() => { checkQrStatus(unikey).catch(() => {}); }, intervalMs);
}

function stopQrPolling() {
  if (qrTimer) { clearInterval(qrTimer); qrTimer = null; }
}
$("#qr-btn").addEventListener("click", startQrLogin);

/* -------------------------------------------------------------- lx 源测试 */
function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function lxDefaultName(url) {
  if (!url) return "自定义源";
  try {
    const raw = url.split("?")[0].split("#")[0];
    const segment = raw.split("/").filter(Boolean).pop() || "lx-source";
    return decodeURIComponent(segment);
  } catch (_) {
    return "自定义源";
  }
}

function syncLxAddButton() {
  const url = ($("#lx-url")?.value || "").trim();
  const addBtn = $("#lx-add");
  if (addBtn) addBtn.disabled = !url;
}

function renderLxSourceList() {
  const container = $("#lx-source-list");
  if (!container) return;
  const currentUrl = ($("#lx-url")?.value || "").trim();

  if (!lxSourceList.length) {
    container.innerHTML = `<span class="muted pad">暂无洛雪音乐源，上传 .js 或填写脚本地址后点击「添加」加入列表</span>`;
    return;
  }

  container.innerHTML = lxSourceList.map((item, idx) => {
    const isActive = item.url === currentUrl;
    const displayName = escapeHtml(item.name || lxDefaultName(item.url));
    const safeUrl = escapeHtml(item.url);
    if (isActive) {
      return `
        <div class="lx-item on">
          <div class="lx-item-main">
            <div class="lx-item-header">
              <span class="lx-item-name">${displayName}</span>
              <span class="lx-badge">已激活</span>
            </div>
            <div class="lx-item-url" title="${safeUrl}">${safeUrl}</div>
          </div>
        </div>`;
    }
    return `
      <div class="lx-item">
        <div class="lx-item-main">
          <div class="lx-item-header">
            <span class="lx-item-name">${displayName}</span>
          </div>
          <div class="lx-item-url" title="${safeUrl}">${safeUrl}</div>
        </div>
        <div class="lx-item-btns">
          <button class="btn small lx-btn-activate" data-idx="${idx}">激活</button>
          <button class="btn small danger lx-btn-del" data-idx="${idx}">删除</button>
        </div>
      </div>`;
  }).join("");

  container.querySelectorAll(".lx-btn-activate").forEach((btn) => {
    btn.addEventListener("click", () => activateLxSource(parseInt(btn.dataset.idx, 10)));
  });
  container.querySelectorAll(".lx-btn-del").forEach((btn) => {
    btn.addEventListener("click", () => deleteLxSource(parseInt(btn.dataset.idx, 10)));
  });
}

function activateLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item) return;
  $("#lx-url").value = item.url;
  if ($("#lx-name")) $("#lx-name").value = item.name || "";
  lxVerifiedUrl = null;
  renderLxSourceList();
  syncLxAddButton();
  markDirty("已切换激活源，需点击下方「保存并生效」");
  toast(`已激活「${item.name || lxDefaultName(item.url)}」，请点击下方「保存并生效」`, "ok");
}

function deleteLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item) return;
  const name = item.name || lxDefaultName(item.url);
  lxSourceList.splice(idx, 1);
  renderLxSourceList();
  markDirty("洛雪源列表已修改，需保存后生效");
  toast(`已删除「${name}」`, "ok");
}

$("#lx-test").addEventListener("click", async () => {
  const url = $("#lx-url").value.trim();
  const box = $("#lx-report");
  if (!url) { toast("请先填写源 URL", "fail"); return; }
  box.hidden = false;
  box.className = "report";
  box.textContent = "测试中（下载脚本 → 沙箱初始化 → 多首抽样搜索/解析/探活）…";
  const callVerify = () => api("/api/lx/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { url } }),
  });
  try {
    let r;
    try {
      r = await callVerify();
    } catch (exc) {
      // lxmusic 进程未运行（未点选/预览过期）：临时拉起后重试一次
      if (!await startPreview("lxmusic")) throw exc;
      r = await callVerify();
    }
    if (r.ok) {
      const d = r.data || {};
      const meta = d.meta || {};
      const probe = d.probe || {};
      lxVerifiedUrl = url;
      if (meta.name && $("#lx-name") && !$("#lx-name").value.trim()) {
        $("#lx-name").value = meta.name;
      }
      box.className = "report ok";
      box.innerHTML =
        `<div class="kv"><b>源名称</b>${meta.name || "-"} ${meta.version ? "v" + meta.version : ""}（${meta.author || "未知作者"}）</div>` +
        `<div class="kv"><b>可用平台</b>${(d.platforms || []).join("、")}</div>` +
        (probe.title ? `<div class="kv"><b>实测</b>${probe.title} - ${probe.artist} [${probe.platform}/${probe.quality}] ${probe.content_type || ""}</div>` : "") +
        `<div class="kv"><b>结论</b>可用 ✓（保存后激活）</div>`;
    } else {
      const d = r.data || {};
      box.className = "report fail";
      box.innerHTML = `<div class="kv"><b>不可用</b>${d.message || r.error || "校验失败"}</div>`;
    }
  } catch (exc) {
    box.className = "report fail";
    box.textContent = "测试失败：" + exc.message;
  }
});

/* -------------------------------------------------- lx 源：文件上传 / NAS 选择 */
async function lxUploadScript(filename, script) {
  // 先确保 lxmusic 进程可用（预览拉起），再转发落盘
  const callUpload = () => api("/api/lx/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, script }),
  });
  try {
    return await callUpload();
  } catch (exc) {
    if (!await startPreview("lxmusic")) throw exc;
    return await callUpload();
  }
}

async function lxAfterUpload(r) {
  const d = r.data || {};
  $("#lx-url").value = d.url || "";
  lxVerifiedUrl = null; // 上传地址仍需走一次"测试"
  const scriptName = (d.meta && d.meta.name) || "";
  if (scriptName && $("#lx-name") && !$("#lx-name").value.trim()) {
    $("#lx-name").value = scriptName;
  }
  $("#lx-upload-note").textContent = scriptName ? `已上传：${scriptName}` : "已上传";
  syncLxAddButton();
  renderLxSourceList();
  markDirty("洛雪源已更新为上传脚本，测试后保存生效");
  toast("脚本已上传，请点「测试」验证或点击「添加」加入列表", "ok");
}

$("#lx-upload").addEventListener("click", () => $("#lx-file").click());
$("#lx-file").addEventListener("change", async () => {
  const file = $("#lx-file").files && $("#lx-file").files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".js")) { toast("只支持 .js 后缀文件", "fail"); return; }
  if (file.size > 9_000_000) { toast("脚本超过 9MB 上限", "fail"); return; }
  $("#lx-upload-note").textContent = "读取并上传中…";
  try {
    const script = await file.text();
    const r = await lxUploadScript(file.name, script);
    await lxAfterUpload(r);
  } catch (exc) {
    $("#lx-upload-note").textContent = "";
    toast("上传失败：" + exc.message, "fail");
  } finally {
    $("#lx-file").value = ""; // 允许重复选择同一文件
  }
});

/* NAS 文件选择：仅桌面环境（统一网关 /app/fnmusic-ext 内）可用。
   选中的主机路径经 /api/host-file 代读（webui_gateway 本地处理），再走上传落盘。 */
let lxTrimSdk = null;
async function lxLoadTrimSdk() {
  if (lxTrimSdk !== null) return lxTrimSdk;
  try {
    const mod = await import("/app/fnmusic-ext/static/vendor/trim-web-app.js");
    lxTrimSdk = new mod.TrimApp();
  } catch (_) {
    lxTrimSdk = false;
  }
  return lxTrimSdk;
}

async function lxPickFromNas() {
  const sdk = await lxLoadTrimSdk();
  if (!sdk) { toast("当前环境不支持 NAS 文件选择（直连 8774 时请用上传或 URL）", "fail"); return; }
  try {
    const result = await sdk.pickUserFile({
      directory: false,
      accept: [".js"],
      title: "选择洛雪源脚本",
      okText: "选择",
      sidebarGroup: ["myFiles", "otherShare", "favorites"],
    });
    const paths = (result && result.data) || [];
    if (!paths.length) return;
    const hostPath = paths[0];
    $("#lx-upload-note").textContent = "读取 NAS 文件中…";
    const resp = await fetch(APP_BASE + "/api/host-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: hostPath }),
    });
    let body = {};
    try { body = await resp.json(); } catch (_) { /* 非 JSON */ }
    if (!resp.ok) throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
    const filename = hostPath.split("/").pop() || "source.js";
    const r = await lxUploadScript(filename, body.script || "");
    await lxAfterUpload(r);
  } catch (exc) {
    $("#lx-upload-note").textContent = "";
    toast("NAS 选择失败：" + exc.message, "fail");
  }
}
$("#lx-pick").addEventListener("click", lxPickFromNas);

$("#lx-add").addEventListener("click", () => {
  const url = ($("#lx-url")?.value || "").trim();
  if (!url) {
    toast("请先填写脚本地址或上传 .js 文件", "fail");
    return;
  }
  const nameInput = ($("#lx-name")?.value || "").trim();
  const name = nameInput || lxDefaultName(url);

  const existingIdx = lxSourceList.findIndex((item) => item.url === url);
  if (existingIdx >= 0) {
    lxSourceList[existingIdx].name = name;
    toast(`已更新列表中该源的名称为「${name}」`, "ok");
  } else {
    lxSourceList.push({ name, url });
    toast(`已添加「${name}」至洛雪源列表`, "ok");
  }
  if ($("#lx-name")) $("#lx-name").value = "";
  renderLxSourceList();
  markDirty("洛雪源列表已修改，需保存后生效");
});

(async function detectNasPicker() {
  // 桌面网关路径下才尝试加载 SDK；探测失败（直连 8774）保持隐藏
  const pathname = (typeof window !== "undefined" && window.location && window.location.pathname) || "";
  if (pathname.startsWith("/app/")) {
    const sdk = await lxLoadTrimSdk();
    if (sdk) $("#lx-pick").hidden = false;
  }
})();

/* -------------------------------------------------------------- 表单脏标记 */
["#tee-dir", "#tee-max", "#llm-base", "#llm-key", "#llm-model", "#search-timeout", "#search-deep-max", "#bind-timeout", "#handoff-max", "#scan-path"].forEach((sel) =>
  $(sel).addEventListener("input", () => markDirty()));
$("#lx-url").addEventListener("input", () => {
  syncLxAddButton();
  renderLxSourceList();
  markDirty();
});
if ($("#lx-name")) {
  $("#lx-name").addEventListener("input", () => markDirty());
}
$$("input[name=quality]").forEach((el) => el.addEventListener("change", () => markDirty("音质偏好需保存后生效")));
["#recommend-hot", "#recommend-daily", "#recommend-playlist", "#search-probe", "#search-deep", "#tee-enabled", "#fav-autobind", "#auto-cover", "#lyric-auto-dl", "#netease-my-playlists"].forEach((sel) =>
  $(sel).addEventListener("change", () => markDirty()));
$("#recommend-playlist").addEventListener("change", updatePlaylistDetailVisibility);
["#recommend-playlist-size"].forEach((sel) =>
  $(sel).addEventListener("input", () => markDirty()));

function updateTeeCountLabel() {
  const n = $("#tee-max").value || configValues.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#tee-count-label").textContent = `（缓存数 ${n} 首）`;
}
$("#tee-max").addEventListener("input", updateTeeCountLabel);

function updateBindTimeoutLabel() {
  const n = $("#bind-timeout").value || configValues.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#bind-timeout-label").textContent = `（${n} 秒）`;
}
$("#bind-timeout").addEventListener("input", updateBindTimeoutLabel);

window.addEventListener("beforeunload", (ev) => {
  if (dirty) ev.preventDefault();
});

/* -------------------------------------------------------------- 启动 */
(async function boot() {
  await loadConfig();
  await loadStatus();
  await loadPlatforms(false);  // 页面加载不自动拉起预览进程，等用户点选音源
  setInterval(loadStatus, 15000);
})();
