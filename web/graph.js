(function () {
  "use strict";

  var STYLE = {
    project: { shape: "hex", radius: 15, color: "#2ee6ff", glow: "cyan" },
    report: { shape: "square", radius: 6, color: "#bcd6f0", glow: "pale" },
    routine: { shape: "ring", radius: 8, color: "#3dff9a", glow: "green" },
    skill: { shape: "dot", radius: 3, color: "#2ee6ff", glow: "cyan" },
    agent: { shape: "diamond", radius: 5, color: "#a78bfa", glow: "violet" },
    idea: { shape: "spark", radius: 4, color: "#ffb000", glow: "amber" },
    hub: { shape: "ring", radius: 10, color: "#2ee6ff", glow: "cyan" }
  };
  var LINK = {
    member: { alpha: 0.09, width: 1 },
    mentions: { alpha: 0.24, width: 1 },
    holds: { alpha: 0.4, width: 1.4 },
    "works in": { alpha: 0.4, width: 1.4 },
    writes: { alpha: 0.4, width: 1.4 },
    idea: { alpha: 0.4, width: 1 }
  };
  var ACCENT = "46,230,255";
  var GLOWS = { cyan: ACCENT, green: "61,255,154", violet: "167,139,250", amber: "255,176,0", pale: "188,214,240" };
  var FONT = "12px Bahnschrift, 'Cascadia Mono', Consolas, sans-serif";
  var TAU = Math.PI * 2;

  function hash(text) {
    var value = 2166136261;
    for (var i = 0; i < text.length; i += 1) {
      value = Math.imul(value ^ text.charCodeAt(i), 16777619);
    }
    return (value >>> 0) / 4294967296;
  }

  function sprite(color, size) {
    var canvas = document.createElement("canvas");
    var context = canvas.getContext("2d");
    var gradient = context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    canvas.width = size;
    canvas.height = size;
    gradient.addColorStop(0, "rgba(" + color + ",0.95)");
    gradient.addColorStop(0.25, "rgba(" + color + ",0.35)");
    gradient.addColorStop(1, "rgba(" + color + ",0)");
    context.fillStyle = gradient;
    context.fillRect(0, 0, size, size);
    return canvas;
  }

  function create(canvas, options) {
    var settings = options || {};
    var context = canvas.getContext("2d");
    var glows = {};
    Object.keys(GLOWS).forEach(function (name) {
      glows[name] = sprite(GLOWS[name], 64);
    });
    var nodes = [];
    var edges = [];
    var byId = {};
    var adjacent = [];
    var visibleTypes = null;
    var camera = { x: 0, y: 0, z: 0.5 };
    var pendingFit = false;
    var fly = null;
    var size = { w: 800, h: 600, dpr: 1 };
    var calm = Boolean(settings.calm);
    var level = 3;
    var selected = -1;
    var hovered = -1;
    var pulses = [];
    var pulseEdges = [];
    var drag = null;
    var dirty = true;
    var settled = false;
    var frames = 0;
    var draws = 0;
    var active = true;
    var frameTimes = [];
    var lastFrame = 0;
    var fps = 0;
    var clock = 0;

    function nodeIndex(id) {
      return Object.prototype.hasOwnProperty.call(byId, id) ? byId[id] : -1;
    }

    function isVisible(node) {
      return !visibleTypes || visibleTypes.has(node.type);
    }

    function seed() {
      var groups = {};
      var order = [];
      nodes.forEach(function (node, i) {
        if (node.type === "skill" || node.type === "agent") {
          var key = node.type + ":" + node.scope;
          if (!groups[key]) {
            groups[key] = [];
            order.push(key);
          }
          groups[key].push(i);
        }
      });
      order.sort();
      var total = order.reduce(function (sum, key) {
        return sum + groups[key].length;
      }, 0) || 1;
      var count = 0;
      var hubAngle = {};
      order.forEach(function (key) {
        var members = groups[key];
        var first = count;
        members.forEach(function (i) {
          var node = nodes[i];
          var angle = ((count + 0.5) / total) * TAU;
          var radius = node.type === "agent" ? 560 : node.scope === "global" ? 700 : 850;
          radius += (hash(node.id) - 0.5) * 70;
          node.sx = Math.cos(angle) * radius;
          node.sy = Math.sin(angle) * radius;
          count += 1;
        });
        hubAngle[key] = { angle: ((first + (count - first) / 2) / total) * TAU, radius: key.indexOf("agent") === 0 ? 360 : key.indexOf("skill:global") === 0 ? 440 : 540 };
      });
      var other = nodes.map(function (node, i) {
        return i;
      }).filter(function (i) {
        return nodes[i].sx === undefined;
      });
      var kinds = { project: [], routine: [], report: [], hub: [], idea: [] };
      other.forEach(function (i) {
        (kinds[nodes[i].type] || kinds.hub).push(i);
      });
      kinds.project.forEach(function (i, n) {
        var angle = (n / Math.max(kinds.project.length, 1)) * TAU;
        nodes[i].sx = Math.cos(angle) * 110;
        nodes[i].sy = Math.sin(angle) * 110;
      });
      kinds.routine.forEach(function (i, n) {
        var angle = ((n + 0.5) / Math.max(kinds.routine.length, 1)) * TAU + 0.3;
        nodes[i].sx = Math.cos(angle) * 340;
        nodes[i].sy = Math.sin(angle) * 340;
      });
      kinds.report.forEach(function (i, n) {
        var angle = ((n + 0.25) / Math.max(kinds.report.length, 1)) * TAU + 0.9;
        nodes[i].sx = Math.cos(angle) * 230;
        nodes[i].sy = Math.sin(angle) * 230;
      });
      kinds.hub.forEach(function (i, n) {
        var node = nodes[i];
        var placed = null;
        Object.keys(hubAngle).forEach(function (key) {
          var scope = key.slice(key.indexOf(":") + 1);
          if (node.type === "hub" && ((key.indexOf("agent:") === 0 && node.scope === "agents") || (key.indexOf("skill:") === 0 && node.scope === "skills-" + scope))) {
            placed = placed || hubAngle[key];
          }
        });
        if (placed) {
          node.sx = Math.cos(placed.angle) * placed.radius;
          node.sy = Math.sin(placed.angle) * placed.radius;
        } else {
          node.sx = -372;
          node.sy = -185 - n * 20;
        }
      });
      kinds.idea.forEach(function (i, n) {
        var angle = (n / Math.max(kinds.idea.length, 1)) * TAU;
        nodes[i].sx = -372 + Math.cos(angle) * 46;
        nodes[i].sy = -185 + Math.sin(angle) * 46;
      });
      nodes.forEach(function (node) {
        node.x = node.sx;
        node.y = node.sy;
        node.vx = 0;
        node.vy = 0;
      });
    }

    function setData(newNodes, newEdges) {
      var previous = {};
      var kept = selected >= 0 && nodes[selected] ? nodes[selected].id : null;
      var firstLoad = nodes.length === 0;
      nodes.forEach(function (node) {
        previous[node.id] = node;
      });
      nodes = newNodes.map(function (source) {
        var style = STYLE[source.type] || STYLE.skill;
        var node = Object.assign({}, source);
        node.radius = style.radius;
        node.phase = hash(source.id) * TAU;
        return node;
      });
      byId = {};
      nodes.forEach(function (node, i) {
        byId[node.id] = i;
      });
      edges = [];
      adjacent = nodes.map(function () {
        return [];
      });
      newEdges.forEach(function (source) {
        var a = nodeIndex(source.a);
        var b = nodeIndex(source.b);
        if (a >= 0 && b >= 0) {
          edges.push({ a: a, b: b, kind: source.kind, bend: (hash(source.a + source.b) > 0.5 ? 1 : -1) * (0.08 + hash(source.b + source.a) * 0.1) });
          adjacent[a].push(edges.length - 1);
          adjacent[b].push(edges.length - 1);
        }
      });
      hovered = -1;
      seed();
      nodes.forEach(function (node) {
        var old = previous[node.id];
        if (old) {
          node.x = old.x;
          node.y = old.y;
        }
      });
      selected = kept !== null && nodeIndex(kept) >= 0 ? nodeIndex(kept) : -1;
      refreshPulseEdges();
      if (firstLoad) {
        fit(true);
      }
      wake();
    }

    function refreshPulseEdges() {
      var related = selected >= 0 ? adjacent[selected] : hovered >= 0 ? adjacent[hovered] : null;
      pulseEdges = [];
      edges.forEach(function (edge, i) {
        if (edge.kind === "member" && !related) {
          return;
        }
        if (!isVisible(nodes[edge.a]) || !isVisible(nodes[edge.b])) {
          return;
        }
        if (!related || related.indexOf(i) >= 0) {
          pulseEdges.push(i);
        }
      });
      pulses = [];
    }

    function step() {
      var cell = 44;
      var grid = {};
      var i;
      var energy = 0;
      for (i = 0; i < nodes.length; i += 1) {
        var key = Math.floor(nodes[i].x / cell) + "," + Math.floor(nodes[i].y / cell);
        (grid[key] = grid[key] || []).push(i);
      }
      for (i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        var cx = Math.floor(node.x / cell);
        var cy = Math.floor(node.y / cell);
        for (var gx = cx - 1; gx <= cx + 1; gx += 1) {
          for (var gy = cy - 1; gy <= cy + 1; gy += 1) {
            var bucket = grid[gx + "," + gy];
            if (!bucket) {
              continue;
            }
            for (var k = 0; k < bucket.length; k += 1) {
              var j = bucket[k];
              if (j <= i) {
                continue;
              }
              var other = nodes[j];
              var dx = other.x - node.x;
              var dy = other.y - node.y;
              var d2 = dx * dx + dy * dy + 0.01;
              var min = Math.max(node.radius + other.radius + 7, 26);
              if (d2 < min * min) {
                var d = Math.sqrt(d2);
                var push = (min - d) * 0.05;
                dx = (dx / d) * push;
                dy = (dy / d) * push;
                node.vx -= dx;
                node.vy -= dy;
                other.vx += dx;
                other.vy += dy;
              }
            }
          }
        }
        node.vx += (node.sx - node.x) * 0.006;
        node.vy += (node.sy - node.y) * 0.006;
      }
      edges.forEach(function (edge) {
        var a = nodes[edge.a];
        var b = nodes[edge.b];
        var rest = edge.kind === "member" ? 150 : edge.kind === "mentions" ? 170 : 90;
        var pull = edge.kind === "member" ? 0.003 : edge.kind === "mentions" ? 0.0012 : 0.02;
        var dx = b.x - a.x;
        var dy = b.y - a.y;
        var d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        var force = (d - rest) * pull;
        dx = (dx / d) * force;
        dy = (dy / d) * force;
        a.vx += dx;
        a.vy += dy;
        b.vx -= dx;
        b.vy -= dy;
      });
      nodes.forEach(function (node) {
        node.vx *= 0.82;
        node.vy *= 0.82;
        node.x += node.vx;
        node.y += node.vy;
        energy += Math.abs(node.vx) + Math.abs(node.vy);
      });
      settled = energy < 0.04 * Math.max(nodes.length, 1);
    }

    function toScreen(x, y) {
      return [(x - camera.x) * camera.z + size.w / 2, (y - camera.y) * camera.z + size.h / 2];
    }

    function toWorld(sx, sy) {
      return [(sx - size.w / 2) / camera.z + camera.x, (sy - size.h / 2) / camera.z + camera.y];
    }

    function drawnRadius(node) {
      var style = STYLE[node.type] || STYLE.skill;
      var boost = node.type === "skill" || node.type === "agent" ? Math.min(Math.sqrt(node.use || 0) * 1.1, 9) : 0;
      return (style.radius + boost) * Math.max(Math.min(camera.z, 2.2), 0.7);
    }

    function position(node) {
      if (calm || level < 1) {
        return [node.x, node.y];
      }
      return [node.x + Math.sin(clock * 0.0006 + node.phase) * 2.2, node.y + Math.cos(clock * 0.0005 + node.phase) * 2.2];
    }

    function related(index) {
      var set = new Set();
      if (index < 0) {
        return set;
      }
      set.add(index);
      adjacent[index].forEach(function (e) {
        set.add(edges[e].a);
        set.add(edges[e].b);
      });
      return set;
    }

    function curve(edge) {
      var a = position(nodes[edge.a]);
      var b = position(nodes[edge.b]);
      var mx = (a[0] + b[0]) / 2;
      var my = (a[1] + b[1]) / 2;
      var dx = b[0] - a[0];
      var dy = b[1] - a[1];
      return { ax: a[0], ay: a[1], bx: b[0], by: b[1], cx: mx - dy * edge.bend, cy: my + dx * edge.bend };
    }

    function pointOn(c, t) {
      var u = 1 - t;
      return [u * u * c.ax + 2 * u * t * c.cx + t * t * c.bx, u * u * c.ay + 2 * u * t * c.cy + t * t * c.by];
    }

    function drawBackdrop() {
      var centre = toScreen(0, 0);
      var maxRadius = Math.max(size.w, size.h) * 2;
      context.save();
      context.strokeStyle = "rgba(" + ACCENT + ",0.09)";
      context.lineWidth = 1;
      for (var r = 100; r <= 900; r += 100) {
        context.beginPath();
        context.arc(centre[0], centre[1], r * camera.z, 0, TAU);
        context.stroke();
      }
      context.beginPath();
      context.moveTo(centre[0] - maxRadius, centre[1]);
      context.lineTo(centre[0] + maxRadius, centre[1]);
      context.moveTo(centre[0], centre[1] - maxRadius);
      context.lineTo(centre[0], centre[1] + maxRadius);
      context.stroke();
      context.restore();
    }

    function drawLinks(focus) {
      var groups = {};
      var bright = [];
      edges.forEach(function (edge, i) {
        if (!isVisible(nodes[edge.a]) || !isVisible(nodes[edge.b])) {
          return;
        }
        if (focus && (focus.has(edge.a) && focus.has(edge.b)) && (edge.a === selected || edge.b === selected || edge.a === hovered || edge.b === hovered)) {
          bright.push(i);
          return;
        }
        (groups[edge.kind] = groups[edge.kind] || []).push(i);
      });
      Object.keys(groups).forEach(function (kind) {
        var style = LINK[kind] || LINK.mentions;
        context.beginPath();
        groups[kind].forEach(function (i) {
          var c = curve(edges[i]);
          var a = toScreen(c.ax, c.ay);
          var cp = toScreen(c.cx, c.cy);
          var b = toScreen(c.bx, c.by);
          context.moveTo(a[0], a[1]);
          context.quadraticCurveTo(cp[0], cp[1], b[0], b[1]);
        });
        context.strokeStyle = "rgba(" + ACCENT + "," + (focus ? style.alpha * 0.3 : style.alpha) + ")";
        context.lineWidth = style.width;
        context.stroke();
      });
      if (bright.length) {
        context.beginPath();
        bright.forEach(function (i) {
          var c = curve(edges[i]);
          var a = toScreen(c.ax, c.ay);
          var cp = toScreen(c.cx, c.cy);
          var b = toScreen(c.bx, c.by);
          context.moveTo(a[0], a[1]);
          context.quadraticCurveTo(cp[0], cp[1], b[0], b[1]);
        });
        context.strokeStyle = "rgba(" + ACCENT + ",0.95)";
        context.lineWidth = 1.8;
        context.stroke();
      }
    }

    function drawPulses(dt) {
      if (calm || level < 2 || !pulseEdges.length) {
        return;
      }
      var wanted = Math.min(level >= 3 ? 70 : 24, pulseEdges.length);
      while (pulses.length < wanted) {
        pulses.push({ edge: pulseEdges[Math.floor(Math.random() * pulseEdges.length)], t: Math.random(), speed: 0.0003 + Math.random() * 0.0006, back: Math.random() > 0.5 });
      }
      pulses.forEach(function (pulse) {
        pulse.t += pulse.speed * dt;
        if (pulse.t > 1) {
          pulse.t = 0;
          pulse.edge = pulseEdges[Math.floor(Math.random() * pulseEdges.length)];
          pulse.back = Math.random() > 0.5;
        }
        var edge = edges[pulse.edge];
        if (!edge) {
          return;
        }
        var point = pointOn(curve(edge), pulse.back ? 1 - pulse.t : pulse.t);
        var screen = toScreen(point[0], point[1]);
        var half = 7 * Math.max(Math.min(camera.z, 1.4), 0.6);
        context.drawImage(glows.cyan, screen[0] - half, screen[1] - half, half * 2, half * 2);
      });
    }

    function path(node, x, y, r) {
      var style = STYLE[node.type] || STYLE.skill;
      var i;
      context.beginPath();
      if (style.shape === "hex") {
        for (i = 0; i < 6; i += 1) {
          context.lineTo(x + Math.cos((i / 6) * TAU + 0.5236) * r, y + Math.sin((i / 6) * TAU + 0.5236) * r);
        }
        context.closePath();
      } else if (style.shape === "square") {
        context.rect(x - r, y - r, r * 2, r * 2);
      } else if (style.shape === "diamond") {
        context.moveTo(x, y - r * 1.3);
        context.lineTo(x + r, y);
        context.lineTo(x, y + r * 1.3);
        context.lineTo(x - r, y);
        context.closePath();
      } else if (style.shape === "spark") {
        context.moveTo(x - r, y);
        context.lineTo(x + r, y);
        context.moveTo(x, y - r);
        context.lineTo(x, y + r);
        context.moveTo(x - r * 0.7, y - r * 0.7);
        context.lineTo(x + r * 0.7, y + r * 0.7);
      } else {
        context.arc(x, y, r, 0, TAU);
      }
    }

    function drawNodes(focus) {
      var labels = [];
      var placed = [];
      nodes.forEach(function (node, i) {
        if (!isVisible(node)) {
          return;
        }
        var style = STYLE[node.type] || STYLE.skill;
        var p = position(node);
        var s = toScreen(p[0], p[1]);
        if (s[0] < -30 || s[1] < -30 || s[0] > size.w + 30 || s[1] > size.h + 30) {
          return;
        }
        var used = node.use > 0;
        var r = drawnRadius(node);
        var alpha = node.type === "skill" || node.type === "agent" ? (used ? 0.95 : 0.4) : 1;
        if (focus && !focus.has(i)) {
          alpha *= 0.22;
        }
        if (level >= 2 && (used || node.type === "project" || node.type === "hub" || i === selected || i === hovered) && (!focus || focus.has(i))) {
          var halo = r * (node.type === "project" ? 5 : 2.6);
          context.globalAlpha = Math.min(alpha, node.type === "project" ? 0.8 : 0.45);
          context.drawImage(glows[style.glow], s[0] - halo, s[1] - halo, halo * 2, halo * 2);
        }
        context.globalAlpha = alpha;
        path(node, s[0], s[1], r);
        if (style.shape === "ring" || style.shape === "spark") {
          context.strokeStyle = style.color;
          context.lineWidth = style.shape === "spark" ? 1.2 : 1.8;
          context.stroke();
        } else {
          context.fillStyle = style.color;
          context.fill();
        }
        if (i === selected) {
          context.globalAlpha = 1;
          context.strokeStyle = "#f2fbff";
          context.lineWidth = 1.5;
          context.beginPath();
          context.arc(s[0], s[1], r + 6, 0, TAU);
          context.stroke();
        }
        context.globalAlpha = 1;
        var always = (node.type === "project" || node.type === "hub" || node.type === "routine") && camera.z > 0.35;
        var near = focus && focus.has(i) && (i === selected || i === hovered || (selected >= 0 && labels.length < 14));
        if (always || near || (camera.z > 2 && used)) {
          labels.push({ node: node, x: s[0], y: s[1] - r - 6, priority: i === selected || i === hovered ? 0 : near ? 1 : 2 });
        }
      });
      context.font = FONT;
      context.textAlign = "center";
      labels.sort(function (a, b) {
        return a.priority - b.priority;
      });
      labels.forEach(function (label) {
        var text = label.node.label.length > 30 ? label.node.label.slice(0, 29) + "…" : label.node.label;
        var width = context.measureText(text).width;
        var box = { l: label.x - width / 2 - 4, r: label.x + width / 2 + 4, t: label.y - 12, b: label.y + 4 };
        var blocked = placed.some(function (other) {
          return box.l < other.r && box.r > other.l && box.t < other.b && box.b > other.t;
        });
        if (blocked && label.priority > 0) {
          return;
        }
        placed.push(box);
        context.fillStyle = "rgba(5,8,16,0.82)";
        context.fillRect(label.x - width / 2 - 4, label.y - 12, width + 8, 16);
        context.fillStyle = label.priority === 0 ? "#f2fbff" : label.node.type === "routine" ? "#3dff9a" : "#bcd6f0";
        context.fillText(text, label.x, label.y);
      });
    }

    function draw(now) {
      var dt = Math.min(now - lastFrame, 64);
      lastFrame = now;
      clock = now;
      context.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
      context.clearRect(0, 0, size.w, size.h);
      if (fly) {
        var t = Math.min((now - fly.start) / 600, 1);
        var e = 1 - Math.pow(1 - t, 3);
        camera.x = fly.x0 + (fly.x1 - fly.x0) * e;
        camera.y = fly.y0 + (fly.y1 - fly.y0) * e;
        camera.z = fly.z0 + (fly.z1 - fly.z0) * e;
        if (t >= 1) {
          fly = null;
        }
      }
      if (!settled) {
        step();
      }
      var focusIndex = selected >= 0 ? selected : hovered;
      var focus = focusIndex >= 0 ? related(focusIndex) : null;
      drawBackdrop();
      drawLinks(focus);
      drawPulses(dt);
      drawNodes(focus);
      context.globalAlpha = 1;
    }

    function monitor(now) {
      frameTimes.push(now);
      if (frameTimes.length > 45) {
        frameTimes.shift();
        var span = frameTimes[frameTimes.length - 1] - frameTimes[0];
        fps = Math.round((44 / span) * 1000);
        if (span / 44 > 28 && level > 0 && !calm) {
          level -= 1;
          frameTimes = [];
        }
      }
    }

    function tick(now) {
      if (!active) {
        requestAnimationFrame(tick);
        return;
      }
      frames += 1;
      var moving = !calm && level >= 1;
      if (moving || dirty || fly || !settled) {
        draw(now);
        draws += 1;
        canvas.dataset.draws = String(draws);
        dirty = false;
        if (moving) {
          monitor(now);
        }
      }
      requestAnimationFrame(tick);
    }

    function wake() {
      settled = false;
      dirty = true;
    }

    function resize() {
      var box = canvas.getBoundingClientRect();
      if (pendingFit && box.width > 0 && box.height > 0) {
        pendingFit = false;
        size.w = Math.round(box.width);
        size.h = Math.round(box.height);
        fit(true);
      }
      size.dpr = Math.min(window.devicePixelRatio || 1, 2);
      size.w = Math.max(Math.round(box.width), 1);
      size.h = Math.max(Math.round(box.height), 1);
      canvas.width = Math.round(size.w * size.dpr);
      canvas.height = Math.round(size.h * size.dpr);
      dirty = true;
    }

    function fit(instant) {
      var list = nodes.filter(isVisible);
      if (!list.length) {
        return;
      }
      var shown = canvas.getBoundingClientRect();
      if (shown.width < 1 || shown.height < 1) {
        pendingFit = true;
        return;
      }
      var minX = Infinity;
      var maxX = -Infinity;
      var minY = Infinity;
      var maxY = -Infinity;
      list.forEach(function (node) {
        minX = Math.min(minX, node.x);
        maxX = Math.max(maxX, node.x);
        minY = Math.min(minY, node.y);
        maxY = Math.max(maxY, node.y);
      });
      var zoom = Math.max(Math.min(size.w / Math.max(maxX - minX + 140, 200), size.h / Math.max(maxY - minY + 140, 200), 2), 0.15);
      goTo((minX + maxX) / 2, (minY + maxY) / 2, zoom, instant);
    }

    function goTo(x, y, z, instant) {
      if (instant || calm) {
        camera.x = x;
        camera.y = y;
        camera.z = z;
        fly = null;
      } else {
        fly = { start: performance.now(), x0: camera.x, y0: camera.y, z0: camera.z, x1: x, y1: y, z1: z };
      }
      dirty = true;
    }

    function hit(px, py) {
      var best = -1;
      var bestDistance = Infinity;
      nodes.forEach(function (node, i) {
        if (!isVisible(node)) {
          return;
        }
        var p = position(node);
        var s = toScreen(p[0], p[1]);
        var reach = Math.max(drawnRadius(node), 4) + 5;
        var d = (s[0] - px) * (s[0] - px) + (s[1] - py) * (s[1] - py);
        if (d <= reach * reach && d < bestDistance) {
          best = i;
          bestDistance = d;
        }
      });
      return best;
    }

    function choose(index, flyThere) {
      selected = index;
      refreshPulseEdges();
      if (index >= 0 && flyThere) {
        goTo(nodes[index].x, nodes[index].y, Math.max(camera.z, 1.4));
      }
      dirty = true;
      if (settings.onSelect) {
        settings.onSelect(index >= 0 ? nodes[index] : null);
      }
    }

    function local(event) {
      var box = canvas.getBoundingClientRect();
      return [event.clientX - box.left, event.clientY - box.top];
    }

    canvas.addEventListener("pointerdown", function (event) {
      var p = local(event);
      drag = { x: p[0], y: p[1], cx: camera.x, cy: camera.y, moved: false };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", function (event) {
      var p = local(event);
      if (drag) {
        var dx = p[0] - drag.x;
        var dy = p[1] - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) {
          drag.moved = true;
        }
        if (drag.moved) {
          camera.x = drag.cx - dx / camera.z;
          camera.y = drag.cy - dy / camera.z;
          fly = null;
          dirty = true;
        }
        return;
      }
      var index = hit(p[0], p[1]);
      if (index !== hovered) {
        hovered = index;
        refreshPulseEdges();
        dirty = true;
        canvas.classList.toggle("over", index >= 0);
        if (settings.onHover) {
          settings.onHover(index >= 0 ? nodes[index] : null);
        }
      }
    });
    canvas.addEventListener("pointerup", function (event) {
      var p = local(event);
      if (drag && !drag.moved) {
        choose(hit(p[0], p[1]), false);
      }
      drag = null;
    });
    canvas.addEventListener("pointercancel", function () {
      drag = null;
    });
    canvas.addEventListener("lostpointercapture", function () {
      drag = null;
    });
    canvas.addEventListener("pointerleave", function () {
      if (!drag && hovered >= 0) {
        hovered = -1;
        refreshPulseEdges();
        dirty = true;
        if (settings.onHover) {
          settings.onHover(null);
        }
      }
    });
    canvas.addEventListener("dblclick", function (event) {
      var p = local(event);
      if (hit(p[0], p[1]) < 0) {
        fit();
      }
    });
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var p = local(event);
      var before = toWorld(p[0], p[1]);
      camera.z = Math.min(Math.max(camera.z * Math.exp(-event.deltaY * 0.0012), 0.15), 6);
      var after = toWorld(p[0], p[1]);
      camera.x += before[0] - after[0];
      camera.y += before[1] - after[1];
      fly = null;
      dirty = true;
    }, { passive: false });
    canvas.addEventListener("keydown", function (event) {
      var pan = 60 / camera.z;
      var handled = true;
      if (event.key === "+" || event.key === "=") {
        camera.z = Math.min(camera.z * 1.2, 6);
      } else if (event.key === "-") {
        camera.z = Math.max(camera.z / 1.2, 0.15);
      } else if (event.key === "ArrowLeft") {
        camera.x -= pan;
      } else if (event.key === "ArrowRight") {
        camera.x += pan;
      } else if (event.key === "ArrowUp") {
        camera.y -= pan;
      } else if (event.key === "ArrowDown") {
        camera.y += pan;
      } else if (event.key === "f") {
        fit();
      } else if (event.key === "Escape") {
        choose(-1, false);
      } else {
        handled = false;
      }
      if (handled) {
        event.preventDefault();
        dirty = true;
      }
    });
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () {
      frameTimes = [];
      lastFrame = performance.now();
      dirty = true;
    });
    resize();
    requestAnimationFrame(tick);

    return {
      setData: setData,
      resize: resize,
      fit: fit,
      select: function (id, flyThere) {
        choose(nodeIndex(id), flyThere);
      },
      setFilter: function (types) {
        visibleTypes = types ? new Set(types) : null;
        refreshPulseEdges();
        wake();
      },
      setCalm: function (flag) {
        calm = Boolean(flag);
        level = 3;
        frameTimes = [];
        dirty = true;
      },
      neighbours: function (id) {
        var index = nodeIndex(id);
        var out = [];
        if (index >= 0) {
          adjacent[index].forEach(function (e) {
            var other = edges[e].a === index ? edges[e].b : edges[e].a;
            out.push({ node: nodes[other], kind: edges[e].kind });
          });
        }
        return out;
      },
      stats: function () {
        return { fps: fps, level: level, frames: frames, nodes: nodes.length, edges: edges.length, settled: settled, calm: calm, zoom: camera.z };
      },
      setActive: function (flag) {
        active = Boolean(flag);
        frameTimes = [];
        dirty = true;
      }
    };
  }

  window.OSGraph = { create: create };
}());
