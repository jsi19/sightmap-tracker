(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  function fmtTs(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
    } catch {
      return iso;
    }
  }

  function floorNum(label) {
    const m = String(label || "").match(/(\d+)/);
    return m ? parseInt(m[1], 10) : -1;
  }

  /** High floor first (e.g. Floor 8 before Floor 3). */
  function groupByFloor(units) {
    const m = new Map();
    for (const u of units) {
      const f = u.floor_label || "—";
      if (!m.has(f)) m.set(f, []);
      m.get(f).push(u);
    }
    return Array.from(m.entries()).sort((a, b) => floorNum(b[0]) - floorNum(a[0]));
  }

  function renderFloors(container, units) {
    container.innerHTML = "";
    const groups = groupByFloor(units);
    for (const [floor, list] of groups) {
      const block = document.createElement("div");
      block.className = "floor-block";
      const ht = document.createElement("h3");
      ht.className = "floor-title";
      ht.textContent = floor;
      block.appendChild(ht);

      const table = document.createElement("table");
      table.innerHTML =
        "<thead><tr><th>Unit</th><th>Plan</th><th>Layout</th><th>Sq ft</th><th>Rent</th><th>Available</th><th>Special</th></tr></thead>";
      const tb = document.createElement("tbody");
      for (const u of list) {
        const tr = document.createElement("tr");
        const apt = u.display_unit_number || u.unit_number || "—";
        const sq = u.area != null ? u.area.toLocaleString() : "—";
        const rent = u.display_price || (u.price != null ? "$" + u.price.toLocaleString() : "—");
        const av = u.display_available_on || u.available_on || "—";
        const spRaw = (u.specials_description || "").trim().replace(/\s+/g, " ");
        const sp =
          spRaw.length > 56 ? spRaw.slice(0, 55) + "…" : spRaw || "—";
        const layout = (u.bed_bath_label || "—").trim() || "—";
        tr.innerHTML =
          "<td>" +
          escapeHtml(apt) +
          "</td><td>" +
          escapeHtml(u.floor_plan || "—") +
          "</td><td>" +
          escapeHtml(layout) +
          '</td><td class="num">' +
          escapeHtml(sq) +
          '</td><td class="num">' +
          escapeHtml(rent) +
          "</td><td>" +
          escapeHtml(av) +
          '</td><td class="special-cell">' +
          escapeHtml(sp) +
          "</td>";
        tb.appendChild(tr);
      }
      table.appendChild(tb);
      block.appendChild(table);
      container.appendChild(block);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderChanges(container, data) {
    const c = data.changes;
    container.innerHTML = "";

    if (c.baseline) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent =
        "Baseline snapshot — the next scheduled run will show comparisons here.";
      container.appendChild(p);
      return;
    }

    if (!c.has_changes) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = "No changes since the last snapshot.";
      container.appendChild(p);
      return;
    }

    const events = Array.isArray(c.events) ? c.events : [];
    const hint = document.createElement("p");
    hint.className = "timeline-hint";
    hint.textContent =
      "Timeline: higher floors first, then unit number. Each line is one event.";
    container.appendChild(hint);

    const ul = document.createElement("ul");
    ul.className = "timeline";

    const kindClass = {
      REMOVED: "kind-removed",
      NEW: "kind-new",
      PRICE: "kind-price",
      AVAILABLE: "kind-available",
      SPECIAL: "kind-special",
    };

    const kindLabel = {
      REMOVED: "Delisted",
      NEW: "New",
      PRICE: "Price",
      AVAILABLE: "Move-in",
      SPECIAL: "Special",
    };

    for (const ev of events) {
      const li = document.createElement("li");
      li.className = "timeline-item";
      const t = ev.type || "";
      const span = document.createElement("span");
      span.className = "kind " + (kindClass[t] || "kind-default");
      span.textContent = kindLabel[t] || t;
      const text = document.createElement("span");
      text.className = "timeline-text";
      text.textContent = ev.summary || "";
      li.appendChild(span);
      li.appendChild(text);
      ul.appendChild(li);
    }
    container.appendChild(ul);
  }

  async function load() {
    const errEl = $("error");
    errEl.classList.add("hidden");

    let data;
    try {
      const res = await fetch("snapshot.json", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      data = await res.json();
    } catch (e) {
      errEl.textContent =
        "Could not load snapshot.json. If you opened this file from disk, use the GitHub Pages URL or run a local server.";
      errEl.classList.remove("hidden");
      $("headline").textContent = "Availability";
      document.title = "Availability";
      return;
    }

    document.title = data.asset_name || "Availability";
    $("headline").textContent = data.asset_name || "Availability";
    $("updated").textContent =
      "Snapshot · " + fmtTs(data.snapshot_fetched_at || data.generated_at);
    $("unit-count").textContent = data.unit_count + " listed";

    const pill = $("status-pill");
    pill.classList.remove("live", "quiet");
    if (data.changes && data.changes.baseline) {
      pill.textContent = "Baseline";
      pill.classList.add("quiet");
    } else if (data.changes && data.changes.has_changes) {
      const n = data.changes.counts;
      pill.textContent =
        (n.new || 0) +
        " new · " +
        (n.removed || 0) +
        " gone · " +
        (n.price || 0) +
        " price · " +
        (n.available || 0) +
        " dates · " +
        (n.special || 0) +
        " special";
      pill.classList.add("live");
    } else {
      pill.textContent = "No changes";
      pill.classList.add("quiet");
    }

    renderFloors($("floors"), data.units || []);
    renderChanges($("changes-body"), data);
  }

  load();
})();
