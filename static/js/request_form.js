// Форма заявки: карточки людей, логика галочек, импорт из Excel.
(function () {
  "use strict";
  const cfg = window.REQUEST_FORM;
  const container = document.getElementById("visitor-forms");
  const template = document.getElementById("visitor-template");
  const total = document.getElementById(`id_${cfg.prefix}-TOTAL_FORMS`);
  const COPY_FIELDS = ["organization_name", "arrival_date", "arrival_time", "departure_date",
    "one_day", "needs_transport", "needs_camp", "needs_meals", "breakfast", "lunch", "dinner"];

  const field = (card, name) => card.querySelector(`[name$="-${name}"]`);
  const cards = () => Array.from(container.querySelectorAll("[data-form]"));
  const visibleCards = () => cards().filter((c) => !c.classList.contains("is-deleted"));

  function setValue(input, value) {
    if (!input || input.disabled) return;
    if (input.type === "checkbox") input.checked = !!value;
    else input.value = value;
  }
  function getValue(input) {
    if (!input) return undefined;
    return input.type === "checkbox" ? input.checked : input.value;
  }

  function sync(card) {
    const oneDay = field(card, "one_day");
    const arrival = field(card, "arrival_date");
    const departure = field(card, "departure_date");
    const camp = field(card, "needs_camp");
    const meals = field(card, "needs_meals");
    const mealsBox = card.querySelector("[data-meals]");

    if (oneDay && oneDay.checked) {
      if (!departure.disabled) departure.value = arrival.value;
      departure.readOnly = true;
      camp.checked = false;
      camp.disabled = true;
    } else if (oneDay) {
      departure.readOnly = false;
      camp.disabled = false;
    }
    camp.closest(".check").classList.toggle("is-disabled", camp.disabled);

    // машину назначит диспетчер — свою марку и госномер не вводят
    const transport = field(card, "needs_transport");
    ["vehicle_make", "vehicle_plate"].forEach((n) => {
      const input = field(card, n);
      if (!input || !transport) return;
      input.disabled = transport.checked;
      if (transport.checked) input.value = "";
    });

    mealsBox.classList.toggle("hidden", !meals.checked);
    if (!meals.checked) {
      ["breakfast", "lunch", "dinner"].forEach((n) => (field(card, n).checked = false));
    }
    const del = field(card, "DELETE");
    card.classList.toggle("is-deleted", !!(del && del.checked));
  }

  function renumber() {
    visibleCards().forEach((c, i) => {
      const num = c.querySelector("[data-num]");
      if (num) num.textContent = `${cfg.personLabel} ${i + 1}`;
    });
    document.getElementById("people-count").textContent = visibleCards().length;
  }

  function bind(card) {
    card.addEventListener("change", (e) => {
      const name = (e.target.name || "").split("-").pop();
      if (name === "arrival_date") {
        const dep = field(card, "departure_date");
        if (field(card, "one_day").checked || !dep.value || dep.value < e.target.value) dep.value = e.target.value;
      }
      sync(card);
      if (name === "DELETE") renumber();
    });
    sync(card);
  }

  function copyFrom(source, target) {
    COPY_FIELDS.forEach((n) => {
      const v = getValue(field(source, n));
      if (v !== undefined) setValue(field(target, n), v);
    });
    sync(target);
  }

  function addCard(copySource) {
    const index = parseInt(total.value, 10);
    const wrap = document.createElement("div");
    wrap.innerHTML = template.innerHTML.replace(/__prefix__/g, index);
    const card = wrap.firstElementChild;
    container.appendChild(card);
    total.value = index + 1;
    bind(card);
    const src = copySource === undefined ? visibleCards().slice(-2)[0] : copySource;
    if (src && src !== card) copyFrom(src, card);
    renumber();
    return card;
  }

  document.getElementById("add-person").addEventListener("click", () => {
    const card = addCard();
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    field(card, "full_name").focus();
  });

  document.getElementById("apply-all").addEventListener("click", () => {
    const [first, ...rest] = visibleCards();
    if (!first) return;
    rest.filter((c) => !c.hasAttribute("data-locked")).forEach((c) => copyFrom(first, c));
  });

  // --- импорт из Excel ---
  const fileInput = document.getElementById("import-file");
  const result = document.getElementById("import-result");
  fileInput.addEventListener("change", async () => {
    if (!fileInput.files.length) return;
    const data = new FormData();
    data.append("file", fileInput.files[0]);
    data.append("csrfmiddlewaretoken", document.querySelector("[name=csrfmiddlewaretoken]").value);
    result.textContent = cfg.importing;
    try {
      const resp = await fetch(cfg.importUrl, { method: "POST", body: data, credentials: "same-origin" });
      const json = await resp.json();
      const first = visibleCards()[0];
      json.rows.forEach((row) => {
        const empty = visibleCards().find((c) => !c.hasAttribute("data-locked") && !field(c, "full_name").value.trim());
        const card = empty || addCard(first || null);
        if (first && card !== first && empty) copyFrom(first, card);
        Object.entries(row).forEach(([name, value]) => {
          if (value === "" || value === null) return;
          setValue(field(card, name), value);
        });
        if (["breakfast", "lunch", "dinner"].some((m) => m in row)) {
          // колонки питания в файле: «Питание» = есть хотя бы один приём пищи
          field(card, "needs_meals").checked = ["breakfast", "lunch", "dinner"].some((m) => field(card, m).checked);
        }
        if (row.departure_date || row.arrival_date) {
          const oneDay = field(card, "one_day");
          const arr = field(card, "arrival_date").value, dep = field(card, "departure_date").value;
          if (!oneDay.disabled) oneDay.checked = !!arr && arr === dep && !field(card, "needs_camp").checked;
        }
        sync(card);
      });
      renumber();
      const lines = [`${cfg.imported}: ${json.rows.length}`].concat(json.errors || []);
      result.innerHTML = "";
      lines.forEach((l) => { const d = document.createElement("div"); d.textContent = l; result.appendChild(d); });
    } catch (err) {
      result.textContent = String(err);
    }
    fileInput.value = "";
  });

  cards().forEach(bind);
  renumber();
})();
