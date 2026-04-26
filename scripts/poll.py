"""
Verifica o status de todos os batches em andamento e baixa os resultados
dos que já terminaram.

Rode este script repetidamente (ou deixe num loop) entre cada pass até que
não haja mais batches "in_progress". Os resultados são salvos como JSONL em
data/raw_results/ antes de qualquer processamento — isso garante idempotência:
se um script posterior falhar, não é necessário resubmeter o batch.

Uso:
    python scripts/2_poll.py
    # ou, para rodar a cada 60 s até tudo terminar:
    watch -n 60 python scripts/2_poll.py
"""

import json
import os
import sys
from collections import Counter

import anthropic

from batch_utils import RAW_DIR, load_state, save_state


def main() -> None:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    state  = load_state()

    in_progress = [b for b in state["batches"] if b["status"] == "in_progress"]

    if not in_progress:
        print("Nenhum batch em andamento.")
        _print_summary(state)
        return

    for entry in in_progress:
        batch = client.messages.batches.retrieve(entry["batch_id"])
        status = batch.processing_status
        print(f"Pass {entry['pass']} | {entry['batch_id']} → {status}")

        if status == "ended":
            out_file = RAW_DIR / f"pass{entry['pass']}_{entry['batch_id']}.jsonl"
            print(f"  Baixando resultados → {out_file} …", flush=True)

            n_lines = 0
            with out_file.open("w", encoding="utf-8") as f:
                for result in client.messages.batches.results(entry["batch_id"]):
                    f.write(result.model_dump_json() + "\n")
                    n_lines += 1

            entry["status"]       = "downloaded"
            entry["results_file"] = str(out_file)
            save_state(state)
            print(f"  Concluído: {n_lines:,} linhas salvas.")

    _print_summary(state)


def _print_summary(state: dict) -> None:
    counts = Counter((b["pass"], b["status"]) for b in state["batches"])
    print("\nEstado atual dos batches:")
    for (pass_n, status), n in sorted(counts.items()):
        print(f"  Pass {pass_n} — {status}: {n} batch(es)")

    # Indica o próximo passo
    passes_done = {
        p for p in (1, 2, 3)
        if all(
            b["status"] == "downloaded"
            for b in state["batches"]
            if b["pass"] == p
        ) and any(b["pass"] == p for b in state["batches"])
    }
    all_in_progress = [b for b in state["batches"] if b["status"] == "in_progress"]

    if all_in_progress:
        print("\nAinda há batches em andamento. Rode 2_poll.py novamente em alguns minutos.")
    elif 1 in passes_done and 2 not in passes_done:
        print("\nPass 1 completo → rode 3_submit_pass2.py")
    elif 2 in passes_done and 3 not in passes_done:
        print("\nPass 2 completo → rode 4_submit_pass3.py")
        print("(se não houver escalações, rode diretamente 5_assemble.py)")
    elif passes_done:
        print("\nTodos os passes baixados → rode 5_assemble.py")


if __name__ == "__main__":
    main()
