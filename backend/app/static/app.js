const state = {
  files: [],
  jobId: localStorage.getItem("hanall:lastJob") || null,
  products: [],
  allProducts: [],
  currentProduct: null,
  crop: [0.1, 0.1, 0.9, 0.9],
  cropDirty: false,
  polling: null,
};

const $ = (selector) => document.querySelector(selector);
const uploadView = $("#uploadView");
const progressView = $("#progressView");
const resultsView = $("#resultsView");
const fileInput = $("#fileInput");
const dropZone = $("#dropZone");
const selectedFiles = $("#selectedFiles");
const uploadEmpty = $("#uploadEmpty");
const startButton = $("#startButton");
const productGrid = $("#productGrid");
const editModal = $("#editModal");
const pagePreview = $("#pagePreview");
const cropStage = $("#cropStage");
const cropBox = $("#cropBox");

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function showView(view) {
  [uploadView, progressView, resultsView].forEach((node) => node.classList.add("hidden"));
  view.classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

let toastTimer;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `요청 실패 (${response.status})`;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => addFiles([...fileInput.files]));
["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
});
dropZone.addEventListener("drop", (event) => addFiles([...event.dataTransfer.files]));

function addFiles(files) {
  const pdfs = files.filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
  if (pdfs.length !== files.length) toast("PDF가 아닌 파일은 제외했습니다.", true);
  for (const file of pdfs) {
    const duplicate = state.files.some((item) => item.file.name === file.name && item.file.size === file.size);
    if (!duplicate && state.files.length < 20) {
      state.files.push({ id: crypto.randomUUID(), file, brand: "", collection: "" });
    }
  }
  if (state.files.length >= 20 && pdfs.length) toast("한 번에 최대 20개까지 선택할 수 있습니다.");
  fileInput.value = "";
  renderSelectedFiles();
}

function renderSelectedFiles() {
  uploadEmpty.classList.toggle("hidden", state.files.length > 0);
  startButton.disabled = state.files.length === 0;
  selectedFiles.innerHTML = state.files.map((item) => `
    <div class="file-item" data-file-id="${item.id}">
      <div class="file-name"><strong title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</strong><small>${formatBytes(item.file.size)}</small></div>
      <label class="mini-label">브랜드<input data-field="brand" placeholder="자동 추정" value="${escapeHtml(item.brand)}"></label>
      <label class="mini-label">컬렉션<input data-field="collection" placeholder="자동 추정" value="${escapeHtml(item.collection)}"></label>
      <button class="remove-file" type="button" aria-label="파일 제거">×</button>
    </div>
  `).join("");
  selectedFiles.querySelectorAll(".file-item").forEach((row) => {
    const item = state.files.find((file) => file.id === row.dataset.fileId);
    row.querySelectorAll("input").forEach((input) => {
      input.addEventListener("input", () => { item[input.dataset.field] = input.value; });
    });
    row.querySelector(".remove-file").addEventListener("click", () => {
      state.files = state.files.filter((file) => file.id !== row.dataset.fileId);
      renderSelectedFiles();
    });
  });
}

startButton.addEventListener("click", async () => {
  if (!state.files.length) return;
  startButton.disabled = true;
  const form = new FormData();
  state.files.forEach((item) => form.append("files", item.file, item.file.name));
  form.append("metadata", JSON.stringify(state.files.map((item) => ({ brand: item.brand, collection: item.collection }))));
  showView(progressView);
  setProgress({ progress: 0, current_step: "PDF 업로드 중", processed_pages: 0, total_pages: 0, catalogs: [] });
  try {
    const data = await api("/api/jobs", { method: "POST", body: form });
    state.jobId = data.job_id;
    localStorage.setItem("hanall:lastJob", state.jobId);
    pollJob();
  } catch (error) {
    toast(error.message, true);
    showView(uploadView);
    startButton.disabled = false;
  }
});

function setProgress(job) {
  const percent = Math.round((job.progress || 0) * 100);
  $("#progressBar").style.width = `${percent}%`;
  $("#progressPercent").textContent = `${percent}%`;
  $("#progressStep").textContent = job.current_step || "분석 준비 중";
  $("#progressPages").textContent = `${job.processed_pages || 0} / ${job.total_pages || 0} 페이지`;
  $("#catalogProgress").innerHTML = (job.catalogs || []).map((catalog) => `
    <div class="catalog-progress-item">
      <span><strong>${escapeHtml(catalog.filename)}</strong> · ${escapeHtml(catalog.brand || "브랜드 자동 추정")}</span>
      <span class="${catalog.status === "failed" ? "failed" : ""}">${catalogStatus(catalog)}</span>
    </div>
  `).join("");
}

function catalogStatus(catalog) {
  const labels = { queued: "대기", processing: "분석 중", complete: `${catalog.page_count}페이지 완료`, failed: "분석 실패" };
  return labels[catalog.status] || catalog.status;
}

async function pollJob() {
  clearTimeout(state.polling);
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    if (job.status === "complete") {
      setProgress(job);
      await loadResults(job);
      return;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "카탈로그 분석에 실패했습니다.");
    }
    showView(progressView);
    setProgress(job);
    state.polling = setTimeout(pollJob, 900);
  } catch (error) {
    toast(error.message, true);
    showView(uploadView);
  }
}

async function loadResults(jobData = null) {
  const job = jobData || await api(`/api/jobs/${state.jobId}`);
  const data = await api(`/api/jobs/${state.jobId}/products?limit=2000`);
  state.allProducts = data.items;
  state.products = data.items;
  populateFilters(data.items);
  updateSummary(job, data.summary);
  renderProducts();
  $("#exportButton").href = `/api/jobs/${state.jobId}/export`;
  showView(resultsView);
}

function updateSummary(job, summary) {
  $("#metricProducts").textContent = Math.max(0, (summary.total || 0) - (summary.excluded || 0)).toLocaleString();
  $("#metricBrands").textContent = (summary.brands || 0).toLocaleString();
  $("#metricReview").textContent = (summary.needs_review || 0).toLocaleString();
  $("#metricDuplicates").textContent = (job.duplicates_removed || 0).toLocaleString();
}

function populateFilters(products) {
  const brands = [...new Set(products.map((item) => item.brand).filter(Boolean))].sort();
  const catalogs = [...new Set(products.map((item) => item.catalog).filter(Boolean))].sort();
  $("#brandFilter").innerHTML = `<option value="">모든 브랜드</option>${brands.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}`;
  $("#catalogFilter").innerHTML = `<option value="">모든 카탈로그</option>${catalogs.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}`;
}

function renderProducts() {
  $("#resultCount").textContent = `${state.products.length.toLocaleString()}개 제품`;
  $("#noResults").classList.toggle("hidden", state.products.length > 0);
  productGrid.innerHTML = state.products.map((product) => {
    const needsReview = product.confidence < .7 && product.status !== "reviewed";
    const excluded = product.status === "excluded";
    return `
      <article class="product-card ${excluded ? "excluded" : ""}" data-product-id="${product.id}">
        <div class="texture-wrap">
          <img src="${product.texture_url}" alt="${escapeHtml(product.product_code)} 샘플" loading="lazy">
          <span class="confidence-badge ${needsReview ? "review" : ""}">${excluded ? "제외됨" : needsReview ? "검수 필요" : `${Math.round(product.confidence * 100)}%`}</span>
          <i class="color-chip" style="background:${escapeHtml(product.dominant_color || "#ddd")}"></i>
        </div>
        <div class="product-info">
          <div class="product-code-row"><strong>${escapeHtml(product.product_code)}</strong><button class="edit-button" type="button" aria-label="수정"><svg viewBox="0 0 24 24"><path d="m4 20 4.2-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z"/><path d="m13.8 7.2 3 3"/></svg></button></div>
          <p>${escapeHtml(product.brand || "미지정")} · ${escapeHtml(product.collection || "기본 컬렉션")}</p>
          <small>${escapeHtml(product.source_pdf)} · ${product.page_number}p</small>
        </div>
      </article>
    `;
  }).join("");
  productGrid.querySelectorAll(".edit-button").forEach((button) => {
    button.addEventListener("click", () => openEditor(button.closest(".product-card").dataset.productId));
  });
}

let filterTimer;
async function applyFilters() {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(async () => {
    const params = new URLSearchParams({ limit: "2000" });
    const entries = [
      ["search", $("#searchInput").value.trim()],
      ["brand", $("#brandFilter").value],
      ["catalog", $("#catalogFilter").value],
      ["status", $("#statusFilter").value],
    ];
    entries.forEach(([key, value]) => value && params.set(key, value));
    try {
      const data = await api(`/api/jobs/${state.jobId}/products?${params}`);
      state.products = data.items;
      renderProducts();
    } catch (error) { toast(error.message, true); }
  }, 230);
}
["#searchInput", "#brandFilter", "#catalogFilter", "#statusFilter"].forEach((selector) => {
  $(selector).addEventListener(selector === "#searchInput" ? "input" : "change", applyFilters);
});

function openEditor(productId) {
  const product = [...state.products, ...state.allProducts].find((item) => item.id === productId);
  if (!product) return;
  state.currentProduct = product;
  state.crop = [...product.bbox];
  state.cropDirty = false;
  $("#editCode").value = product.product_code;
  $("#editBrand").value = product.brand || "";
  $("#editCatalog").value = product.catalog || "";
  $("#editCollection").value = product.collection || "";
  $("#editExcluded").checked = product.status === "excluded";
  $("#editConfidence").textContent = `${Math.round(product.confidence * 100)}% · ${product.method}`;
  $("#sourcePdf").textContent = product.source_pdf;
  $("#sourcePage").textContent = `${product.page_number} 페이지`;
  $("#texturePreview").src = `${product.texture_url}?v=${Date.now()}`;
  pagePreview.onload = positionCropBox;
  pagePreview.src = `${product.page_url}?v=${Date.now()}`;
  editModal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeEditor() {
  editModal.classList.add("hidden");
  document.body.style.overflow = "";
  state.currentProduct = null;
}
document.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", closeEditor));
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !editModal.classList.contains("hidden")) closeEditor(); });

function imageBounds() {
  const stageRect = cropStage.getBoundingClientRect();
  const imageRect = pagePreview.getBoundingClientRect();
  return {
    left: imageRect.left - stageRect.left,
    top: imageRect.top - stageRect.top,
    width: imageRect.width,
    height: imageRect.height,
  };
}

function positionCropBox() {
  if (!pagePreview.clientWidth) return;
  const image = imageBounds();
  const [x0, y0, x1, y1] = state.crop;
  cropBox.style.left = `${image.left + x0 * image.width}px`;
  cropBox.style.top = `${image.top + y0 * image.height}px`;
  cropBox.style.width = `${(x1 - x0) * image.width}px`;
  cropBox.style.height = `${(y1 - y0) * image.height}px`;
}
window.addEventListener("resize", positionCropBox);

let drag = null;
cropBox.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  cropBox.setPointerCapture(event.pointerId);
  drag = {
    mode: event.target.classList.contains("resize-handle") ? "resize" : "move",
    x: event.clientX,
    y: event.clientY,
    start: [...state.crop],
  };
});
cropBox.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const image = imageBounds();
  const dx = (event.clientX - drag.x) / image.width;
  const dy = (event.clientY - drag.y) / image.height;
  const [x0, y0, x1, y1] = drag.start;
  if (drag.mode === "move") {
    const width = x1 - x0;
    const height = y1 - y0;
    const nextX = Math.max(0, Math.min(1 - width, x0 + dx));
    const nextY = Math.max(0, Math.min(1 - height, y0 + dy));
    state.crop = [nextX, nextY, nextX + width, nextY + height];
  } else {
    state.crop = [x0, y0, Math.max(x0 + .025, Math.min(1, x1 + dx)), Math.max(y0 + .025, Math.min(1, y1 + dy))];
  }
  state.cropDirty = true;
  positionCropBox();
});
["pointerup", "pointercancel"].forEach((eventName) => cropBox.addEventListener(eventName, () => { drag = null; }));

$("#editForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.currentProduct) return;
  const productId = state.currentProduct.id;
  const submit = event.submitter;
  submit.disabled = true;
  try {
    await api(`/api/jobs/${state.jobId}/products/${productId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product_code: $("#editCode").value,
        brand: $("#editBrand").value,
        catalog: $("#editCatalog").value,
        collection: $("#editCollection").value,
        status: $("#editExcluded").checked ? "excluded" : "reviewed",
      }),
    });
    if (state.cropDirty) {
      await api(`/api/jobs/${state.jobId}/products/${productId}/crop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bbox: state.crop }),
      });
    }
    const job = await api(`/api/jobs/${state.jobId}`);
    const data = await api(`/api/jobs/${state.jobId}/products?limit=2000`);
    state.allProducts = data.items;
    state.products = data.items;
    updateSummary(job, data.summary);
    renderProducts();
    closeEditor();
    toast("수정 내용을 저장했습니다.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    submit.disabled = false;
  }
});

$("#newJobButton").addEventListener("click", () => {
  if (!confirm("새 추출 작업을 시작할까요? 현재 결과는 서버에 남아 있지만 이 화면에서는 새 작업으로 전환됩니다.")) return;
  localStorage.removeItem("hanall:lastJob");
  state.jobId = null;
  state.files = [];
  renderSelectedFiles();
  showView(uploadView);
});

$("#activateButton").addEventListener("click", async () => {
  if (!state.jobId) return;
  const button = $("#activateButton");
  button.disabled = true;
  try {
    const result = await api(`/api/jobs/${state.jobId}/activate`, { method: "POST" });
    button.textContent = "적용 완료";
    toast(result.message);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

async function resumeLastJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    if (job.status === "complete") await loadResults(job);
    else if (["queued", "processing"].includes(job.status)) {
      showView(progressView);
      setProgress(job);
      pollJob();
    } else {
      localStorage.removeItem("hanall:lastJob");
      state.jobId = null;
    }
  } catch (_) {
    localStorage.removeItem("hanall:lastJob");
    state.jobId = null;
  }
}

renderSelectedFiles();
resumeLastJob();
