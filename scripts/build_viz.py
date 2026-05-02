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

TEMPLATE_PATH = os.path.join(ROOT, "viz", "template.html")
HTML = open(TEMPLATE_PATH, encoding="utf-8").read()


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
