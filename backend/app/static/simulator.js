const $ = (selector) => document.querySelector(selector);
const start = $("#start");
const editor = $("#editor");
const room = $("#room");
const canvas = $("#canvas");
const ctx = canvas.getContext("2d");
const hint = $("#hint");
const aiText = $("#aiText");
const loader = $("#loader");

let products = [];
let visibleProducts = [];
let selected = null;
let texture = new Image();
let points = [];
let manual = false;
let showAfter = true;
let wallMask = null;
let segmenter = null;
let modelLoading = false;
let currentDataUrl = "";

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2600);
}

async function loadProducts() {
  try {
    const response = await fetch("/api/catalog/products");
    if (!response.ok) throw new Error("자재 목록을 불러오지 못했습니다.");
    const data = await response.json();
    products = data.items || [];
    visibleProducts = products;
    $("#catalogCount").textContent = products.length.toLocaleString();
    $("#catalogMeta").textContent = products.length ? `${products.length.toLocaleString()}개 실제 품번 · 관리자 검수 DB` : "등록된 자재가 없습니다.";
    selected = products[0] || null;
    $("#emptyMaterials").classList.toggle("hidden", products.length > 0);
    renderMaterials();
    if (selected) loadTexture();
  } catch (error) {
    $("#catalogMeta").textContent = error.message;
    $("#emptyMaterials").classList.remove("hidden");
  }
}

function renderMaterials() {
  const box = $("#materials");
  box.innerHTML = "";
  visibleProducts.slice(0, 120).forEach((product) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `material ${selected?.id === product.id ? "selected" : ""}`;
    const image = document.createElement("img");
    image.src = product.texture_url;
    image.alt = `${product.product_code} 자재`;
    image.loading = "lazy";
    const code = document.createElement("b");
    code.textContent = product.product_code;
    const meta = document.createElement("small");
    meta.textContent = product.brand || product.collection || "자재";
    button.append(image, code, meta);
    button.addEventListener("click", () => {
      selected = product;
      loadTexture();
      renderMaterials();
      updateSelectedLabel();
    });
    box.appendChild(button);
  });
}

$("#materialSearch").addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  visibleProducts = !query ? products : products.filter((product) =>
    [product.product_code, product.brand, product.collection, product.catalog].some((value) => String(value || "").toLowerCase().includes(query))
  );
  renderMaterials();
});

function updateSelectedLabel() {
  $("#selectedLabel").textContent = selected?.product_code || "자재를 선택해 주세요";
  $("#selectedMeta").textContent = selected ? `${selected.brand || "미지정"} · ${selected.collection || selected.catalog || "카탈로그"}` : "실제 카탈로그 DB 연동";
}

function loadTexture() {
  if (!selected) return;
  texture = new Image();
  texture.onload = draw;
  texture.src = selected.texture_url;
  updateSelectedLabel();
}

function openPicker(input) { input.value = ""; input.click(); }
function setStatus(title, text, loading = false) {
  $("#aiStatus strong").textContent = title;
  aiText.textContent = text;
  loader.classList.toggle("hidden", !loading);
}

function loadPhoto(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (event) => {
    currentDataUrl = event.target.result;
    room.src = currentDataUrl;
    room.onload = async () => {
      canvas.width = room.naturalWidth;
      canvas.height = room.naturalHeight;
      points = []; wallMask = null; manual = false; showAfter = true;
      start.classList.add("hidden");
      editor.classList.remove("hidden");
      draw();
      await detectWall();
    };
  };
  reader.readAsDataURL(file);
}

async function getSegmenter() {
  if (segmenter) return segmenter;
  if (modelLoading) {
    while (modelLoading) await new Promise((resolve) => setTimeout(resolve, 200));
    return segmenter;
  }
  modelLoading = true;
  setStatus("AI 모델 불러오는 중", "최초 한 번은 모델 다운로드 시간이 필요합니다.", true);
  try {
    const { pipeline, env } = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.2");
    env.allowLocalModels = false;
    segmenter = await pipeline("image-segmentation", "Xenova/segformer-b0-finetuned-ade-512-512", {
      progress_callback: (progress) => {
        if (progress.status === "progress" && progress.total) aiText.textContent = `AI 모델 다운로드 ${Math.round(progress.loaded / progress.total * 100)}%`;
      },
    });
    return segmenter;
  } finally { modelLoading = false; }
}

async function detectWall() {
  if (!currentDataUrl) return;
  manual = false; points = []; wallMask = null; draw();
  hint.textContent = "AI가 벽을 찾는 중입니다...";
  setStatus("AI 분석 중", "벽, 바닥, 천장과 가구를 구분하고 있습니다.", true);
  try {
    const model = await getSegmenter();
    const result = await model(currentDataUrl, { subtask: "semantic" });
    const walls = result.filter((item) => String(item.label).toLowerCase().includes("wall"));
    if (!walls.length) throw new Error("벽 클래스를 찾지 못했습니다.");
    wallMask = mergeMasks(walls.map((item) => item.mask), canvas.width, canvas.height);
    const coverage = maskCoverage(wallMask);
    if (coverage < .025 || coverage > .85) throw new Error("인식 범위가 비정상적입니다.");
    setStatus("벽 자동 인식 완료", `사진의 약 ${Math.round(coverage * 100)}%를 벽으로 인식했습니다.`, false);
    hint.textContent = "벽 인식 완료 · 필요하면 4점 수동 보정을 사용하세요";
    draw();
  } catch (error) {
    console.error(error);
    wallMask = makeFallbackMask(canvas.width, canvas.height);
    setStatus("간편 자동 영역 적용", "모델 실행이 어렵거나 벽을 찾지 못했습니다. 4점 보정으로 정확히 수정할 수 있습니다.", false);
    hint.textContent = "간편 영역 적용 · 정확하지 않으면 4점 수동 보정";
    draw();
  }
}

function maskPixelValue(mask, index) {
  const channels = mask.channels || 1;
  const value = mask.data[index * channels];
  return value > 0 ? 255 : 0;
}

function mergeMasks(masks, width, height) {
  const merged = document.createElement("canvas");
  merged.width = width; merged.height = height;
  const context = merged.getContext("2d");
  for (const mask of masks) {
    const source = document.createElement("canvas");
    source.width = mask.width; source.height = mask.height;
    const sourceContext = source.getContext("2d");
    const image = sourceContext.createImageData(mask.width, mask.height);
    for (let index = 0; index < mask.width * mask.height; index += 1) {
      const value = maskPixelValue(mask, index);
      image.data[index * 4] = 255;
      image.data[index * 4 + 1] = 255;
      image.data[index * 4 + 2] = 255;
      image.data[index * 4 + 3] = value;
    }
    sourceContext.putImageData(image, 0, 0);
    context.drawImage(source, 0, 0, width, height);
  }
  return context.getImageData(0, 0, width, height);
}

function makeFallbackMask(width, height) {
  const mask = document.createElement("canvas"); mask.width = width; mask.height = height;
  const context = mask.getContext("2d"); context.fillStyle = "#fff";
  context.beginPath(); context.moveTo(width * .04, height * .06); context.lineTo(width * .96, height * .06); context.lineTo(width * .91, height * .72); context.lineTo(width * .09, height * .72); context.closePath(); context.fill();
  return context.getImageData(0, 0, width, height);
}

function maskCoverage(mask) {
  let count = 0;
  for (let index = 3; index < mask.data.length; index += 4) if (mask.data[index] > 20) count += 1;
  return count / (mask.width * mask.height);
}

function textureLayer() {
  const layer = document.createElement("canvas"); layer.width = canvas.width; layer.height = canvas.height;
  const context = layer.getContext("2d");
  const scale = Math.max(.45, canvas.width / 1800);
  const pattern = context.createPattern(texture, "repeat");
  context.save(); context.scale(scale, scale); context.fillStyle = pattern; context.fillRect(0, 0, layer.width / scale, layer.height / scale); context.restore();
  context.globalCompositeOperation = "multiply"; context.globalAlpha = .3; context.drawImage(room, 0, 0, layer.width, layer.height);
  return layer;
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (showAfter && selected && texture.complete) {
    if (manual && points.length === 4) drawTexturePolygon();
    else if (wallMask) drawTextureMask();
  }
  if (manual) drawManualPoints();
}

function drawTextureMask() {
  const layer = textureLayer();
  const mask = document.createElement("canvas"); mask.width = canvas.width; mask.height = canvas.height; mask.getContext("2d").putImageData(wallMask, 0, 0);
  const output = document.createElement("canvas"); output.width = canvas.width; output.height = canvas.height;
  const context = output.getContext("2d"); context.drawImage(layer, 0, 0); context.globalCompositeOperation = "destination-in"; context.drawImage(mask, 0, 0);
  ctx.globalAlpha = .9; ctx.drawImage(output, 0, 0); ctx.globalAlpha = 1;
}

function drawTexturePolygon() {
  const layer = textureLayer();
  ctx.save(); ctx.beginPath(); ctx.moveTo(...points[0]); for (let index = 1; index < 4; index += 1) ctx.lineTo(...points[index]); ctx.closePath(); ctx.clip(); ctx.globalAlpha = .9; ctx.drawImage(layer, 0, 0); ctx.restore(); ctx.globalAlpha = 1;
}

function drawManualPoints() {
  ctx.save(); ctx.strokeStyle = "#ff493d"; ctx.fillStyle = "#fff"; ctx.lineWidth = Math.max(3, canvas.width / 400);
  if (points.length) { ctx.beginPath(); ctx.moveTo(...points[0]); for (let index = 1; index < points.length; index += 1) ctx.lineTo(...points[index]); ctx.stroke(); }
  points.forEach((point) => { ctx.beginPath(); ctx.arc(point[0], point[1], Math.max(7, canvas.width / 180), 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }); ctx.restore();
}

canvas.addEventListener("pointerdown", (event) => {
  if (!manual) return;
  const bounds = canvas.getBoundingClientRect();
  const point = [(event.clientX - bounds.left) * canvas.width / bounds.width, (event.clientY - bounds.top) * canvas.height / bounds.height];
  if (points.length >= 4) points = [];
  points.push(point); hint.textContent = `벽 모서리 ${points.length}/4 선택`;
  if (points.length === 4) hint.textContent = "수동 벽 영역 적용 완료";
  draw();
});

$("#aiBtn").addEventListener("click", detectWall);
$("#areaBtn").addEventListener("click", () => { manual = true; points = []; showAfter = true; hint.textContent = "벽 모서리 4곳을 차례로 눌러주세요"; setStatus("수동 보정", "네 점으로 벽 영역을 직접 지정합니다.", false); draw(); });
$("#beforeBtn").addEventListener("click", () => { showAfter = false; draw(); });
$("#afterBtn").addEventListener("click", () => { showAfter = true; draw(); });
$("#saveBtn").addEventListener("click", () => {
  if (!room.src) return;
  const output = document.createElement("canvas"); output.width = canvas.width; output.height = canvas.height;
  const context = output.getContext("2d"); context.drawImage(room, 0, 0, output.width, output.height); context.drawImage(canvas, 0, 0);
  const link = document.createElement("a"); link.download = `HANALL_${selected?.product_code || "result"}.jpg`; link.href = output.toDataURL("image/jpeg", .94); link.click();
});

function reset() { editor.classList.add("hidden"); start.classList.remove("hidden"); room.removeAttribute("src"); points = []; wallMask = null; currentDataUrl = ""; }
$("#reset").addEventListener("click", reset);
$("#cameraBtn").addEventListener("click", () => openPicker($("#cameraInput")));
$("#albumBtn").addEventListener("click", () => openPicker($("#albumInput")));
$("#changeBtn").addEventListener("click", () => openPicker($("#albumInput")));
$("#cameraInput").addEventListener("change", (event) => loadPhoto(event.target.files[0]));
$("#albumInput").addEventListener("change", (event) => loadPhoto(event.target.files[0]));

loadProducts();
