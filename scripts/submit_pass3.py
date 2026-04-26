"""
Pass 3 — escalação dos pares discordantes ao Sonnet com temperature=0.

Discordância: |score_pass1 - score_pass2| > ESCALATION_THRESHOLD (0.25),
ou seja, os dois julgamentos Haiku divergiram por mais de um degrau na escala.

O prompt do Sonnet inclui uma nota de contexto com os dois julgamentos anteriores
e seus raciocínios, para que o modelo possa fazer um julgamento informado — mas
ainda independente (o Sonnet não é instruído a "desempatar", e sim a avaliar o par
por conta própria tendo o contexto como referência).

Também salva data/interim/pass2_scores.csv para auditoria.

Uso:
    python scripts/submit_pass3.py
    (somente após poll.py confirmar que todos os batches do Pass 2 foram baixados)
"""

import csv
import os
import sys

import anthropic

from batch_utils import (
    PAIRS_CSV,
    INTERIM_DIR,
    MODEL_SECONDARY,
    ESCALATION_THRESHOLD,
    custom_id,
    load_prompt,
    make_request,
    load_state,
    submit_batches,
    load_jsonl_results,
)


def main() -> None:
    state = load_state()

    if any(b["pass"] == 3 for b in state["batches"]):
        print("Batches do Pass 3 já existem. Aborting.")
        return

    p2_batches = [b for b in state["batches"] if b["pass"] == 2]
    if not p2_batches or any(b["status"] != "downloaded" for b in p2_batches):
        print("Nem todos os batches do Pass 2 foram baixados. Rode poll.py primeiro.")
        return

    print("Carregando resultados dos Passes 1 e 2…")
    pass1 = load_jsonl_results(1)
    pass2 = load_jsonl_results(2)

    rows = list(csv.DictReader(PAIRS_CSV.open(encoding="utf-8")))

    # Salva CSV intermediário Pass 2
    interim = INTERIM_DIR / "pass2_scores.csv"
    with interim.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "codigo_a", "ano_a", "habilidade_a",
            "codigo_b", "ano_b", "habilidade_b",
            "pass1_label", "pass1_score",
            "pass2_label", "pass2_score",
            "delta_scores",
        ])
        writer.writeheader()
        for row in rows:
            cid = custom_id(row)
            if cid not in pass2:
                continue
            p1 = pass1.get(cid, {})
            p2 = pass2[cid]
            delta = abs(p1.get("score", 0) - p2["score"]) if p1 else ""
            writer.writerow({
                **row,
                "pass1_label":  p1.get("label", ""),
                "pass1_score":  p1.get("score", ""),
                "pass2_label":  p2["label"],
                "pass2_score":  p2["score"],
                "delta_scores": round(delta, 4) if delta != "" else "",
            })
    print(f"Pass 2 scores salvos → {interim}")

    # Identifica escalações
    escalated: list[tuple[dict, dict, dict]] = []
    for row in rows:
        cid = custom_id(row)
        p1 = pass1.get(cid)
        p2 = pass2.get(cid)
        if p1 and p2 and abs(p1["score"] - p2["score"]) > ESCALATION_THRESHOLD:
            escalated.append((row, p1, p2))

    print(f"Pares escalados para Sonnet (Pass 3): {len(escalated):,}")

    if not escalated:
        print("Nenhuma escalação. Pule para assemble.py.")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system, user_tpl = load_prompt()

    requests = []
    for row, p1, p2 in escalated:
        note = (
            "[Nota de contexto: este par foi escalado porque dois julgamentos "
            "independentes do modelo primário discordaram.\n"
            f"Julgamento 1: {p1['label']} (score {p1['score']}) "
            f"— \"{p1['reasoning']}\"\n"
            f"Julgamento 2: {p2['label']} (score {p2['score']}) "
            f"— \"{p2['reasoning']}\"\n"
            "Por favor, avalie o par com atenção e emita seu próprio julgamento.]"
        )
        requests.append(
            make_request(row, system, user_tpl, MODEL_SECONDARY, temperature=0, context_note=note)
        )

    n = submit_batches(client, requests, pass_n=3, state=state)
    print(f"\n{n} batch(es) submetido(s). Rode poll.py novamente.")


if __name__ == "__main__":
    main()
