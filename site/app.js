/**
 * Draws the leaderboard from leaderboard.json, one lane at a time, chosen by
 * the URL's hash. Every value goes in as text, never as markup. Each lane gives
 * the range of its rate axis and of its cost chart, since its arms' rates and
 * costs sit in a range of their own.
 */
(function () {
  "use strict";

  var SVG = "http://www.w3.org/2000/svg";
  var LANES = ["rebench", "pro-hard51"];
  var app = document.getElementById("app");
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

  function icon(path) {
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.5");
    var circle = document.createElementNS(SVG, "circle");
    circle.setAttribute("cx", "8"); circle.setAttribute("cy", "8"); circle.setAttribute("r", "6.5");
    var line = document.createElementNS(SVG, "path");
    line.setAttribute("d", path); line.setAttribute("stroke-linecap", "round");
    svg.appendChild(circle); svg.appendChild(line);
    return svg;
  }

  function pct(x) { return Math.round(x * 100) + "%"; }
  function money(x) { return "$" + x.toFixed(2); }
  function hours(x) { return x.toFixed(1) + " h"; }
  function tokens(x) { return x == null ? "—" : Math.round(x / 1000) + "k"; }
  function at(x) { return ((x - current.axis.lo) / current.axis.span) * 100; }
  function date(iso) {
    return new Date(iso + "T00:00:00Z").toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
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

  function laneHead(lane, stats) {
    return el("section", { class: "lane-head", "aria-labelledby": "lane-title" }, [
      el("div", {}, [
        el("p", { class: "eyebrow", text: "Lane" }),
        el("h1", { id: "lane-title", text: lane.title }),
        el("p", { class: "about", text: lane.about })
      ]),
      el("dl", { class: "stats" }, stats.map(function (s) {
        return el("div", { class: "stat" }, [el("dt", { text: s[0] }), el("dd", { text: String(s[1]) })]);
      }))
    ]);
  }

  function laneView(lane) {
    var arms = lane.arms;
    if (!picked || !arms.some(function (a) { return a.name === picked; })) picked = arms[arms.length - 1].name;
    var runs = arms.reduce(function (n, a) { return n + a.runs.length; }, 0);
    var out = [laneHead(lane, [["Arms", arms.length], ["Runs", runs], ["Trials", (runs * lane.tasks).toLocaleString("en-AU")]])];

    var rows = arms.map(function (arm, i) {
      var selected = arm.name === picked;
      return el("tr", { class: selected ? "selected" : "" }, [
        el("td", { class: "rank", text: String(i + 1) }),
        el("td", {}, [el("button", { type: "button", class: "arm-btn", "data-arm": arm.name, "aria-pressed": String(selected), on: { click: function () { pick(arm.name); } } }, [
          el("span", { class: "arm-name", text: arm.name }),
          el("span", { class: "arm-sub", text: arm.agent + " · " + arm.model })
        ])]),
        el("td", {}, [el("span", { class: "chip", text: arm.harness, title: arm.harnessDetail || arm.harness })]),
        el("td", {}, [el("div", { class: "rate-cell" }, [el("span", { class: "rate-text", text: pct(arm.rate), title: rateText(arm) }), track(arm)])]),
        el("td", { class: "num", text: passText(arm) }),
        el("td", { class: "num", text: String(arm.runs.length) }),
        el("td", { class: "num", text: String(arm.timedOut) }),
        el("td", { class: "num", text: money(arm.cost) }),
        el("td", { class: "num", text: hours(arm.hours) }),
        el("td", { class: "num", text: tokens(arm.tokens) }),
        el("td", {}, [el("span", { class: "chip", text: arm.where })])
      ]);
    });
    var heads = ["#", "Arm", "Harness", "Resolved rate", "pass@k", "Runs", "Timed out", "Cost / run", "Agent time / run", "Tokens / trial", "Ran on"];
    var thead = el("tr", {}, heads.map(function (h, i) {
      var cls = i >= 4 && i <= 9 ? "num" : "";
      if (h !== "Resolved rate") return el("th", { scope: "col", class: cls, text: h });
      return el("th", { scope: "col" }, [el("div", { text: h }), el("div", { class: "axis-labels", "aria-hidden": "true" }, lane.axis.labels.map(function (t) { return el("span", { text: t }); }))]);
    }));
    out.push(el("section", { class: "card rank-card", "aria-labelledby": "rank-title" }, [
      el("div", { class: "card-head" }, [
        el("div", {}, [el("h2", { id: "rank-title", text: "Ranked by resolved rate" }), el("p", { class: "small muted", text: "Select an arm to see its runs." })]),
        el("div", { class: "legend" }, [el("span", {}, [el("span", { class: "key-dot", "aria-hidden": "true" }), "Resolved rate"]), el("span", {}, [el("span", { class: "key-bar", "aria-hidden": "true" }), "95% interval"])])
      ]),
      el("div", { class: "scroll" }, [el("table", { style: "min-width: 1160px" }, [el("thead", {}, [thead]), el("tbody", {}, rows)])]),
      el("div", { class: "card-foot" }, [icon("M8 7.2v4M8 4.8v.1"), el("span", {}, [el("strong", { text: "Gaps of a few points may be noise. " }), "Every interval here overlaps the others, and each covers an arm’s own rate, not the gap between two arms. An arm with one run shows no interval."])])
    ]));

    out.push(el("ol", { class: "cards", "aria-label": "Arms ranked by resolved rate" }, arms.map(function (arm, i) {
      var facts = [["Harness", arm.harnessDetail || arm.harness], ["pass@k", passText(arm)], ["Cost / run", money(arm.cost)], ["Time / run", hours(arm.hours)], ["Runs", String(arm.runs.length)], ["Timed out", String(arm.timedOut)], ["Ran on", arm.where], ["Tokens / trial", tokens(arm.tokens)]];
      return el("li", { class: "acard" }, [
        el("div", { class: "acard-top" }, [
          el("span", { class: "badge", text: String(i + 1) }),
          el("div", { class: "who" }, [el("span", { class: "arm-name", text: arm.name }), el("span", { class: "arm-sub", text: arm.agent + " · " + arm.model })]),
          el("span", { class: "big", text: pct(arm.rate), title: rateText(arm) })
        ]),
        track(arm),
        el("dl", {}, facts.map(function (f) { return el("div", {}, [el("dt", { text: f[0] }), el("dd", { text: f[1] })]); })),
        arm.note ? el("p", { class: "note", text: arm.note }) : null
      ]);
    })));

    var sel = arms.filter(function (a) { return a.name === picked; })[0];
    out.push(el("section", { class: "card rank-card", "aria-labelledby": "runs-title" }, [el("div", { class: "card-body" }, [
      el("div", { style: "display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; justify-content: space-between" }, [
        el("h2", { id: "runs-title" }, [el("span", { class: "mono", text: sel.name }), "’s runs"]),
        el("span", { class: "small muted", text: sel.params })
      ]),
      el("div", { class: "scroll" }, [el("table", { style: "min-width: 620px" }, [
        el("thead", {}, [el("tr", {}, ["Job", "Date", "Where it ran", "Median trial"].map(function (h, i) { return el("th", { scope: "col", class: i === 3 ? "num" : "", text: h }); }))]),
        el("tbody", {}, sel.runs.map(function (run) {
          return el("tr", {}, [el("td", { class: "mono small", text: run.job }), el("td", { text: date(run.date) }), el("td", { text: run.where }), el("td", { class: "num", text: run.median.toFixed(1) + " min" })]);
        }))
      ])]),
      sel.note ? el("div", { class: "note" }, [el("span", { text: sel.note })]) : null
    ])]));

    var maxCost = lane.scatter.maxCost, yLo = lane.scatter.yLo, ySpan = lane.scatter.ySpan;
    var points = arms.map(function (arm) {
      var left = (arm.cost / maxCost) * 100;
      var bottom = ((arm.rate - yLo) / ySpan) * 100;
      var flip = left > 60;
      return el("div", { class: "pt", title: arm.name + ": " + pct(arm.rate) + " at " + money(arm.cost) + " a run",
        style: "left: " + left + "%; bottom: " + bottom + "%; flex-direction: " + (flip ? "row-reverse" : "row") + "; transform: translate(" + (flip ? "calc(-100% + 6px)" : "-6px") + ", 50%)" },
        [el("span", { class: "dot", "aria-hidden": "true" }), el("span", { class: "label", text: arm.name })]);
    });
    var described = arms.map(function (a) { return a.name + " " + pct(a.rate) + " at " + money(a.cost); }).join("; ");
    out.push(el("div", { class: "pair" }, [
      el("section", { class: "card", "aria-labelledby": "cost-title" }, [el("div", { class: "card-body" }, [
        el("div", {}, [el("h2", { id: "cost-title", text: "Resolved rate against cost" }), el("p", { class: "small muted", text: "Cost per run at pay-as-you-go prices. Up and to the left is better." })]),
        el("div", { class: "scatter", role: "img", "aria-label": "Resolved rate against cost per run: " + described }, [
          el("div", { class: "y", "aria-hidden": "true" }, lane.scatter.yLabels.map(function (t) { return el("span", { text: t }); })),
          el("div", { class: "plot-wrap" }, [el("div", { class: "plot" }, points), el("div", { class: "x", "aria-hidden": "true" }, lane.scatter.xTicks.map(function (t) { return el("span", { text: "$" + t, style: "left: " + (t / maxCost) * 100 + "%" }); }))])
        ])
      ])]),
      el("section", { class: "card", "aria-labelledby": "read-title" }, [el("div", { class: "card-body" }, [
        el("h2", { id: "read-title", text: "How to read it" }),
        el("dl", { class: "read" }, [
          ["Harness", "What runs around the coding agent. Default is the agent alone; peer review is squiz reviewing each change the agent makes, with a fixer that answers the findings."],
          ["Runs", "How many times the arm ran every task, one trial per task each time. Rates pool all runs; cost and time are per run."],
          ["Resolved rate", "Resolved trials over valid trials, with its 95% interval."],
          ["pass@k", "The share of tasks that at least one of the arm’s k runs resolved."],
          ["Timed out", "Trials stopped at the agent’s time limit, counted as unresolved."],
          ["Tokens / trial", "The median tokens the agent read and wrote in one trial, cached reads included: a rough size of the task."],
          ["Cost", "AI credits at $0.01 each for Copilot CLI, Anthropic’s API price for Claude Code. A price, not what was paid."]
        ].map(function (d) { return el("div", {}, [el("dt", { text: d[0] }), el("dd", { text: d[1] })]); }))
      ])])
    ]));
    return out;
  }

  // A render replaces every button, so focus goes back to the one just pressed.
  function pick(name) {
    picked = name;
    render();
    var again = Array.prototype.filter.call(app.querySelectorAll(".arm-btn"), function (b) { return b.dataset.arm === name; })[0];
    if (again) again.focus();
  }

  // A hash that names no lane draws the first.
  function render() {
    var asked = decodeURIComponent(location.hash.slice(1));
    var name = LANES.indexOf(asked) >= 0 && data[asked] ? asked : LANES[0];
    current = data[name];
    document.querySelectorAll(".tabs a").forEach(function (a) {
      if (a.dataset.lane === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = "SWE-drill leaderboard: " + (current.tab || current.title);
    app.replaceChildren.apply(app, laneView(current));
  }

  window.addEventListener("hashchange", function () { if (data) { picked = null; render(); window.scrollTo(0, 0); } });

  fetch("leaderboard.json", { cache: "no-cache" })
    .then(function (res) { if (!res.ok) throw new Error("leaderboard.json: HTTP " + res.status); return res.json(); })
    .then(function (json) {
      data = json;
      document.getElementById("updated").textContent = "Updated " + date(json.updated);
      render();
    })
    .catch(function (err) {
      app.replaceChildren(el("p", { class: "note", text: "The leaderboard could not load: " + err.message }));
    });
})();
