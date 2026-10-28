(function () {
  "use strict";

  var token = document.querySelector('meta[name="os-token"]').content;
  var NAME_OK = /^[a-z0-9:_-]{1,64}$/;
  var PATH_OK = /^[A-Za-z]:[\\/][A-Za-z0-9 _.'\-\\/]{1,200}$/;
  var TYPES = [["project", "⬢", "Projects"], ["report", "■", "Reports"], ["routine", "○", "Routines"], ["skill", "•", "Skills"], ["agent", "◆", "Agents"], ["idea", "✦", "Ideas"]];
  var $ = function (id) {
    return document.getElementById(id);
  };
  var nodes = {
    banner: $("banner"),
    q: $("q"),
    results: $("results"),
    detail: $("detail"),
    side: $("side"),
    live: $("live"),
    status: $("scan-status"),
    canvas: $("world"),
    filters: $("filter-list"),
    filtersBox: $("filters"),
    calm: $("calm"),
    rescan: $("rescan"),
    ticker: $("ticker-track"),
    clock: $("clock")
  };
  var graph = null;
  var worldData = null;
  var overviewData = null;
  var searchItems = [];
  var hits = [];
  var active = -1;
  var openIds = new Set();
  var calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var lastScanState = "";
  var selectedNode = null;
  var pollTimer = null;
  var announced = "";
  var VIEWS = ["today", "world", "cards"];
  var currentView = "today";
  var VERBS = { Read: "Reading", Glob: "Listing", Grep: "Searching" };
  var CHIP_TAIL = "read only · Sonnet · files Claude reads are sent to Anthropic";
  var kept = {};
  var chat = {
    node: null,
    id: null,
    running: false,
    ready: false,
    log: $("chat-log"),
    input: $("chat-input"),
    send: $("chat-send"),
    stop: $("chat-stop"),
    fresh: $("chat-new"),
    chip: $("chat-chip"),
    cost: $("chat-cost"),
    problem: $("chat-problem"),
    orphan: null
  };

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function list(values, className) {
    var items = element("ul");
    values.forEach(function (value) {
      var item = element("li");
      if (typeof value === "string") {
        item.textContent = value;
        if (className) {
          item.className = className;
        }
      } else {
        value.forEach(function (part) {
          item.append(part);
        });
      }
      items.append(item);
    });
    return items;
  }

  function figures(pairs) {
    var table = element("dl");
    pairs.forEach(function (pair) {
      table.append(element("dt", "", pair[0]), element("dd", "", String(pair[1])));
    });
    return table;
  }

  function explain(error) {
    if (error.status === 403) {
      return "The server restarted. Reload the page.";
    }
    if (error.status === undefined) {
      return "Could not reach the server. Is the server window still open?";
    }
    return error.message;
  }

  function api(path, options) {
    var settings = options || {};
    settings.headers = Object.assign({ "X-OS-Token": token }, settings.headers || {});
    return fetch(path, settings).then(function (response) {
      return response.json().catch(function () {
        return {};
      }).then(function (data) {
        if (!response.ok) {
          var error = new Error(data.error || "Something went wrong. Try Refresh.");
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  function say(message) {
    nodes.live.textContent = message;
  }

  function showError(error) {
    nodes.banner.hidden = false;
    nodes.banner.textContent = explain(error);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = element("textarea", "sr");
      area.value = text;
      document.body.append(area);
      area.select();
      var ok = document.execCommand("copy");
      area.remove();
      return ok ? resolve() : reject(new Error("Copy was blocked."));
    });
  }

  function promptFor(node) {
    if (node.type === "project" && PATH_OK.test(node.path)) {
      return "Work in " + node.path + ". Read its CLAUDE.md first, then check git status and the last 10 commits and tell me what is unfinished.";
    }
    if (node.type === "report" && PATH_OK.test(node.path)) {
      return "Read the newest report in " + node.path + " and tell me what changed since the one before it.";
    }
    if (node.type === "routine" && NAME_OK.test(node.name)) {
      return "Show me what the " + node.name + " routine last found and what needs me.";
    }
    if (node.type === "skill" && NAME_OK.test(node.name)) {
      return "Use the " + node.name + " skill on ";
    }
    if (node.type === "agent" && NAME_OK.test(node.name)) {
      return "Use the " + node.name + " agent to ";
    }
    return null;
  }

  function wireAddForm(form, input, status) {
    var submit = form.querySelector("button");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = input.value.trim();
      if (!text) {
        status.textContent = "Write an idea first.";
        return;
      }
      submit.disabled = true;
      api("/api/inbox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      }).then(function () {
        input.value = "";
        return Promise.all([loadOverview(true), loadWorld(true)]);
      }).then(function () {
        status.textContent = "Added.";
      }).catch(function (error) {
        status.textContent = explain(error);
      }).then(function () {
        submit.disabled = false;
      });
    });
  }

  function buildAddForm() {
    var form = element("form", "add-form");
    var label = element("label", "", "Add an idea");
    var row = element("div", "row");
    var input = element("input");
    var button = element("button", "tool", "Add");
    var status = element("p", "note");
    input.type = "text";
    input.maxLength = 300;
    input.autocomplete = "off";
    input.id = "idea-world";
    label.htmlFor = input.id;
    status.setAttribute("role", "status");
    button.type = "submit";
    row.append(input, button);
    form.append(label, row, status);
    wireAddForm(form, input, status);
    return form;
  }

  function showGitDetail(panel, detail) {
    var parts = [];
    if (detail.kind === "error") {
      parts.push(element("p", "note", detail.error));
    } else if (detail.kind === "git") {
      parts.push(element("h4", "", "Latest commits"));
      if (detail.commits.length) {
        parts.push(list(detail.commits.map(function (commit) {
          return [element("span", "mono", commit.hash), " " + commit.subject + " ", element("span", "when", commit.date)];
        })));
      } else {
        parts.push(element("p", "note", detail.empty ? "No commits yet." : "No commits."));
      }
      parts.push(element("h4", "", "Uncommitted"));
      if (detail.uncommitted.length) {
        parts.push(list(detail.uncommitted, "mono"));
        if (detail.uncommitted_total > detail.uncommitted.length) {
          parts.push(element("p", "note", "and " + (detail.uncommitted_total - detail.uncommitted.length) + " more"));
        }
      } else {
        parts.push(element("p", "note", "Nothing uncommitted."));
      }
    } else {
      parts.push(element("h4", "", "Changed files, last 7 days"));
      if (detail.files.length) {
        parts.push(list(detail.files.map(function (file) {
          return [file.path + " ", element("span", "when", file.date)];
        })));
        if (detail.total > detail.files.length) {
          parts.push(element("p", "note", "and " + (detail.total - detail.files.length) + " more"));
        }
      } else {
        parts.push(element("p", "note", "Nothing changed."));
      }
    }
    panel.replaceChildren.apply(panel, parts);
  }

  function loadGitPanel(key, panel) {
    var ticket = (panel.ticket || 0) + 1;
    panel.ticket = ticket;
    panel.replaceChildren(element("p", "note", "Loading."));
    api("/api/project?id=" + encodeURIComponent(key)).then(function (detail) {
      if (panel.ticket === ticket) {
        showGitDetail(panel, detail);
      }
    }).catch(function (error) {
      if (panel.ticket === ticket) {
        panel.replaceChildren(element("p", "note", explain(error)));
      }
    });
  }

  function problemCard(label, path, message) {
    var card = element("article", "card problem");
    card.append(element("h3", "", label), element("p", "what", "Could not read"));
    if (path) {
      card.append(element("p", "path", path));
    }
    card.append(element("p", "note", message));
    return card;
  }

  function setCardOpen(button, panel, card, open) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.querySelector(".card-more").textContent = open ? "Hide details" : "Show details";
    panel.hidden = !open;
    if (open) {
      openIds.add(card.id);
      loadGitPanel(card.id, panel);
    } else {
      panel.ticket = (panel.ticket || 0) + 1;
      openIds.delete(card.id);
    }
  }

  function projectCard(card) {
    if (card.kind === "error") {
      return problemCard(card.label, card.path, card.error);
    }
    var article = element("article", "card");
    var heading = element("h3");
    var button = element("button", "card-head");
    var panel = element("div", "panel");
    panel.id = "panel-" + card.id;
    panel.hidden = true;
    panel.tabIndex = 0;
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", card.label + " details");
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", panel.id);
    button.append(element("span", "card-title", card.label), element("span", "path", card.path), element("span", "card-more", "Show details"));
    heading.append(button);
    article.append(heading);
    if (card.kind === "git") {
      article.append(figures([["Commits, last 7 days", card.commits], ["Uncommitted files", card.uncommitted]]));
      if (card.subjects.length) {
        article.append(list(card.subjects));
      }
    } else {
      article.append(element("p", "note", "No git here."), figures([["Files changed, last 7 days", card.changed]]));
      article.append(card.newest.length ? list(card.newest) : element("p", "note", "Nothing changed."));
    }
    article.append(panel);
    button.addEventListener("click", function () {
      setCardOpen(button, panel, card, button.getAttribute("aria-expanded") !== "true");
    });
    if (openIds.has(card.id)) {
      setCardOpen(button, panel, card, true);
    }
    return article;
  }

  function reportCard(card) {
    if (card.kind === "error") {
      return problemCard(card.label, card.path, card.error);
    }
    var article = element("article", "card");
    article.append(element("h3", "", card.label), element("p", "path", card.path));
    if (card.kind === "fixed") {
      article.append(list([[card.name + " ", element("span", "when", card.date)]]));
    } else if (card.files.length) {
      article.append(list(card.files.map(function (file) {
        return [file[0] + " ", element("span", "when", file[1])];
      })));
    } else {
      article.append(element("p", "note", "Nothing in the last 7 days."));
    }
    return article;
  }

  function renderOverview(data) {
    var projects = $("projects");
    var reports = $("reports");
    var ideas = $("ideas");
    renderToday();
    projects.replaceChildren.apply(projects, data.projects.map(projectCard));
    reports.replaceChildren.apply(reports, data.reports.map(reportCard));
    ideas.replaceChildren.apply(ideas, data.inbox.ideas.map(function (idea) {
      return element("li", "", idea);
    }));
    $("ideas-empty").hidden = data.inbox.ideas.length > 0 || Boolean(data.inbox.error);
    $("updated").textContent = "Updated " + data.generated;
    var problems = data.problems.slice();
    if (data.inbox.error) {
      problems.push(data.inbox.error);
    }
    nodes.banner.hidden = problems.length === 0;
    nodes.banner.textContent = problems.join(" ");
    $("strip-updated").textContent = "Updated " + data.generated;
    $("strip-problems").textContent = problems.length + (problems.length === 1 ? " problem" : " problems");
    $("strip-problems").className = problems.length ? "bad" : "";
  }

  function loadOverview(fresh) {
    $("refresh").disabled = true;
    return api(fresh ? "/api/overview?fresh=1" : "/api/overview").then(function (data) {
      overviewData = data;
      renderOverview(data);
      buildSearchItems();
      buildTicker();
    }).catch(showError).then(function () {
      $("refresh").disabled = false;
    });
  }

  function buildTicker() {
    var lines = [];
    (overviewData ? overviewData.projects : []).forEach(function (card) {
      (card.subjects || []).forEach(function (subject) {
        lines.push(card.label + ": " + subject);
      });
    });
    nodes.ticker.replaceChildren.apply(nodes.ticker, lines.map(function (line) {
      return element("span", "", line);
    }));
  }

  function buildSearchItems() {
    searchItems = [];
    if (worldData) {
      worldData.nodes.forEach(function (node) {
        searchItems.push({ kind: node.type, node: node, label: node.label, note: node.desc || node.path, text: [node.label, node.name, node.scope, node.path, node.desc].join(" ").toLowerCase(), use: node.use || 0 });
      });
    }
    if (overviewData && worldData) {
      overviewData.projects.forEach(function (card) {
        var owner = worldData.nodes.filter(function (node) {
          return node.type === "project" && node.key === card.id;
        })[0];
        (card.subjects || []).forEach(function (subject) {
          searchItems.push({ kind: "commit", node: owner || null, label: subject, note: card.label, text: (subject + " " + card.label).toLowerCase(), use: 0 });
        });
      });
    }
  }

  function search(query) {
    var tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
    var kinds = TYPES.map(function (type) {
      return type[0];
    }).concat(["commit", "hub"]);
    var only = null;
    if (tokens.length && kinds.indexOf(tokens[0]) >= 0) {
      only = tokens.shift();
    }
    if (!tokens.length && !only) {
      return [];
    }
    var scored = [];
    searchItems.forEach(function (item) {
      if (only && item.kind !== only) {
        return;
      }
      var label = item.label.toLowerCase();
      var score = 0;
      var every = tokens.every(function (token) {
        if (label.indexOf(token) === 0) {
          score += 6;
          return true;
        }
        if (label.indexOf(token) > 0) {
          score += 4;
          return true;
        }
        if (item.text.indexOf(token) >= 0) {
          score += 1;
          return true;
        }
        return false;
      });
      if (every) {
        scored.push({ item: item, score: score + Math.min(item.use, 50) / 50 });
      }
    });
    scored.sort(function (a, b) {
      return b.score - a.score;
    });
    return scored.slice(0, 12).map(function (entry) {
      return entry.item;
    });
  }

  function renderResults() {
    var items = hits.map(function (item, i) {
      var row = element("li");
      row.setAttribute("role", "option");
      row.id = "opt-" + i;
      row.setAttribute("aria-selected", i === active ? "true" : "false");
      row.append(element("span", "badge", item.kind), element("span", "hit-label", item.label), element("span", "hit-note", item.note || ""));
      row.addEventListener("mousedown", function (event) {
        event.preventDefault();
        pick(item);
      });
      return row;
    });
    if (!items.length && nodes.q.value.trim()) {
      var none = element("li", "none", "Nothing matches. Try a type first, for example skill or report.");
      none.setAttribute("role", "option");
      items.push(none);
    }
    nodes.results.replaceChildren.apply(nodes.results, items);
    nodes.results.hidden = items.length === 0;
    nodes.q.setAttribute("aria-expanded", items.length ? "true" : "false");
    if (active >= 0) {
      nodes.q.setAttribute("aria-activedescendant", "opt-" + active);
    } else {
      nodes.q.removeAttribute("aria-activedescendant");
    }
  }

  function pick(item) {
    nodes.results.hidden = true;
    nodes.q.setAttribute("aria-expanded", "false");
    nodes.q.value = "";
    hits = [];
    active = -1;
    if (item.node) {
      showView("world");
      graph.select(item.node.id, true);
    }
  }

  function setupSearch() {
    nodes.q.addEventListener("input", function () {
      hits = search(nodes.q.value);
      active = hits.length ? 0 : -1;
      renderResults();
    });
    nodes.q.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" && hits.length) {
        active = (active + 1) % hits.length;
      } else if (event.key === "ArrowUp" && hits.length) {
        active = (active - 1 + hits.length) % hits.length;
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault();
        pick(hits[active]);
        return;
      } else if (event.key === "Escape") {
        nodes.results.hidden = true;
        nodes.q.setAttribute("aria-expanded", "false");
        return;
      } else {
        return;
      }
      event.preventDefault();
      renderResults();
    });
    nodes.q.addEventListener("blur", function () {
      nodes.results.hidden = true;
      nodes.q.setAttribute("aria-expanded", "false");
    });
    document.addEventListener("keydown", function (event) {
      var typing = /^(input|textarea|select)$/i.test(event.target.tagName);
      if ((event.key === "/" && !typing) || (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey))) {
        event.preventDefault();
        nodes.q.focus();
        nodes.q.select();
      }
    });
  }

  function usageLine(node) {
    if (node.type !== "skill" && node.type !== "agent") {
      return null;
    }
    if (node.use > 0) {
      return "Used " + node.use + (node.use === 1 ? " time" : " times") + ", last seen " + node.last + ".";
    }
    return worldData && worldData.scan && worldData.scan.state === "done" ? "No recorded use." : "Use is still being counted.";
  }

  function openDetail(node, takeFocus) {
    selectedNode = node;
    var box = nodes.detail;
    var hadFocus = box.contains(document.activeElement);
    var parts = [];
    var title = element("h2", "", node.label);
    title.tabIndex = -1;
    parts.push(title, element("p", "meta", node.type + (node.scope && node.scope !== "index" ? " · " + node.scope : "")));
    if (node.path) {
      parts.push(element("p", "mono", node.path));
    }
    if (node.desc && node.desc !== node.label) {
      parts.push(element("p", "", node.desc));
    }
    var usage = usageLine(node);
    if (usage) {
      parts.push(element("p", "note", usage));
    }
    var prompt = promptFor(node);
    if (prompt) {
      var copy = element("button", "tool", "Copy prompt");
      var done = element("p", "note");
      copy.type = "button";
      done.setAttribute("role", "status");
      copy.addEventListener("click", function () {
        copyText(prompt).then(function () {
          done.textContent = "Copied. Paste it into a new session.";
        }).catch(function () {
          done.textContent = "The browser blocked copying. Select this text instead: " + prompt;
        });
      });
      parts.push(copy, done);
    }
    var ask = element("button", "tool ask", "Ask Claude");
    ask.type = "button";
    ask.disabled = chat.running;
    ask.addEventListener("click", function () {
      openChat(node);
    });
    parts.push(ask);
    var near = graph.neighbours(node.id);
    parts.push(element("h4", "", "Connections (" + near.length + ")"));
    if (near.length) {
      var linkList = element("ul", "links");
      near.slice(0, 40).forEach(function (entry) {
        var item = element("li");
        var go = element("button", "link");
        go.type = "button";
        go.append(element("span", "", entry.node.label), element("span", "kind", entry.kind));
        go.addEventListener("click", function () {
          graph.select(entry.node.id, true);
        });
        item.append(go);
        linkList.append(item);
      });
      parts.push(linkList);
      if (near.length > 40) {
        parts.push(element("p", "note", "and " + (near.length - 40) + " more"));
      }
    } else {
      parts.push(element("p", "note", "No links from this one."));
    }
    if (node.type === "project" && node.key) {
      var panel = element("div", "panel");
      panel.tabIndex = 0;
      panel.setAttribute("role", "region");
      panel.setAttribute("aria-label", node.label + " history");
      parts.push(panel);
      loadGitPanel(node.key, panel);
    }
    if (node.type === "hub" && node.scope === "inbox") {
      parts.push(buildAddForm());
    }
    box.replaceChildren.apply(box, parts);
    if (takeFocus) {
      showSide("details");
    }
    if (announced !== node.id) {
      announced = node.id;
      say(node.label + ", " + node.type + ", " + near.length + " connections.");
    }
    if (takeFocus || hadFocus) {
      title.focus();
    }
  }

  function closeDetail() {
    var lost = !document.activeElement || document.activeElement === document.body || nodes.detail.contains(document.activeElement);
    selectedNode = null;
    announced = "";
    nodes.detail.replaceChildren(element("p", "note", "Nothing selected. Click a node on the map to see its details."));
    if ($("chat").hidden) {
      nodes.side.hidden = true;
    }
    say("Selection cleared.");
    if (lost) {
      nodes.canvas.focus();
    }
  }

  function showSide(name) {
    var details = name === "details";
    nodes.side.hidden = false;
    nodes.detail.hidden = !details;
    $("chat").hidden = details;
    $("tab-details").setAttribute("aria-selected", details ? "true" : "false");
    $("tab-chat").setAttribute("aria-selected", details ? "false" : "true");
    $("tab-details").tabIndex = details ? 0 : -1;
    $("tab-chat").tabIndex = details ? -1 : 0;
  }

  function closeSide() {
    var inside = nodes.side.contains(document.activeElement);
    nodes.side.hidden = true;
    if (graph && selectedNode) {
      graph.select(null, false);
    }
    if (inside) {
      nodes.canvas.focus();
    }
  }

  function money(value) {
    return "$" + Number(value || 0).toFixed(3);
  }

  function pinned(change) {
    var log = chat.log;
    var stick = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    change();
    if (stick) {
      log.scrollTop = log.scrollHeight;
    }
  }

  function chatNote(text, bad) {
    var note = element("p", bad ? "msg-note bad" : "msg-note", text);
    pinned(function () {
      chat.log.append(note);
    });
  }

  function chatMessage(who) {
    var box = element("div", "msg " + who);
    var body = element("p", "msg-text");
    var piece = document.createTextNode("");
    body.append(piece);
    box.append(element("span", "msg-who", who === "you" ? "You" : "Claude"), body);
    pinned(function () {
      chat.log.append(box);
    });
    return piece;
  }

  function setBusy(flag) {
    chat.running = flag;
    chat.log.setAttribute("aria-busy", flag ? "true" : "false");
    chat.send.disabled = flag || !chat.ready;
    chat.stop.hidden = !flag;
    chat.stop.disabled = false;
    chat.fresh.disabled = flag;
    document.querySelectorAll(".ask").forEach(function (button) {
      button.disabled = flag;
    });
  }

  function startFresh(node) {
    chat.node = node;
    chat.id = null;
    chat.log.replaceChildren();
    chat.chip.textContent = (node ? node.label : "General chat") + " · " + CHIP_TAIL;
    chatNote("Ask about " + (node ? node.label : "your work") + ". Claude can read and search files here. It cannot change anything.", false);
  }

  function switchChat(node) {
    var from = chat.node ? chat.node.id : "";
    var to = node ? node.id : "";
    if (from === to || chat.running) {
      return;
    }
    kept[from] = { id: chat.id, items: Array.prototype.slice.call(chat.log.childNodes), chip: chat.chip.textContent };
    var back = kept[to];
    if (back) {
      chat.node = node;
      chat.id = back.id;
      chat.chip.textContent = back.chip;
      chat.log.replaceChildren.apply(chat.log, back.items);
    } else {
      startFresh(node);
    }
  }

  function refreshChat() {
    return api("/api/chat").then(function (status) {
      chat.ready = status.ready;
      chat.problem.hidden = status.ready;
      chat.problem.textContent = status.problem || "";
      if (!chat.running) {
        chat.send.disabled = !status.ready || status.running;
        chat.stop.hidden = !status.running;
        chat.stop.disabled = false;
        if (status.running) {
          chat.problem.hidden = false;
          chat.problem.textContent = "Claude is still answering an earlier message. Press Stop to end it.";
          window.clearTimeout(chat.orphan);
          chat.orphan = window.setTimeout(refreshChat, 3000);
        } else {
          chat.cost.textContent = "This session so far: " + money(status.session_cost) + ". Each message stops at $1.";
        }
      }
    }).catch(function (error) {
      chat.ready = false;
      chat.problem.hidden = false;
      chat.problem.textContent = explain(error);
    });
  }

  function openChat(node) {
    showView("world");
    switchChat(node);
    showSide("chat");
    refreshChat();
    chat.input.focus();
  }

  function onChatEvent(event, state) {
    if (event.t === "start") {
      state.started = true;
      chat.id = event.chat;
      chat.chip.textContent = event.label;
      if (event.refused) {
        chatNote(event.refused, false);
      }
    } else if (event.t === "text" && typeof event.text === "string") {
      if (!state.answer) {
        state.answer = chatMessage("claude");
      }
      pinned(function () {
        state.answer.appendData(event.text);
      });
    } else if (event.t === "tool") {
      state.answer = null;
      pinned(function () {
        chat.log.append(element("p", "msg-tool", (VERBS[event.name] || "Using") + " " + event.target));
      });
    } else if (event.t === "done") {
      state.finished = true;
      chat.cost.textContent = "This message " + money(event.cost) + ", this chat " + money(event.chat_cost) + ", this session " + money(event.session_cost) + ".";
      say("Claude has finished answering.");
    } else if (event.t === "stopped") {
      state.finished = true;
      chatNote("Stopped.", false);
      say("Stopped.");
    } else if (event.t === "error" && typeof event.message === "string") {
      state.finished = true;
      chatNote(event.message, true);
      say(event.message);
    }
  }

  function readStream(response, handle) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    function pump() {
      return reader.read().then(function (step) {
        buffer += decoder.decode(step.value || new Uint8Array(0), { stream: !step.done });
        var lines = buffer.split("\n");
        buffer = step.done ? "" : lines.pop();
        lines.forEach(function (line) {
          var event = null;
          if (!line) {
            return;
          }
          try {
            event = JSON.parse(line);
          } catch (problem) {
            return;
          }
          handle(event);
        });
        return step.done ? null : pump();
      });
    }
    return pump();
  }

  function sendMessage() {
    var text = chat.input.value.trim();
    if (chat.running || !chat.ready) {
      return;
    }
    if (!text) {
      chat.cost.textContent = "Write a message first.";
      return;
    }
    var state = { started: false, finished: false, answer: null };
    setBusy(true);
    chatMessage("you").appendData(text);
    chat.input.value = "";
    fetch("/api/chat", {
      method: "POST",
      headers: { "X-OS-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, node: chat.node ? chat.node.id : null, chat: chat.id })
    }).then(function (response) {
      if (!response.ok) {
        return response.json().catch(function () {
          return {};
        }).then(function (data) {
          var error = new Error(data.error || "Something went wrong. Try again.");
          error.status = response.status;
          throw error;
        });
      }
      return readStream(response, function (event) {
        onChatEvent(event, state);
      });
    }).catch(function (error) {
      if (!state.started) {
        chat.input.value = text;
      }
      chatNote(explain(error), true);
      state.finished = true;
    }).then(function () {
      if (!state.finished) {
        chatNote("The connection closed before Claude finished.", true);
      }
      setBusy(false);
      chat.input.focus();
      if (!state.started || !state.finished) {
        refreshChat();
      }
    });
  }

  function quoteForShell(text) {
    return "'" + text.replace(/['\u2018\u2019\u201a\u201b]/g, function (mark) {
      return mark + mark;
    }) + "'";
  }

  function copyCommand() {
    var text = chat.input.value.trim() || (chat.node ? promptFor(chat.node) || "" : "");
    text = text.replace(/[\u0000-\u001f\u007f\u0085\u2028\u2029]+/g, " ").trim();
    if (!text) {
      chat.cost.textContent = "Write a message first, then copy it as a command.";
      return;
    }
    if (text.charAt(0) === "-") {
      text = " " + text;
    }
    var folder = chat.node && chat.node.type === "project" && PATH_OK.test(chat.node.path) ? chat.node.path : "";
    var line = (folder ? "Set-Location -LiteralPath " + quoteForShell(folder) + "; " : "") + "claude " + quoteForShell(text);
    copyText(line).then(function () {
      chat.cost.textContent = "Copied. Paste it into PowerShell.";
    }).catch(function () {
      chat.cost.textContent = "The browser blocked copying.";
    });
  }

  function buildFilters() {
    var counts = {};
    worldData.nodes.forEach(function (node) {
      counts[node.type] = (counts[node.type] || 0) + 1;
    });
    var previous = {};
    nodes.filters.querySelectorAll("input").forEach(function (box) {
      previous[box.value] = box.checked;
    });
    var rows = TYPES.map(function (type) {
      var label = element("label");
      var box = element("input");
      box.type = "checkbox";
      box.value = type[0];
      box.checked = previous[type[0]] !== false;
      box.addEventListener("change", applyFilters);
      label.append(box, element("span", "swatch", type[1]), element("span", "", type[2]), element("span", "count", String(counts[type[0]] || 0)));
      return label;
    });
    nodes.filters.replaceChildren.apply(nodes.filters, rows);
  }

  function applyFilters() {
    var on = ["hub"];
    nodes.filters.querySelectorAll("input").forEach(function (box) {
      if (box.checked) {
        on.push(box.value);
      }
    });
    graph.setFilter(on);
  }

  function describeScan(live) {
    var scan = live || worldData.scan;
    var parts = ["Nodes " + worldData.nodes.length];
    if (worldData.cut.nodes) {
      parts[0] += " (" + worldData.cut.nodes + " not shown)";
    }
    parts.push("Links " + worldData.edges.length);
    if (scan.state === "scanning") {
      var total = Math.max(scan.files_total, 1);
      parts.push("History scan " + Math.round((scan.files_done / total) * 100) + " percent");
    } else if (scan.state === "done") {
      parts.push("History scan done, " + scan.files_done + " files");
    } else if (scan.state === "stopped") {
      parts.push(scan.note || "History scan stopped");
    } else {
      parts.push("History scan not run");
    }
    nodes.status.textContent = parts.join("  ·  ");
  }

  function loadWorld(fresh) {
    return api(fresh ? "/api/world?fresh=1" : "/api/world").then(function (data) {
      worldData = data;
      if (!graph) {
        graph = window.OSGraph.create(nodes.canvas, { calm: calm, onSelect: function (node) {
          if (node) {
            openDetail(node, true);
          } else {
            closeDetail();
          }
        } });
      }
      graph.setActive(currentView === "world");
      graph.setData(data.nodes, data.edges);
      renderToday();
      buildFilters();
      applyFilters();
      describeScan();
      buildSearchItems();
      lastScanState = data.scan.state;
      if (data.scan.state === "scanning") {
        schedulePoll();
      }
      if (selectedNode) {
        var again = data.nodes.filter(function (node) {
          return node.id === selectedNode.id;
        })[0];
        if (again) {
          openDetail(again, false);
        }
      }
    }).catch(showError);
  }

  function schedulePoll() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(pollScan, 1500);
  }

  function pollScan() {
    api("/api/scan").then(function (state) {
      if (worldData) {
        describeScan(state);
      }
      if (state.state === "scanning") {
        schedulePoll();
      } else if (lastScanState === "scanning") {
        loadWorld(true);
      }
    }).catch(showError);
  }

  function showView(name) {
    var world = name === "world";
    currentView = name;
    VIEWS.forEach(function (view) {
      $(view + "-view").hidden = view !== name;
      $("tab-" + view).setAttribute("aria-selected", view === name ? "true" : "false");
      $("tab-" + view).tabIndex = view === name ? 0 : -1;
    });
    if (graph) {
      graph.setActive(world);
      if (world) {
        graph.resize();
      }
    }
  }

  function findNode(test) {
    return worldData ? worldData.nodes.filter(test)[0] || null : null;
  }

  function mapButton(node) {
    if (!node) {
      return null;
    }
    var button = element("button", "tool", "Open on map");
    button.type = "button";
    button.addEventListener("click", function () {
      showView("world");
      graph.select(node.id, true);
    });
    return button;
  }

  function todayCard(title, parts) {
    var card = element("article", "card today-card");
    card.append(element("h4", "", title));
    parts.forEach(function (part) {
      if (part) {
        card.append(part);
      }
    });
    return card;
  }

  function renderToday() {
    if (!overviewData) {
      return;
    }
    var dirty = [];
    var broken = [];
    overviewData.projects.forEach(function (card) {
      if (card.kind === "error") {
        broken.push(todayCard(card.label, [element("p", "what", "Could not read"), element("p", "note", card.error)]));
      } else if (card.kind === "git" && card.uncommitted > 0) {
        var parts = [element("p", "", card.uncommitted + (card.uncommitted === 1 ? " uncommitted file" : " uncommitted files")), list(card.dirty, "mono")];
        if (card.uncommitted > card.dirty.length) {
          parts.push(element("p", "note", "and " + (card.uncommitted - card.dirty.length) + " more"));
        }
        parts.push(mapButton(findNode(function (node) {
          return node.type === "project" && node.key === card.id;
        })));
        dirty.push(todayCard(card.label, parts));
      }
    });
    if (!dirty.length && !broken.length) {
      dirty.push(todayCard("All clear", [element("p", "note", "Nothing is uncommitted.")]));
    }
    var target = $("today-dirty");
    target.replaceChildren.apply(target, dirty.concat(broken));

    var reports = [];
    var missing = 0;
    overviewData.reports.forEach(function (card) {
      if (card.kind === "error") {
        missing += 1;
      } else if (card.recent && card.recent.length) {
        var items = list(card.recent.map(function (file) {
          return [file.name + " ", element("span", "when", file.when)];
        }));
        reports.push(todayCard(card.label, [items, mapButton(findNode(function (node) {
          return node.type === "report" && node.path === card.path && node.name === card.label;
        }))]));
      }
    });
    if (!reports.length) {
      reports.push(todayCard("Nothing new", [element("p", "note", "No report changed in the last 24 hours.")]));
    }
    if (missing) {
      reports.push(todayCard("Not found", [element("p", "note", missing + (missing === 1 ? " report location" : " report locations") + " could not be read. The Cards tab says which.")]));
    }
    target = $("today-reports");
    target.replaceChildren.apply(target, reports);

    var ideas = overviewData.inbox.ideas.slice(-10).reverse();
    var ideaParts = ideas.length ? [list(ideas), element("p", "note", "Latest added first. " + overviewData.inbox.ideas.length + " in the inbox.")] : [element("p", "note", "The inbox is empty.")];
    ideaParts.push(mapButton(findNode(function (node) {
      return node.type === "hub" && node.scope === "inbox";
    })));
    target = $("today-ideas");
    target.replaceChildren(todayCard("Inbox", ideaParts));

    $("today-updated").textContent = "Updated " + overviewData.generated;
  }

  function setCalm(flag) {
    calm = flag;
    nodes.calm.setAttribute("aria-pressed", flag ? "true" : "false");
    document.body.classList.toggle("calm", flag);
    if (graph) {
      graph.setCalm(flag);
    }
  }

  function tickClock() {
    var now = new Date();
    nodes.clock.textContent = now.toLocaleTimeString("en-GB");
  }

  VIEWS.forEach(function (name, index) {
    var tab = $("tab-" + name);
    tab.addEventListener("click", function () {
      showView(name);
    });
    tab.addEventListener("keydown", function (event) {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        var step = event.key === "ArrowRight" ? 1 : VIEWS.length - 1;
        var next = $("tab-" + VIEWS[(index + step) % VIEWS.length]);
        next.click();
        next.focus();
      }
    });
  });
  $("today-refresh").addEventListener("click", function () {
    loadOverview(true);
  });
  nodes.calm.addEventListener("click", function () {
    setCalm(nodes.calm.getAttribute("aria-pressed") !== "true");
  });
  $("more").addEventListener("click", function () {
    var button = $("more");
    var open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", open ? "true" : "false");
    $("more-body").hidden = !open;
    button.parentNode.classList.toggle("open", open);
  });
  $("tab-details").addEventListener("click", function () {
    showSide("details");
  });
  $("tab-chat").addEventListener("click", function () {
    openChat(chat.node);
  });
  [$("tab-details"), $("tab-chat")].forEach(function (tab) {
    tab.addEventListener("keydown", function (event) {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        var next = tab === $("tab-details") ? $("tab-chat") : $("tab-details");
        next.click();
        if (next === $("tab-details")) {
          next.focus();
        }
      }
    });
  });
  $("side-close").addEventListener("click", closeSide);
  $("chat-open").addEventListener("click", function () {
    openChat(null);
  });
  $("chat-form").addEventListener("submit", function (event) {
    event.preventDefault();
    sendMessage();
  });
  chat.input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      sendMessage();
    }
  });
  chat.stop.addEventListener("click", function () {
    chat.stop.disabled = true;
    api("/api/chat/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(function (error) {
      chat.problem.hidden = false;
      chat.problem.textContent = explain(error);
    }).then(function () {
      if (!chat.running) {
        window.setTimeout(refreshChat, 1000);
      }
    });
  });
  chat.fresh.addEventListener("click", function () {
    startFresh(chat.node);
    chat.input.focus();
  });
  $("chat-copy").addEventListener("click", copyCommand);
  $("fit").addEventListener("click", function () {
    if (graph) {
      graph.fit();
    }
  });
  $("rebuild").addEventListener("click", function () {
    loadWorld(true);
  });
  nodes.rescan.addEventListener("click", function () {
    nodes.rescan.disabled = true;
    api("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(function () {
      lastScanState = "scanning";
      return loadWorld(true);
    }).catch(showError).then(function () {
      nodes.rescan.disabled = false;
    });
  });
  $("refresh").addEventListener("click", function () {
    loadOverview(true);
  });
  wireAddForm($("add-form"), $("idea"), $("add-status"));
  if (window.matchMedia("(max-width: 720px)").matches) {
    nodes.filtersBox.open = false;
  } else {
    nodes.filtersBox.open = true;
  }
  setupSearch();
  startFresh(null);
  chat.send.disabled = true;
  nodes.detail.append(element("p", "note", "Nothing selected. Click a node on the map to see its details."));
  setCalm(calm);
  tickClock();
  window.setInterval(tickClock, 1000);
  loadOverview(false).then(function () {
    return loadWorld(false);
  });
}());
