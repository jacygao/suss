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
      arm.takeaways && arm.takeaways.length ? el("ol", { class: "take" }, arm.takeaways.map(function (t) { return el("li", { text: t }); })) : null,
      el("a", { class: "go", href: "#" + current.title + "/" + arm.name }, ["See the full insights", el("span", { class: "arrow", "aria-hidden": "true", text: "→" })])
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

  function bar(share, cls) {
    return el("span", { class: "meter", "aria-hidden": "true" }, [el("span", { class: "fill" + (cls ? " " + cls : ""), style: "width: " + Math.max(share * 100, 1.5) + "%" })]);
  }

  // A long list shows its first dozen and folds the rest away.
  function taskList(tasks, empty) {
    if (!tasks.length) return el("p", { class: "small muted", text: empty });
    function items(list) { return el("ul", { class: "tasks" }, list.map(function (t) { return el("li", { class: "mono", text: t }); })); }
    if (tasks.length <= 12) return items(tasks);
    return el("div", { class: "fold" }, [items(tasks.slice(0, 12)), el("details", {}, [el("summary", { text: (tasks.length - 12) + " more" }), items(tasks.slice(12))])]);
  }

  function insCard(title, sub, kids) {
    return el("section", { class: "card" }, [el("div", { class: "card-body" }, [
      el("div", {}, [el("h2", { text: title }), sub ? el("p", { class: "small muted", text: sub }) : null])
    ].concat(kids))]);
  }

  function drawArmHero(lane, arm) {
    var rank = lane.arms.indexOf(arm) + 1;
    var facts = [
      "#" + rank + " of " + lane.arms.length,
      pct(arm.rate) + (arm.margin != null ? " ±" + Math.round(arm.margin * 100) : "") + " resolved",
      "pass@k " + passText(arm),
      money(arm.cost) + " a run"
    ];
    hero.replaceChildren(
      el("a", { class: "back", href: "#" + lane.title }, [el("span", { "aria-hidden": "true", text: "← " }), "All arms on " + (lane.tab || lane.title)]),
      el("p", { class: "eyebrow", text: "Insights" }),
      el("h1", { class: "mono arm-title", text: arm.name }),
      el("p", { class: "about", text: [agentName(arm), arm.model].concat(arm.params.split(", ").filter(function (p) { return arm.model.indexOf(p) < 0; })).join(" · ") }),
      el("ul", { class: "facts", "aria-label": "About this arm" }, facts.map(function (f) { return el("li", { text: f }); }))
    );
  }

  function insightsView(lane, arm, ins) {
    var out = [];
    if (arm.takeaways && arm.takeaways.length) {
      out.push(el("section", { class: "card takeaways", "aria-labelledby": "take-title" }, [el("div", { class: "card-body" }, [
        el("h2", { id: "take-title", text: "Key takeaways" }),
        el("ol", {}, arm.takeaways.map(function (t) { return el("li", { text: t }); }))
      ])]));
    }
    if (!ins) {
      out.push(el("p", { class: "note", text: "No computed insights for this arm yet." }));
      return out;
    }
    var n = lane.tasks;
    var cards = [];

    cards.push(insCard("Run by run", "Resolved trials in each run, out of " + n + ".", [el("ul", { class: "rows" }, ins.runs.map(function (run, i) {
      var where = (arm.runs[i] && arm.runs[i].where.split(", ").slice(1).join(", ")) || (arm.runs[i] && arm.runs[i].where) || run.job;
      // Only Copilot CLI reports credits; another agent records none.
      var extra = [];
      if (arm.runs[i] && arm.runs[i].median != null) extra.push("median " + arm.runs[i].median.toFixed(1) + " min");
      if (run.credits) extra.push(run.credits.toLocaleString("en-AU") + " credits");
      if (run.timedOut) extra.push(run.timedOut + " timed out");
      return el("li", {}, [
        el("span", { class: "row-label" }, [where, el("span", { class: "run-job mono", text: run.job }), el("span", { class: "small muted", text: extra.join(" · ") })]),
        bar(run.resolved / run.trials),
        el("span", { class: "row-val", text: run.resolved + " / " + run.trials })
      ]);
    })), arm.note ? el("p", { class: "note", text: arm.note }) : null]));

    var always = ins.tasks.always, some = ins.tasks.sometimes.length, never = ins.tasks.never.length, all = always + some + never;
    cards.push(insCard("How consistent", "Each task, by how many of the arm’s runs resolved it.", [
      el("div", { class: "stack", role: "img", "aria-label": always + " tasks in every run, " + some + " in some, " + never + " in none" }, [
        el("span", { class: "seg s-all", style: "flex-grow: " + always }),
        el("span", { class: "seg s-some", style: "flex-grow: " + some }),
        el("span", { class: "seg s-none", style: "flex-grow: " + never })
      ]),
      el("ul", { class: "key" }, [["s-all", "Every run", always], ["s-some", "Some runs", some], ["s-none", "No run", never]].map(function (k) {
        return el("li", {}, [el("span", { class: "sw " + k[0], "aria-hidden": "true" }), k[1] + " ", el("strong", { text: String(k[2]) }), el("span", { class: "muted", text: " · " + pct(k[2] / all) })]);
      })),
      el("h3", { text: "Solved in some runs" }), taskList(ins.tasks.sometimes, "None: every task went the same way in every run."),
      el("h3", { text: "Never solved" }), taskList(ins.tasks.never, "None.")
    ]));

    cards.push(insCard("Against the other arms", "Tasks that set this arm apart on " + (lane.tab || lane.title) + ".", [
      el("h3", { text: "Only this arm solved" }), taskList(ins.only, "None: another arm solved every task this one did."),
      el("h3", { text: "Every other arm solved, this one never did" }), taskList(ins.missed, "None.")
    ]));

    var reasons = Object.keys(ins.failures), lost = reasons.reduce(function (t, k) { return t + ins.failures[k]; }, 0);
    cards.push(insCard("Why trials failed", lost + " unresolved trials, by the first thing that went wrong.", [el("ul", { class: "rows" }, reasons.map(function (k) {
      return el("li", {}, [el("span", { class: "row-label", text: k }), bar(ins.failures[k] / lost, "warm"), el("span", { class: "row-val", text: String(ins.failures[k]) })]);
    }))]));

    function heavy(title, median, rows, fmt) {
      return [el("h3", {}, [title + " ", el("span", { class: "muted", text: "median " + fmt(median) })]), el("ul", { class: "rows tight" }, rows.map(function (r) {
        return el("li", {}, [el("span", { class: "row-label mono", text: r.task }), el("span", { class: "row-val", text: fmt(r.value) })]);
      }))];
    }
    cards.push(insCard("Heaviest trials", "The trials that took the most tokens and the most time.",
      heavy("Tokens", ins.tokens.median, ins.tokens.top, function (v) { return tokens(v); })
        .concat(heavy("Time", ins.minutes.median, ins.minutes.top, function (v) { return v == null ? "—" : v.toFixed(1) + " min"; }))));

    if (ins.models) {
      var picks = Object.keys(ins.models), total = picks.reduce(function (t, k) { return t + ins.models[k]; }, 0);
      cards.push(insCard("Models it ran on", "Trials by the models Copilot routed them to.", [el("ul", { class: "rows" }, picks.map(function (k) {
        return el("li", {}, [el("span", { class: "row-label mono", text: k }), bar(ins.models[k] / total), el("span", { class: "row-val", text: pct(ins.models[k] / total) })]);
      }))]));
    }
    out.push(el("div", { class: "ins-grid" }, cards));
    return out;
  }

  // A failed load is forgotten, so the next visit to an insights page tries again.
  var insights = null;
  function loadInsights() {
    if (!insights) {
      insights = fetch("insights.json", { cache: "no-cache" })
        .then(function (res) { if (!res.ok) throw new Error("insights.json: HTTP " + res.status); return res.json(); })
        .catch(function (err) { insights = null; throw err; });
    }
    return insights;
  }

  // The lane and, on an insights page, the arm the hash names. Names are plain ASCII, so the raw fragment is compared and never decoded.
  function route() {
    var parts = location.hash.slice(1).split("/");
    var lane = LANES.indexOf(parts[0]) >= 0 && data[parts[0]] ? parts[0] : LANES[0];
    var arm = parts.length === 2 ? data[lane].arms.filter(function (a) { return a.name === parts[1]; })[0] : null;
    return { lane: lane, arm: arm || null };
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

  // A hash that names no lane draws the first; one that names an arm of it draws that arm's insights.
  function render() {
    var at = route();
    current = data[at.lane];
    document.querySelectorAll(".tabs a").forEach(function (a) {
      if (a.dataset.lane === at.lane) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    // A definition shows only on a lane that has something it defines.
    var reviewed = current.arms.some(function (a) { return a.harness !== "Default"; });
    document.querySelectorAll("[data-when=peer-review]").forEach(function (d) { d.hidden = !reviewed; });
    if (at.arm) {
      var arm = at.arm;
      picked = arm.name;
      document.title = arm.name + " on " + (current.tab || current.title) + ": SWE-drill";
      drawArmHero(current, arm);
      app.replaceChildren(el("p", { class: "muted", text: "Loading insights…" }));
      loadInsights().then(function (all) {
        // A later navigation has drawn something else meanwhile.
        if (route().arm !== arm) return;
        app.replaceChildren.apply(app, insightsView(current, arm, (all[at.lane] || {})[arm.name]));
      }, function (err) {
        if (route().arm !== arm) return;
        app.replaceChildren(el("p", { class: "note", text: "The insights could not load: " + err.message + ". Reload the page to try again." }));
      });
      return;
    }
    document.title = "SWE-drill leaderboard: " + (current.tab || current.title);
    drawHero(current);
    app.replaceChildren.apply(app, laneView(current));
  }

  // Leaving a lane forgets its selection; coming back from an arm's insights keeps that arm selected.
  var shown = null;
  window.addEventListener("hashchange", function () {
    if (!data) return;
    var lane = route().lane;
    if (lane !== shown) picked = null;
    shown = lane;
    render();
    window.scrollTo(0, 0);
  });

  fetch("leaderboard.json", { cache: "no-cache" })
    .then(function (res) { if (!res.ok) throw new Error("leaderboard.json: HTTP " + res.status); return res.json(); })
    .then(function (json) {
      data = json;
      shown = route().lane;
      render();
    })
    .catch(function (err) {
      app.replaceChildren(el("p", { class: "note", text: "The leaderboard could not load: " + err.message }));
    });
})();
