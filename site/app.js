/**
 * Draws the leaderboard from leaderboard.json, one lane at a time, chosen by
 * the URL's hash. Every value goes in as text, never as markup. Each lane gives
 * the range of its rate axis and of its cost chart, since its arms' rates and
 * costs sit in a range of their own.
 */
(function () {
  "use strict";

  var LANES = ["rebench", "pro-hard51"];
  var app = document.getElementById("app");
  var hero = document.getElementById("hero");
  var data = null;
  var picked = null;
  // The lane being drawn, whose axis `at` and `track` read.
  var current = null;

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "text") node.textContent = attrs[key];
      else if (key === "style") node.style.cssText = attrs[key];
      else if (key === "on") Object.keys(attrs.on).forEach(function (ev) { node.addEventListener(ev, attrs.on[ev]); });
      else node.setAttribute(key, attrs[key]);
    });
    (kids || []).forEach(function (kid) { if (kid) node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid); });
    return node;
  }

  function pct(x) { return Math.round(x * 100) + "%"; }
  function money(x) { return "$" + x.toFixed(2); }
  function hours(x) { return x.toFixed(1) + " h"; }
  function tokens(x) { return x == null ? "—" : Math.round(x / 1000) + "k"; }
  function at(x) { return ((x - current.axis.lo) / current.axis.span) * 100; }
  function date(iso) {
    return new Date(iso + "T00:00:00Z").toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  }

  // The agent's name without its version, which the arm's params already give.
  function agentName(arm) { return arm.agent.replace(/\s+[\d.]+$/, ""); }

  // The value most of the lane's arms share, so that only an arm that differs is marked.
  function usual(arms, key) {
    var counts = {};
    arms.forEach(function (a) { counts[a[key]] = (counts[a[key]] || 0) + 1; });
    return Object.keys(counts).sort(function (x, y) { return counts[y] - counts[x]; })[0];
  }

  function track(arm) {
    var steps = current.axis.labels.length - 1;
    var kids = [el("div", { class: "axis" })];
    for (var i = 1; i < steps; i++) kids.push(el("div", { class: "tick", style: "left: " + (i / steps) * 100 + "%" }));
    if (arm.margin != null) {
      kids.push(el("div", { class: "ci", style: "left: " + at(arm.rate - arm.margin) + "%; width: " + (2 * arm.margin / current.axis.span) * 100 + "%" }));
    }
    kids.push(el("div", { class: "dot", style: "left: " + at(arm.rate) + "%" }));
    return el("div", { class: "track", "aria-hidden": "true" }, kids);
  }

  function rateText(arm) {
    return pct(arm.rate) + (arm.margin != null ? ", ±" + Math.round(arm.margin * 100) + " points" : "");
  }

  function passText(arm) { return arm.pass == null ? "—" : pct(arm.pass) + " @" + arm.runs.length; }

  function chips(arm, arms) {
    var out = [];
    if (arm.harness !== usual(arms, "harness")) out.push(el("span", { class: "chip chip-accent", text: arm.harness, title: arm.harnessDetail || arm.harness }));
    if (arm.where !== usual(arms, "where")) out.push(el("span", { class: "chip", text: arm.where }));
    return out;
  }

  function drawHero(lane) {
    var runs = lane.arms.reduce(function (n, a) { return n + a.runs.length; }, 0);
    var facts = [
      lane.tasks + " tasks",
      lane.arms.length + " arms",
      (runs * lane.tasks).toLocaleString("en-AU") + " graded trials",
      "updated " + date(data.updated)
    ];
    hero.replaceChildren(
      el("p", { class: "eyebrow" }, ["Lane ", el("span", { class: "mono", text: lane.title })]),
      el("h1", { id: "lane-title", text: lane.tab || lane.title }),
      el("p", { class: "about", text: lane.about }),
      el("ul", { class: "facts", "aria-label": "About this lane" }, facts.map(function (f) { return el("li", { text: f }); }))
    );
  }

  function board(lane) {
    var arms = lane.arms;
    var rows = arms.map(function (arm, i) {
      var selected = arm.name === picked;
      return el("tr", { class: selected ? "selected" : "" }, [
        el("td", { class: "rank" }, [el("span", { class: "badge" + (i === 0 ? " first" : ""), text: String(i + 1) })]),
        el("td", {}, [el("button", { type: "button", class: "arm-btn", "data-arm": arm.name, "aria-pressed": String(selected), on: { click: function () { pick(arm.name); } } }, [
          el("span", { class: "arm-line" }, [el("span", { class: "arm-name", text: arm.name })].concat(chips(arm, arms))),
          el("span", { class: "arm-sub", text: agentName(arm) + " · " + arm.model })
        ])]),
        el("td", {}, [el("div", { class: "rate-cell" }, [el("span", { class: "rate-text", text: pct(arm.rate), title: rateText(arm) }), track(arm)])]),
        el("td", { class: "num", text: passText(arm) }),
        el("td", { class: "num", text: money(arm.cost) }),
        el("td", { class: "num", text: hours(arm.hours) }),
        el("td", { class: "num", text: tokens(arm.tokens) }),
        el("td", { class: "num" + (arm.timedOut ? "" : " faint"), text: String(arm.timedOut) })
      ]);
    });
    var heads = ["#", "Arm", "Resolved rate", "pass@k", "Cost / run", "Agent time / run", "Tokens / trial", "Timed out"];
    var thead = el("tr", {}, heads.map(function (h, i) {
      if (h !== "Resolved rate") return el("th", { scope: "col", class: i >= 3 ? "num" : "", text: h });
      return el("th", { scope: "col", class: "rate-head" }, [el("span", { text: h }), el("div", { class: "axis-labels", "aria-hidden": "true" }, lane.axis.labels.map(function (t) { return el("span", { text: t }); }))]);
    }));

    var cards = el("ol", { class: "cards", "aria-label": "Arms ranked by resolved rate" }, arms.map(function (arm, i) {
      var selected = arm.name === picked;
      return el("li", {}, [el("button", { type: "button", class: "acard" + (selected ? " selected" : ""), "data-arm": arm.name, "aria-pressed": String(selected), on: { click: function () { pick(arm.name); } } }, [
        el("span", { class: "acard-top" }, [
          el("span", { class: "badge" + (i === 0 ? " first" : ""), text: String(i + 1) }),
          el("span", { class: "who" }, [el("span", { class: "arm-line" }, [el("span", { class: "arm-name", text: arm.name })].concat(chips(arm, arms))), el("span", { class: "arm-sub", text: arm.model })]),
          el("span", { class: "big", text: pct(arm.rate), title: rateText(arm) })
        ]),
        track(arm),
        el("span", { class: "acard-facts" }, [["pass@k", passText(arm)], ["Cost / run", money(arm.cost)], ["Time / run", hours(arm.hours)], ["Tokens / trial", tokens(arm.tokens)], ["Timed out", String(arm.timedOut)]].map(function (f) {
          return el("span", {}, [el("span", { class: "k", text: f[0] }), el("span", { class: "v", text: f[1] })]);
        }))
      ])]);
    }));

    return el("section", { class: "card board", "aria-labelledby": "rank-title" }, [
      el("div", { class: "card-head" }, [
        el("h2", { id: "rank-title", text: "Ranked by resolved rate" }),
        el("p", { class: "legend" }, [
          el("span", { class: "nowrap" }, [el("span", { class: "key-dot", "aria-hidden": "true" }), " rate"]),
          el("span", { class: "nowrap" }, [el("span", { class: "key-bar", "aria-hidden": "true" }), " 95% interval."]),
          "Where two intervals overlap, the gap between those arms may be noise."
        ])
      ]),
      el("div", { class: "scroll table-wrap" }, [el("table", {}, [el("thead", {}, [thead]), el("tbody", {}, rows)])]),
      cards
    ]);
  }

  function details(arm) {
    var platforms = {}, dates = {};
    arm.runs.forEach(function (r) { platforms[r.where.split(", ")[0]] = true; dates[r.date] = true; });
    var oneDate = Object.keys(dates).length === 1;
    var meta = [Object.keys(platforms).join(", "), arm.params];
    if (oneDate) meta.push(date(arm.runs[0].date));
    return el("section", { class: "card detail", "aria-labelledby": "runs-title" }, [el("div", { class: "card-body" }, [
      el("div", { class: "detail-head" }, [
        el("p", { class: "eyebrow dark", text: "Selected arm" }),
        el("h2", { id: "runs-title", class: "mono", text: arm.name }),
        el("p", { class: "small muted", text: meta.join(" · ") })
      ]),
      el("div", { class: "runs-cap", "aria-hidden": "true" }, [el("span", { text: "Runs" }), el("span", { text: "Median trial" })]),
      el("ol", { class: "runs" }, arm.runs.map(function (run, i) {
        var region = run.where.split(", ").slice(1).join(", ") || run.where;
        return el("li", {}, [
          el("span", { class: "run-n", text: "Run " + (i + 1) }),
          el("span", { class: "run-where" }, [region, oneDate ? null : el("span", { class: "muted", text: " · " + date(run.date) })]),
          el("span", { class: "run-job mono", text: run.job }),
          el("span", { class: "run-med", "aria-label": "median trial " + run.median.toFixed(1) + " minutes", text: run.median.toFixed(1) + " min" })
        ]);
      })),
      arm.note ? el("p", { class: "note", text: arm.note }) : null
    ])]);
  }

  function costChart(lane) {
    var s = lane.scatter;
    var points = lane.arms.map(function (arm) {
      var left = (arm.cost / s.maxCost) * 100;
      var bottom = ((arm.rate - s.yLo) / s.ySpan) * 100;
      // A label goes left near the right edge, and anywhere the lane's data moves it off a neighbour's dot.
      var side = arm.label || (left > 60 ? "left" : "right");
      var place = {
        right: "flex-direction: row; transform: translate(-6px, 50%)",
        left: "flex-direction: row-reverse; transform: translate(calc(-100% + 6px), 50%)",
        below: "flex-direction: column; gap: 2px; transform: translate(-50%, calc(100% - 6px))"
      }[side];
      return el("button", { type: "button", class: "pt" + (arm.name === picked ? " on" : ""), "data-arm": arm.name, "aria-label": arm.name + ": " + pct(arm.rate) + " at " + money(arm.cost) + " a run",
        style: "left: " + left + "%; bottom: " + bottom + "%; " + place,
        on: { click: function () { pick(arm.name); } } },
        [el("span", { class: "dot", "aria-hidden": "true" }), el("span", { class: "label", text: arm.name })]);
    });
    return el("section", { class: "card", "aria-labelledby": "cost-title" }, [el("div", { class: "card-body" }, [
      el("div", {}, [el("h2", { id: "cost-title", text: "Resolved rate against cost" }), el("p", { class: "small muted", text: "Cost per run. Up and to the left is better; select a dot to see that arm." })]),
      el("div", { class: "scatter" }, [
        el("div", { class: "y", "aria-hidden": "true" }, s.yLabels.map(function (t) { return el("span", { text: t }); })),
        el("div", { class: "plot-wrap" }, [el("div", { class: "plot" }, points), el("div", { class: "x", "aria-hidden": "true" }, s.xTicks.map(function (t) { return el("span", { text: "$" + t, style: "left: " + (t / s.maxCost) * 100 + "%" }); }))])
      ])
    ])]);
  }

  function laneView(lane) {
    if (!picked || !lane.arms.some(function (a) { return a.name === picked; })) picked = lane.arms[0].name;
    var sel = lane.arms.filter(function (a) { return a.name === picked; })[0];
    return [board(lane), el("div", { class: "pair" }, [costChart(lane), details(sel)])];
  }

  // A render replaces every button, so focus goes back to the one just pressed.
  function pick(name) {
    var pressed = document.activeElement;
    var kind = pressed && pressed.classList ? (pressed.classList.contains("pt") ? "pt" : pressed.classList.contains("acard") ? "acard" : "arm-btn") : "arm-btn";
    picked = name;
    render();
    var again = Array.prototype.filter.call(app.querySelectorAll("." + kind), function (b) {
      return b.dataset.arm === name;
    })[0];
    if (again) again.focus({ preventScroll: true });
  }

  // A hash that names no lane draws the first.
  function render() {
    // Lane names are plain ASCII, so the raw fragment is compared and never decoded.
    var asked = location.hash.slice(1);
    var name = LANES.indexOf(asked) >= 0 && data[asked] ? asked : LANES[0];
    current = data[name];
    document.querySelectorAll(".tabs a").forEach(function (a) {
      if (a.dataset.lane === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = "SWE-drill leaderboard: " + (current.tab || current.title);
    drawHero(current);
    // A definition shows only on a lane that has something it defines.
    var reviewed = current.arms.some(function (a) { return a.harness !== "Default"; });
    document.querySelectorAll("[data-when=peer-review]").forEach(function (d) { d.hidden = !reviewed; });
    app.replaceChildren.apply(app, laneView(current));
  }

  window.addEventListener("hashchange", function () { if (data) { picked = null; render(); window.scrollTo(0, 0); } });

  fetch("leaderboard.json", { cache: "no-cache" })
    .then(function (res) { if (!res.ok) throw new Error("leaderboard.json: HTTP " + res.status); return res.json(); })
    .then(function (json) {
      data = json;
      render();
    })
    .catch(function (err) {
      app.replaceChildren(el("p", { class: "note", text: "The leaderboard could not load: " + err.message }));
    });
})();
