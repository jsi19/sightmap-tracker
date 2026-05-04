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

  function groupByFloor(units) {
    const m = new Map();
    for (const u of units) {
      const f = u.floor_label || "—";
      if (!m.has(f)) m.set(f, []);
      m.get(f).push(u);
    }
    return Array.from(m.entries());
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
        "<thead><tr><th>Unit</th><th>Plan</th><th>Sq ft</th><th>Rent</th><th>Available</th></tr></thead>";
      const tb = document.createElement("tbody");
      for (const u of list) {
        const tr = document.createElement("tr");
        const apt = u.display_unit_number || u.unit_number || "—";
        const sq = u.area != null ? u.area.toLocaleString() : "—";
        const rent = u.display_price || (u.price != null ? "$" + u.price.toLocaleString() : "—");
        const av = u.display_available_on || u.available_on || "—";
        tr.innerHTML =
          "<td>" +
          escapeHtml(apt) +
          "</td><td>" +
          escapeHtml(u.floor_plan || "—") +
          '</td><td class="num">' +
          escapeHtml(sq) +
          '</td><td class="num">' +
          escapeHtml(rent) +
          "</td><td>" +
          escapeHtml(av) +
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

    const sections = [
      ["New on market", c.new, (u) => u.label + " · " + (u.display_price || "") + " · " + (u.display_available_on || u.available_on || "")],
      ["No longer listed", c.removed, (u) => u.label + " · " + (u.display_price || "")],
      ["Price changes", c.price, (x) => x.label + " · " + x.was_display_price + " → " + x.now_display_price],
      ["Move-in / availability", c.available, (x) => x.label + " · " + x.was + " → " + x.now],
    ];

    for (const [title, arr, line] of sections) {
      if (!arr || !arr.length) continue;
      const sec = document.createElement("div");
      sec.className = "chg-section";
      const h = document.createElement("h3");
      h.textContent = title;
      sec.appendChild(h);
      const ul = document.createElement("ul");
      ul.className = "chg-list";
      for (const item of arr) {
        const li = document.createElement("li");
        li.textContent = line(item);
        ul.appendChild(li);
      }
      sec.appendChild(ul);
      container.appendChild(sec);
    }
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
        " dates";
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
