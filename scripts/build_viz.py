#!/usr/bin/env python3
"""
Gera viz/prereq_graph.html — grafo interativo dos pré-requisitos BNCC.

Layout pré-computado com networkx (zero simulação no browser).
Renderizado em <canvas> em vez de SVG (sem overhead de DOM).

Uso:
    python3 scripts/build_viz.py
    open viz/prereq_graph.html
"""
import csv
import json
import os
import random

import networkx as nx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>BNCC Matemática · Grafo de pré-requisitos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', system-ui, sans-serif; background: #f5f6fa; overflow: hidden; color: #111; }

/* ── header ─────────────────────────────────────────────────── */
#header {
  display: flex; align-items: center; gap: 20px;
  padding: 0 20px; background: #fff; border-bottom: 1px solid #e5e7eb;
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  height: 48px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
#header h1 { font-size: 13px; font-weight: 600; color: #111; white-space: nowrap; letter-spacing: -0.01em; }

.seg-label { font-size: 12px; color: #6b7280; display: flex; align-items: center; gap: 8px; }
.seg { display: flex; border: 1px solid #e5e7eb; border-radius: 7px; overflow: hidden; }
.seg button {
  background: none; border: none; border-right: 1px solid #e5e7eb;
  padding: 4px 13px; font-size: 12px; font-family: inherit; font-weight: 500;
  cursor: pointer; color: #6b7280; transition: background .1s, color .1s;
}
.seg button:last-child { border-right: none; }
.seg button.active { background: #2563eb; color: #fff; font-weight: 600; }
.seg button:hover:not(.active) { background: #f3f4f6; color: #374151; }

#stats { font-size: 11.5px; color: #9ca3af; margin-left: auto; }
#reset-view {
  background: none; border: 1px solid #e5e7eb; border-radius: 6px;
  padding: 4px 11px; font-size: 12px; font-family: inherit; font-weight: 500;
  color: #6b7280; cursor: pointer; transition: background .1s, color .1s; white-space: nowrap;
}
#reset-view:hover { background: #f3f4f6; color: #374151; }

/* ── canvas ─────────────────────────────────────────────────── */
#graph { position: fixed; top: 48px; left: 0; right: 0; bottom: 0; display: block; cursor: default; }

/* ── drawer ─────────────────────────────────────────────────── */
#drawer {
  position: fixed; top: 48px; right: 0; bottom: 0; width: 296px;
  background: #fff; border-left: 1px solid #e5e7eb;
  box-shadow: -4px 0 20px rgba(0,0,0,0.07);
  transform: translateX(100%); transition: transform .2s cubic-bezier(.4,0,.2,1);
  z-index: 200; display: flex; flex-direction: column; overflow: hidden;
}
#drawer.open { transform: translateX(0); }

#drawer-head {
  padding: 16px 16px 14px; border-bottom: 1px solid #f3f4f6; flex-shrink: 0;
}
#drawer-top-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
#drawer-code { font-size: 16px; font-weight: 700; color: #111; letter-spacing: -0.02em; line-height: 1.2; }
#drawer-close {
  background: none; border: none; font-size: 17px; color: #9ca3af;
  cursor: pointer; padding: 0 2px; line-height: 1; flex-shrink: 0; font-family: inherit;
}
#drawer-close:hover { color: #374151; }
#drawer-year-badge {
  display: inline-flex; align-items: center; margin-top: 6px;
  font-size: 10.5px; font-weight: 600; padding: 2px 8px; border-radius: 5px;
  color: #fff; letter-spacing: 0.01em;
}
#drawer-hab {
  font-size: 12px; color: #4b5563; line-height: 1.65; margin-top: 9px;
}

#drawer-body { flex: 1; overflow-y: auto; padding: 4px 16px 24px; }
.drawer-section { margin-top: 18px; }
.drawer-section-title {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.07em; color: #9ca3af; margin-bottom: 8px;
}
.drawer-empty { font-size: 12px; color: #d1d5db; font-style: italic; }

.drawer-item { padding: 8px 0; border-bottom: 1px solid #f9fafb; }
.drawer-item:last-child { border-bottom: none; }
.item-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 3px; }
.item-code { font-size: 11.5px; font-weight: 600; letter-spacing: -0.01em; }
.item-score {
  font-size: 10.5px; font-weight: 600; color: #fff;
  padding: 1px 7px; border-radius: 4px; flex-shrink: 0;
}
.item-hab {
  font-size: 11px; color: #6b7280; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}

/* ── tooltip ─────────────────────────────────────────────────── */
#tooltip {
  position: fixed; display: none; pointer-events: none;
  background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
  padding: 11px 14px; max-width: 340px; font-size: 12.5px; line-height: 1.55;
  box-shadow: 0 4px 20px rgba(0,0,0,0.09); z-index: 300;
}
#tooltip .t-code { font-size: 14px; font-weight: 700; color: #111; margin-bottom: 2px; letter-spacing: -0.01em; }
#tooltip .t-ano {
  display: inline-flex; align-items: center;
  font-size: 10.5px; font-weight: 600; padding: 2px 7px; border-radius: 4px;
  color: #fff; margin-left: 7px; vertical-align: middle; position: relative; top: -1px;
}
#tooltip .t-hab { margin-top: 7px; color: #6b7280; font-size: 12px; }

/* ── hint ────────────────────────────────────────────────────── */
#hint {
  position: fixed; bottom: 14px; right: 18px; z-index: 100;
  font-size: 10.5px; color: #d1d5db; text-align: right; line-height: 1.9;
  pointer-events: none; font-family: inherit;
}
</style>
</head>
<body>

<div id="header">
  <h1>BNCC Matemática <span style="color:#d1d5db;font-weight:400;margin:0 1px">·</span> <span style="font-weight:400;color:#6b7280">Grafo de pré-requisitos</span></h1>
  <div class="seg-label">
    Limiar de score
    <div class="seg">
      <button data-val="0.5">≥ 0,50</button>
      <button data-val="0.75" class="active">≥ 0,75</button>
      <button data-val="1.0">= 1,00</button>
    </div>
  </div>
  <span id="stats"></span>
  <button id="reset-view" title="Resetar zoom e posição">Resetar visão</button>
</div>

<canvas id="graph"></canvas>

<div id="drawer">
  <div id="drawer-head">
    <div id="drawer-top-row">
      <span id="drawer-code"></span>
      <button id="drawer-close" title="Fechar">×</button>
    </div>
    <div id="drawer-year-badge"></div>
    <div id="drawer-hab"></div>
  </div>
  <div id="drawer-body">
    <div class="drawer-section">
      <div class="drawer-section-title">Pré-requisitos</div>
      <div id="drawer-preds"></div>
    </div>
    <div class="drawer-section">
      <div class="drawer-section-title">Dependentes</div>
      <div id="drawer-succs"></div>
    </div>
  </div>
</div>

<div id="tooltip"></div>
<div id="hint">Hover — detalhes &nbsp;·&nbsp; Clique — gaveta &nbsp;·&nbsp; Fundo — fechar<br>Scroll — zoom &nbsp;·&nbsp; Arrastar — mover</div>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
/*GRAPH_DATA*/

// ── canvas setup ───────────────────────────────────────────────
const canvas = document.getElementById('graph');
const ctx    = canvas.getContext('2d');
const W = window.innerWidth;
const H = window.innerHeight - 48;
const dpr = window.devicePixelRatio || 1;
canvas.style.width  = W + 'px';
canvas.style.height = H + 'px';
canvas.width  = W * dpr;
canvas.height = H * dpr;
ctx.scale(dpr, dpr);

// ── constants ─────────────────────────────────────────────────
const NODE_R = 6;
const PAD_L  = 56, PAD_R = 56, PAD_T = 52, PAD_B = 22;
const YEARS  = d3.range(1, 11);

// ── color scale ───────────────────────────────────────────────
const PALETTE = [
  '#1d6fa4',  // 1  cerulean
  '#c0562a',  // 2  terracotta
  '#2e8b57',  // 3  sea green
  '#7b2d8b',  // 4  plum
  '#c9840a',  // 5  amber
  '#1e7b8c',  // 6  dark teal
  '#b83c5a',  // 7  carmine
  '#4a7c59',  // 8  pine
  '#5c4784',  // 9  slate
  '#2d6b8c',  // 10 prussian blue
];
const colorScale = d3.scaleOrdinal().domain(YEARS).range(PALETTE);

// ── index ─────────────────────────────────────────────────────
const nodeById = Object.fromEntries(graphData.nodes.map(n => [n.id, n]));

// ── coordinate mapping ────────────────────────────────────────
function sx(n) { return PAD_L + n.x * (W - PAD_L - PAD_R); }
function sy(n) { return PAD_T + n.y * (H - PAD_T - PAD_B); }

// ── zoom ──────────────────────────────────────────────────────
let transform = d3.zoomIdentity;

// ── threshold ─────────────────────────────────────────────────
let currentThreshold = 0.75;
let visibleLinks = [];

document.querySelectorAll('.seg button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.seg button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentThreshold = +btn.dataset.val;
    updateVisible();
    if (focused !== null) { setFocus(focused); populateDrawer(focused); }
    draw();
  });
});

function updateVisible() {
  visibleLinks = graphData.links.filter(l => l.score >= currentThreshold);
  document.getElementById('stats').textContent =
    `${visibleLinks.length.toLocaleString('pt-BR')} arestas · ${graphData.nodes.length} habilidades`;
}

// ── focus state ───────────────────────────────────────────────
let focused = null, focusedNodes = null, focusedLinks = null;

function setFocus(nid) {
  if (nid === null) {
    focused = null; focusedNodes = null; focusedLinks = null; return;
  }
  focused = nid;
  focusedNodes = new Set([nid]);
  focusedLinks  = new Set();
  visibleLinks.forEach((l, i) => {
    if (l.source === nid || l.target === nid) {
      focusedNodes.add(l.source);
      focusedNodes.add(l.target);
      focusedLinks.add(i);
    }
  });
}

// ── drawer ────────────────────────────────────────────────────
const drawer = document.getElementById('drawer');

function fmtScore(s) {
  return s.toFixed(2);
}

function itemHtml(node, score) {
  return `<div class="drawer-item">
    <div class="item-row">
      <span class="item-code" style="color:${colorScale(node.ano)}">${node.id}</span>
      <span class="item-score" style="background:#9ca3af">${fmtScore(score)}</span>
    </div>
    <div class="item-hab">${node.habilidade}</div>
  </div>`;
}

function populateDrawer(nid) {
  const n = nodeById[nid];
  document.getElementById('drawer-code').textContent = n.id;
  const badge = document.getElementById('drawer-year-badge');
  badge.textContent = n.ano < 10 ? `${n.ano}º ano — Ensino Fundamental` : 'Ensino Médio';
  badge.style.background = colorScale(n.ano);
  document.getElementById('drawer-hab').textContent = n.habilidade;

  const preds = visibleLinks.filter(l => l.target === nid).sort((a, b) => b.score - a.score);
  const succs = visibleLinks.filter(l => l.source === nid).sort((a, b) => b.score - a.score);

  document.getElementById('drawer-preds').innerHTML = preds.length
    ? preds.map(l => itemHtml(nodeById[l.source], l.score)).join('')
    : '<div class="drawer-empty">Nenhum no limiar atual</div>';

  document.getElementById('drawer-succs').innerHTML = succs.length
    ? succs.map(l => itemHtml(nodeById[l.target], l.score)).join('')
    : '<div class="drawer-empty">Nenhum no limiar atual</div>';
}

function openDrawer(nid) { populateDrawer(nid); drawer.classList.add('open'); }
function closeDrawer()   { drawer.classList.remove('open'); }

document.getElementById('drawer-close').addEventListener('click', () => {
  setFocus(null); closeDrawer(); draw();
});

// ── draw helpers ──────────────────────────────────────────────
function drawArrow(x1, y1, x2, y2, color) {
  const dx = x2 - x1, dy = y2 - y1;
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;
  if (dist < (NODE_R + 2) * 2) return;
  const ux = dx / dist, uy = dy / dist;
  const gap = NODE_R + 2;
  const ex = x2 - ux * gap, ey = y2 - uy * gap;

  ctx.strokeStyle = color;
  ctx.fillStyle   = color;
  ctx.lineWidth   = 1.2;

  ctx.beginPath();
  ctx.moveTo(x1 + ux * gap, y1 + uy * gap);
  ctx.lineTo(ex, ey);
  ctx.stroke();

  const aL = 7, aW = 3;
  ctx.beginPath();
  ctx.moveTo(ex, ey);
  ctx.lineTo(ex - ux * aL + uy * aW, ey - uy * aL - ux * aW);
  ctx.lineTo(ex - ux * aL - uy * aW, ey - uy * aL + ux * aW);
  ctx.closePath();
  ctx.fill();
}

// ── main draw ─────────────────────────────────────────────────
function draw() {
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.k, transform.k);

  // year lane guides
  ctx.save();
  ctx.setLineDash([3, 6]);
  ctx.lineWidth = 1;
  YEARS.forEach(yr => {
    const x = PAD_L + (yr - 1) / 9 * (W - PAD_L - PAD_R);
    ctx.strokeStyle = '#eaecf0';
    ctx.beginPath(); ctx.moveTo(x, PAD_T + 8); ctx.lineTo(x, H - 8); ctx.stroke();
  });
  ctx.setLineDash([]);
  ctx.restore();

  // year labels
  ctx.save();
  ctx.textAlign = 'center';
  ctx.font = '600 11px "Inter", system-ui';
  YEARS.forEach(yr => {
    const x = PAD_L + (yr - 1) / 9 * (W - PAD_L - PAD_R);
    ctx.fillStyle = colorScale(yr);
    ctx.fillText(yr < 10 ? `${yr}º EF` : 'EM', x, PAD_T - 9);
  });
  ctx.restore();

  // edges
  const EDGE_NORMAL = 'rgba(176,184,200,0.38)';
  const EDGE_FOCUS  = 'rgba(60,68,80,0.78)';
  const EDGE_FAINT  = 'rgba(176,184,200,0.055)';

  visibleLinks.forEach((l, i) => {
    const src = nodeById[l.source], tgt = nodeById[l.target];
    if (!src || !tgt) return;
    drawArrow(
      sx(src), sy(src), sx(tgt), sy(tgt),
      focused === null ? EDGE_NORMAL : focusedLinks.has(i) ? EDGE_FOCUS : EDGE_FAINT
    );
  });

  // nodes
  graphData.nodes.forEach(n => {
    const dim = focused !== null && !focusedNodes.has(n.id);
    ctx.globalAlpha = dim ? 0.06 : 1;
    ctx.beginPath();
    ctx.arc(sx(n), sy(n), NODE_R, 0, Math.PI * 2);
    ctx.fillStyle = colorScale(n.ano);
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });
  ctx.globalAlpha = 1;

  ctx.restore();
}

// ── mouse helpers ─────────────────────────────────────────────
function toGraphCoords(e) {
  const rect = canvas.getBoundingClientRect();
  return [
    (e.clientX - rect.left - transform.x) / transform.k,
    (e.clientY - rect.top  - transform.y) / transform.k,
  ];
}

function nearestNode(gx, gy, maxScreenPx) {
  const thresh = maxScreenPx / transform.k;
  let best = null, bestD = Infinity;
  for (const n of graphData.nodes) {
    const d = Math.hypot(sx(n) - gx, sy(n) - gy);
    if (d < bestD) { bestD = d; best = n; }
  }
  return bestD < thresh ? best : null;
}

// ── hover / tooltip ───────────────────────────────────────────
const tooltip = document.getElementById('tooltip');
let hoveredId = null;

canvas.addEventListener('mousemove', e => {
  const [gx, gy] = toGraphCoords(e);
  const n = nearestNode(gx, gy, 14);

  if (n) {
    canvas.style.cursor = 'pointer';
    if (hoveredId !== n.id) {
      hoveredId = n.id;
      const label = n.ano < 10 ? `${n.ano}º ano EF` : 'Ensino Médio';
      tooltip.style.display = 'block';
      tooltip.innerHTML =
        `<div class="t-code">${n.id}<span class="t-ano" style="background:${colorScale(n.ano)}">${label}</span></div>` +
        `<div class="t-hab">${n.habilidade}</div>`;
    }
    const tw = 340, th = 120;
    const lx = e.clientX + tw + 18 > W ? e.clientX - tw - 14 : e.clientX + 14;
    const ly = e.clientY + th > window.innerHeight ? e.clientY - th : e.clientY - 4;
    tooltip.style.left = lx + 'px';
    tooltip.style.top  = ly + 'px';
  } else {
    canvas.style.cursor = 'default';
    hoveredId = null;
    tooltip.style.display = 'none';
  }
});

canvas.addEventListener('mouseleave', () => {
  hoveredId = null;
  tooltip.style.display = 'none';
});

// ── click-to-focus + drawer ───────────────────────────────────
let mouseDownPos = null;
canvas.addEventListener('mousedown', e => { mouseDownPos = [e.clientX, e.clientY]; });
canvas.addEventListener('click', e => {
  if (mouseDownPos && Math.hypot(e.clientX - mouseDownPos[0], e.clientY - mouseDownPos[1]) > 4) return;
  const [gx, gy] = toGraphCoords(e);
  const n = nearestNode(gx, gy, 16);
  tooltip.style.display = 'none';

  if (n && focused !== n.id) {
    setFocus(n.id);
    openDrawer(n.id);
  } else {
    setFocus(null);
    closeDrawer();
  }
  draw();
});

// ── reset view ────────────────────────────────────────────────
const zoom = d3.zoom().scaleExtent([0.1, 14]).on('zoom', e => { transform = e.transform; draw(); });
d3.select(canvas).call(zoom).on('dblclick.zoom', null);

document.getElementById('reset-view').addEventListener('click', () => {
  d3.select(canvas).transition().duration(400).call(zoom.transform, d3.zoomIdentity);
});

// ── init ──────────────────────────────────────────────────────
updateVisible();
draw();
</script>
</body>
</html>
"""


def main():
    nodes = {}
    with open(os.path.join(ROOT, 'data', 'matematica_bncc.csv'), encoding='utf-8') as f:
        for row in csv.DictReader(f):
            nodes[row['codigo']] = {
                'id':        row['codigo'],
                'ano':       int(row['ano_equivalente']),
                'habilidade': row['habilidade'],
            }

    links = []
    with open(os.path.join(ROOT, 'data', 'prereq_pairs_final.csv'), encoding='utf-8') as f:
        for row in csv.DictReader(f):
            s = float(row['score'])
            if s >= 0.5:
                links.append({
                    'source': row['codigo_a'],
                    'target': row['codigo_b'],
                    'score':  round(s, 4),
                })

    # ── layout pré-computado com networkx ─────────────────────
    G = nx.DiGraph()
    for nid in nodes:
        G.add_node(nid)
    for lnk in links:
        if lnk['score'] >= 0.75:
            G.add_edge(lnk['source'], lnk['target'], weight=lnk['score'])

    # Posições iniciais: x por ano, y escalonado por coluna
    init_pos, y_ctr = {}, {}
    for nid, n in nodes.items():
        yr = n['ano']
        y_ctr[yr] = y_ctr.get(yr, 0)
        init_pos[nid] = [float((yr - 1) / 9), y_ctr[yr] * 0.08]
        y_ctr[yr] += 1

    print("Calculando layout (spring_layout, 300 iterações)…")
    pos = nx.spring_layout(G, pos=init_pos, k=0.18, iterations=300, seed=42, weight='weight')

    # Restaura x estritamente por ano
    for nid in pos:
        pos[nid][0] = float((nodes[nid]['ano'] - 1) / 9)

    # Normaliza y para [0.04, 0.96]
    ys  = [p[1] for p in pos.values()]
    ymin, ymax = min(ys), max(ys)
    span = ymax - ymin if ymax > ymin else 1.0
    for nid in pos:
        pos[nid][1] = 0.04 + 0.92 * (pos[nid][1] - ymin) / span

    # Jitter horizontal para separar arestas intra-ano
    rng = random.Random(7)
    for n in nodes.values():
        jitter = (rng.random() - 0.5) * 0.055   # ±0.028 em espaço normalizado
        n['x'] = round(max(0.0, min(1.0, pos[n['id']][0] + jitter)), 4)
        n['y'] = round(float(pos[n['id']][1]), 4)

    data_json = json.dumps(
        {'nodes': list(nodes.values()), 'links': links},
        ensure_ascii=False,
    )
    html = HTML.replace('/*GRAPH_DATA*/', f'const graphData = {data_json};')

    out_dir = os.path.join(ROOT, 'viz')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'prereq_graph.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✓  viz/prereq_graph.html")
    print(f"   {len(nodes)} nós · {len(links)} arestas (score ≥ 0.50)")
    print("   Abra: open viz/prereq_graph.html")


if __name__ == '__main__':
    main()
