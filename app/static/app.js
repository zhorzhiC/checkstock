"use strict";
const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icon = (name) =>
  `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const num = (value) => Number(value || 0).toLocaleString("ru-RU");
const kindLabels = {
  RECEIPT: "Приход",
  TRANSFER: "Между ФФ",
  FBS: "FBS",
  FBO: "FBO",
};
const statusLabels = {
  RESERVED: "В резерве",
  IN_TRANSIT: "В пути",
  RECEIVED: "Принято",
  SHIPPED: "К клиенту",
  CANCELLED: "Отменено",
};
const actionLabels = {
  RECEIPT: "Приход на ФФ",
  TRANSFER_SENT: "Перемещение отправлено",
  TRANSFER_RECEIVED: "Перемещение принято",
  FBS_RESERVED: "Резерв FBS",
  FBS_CANCELLED: "Резерв FBS отменён",
  FBS_SHIPPED: "Заказ FBS отправлен",
  FBO_SENT: "Поставка FBO отправлена",
  FBO_RECEIVED: "Поставка FBO принята",
  WAREHOUSE_CREATED: "ФФ добавлен",
  WAREHOUSE_RENAMED: "ФФ переименован",
  WAREHOUSE_ARCHIVED: "ФФ отправлен в архив",
};
const pages = {
  stock: "Остатки",
  documents: "Отправки и перемещения",
  history: "История",
  warehouses: "Склады",
};
let data,
  currentView = "stock",
  mode = "receipt",
  formRequest = null,
  confirmation = null;
let toastTimer;
const product = (id) => data.products.find((p) => p.id === id);
const warehouse = (id) => data.warehouses.find((w) => w.id === id);
const stockFor = (id) => data.stocks.find((p) => p.product_id === id);
const time = (value, full = false) =>
  new Date(value).toLocaleString(
    "ru-RU",
    full
      ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }
      : { hour: "2-digit", minute: "2-digit" },
  );
const empty = (title) =>
  `<div class="empty-state">${icon("box")}<strong>${esc(title)}</strong></div>`;

async function api(path, { method = "GET", body, key } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok)
      throw result.error || { message: "Не удалось выполнить действие." };
    return result;
  } catch (error) {
    if (
      error.message &&
      ![
        "Failed to fetch",
        "Load failed",
        "NetworkError when attempting to fetch resource.",
      ].includes(error.message) &&
      error.name !== "AbortError"
    )
      throw error;
    throw {
      message:
        "Нет связи. Повторите запрос.",
    };
  } finally {
    clearTimeout(timer);
  }
}

async function loadState() {
  $("#refresh").disabled = true;
  try {
    const next = await api("/api/v1/state");
    if (!next.store)
      throw {
        message:
          "Магазин не настроен.",
      };
    data = next;
    $("#loading").hidden = true;
    $("#workspace").hidden = false;
    $("#load-error").hidden = true;
    $("#updated-time").textContent = `Обновлено в ${time(new Date())}`;
    render();
    return true;
  } catch (error) {
    $("#loading").hidden = true;
    $("#load-error").hidden = false;
    $("#load-error").innerHTML =
      `${esc(error.message)} <button class="mini-button" data-retry>Повторить загрузку</button>`;
    return false;
  } finally {
    $("#refresh").disabled = false;
  }
}

function render() {
  $("#sku-count").textContent = data.products.length;
  const pending = data.documents.filter((d) =>
    ["RESERVED", "IN_TRANSIT"].includes(d.status),
  ).length;
  $("#pending-count").textContent = pending || "";
  const old = $("#stock-warehouse").value;
  $("#stock-warehouse").innerHTML =
    '<option value="">Все склады</option>' +
    data.warehouses
      .filter((w) => w.visible)
      .map(
        (w) =>
          `<option value="${w.id}">${esc(w.name)}${w.archived ? " · архив" : ""}</option>`,
      )
      .join("");
  if (data.warehouses.some((w) => w.id === old && w.visible))
    $("#stock-warehouse").value = old;
  renderMetrics();
  renderStocks();
  renderDocuments();
  renderHistory();
  renderWarehouses();
  const events = data.history.slice(0, 4);
  $("#recent-events").innerHTML = events.length
    ? events
        .map((event) => {
          const p = product(event.product_id);
          return `<div class="event-row"><span class="event-symbol">${icon(event.action.includes("SENT") || event.action.includes("SHIPPED") ? "arrow" : "down")}</span><div class="event-text"><strong>${esc(actionLabels[event.action])}</strong><small>${esc(p?.sku || "Склады")} · ${esc(event.source || "—")} → ${esc(event.destination || "Архив")}</small></div><span class="event-quantity">${event.quantity ? `${num(event.quantity)} шт.` : ""}</span><span class="event-time">${time(event.created_at)}</span></div>`;
        })
        .join("")
    : empty("Нет операций");
}

function renderMetrics() {
  const s = data.summary,
    transit = s.transfer + s.fbo_transit + s.fbs_transit;
  const metrics = [
    [
      "Физически на ФФ",
      s.physical,
      "box",
      s.archived_physical
        ? `В архиве: ${num(s.archived_physical)} шт.`
        : "",
    ],
    [
      "Доступно к отправке",
      s.available_active,
      "check",
    ],
    ["В резерве FBS", s.reserved, "clock"],
    ["В пути", transit, "truck"],
  ];
  $("#metrics").innerHTML = metrics
    .map(
      ([label, value, symbol, caption]) =>
        `<article class="metric"><div class="metric-top">${label}${icon(symbol)}</div><div class="metric-value">${num(value)}<small>шт.</small></div>${caption ? `<div class="metric-caption">${caption}</div>` : ""}</article>`,
    )
    .join("");
  $("#integrity-badge").classList.toggle("bad", !data.integrity.ok);
  $("#integrity-badge").innerHTML =
    `${icon("check")} ${data.integrity.ok ? `${num(data.integrity.accounted)} из ${num(data.integrity.received)} шт. учтено` : "Требуется сверка учёта"}`;
  $("#distribution").innerHTML = [
    "physical",
    "transfer",
    "fbo_transit",
    "fbs_transit",
    "marketplace",
  ]
    .map(
      (k) =>
        `<span class="${k}" style="flex:${s[k]}" title="${esc(k === "physical" ? "На ФФ" : { transfer: "Между ФФ", fbo_transit: "В пути FBO", fbs_transit: "К клиенту FBS", marketplace: "На маркетплейсе" }[k])}: ${num(s[k])}"></span>`,
    )
    .join("");
  const locations = [
    ["Между ФФ", "transfer", "arrow"],
    ["На маркетплейс · FBO", "fbo_transit", "truck"],
    ["К клиенту · FBS", "fbs_transit", "box"],
    ["Принято на маркетплейсе", "marketplace", "warehouse"],
  ];
  $("#location-stats").innerHTML = locations
    .map(
      ([label, key, symbol]) =>
        `<div class="location-stat">${icon(symbol)}<div><strong>${label}</strong><b>${num(s[key])}</b><small>шт.</small></div></div>`,
    )
    .join("");
}

function renderStocks() {
  const query = $("#stock-search").value.toLocaleLowerCase(),
    wid = $("#stock-warehouse").value;
  const warehouses = data.warehouses.filter(
    (w) => w.visible && (!wid || w.id === wid),
  );
  const rows = data.stocks.filter((p) =>
    `${p.sku} ${p.name}`.toLocaleLowerCase().includes(query),
  );
  $("#stock-count").textContent = `${rows.length} SKU`;
  $("#archive-note").textContent = data.summary.archived_physical
    ? `В архиве: ${num(data.summary.archived_physical)} шт.`
    : "";
  $("#archive-note").hidden = !data.summary.archived_physical;
  if (!rows.length) {
    $("#stock-table").innerHTML = empty("Товары не найдены");
    return;
  }
  $("#stock-table").innerHTML =
    `<table class="stock-table"><thead><tr><th rowspan="2">АРТИКУЛ / ТОВАР</th>${warehouses.map((w) => `<th colspan="3" class="group">${esc(w.name)}${w.archived ? '<span class="archive-chip">Архив</span>' : ""}</th>`).join("")}<th rowspan="2" class="number" title="Только действующие ФФ">ДОСТУПНО<br>К ОТПРАВКЕ</th><th rowspan="2"></th></tr><tr>${warehouses.map(() => '<th class="number subhead ff-start">ФИЗИЧЕСКИ</th><th class="number subhead">ДОСТУПНО</th><th class="number subhead">РЕЗЕРВ FBS</th>').join("")}</tr></thead><tbody>${rows
      .map(
        (p) =>
          `<tr><td><div class="product-cell"><span class="product-picture">${icon("box")}</span><span class="product-text"><strong>${esc(p.name)}</strong><small>${esc(p.sku)}</small></span></div></td>${warehouses
            .map((w) => {
              const v = p.warehouses[w.id];
              return `<td class="number ff-start ${v.physical ? "" : "zero"}">${num(v.physical)}</td><td class="number available-col ${v.available ? "" : "zero"}">${num(v.available)}</td><td class="number ${v.reserved ? "" : "zero"}">${num(v.reserved)}</td>`;
            })
            .join(
              "",
            )}<td class="number total-col">${num(warehouses.reduce((sum, w) => sum + (w.archived ? 0 : p.warehouses[w.id].available), 0))}</td><td><button class="row-action" data-product="${p.product_id}" aria-label="Новая операция: ${esc(p.name)}" title="Новая операция">+</button></td></tr>`,
      )
      .join("")}</tbody></table>`;
}

function renderDocuments() {
  const query = $("#document-search").value.toLocaleLowerCase(),
    kind = $("#document-kind").value,
    status = $("#document-status").value;
  const docs = data.documents.filter((d) => {
    const p = product(d.product_id),
      pending = ["RESERVED", "IN_TRANSIT"].includes(d.status);
    return (
      (!kind || d.kind === kind) &&
      (!status || (status === "pending" ? pending : !pending)) &&
      `${p?.sku} ${p?.name} ${d.id}`.toLocaleLowerCase().includes(query)
    );
  });
  if (!docs.length) {
    $("#documents-table").innerHTML = empty("Нет документов");
    return;
  }
  $("#documents-table").innerHTML =
    `<table><thead><tr><th>ДОКУМЕНТ</th><th>ТОВАР</th><th>МАРШРУТ</th><th class="number">ШТ.</th><th>СТАТУС</th><th></th></tr></thead><tbody>${docs
      .map((d) => {
        const p = product(d.product_id);
        let actions = "";
        if (d.status === "RESERVED")
          actions = `<button class="mini-button" data-transition="ship" data-doc="${d.id}">Отправить</button><button class="mini-button quiet" data-transition="cancel" data-doc="${d.id}">Отменить</button>`;
        if (d.status === "IN_TRANSIT")
          actions = `<button class="mini-button" data-transition="receive" data-doc="${d.id}">Принять</button>`;
        if (d.status === "RECEIVED" && d.kind !== "RECEIPT")
          actions = `<button class="mini-button quiet" data-transition="receive" data-doc="${d.id}" title="Повторное подтверждение не изменит остатки">Проверить повтор</button>`;
        return `<tr><td><span class="kind-chip ${d.kind}">${kindLabels[d.kind]}</span><span class="doc-id">#${d.id.slice(0, 8)}</span></td><td class="document-product">${esc(p?.name)}<small>${esc(p?.sku)}</small></td><td class="route">${esc(d.source_name)}<span>→</span>${esc(d.destination_name)}</td><td class="number">${num(d.quantity)}</td><td><span class="badge ${d.status}">${statusLabels[d.status]}</span></td><td><div class="actions">${actions}</div></td></tr>`;
      })
      .join("")}</tbody></table>`;
}

function renderHistory() {
  const query = $("#history-search").value.toLocaleLowerCase(),
    kind = $("#history-kind").value;
  const events = data.history.filter((e) => {
    const p = product(e.product_id);
    return (
      (!kind || e.action.startsWith(kind)) &&
      `${p?.sku || ""} ${p?.name || ""} ${e.source} ${e.destination}`
        .toLocaleLowerCase()
        .includes(query)
    );
  });
  $("#history-table").innerHTML = events.length
    ? `<table><thead><tr><th>ДАТА</th><th>ОПЕРАЦИЯ</th><th>ТОВАР</th><th>ОТКУДА → КУДА</th><th class="number">ШТ.</th></tr></thead><tbody>${events
        .map((e) => {
          const p = product(e.product_id);
          return `<tr><td>${time(e.created_at, true)}<span class="doc-id">${e.document_id ? "#" + e.document_id.slice(0, 8) : "Справочник"}</span></td><td>${esc(actionLabels[e.action] || e.action)}</td><td class="document-product">${esc(p?.name || "—")}<small>${esc(p?.sku || "")}</small></td><td class="route">${esc(e.source || "—")}<span>→</span>${esc(e.destination || "Архив")}</td><td class="number">${e.quantity ? num(e.quantity) : "—"}</td></tr>`;
        })
        .join("")}</tbody></table>`
    : empty("Нет событий");
}

function renderWarehouses() {
  $("#warehouse-cards").innerHTML =
    data.warehouses
      .map((w) => {
        const physical = data.stocks.reduce(
            (n, p) => n + p.warehouses[w.id].physical,
            0,
          ),
          reserved = data.stocks.reduce(
            (n, p) => n + p.warehouses[w.id].reserved,
            0,
          );
        const pending = data.documents.filter(
          (d) =>
            ["RESERVED", "IN_TRANSIT"].includes(d.status) &&
            [d.source_id, d.destination_id].includes(w.id),
        ).length;
        return `<article class="warehouse-card ${w.archived ? "archived" : ""}"><div class="warehouse-card-top"><span class="warehouse-symbol">${icon("warehouse")}</span><span class="badge ${w.archived ? "CANCELLED" : "RECEIVED"}">${w.archived ? "Архив" : "Действующий"}</span></div><h2>${esc(w.name)}</h2><div class="warehouse-card-stats"><div><b>${num(physical)}</b><small>физически, шт.</small></div><div><b>${num(reserved)}</b><small>резерв FBS, шт.</small></div></div>${pending ? `<div class="pending-inline">В работе: ${pending}</div>` : ""}<div class="warehouse-card-actions"><button data-rename="${w.id}">Переименовать</button>${w.archived ? `<button data-evacuate="${w.id}">Вывезти товар →</button>` : `<button data-archive="${w.id}">В архив</button>`}</div></article>`;
      })
      .join("") +
    `<button class="new-warehouse" id="add-warehouse">${icon("plus")}Добавить склад</button>`;
}

function setView() {
  const previousView = currentView;
  currentView = location.hash.slice(1);
  if (!pages[currentView]) currentView = "stock";
  const title = pages[currentView];
  $("#page-title").textContent = title;
  document.title = `${title} — CheckStock`;
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== `view-${currentView}`));
  document
    .querySelectorAll(".nav-item")
    .forEach((el) => {
      const active = el.dataset.view === currentView;
      el.classList.toggle("active", active);
      if (active) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  if (previousView !== currentView) window.scrollTo({ top: 0 });
}

function focusStockSearch() {
  if (!data || document.querySelector("dialog[open]")) return;
  location.hash = "stock";
  setView();
  $("#stock-search").focus({ preventScroll: true });
  $("#stock-search").scrollIntoView({ block: "center" });
}

function options(list, chosen) {
  return list
    .map(
      (w) =>
        `<option value="${w.id}" ${w.id === chosen ? "selected" : ""}>${esc(w.name)}${w.archived ? " · архив" : ""}</option>`,
    )
    .join("");
}
function openOperation(kind = "receipt", values = {}) {
  if (!data) return;
  mode = kind;
  formRequest = null;
  $("#form-error").hidden = true;
  $("#kind-field").hidden = ["createWarehouse", "renameWarehouse"].includes(
    kind,
  );
  if (!$("#kind-field").hidden) $("#operation-kind").value = kind;
  buildFields(values);
  $("#operation-dialog").showModal();
}

function buildFields(values = {}) {
  const titles = {
    receipt: "Приход на ФФ",
    reserve: "Резерв FBS",
    transfer: "Перемещение между ФФ",
    fbo: "Отправка FBO",
    createWarehouse: "Новый склад",
    renameWarehouse: "Название склада",
  };
  $("#drawer-title").textContent = titles[mode];
  $("#operation-preview").hidden = ["createWarehouse", "renameWarehouse"].includes(mode);
  if (["createWarehouse", "renameWarehouse"].includes(mode)) {
    $("#operation-fields").innerHTML =
      `<label class="field">Название<input name="name" required maxlength="80" autocomplete="off" value="${esc(values.name || "")}"><span class="field-hint">Название должно быть уникальным.</span></label><input type="hidden" name="warehouse_id" value="${esc(values.warehouse_id || "")}">`;
    $("#submit-operation").textContent = "Сохранить";
    return;
  }
  const active = data.warehouses.filter((w) => !w.archived);
  const source = mode === "transfer" ? data.warehouses : active;
  const selectedSource =
    values.source_id || values.warehouse_id || source[0]?.id;
  const selectedDestination =
    values.destination_id ||
    active.find((w) => w.id !== selectedSource)?.id ||
    active[0]?.id;
  $("#operation-fields").innerHTML =
    `<label class="field">Товар<select name="product_id" required>${data.products.map((p) => `<option value="${p.id}" ${p.id === values.product_id ? "selected" : ""}>${esc(p.sku)} · ${esc(p.name)}</option>`).join("")}</select></label><label class="field">${mode === "receipt" ? "Склад приёмки" : "Склад отправления"}<select name="${mode === "transfer" ? "source_id" : "warehouse_id"}" required>${options(source, selectedSource)}</select></label>${mode === "transfer" ? `<label class="field">Склад приёмки<select name="destination_id" required>${options(active, selectedDestination)}</select><span class="field-hint">Доступно после приёмки.</span></label>` : ""}${mode === "fbo" ? '<label class="field">Склад приёмки<input name="destination" value="Склад маркетплейса" required maxlength="80"></label>' : ""}<label class="field">Количество, шт.<input type="number" name="quantity" value="${values.quantity || 1}" min="1" max="1000000" step="1" required inputmode="numeric"></label>`;
  $("#submit-operation").textContent = {
    receipt: "Оформить приход",
    reserve: "Зарезервировать",
    transfer: "Отправить перемещение",
    fbo: "Отправить FBO",
  }[mode];
  preview();
}

function preview() {
  if (["createWarehouse", "renameWarehouse"].includes(mode)) return;
  const f = new FormData($("#operation-form")),
    p = stockFor(f.get("product_id"));
  const wid = f.get("warehouse_id") || f.get("source_id"),
    w = warehouse(wid),
    stock = p?.warehouses[wid];
  const qty = Number(f.get("quantity"));
  if (!stock || !w || !Number.isInteger(qty) || qty <= 0) {
    $("#operation-preview").textContent =
      "Укажите товар, склад и количество.";
    return;
  }
  let result = "";
  if (mode === "receipt")
    result = `Физически: ${num(stock.physical)} → ${num(stock.physical + qty)} шт.<br>Доступно: ${num(stock.available)} → ${num(stock.available + qty)} шт.`;
  if (mode === "reserve")
    result = `Резерв: ${num(stock.reserved)} → ${num(stock.reserved + qty)} шт.<br>Доступно: ${num(stock.available)} → ${num(stock.available - qty)} шт.`;
  if (mode === "transfer" || mode === "fbo")
    result = `На ФФ: ${num(stock.physical)} → ${num(stock.physical - qty)} шт.<br>В пути ${mode === "transfer" ? "между ФФ" : "на маркетплейс"}: +${num(qty)} шт.`;
  const warning =
    mode !== "receipt" && qty > stock.available
      ? '<br><span class="preview-warning">Недостаточно товара.</span>'
      : "";
  $("#operation-preview").innerHTML =
    `<strong>После операции</strong>${result}${warning}`;
}

async function submitOperation(event) {
  event.preventDefault();
  if (!$("#operation-form").reportValidity()) return;
  const f = new FormData($("#operation-form"));
  let method = "POST",
    path = "",
    body = {};
  if (["createWarehouse", "renameWarehouse"].includes(mode)) {
    body = { name: String(f.get("name")).trim() };
    path =
      "/api/v1/warehouses" +
      (mode === "renameWarehouse" ? `/${f.get("warehouse_id")}` : "");
    method = mode === "renameWarehouse" ? "PATCH" : "POST";
  } else {
    body = {
      product_id: f.get("product_id"),
      quantity: Number(f.get("quantity")),
    };
    if (mode === "transfer")
      Object.assign(body, {
        source_id: f.get("source_id"),
        destination_id: f.get("destination_id"),
      });
    else body.warehouse_id = f.get("warehouse_id");
    if (mode === "fbo") body.destination = String(f.get("destination")).trim();
    path =
      "/api/v1/" +
      {
        receipt: "receipts",
        reserve: "fbs-orders",
        transfer: "transfers",
        fbo: "fbo-shipments",
      }[mode];
  }
  const fingerprint = JSON.stringify([method, path, body]);
  if (formRequest?.fingerprint !== fingerprint)
    formRequest = { fingerprint, key: crypto.randomUUID() };
  $("#submit-operation").disabled = true;
  $("#form-error").hidden = true;
  try {
    const result = await api(path, { method, body, key: formRequest.key });
    $("#operation-dialog").close();
    if (["reserve", "transfer", "fbo"].includes(mode)) {
      location.hash = "documents";
      $("#document-status").value = "pending";
    }
    await loadState();
    toast(
      result.replayed
        ? "Уже выполнено"
        : "Операция выполнена",
    );
  } catch (error) {
    const box = $("#form-error");
    box.hidden = false;
    box.innerHTML = `<p>${esc(error.message)}</p>`;
    if (error.code === "insufficient_stock") {
      box.innerHTML += `Не хватает ${num(error.shortage)} шт. ${error.alternatives?.length ? "" : "На других складах нет свободного товара."}`;
      (error.alternatives || []).forEach((alt, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "suggestion";
        button.textContent = `Переместить ${num(alt.quantity)} шт. из «${alt.name}»${alt.archived ? " (архив)" : ""}`;
        button.addEventListener("click", () => {
          const original = body.warehouse_id || body.source_id;
          mode = "transfer";
          formRequest = null;
          $("#operation-kind").value = mode;
          box.hidden = true;
          buildFields({
            product_id: body.product_id,
            source_id: alt.warehouse_id,
            destination_id: original,
            quantity: alt.quantity,
          });
          $("#operation-fields select").focus();
        });
        box.append(button);
      });
    }
  } finally {
    $("#submit-operation").disabled = false;
  }
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").classList.toggle("error", error);
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 5500);
}

function confirmAction(title, description, path, method = "POST") {
  confirmation = { path, method, key: crypto.randomUUID() };
  $("#confirm-title").textContent = title;
  $("#confirm-description").textContent = description;
  $("#confirm-error").hidden = true;
  $("#confirm-submit").disabled = false;
  $("#confirm-dialog").showModal();
}

document.addEventListener("click", (event) => {
  const el = event.target.closest("button");
  if (!el) return;
  if (el.matches("[data-retry]")) loadState();
  if (el.dataset.action) openOperation(el.dataset.action);
  if (el.dataset.product)
    openOperation("receipt", { product_id: el.dataset.product });
  if (el.id === "add-warehouse") openOperation("createWarehouse");
  if (el.dataset.rename) {
    const w = warehouse(el.dataset.rename);
    openOperation("renameWarehouse", { warehouse_id: w.id, name: w.name });
  }
  if (el.dataset.evacuate) {
    const p = data.stocks.find(
      (p) => p.warehouses[el.dataset.evacuate].available > 0,
    );
    openOperation("transfer", {
      source_id: el.dataset.evacuate,
      product_id: p?.product_id,
      quantity: p?.warehouses[el.dataset.evacuate].available || 1,
    });
  }
  if (el.dataset.archive) {
    const w = warehouse(el.dataset.archive),
      qty = data.stocks.reduce((n, p) => n + p.warehouses[w.id].physical, 0);
    confirmAction(
      `Архивировать склад «${w.name}»?`,
      `${num(qty)} шт. останутся на складе. Вывоз и завершение операций будут доступны.`,
      `/api/v1/warehouses/${w.id}`,
      "DELETE",
    );
  }
  if (el.dataset.transition) {
    const d = data.documents.find((d) => d.id === el.dataset.doc),
      action = el.dataset.transition;
    const noun = {
      receive: "Подтвердить приёмку?",
      ship: "Отправить заказ FBS?",
      cancel: "Отменить резерв FBS?",
    }[action];
    let description = `${product(d.product_id).sku} · ${product(d.product_id).name}\n${num(d.quantity)} шт. · ${d.source_name} → ${d.destination_name}`;
    if (d.status === "RECEIVED")
      description += "\nУже принято. Остатки не изменятся.";
    const group = {
      TRANSFER: "transfers",
      FBO: "fbo-shipments",
      FBS: "fbs-orders",
    }[d.kind];
    confirmAction(noun, description, `/api/v1/${group}/${d.id}/${action}`);
  }
});
$("#confirm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#confirm-submit").disabled = true;
  try {
    const result = await api(confirmation.path, {
      method: confirmation.method,
      key: confirmation.key,
    });
    $("#confirm-dialog").close();
    await loadState();
    toast(
      result.replayed
        ? "Уже выполнено"
        : "Операция выполнена",
    );
  } catch (error) {
    $("#confirm-error").hidden = false;
    $("#confirm-error").textContent = error.message;
  } finally {
    $("#confirm-submit").disabled = false;
  }
});
$("#cancel-confirm").addEventListener("click", () =>
  $("#confirm-dialog").close(),
);
$("#operation-form").addEventListener("submit", submitOperation);
$("#operation-fields").addEventListener("input", preview);
$("#operation-fields").addEventListener("change", preview);
$("#operation-kind").addEventListener("change", () => {
  const values = Object.fromEntries(new FormData($("#operation-form")));
  mode = $("#operation-kind").value;
  formRequest = null;
  $("#form-error").hidden = true;
  buildFields(values);
});
$("#close-drawer").addEventListener("click", () =>
  $("#operation-dialog").close(),
);
$("#cancel-drawer").addEventListener("click", () =>
  $("#operation-dialog").close(),
);
$("#new-operation").addEventListener("click", () => openOperation("receipt"));
$("#quick-search").addEventListener("click", focusStockSearch);
$(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  $("#main-content").focus();
});
document.addEventListener("keydown", (event) => {
  if (
    (event.ctrlKey || event.metaKey) &&
    event.key.toLowerCase() === "k" &&
    !document.querySelector("dialog[open]")
  ) {
    event.preventDefault();
    focusStockSearch();
  }
});
$("#refresh").addEventListener("click", loadState);
["stock-search", "stock-warehouse"].forEach((id) =>
  $("#" + id).addEventListener("input", renderStocks),
);
["document-search", "document-kind", "document-status"].forEach((id) =>
  $("#" + id).addEventListener("input", renderDocuments),
);
["history-search", "history-kind"].forEach((id) =>
  $("#" + id).addEventListener("input", renderHistory),
);
window.addEventListener("hashchange", setView);
setView();
loadState();
