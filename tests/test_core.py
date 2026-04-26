"""
Testes das funções puras do pipeline.
Sem chamadas de API, sem leitura de arquivos de dados.
"""
import pytest

from batch_utils import LABEL_TO_SCORE, apply_consistency, decide, fill_user, parse_response
from build_pairs import candidate_pairs


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_valid(self):
        text = '{"reasoning": "A precede B.", "label": "DEFINITIVAMENTE_SIM"}'
        r = parse_response(text)
        assert r["label"] == "DEFINITIVAMENTE_SIM"
        assert r["score"] == 1.0
        assert r["reasoning"] == "A precede B."

    def test_all_labels_map_to_canonical_score(self):
        for label, expected in LABEL_TO_SCORE.items():
            r = parse_response(f'{{"reasoning": "x", "label": "{label}"}}')
            assert r["score"] == expected

    def test_json_buried_in_prose(self):
        text = 'Análise:\n{"reasoning": "ok", "label": "INCERTO"}\nFim.'
        assert parse_response(text)["score"] == 0.5

    def test_model_score_field_overwritten_by_canonical(self):
        # Se o modelo incluir "score", deve ser descartado em favor do canônico.
        text = '{"reasoning": "x", "label": "PROVAVELMENTE_SIM", "score": 0.99}'
        assert parse_response(text)["score"] == 0.75

    def test_invalid_label(self):
        assert parse_response('{"reasoning": "x", "label": "TALVEZ"}') is None

    def test_missing_label(self):
        assert parse_response('{"reasoning": "x"}') is None

    def test_missing_reasoning(self):
        assert parse_response('{"label": "INCERTO"}') is None

    def test_no_json(self):
        assert parse_response("sem JSON aqui") is None

    def test_malformed_json(self):
        assert parse_response('{"reasoning": "x", "label":}') is None

    def test_empty_string(self):
        assert parse_response("") is None


# ---------------------------------------------------------------------------
# fill_user
# ---------------------------------------------------------------------------

class TestFillUser:
    def test_all_placeholders_substituted(self):
        tpl = "{{ANO_A}} {{CODIGO_A}} {{HABILIDADE_A}} {{ANO_B}} {{CODIGO_B}} {{HABILIDADE_B}}"
        row = {
            "ano_a": "1", "codigo_a": "EF01MA01", "habilidade_a": "contar",
            "ano_b": "2", "codigo_b": "EF02MA01", "habilidade_b": "somar",
        }
        assert fill_user(tpl, row) == "1 EF01MA01 contar 2 EF02MA01 somar"

    def test_no_leftover_placeholders(self):
        tpl = "A ({{CODIGO_A}}) → B ({{CODIGO_B}})"
        row = {
            "ano_a": "1", "codigo_a": "X", "habilidade_a": "x",
            "ano_b": "1", "codigo_b": "Y", "habilidade_b": "y",
        }
        result = fill_user(tpl, row)
        assert "{{" not in result


# ---------------------------------------------------------------------------
# decide  (5_assemble.py)
# ---------------------------------------------------------------------------

def _r(label, score):
    return {"label": label, "score": score, "reasoning": "x"}


class TestDecide:
    def test_missing_from_pass1(self):
        score, src = decide("a__b", {}, {}, {})
        assert score is None and src == "missing"

    def test_extreme_goes_directly(self):
        p1 = {"a__b": _r("DEFINITIVAMENTE_SIM", 1.0)}
        score, src = decide("a__b", p1, {}, {})
        assert score == 1.0 and src == "pass1"

    def test_borderline_without_pass2_keeps_pass1(self):
        p1 = {"a__b": _r("INCERTO", 0.5)}
        score, src = decide("a__b", p1, {}, {})
        assert score == 0.5 and src == "pass1"

    def test_borderline_agreement_averages(self):
        p1 = {"a__b": _r("PROVAVELMENTE_SIM", 0.75)}
        p2 = {"a__b": _r("PROVAVELMENTE_SIM", 0.75)}
        score, src = decide("a__b", p1, p2, {})
        assert score == 0.75 and src == "pass1+pass2_avg"

    def test_borderline_avg_produces_non_canonical_score(self):
        # 0.75 e 0.5 → média 0.625, valor final válido sem conversão a label
        p1 = {"a__b": _r("PROVAVELMENTE_SIM", 0.75)}
        p2 = {"a__b": _r("INCERTO", 0.5)}
        score, src = decide("a__b", p1, p2, {})
        assert score == 0.625 and src == "pass1+pass2_avg"

    def test_disagreement_escalates_to_pass3(self):
        p1 = {"a__b": _r("PROVAVELMENTE_SIM", 0.75)}
        p2 = {"a__b": _r("PROVAVELMENTE_NÃO", 0.25)}
        p3 = {"a__b": _r("DEFINITIVAMENTE_SIM", 1.0)}
        score, src = decide("a__b", p1, p2, p3)
        assert score == 1.0 and src == "pass3"

    def test_disagreement_without_pass3_falls_back_to_avg(self):
        p1 = {"a__b": _r("PROVAVELMENTE_SIM", 0.75)}
        p2 = {"a__b": _r("PROVAVELMENTE_NÃO", 0.25)}
        score, src = decide("a__b", p1, p2, {})
        assert score == 0.5 and src == "pass1+pass2_fallback"

    @pytest.mark.parametrize("label", ["DEFINITIVAMENTE_SIM", "DEFINITIVAMENTE_NÃO"])
    def test_all_extremes_bypass_pass2(self, label):
        score_val = LABEL_TO_SCORE[label]
        p1 = {"a__b": _r(label, score_val)}
        p2 = {"a__b": _r("INCERTO", 0.5)}  # presente mas irrelevante
        _, src = decide("a__b", p1, p2, {})
        assert src == "pass1"


# ---------------------------------------------------------------------------
# candidate_pairs  (build_pairs.py)
# ---------------------------------------------------------------------------

def _skill(codigo, ano):
    return {"codigo": codigo, "ano_equivalente": ano, "habilidade": "x"}


class TestCandidatePairs:
    def test_delta_1_included_forward_only(self):
        skills = [_skill("A", 1), _skill("B", 2)]
        pairs, dist = candidate_pairs(skills)
        assert len(pairs) == 1
        assert (pairs[0][0], pairs[0][3]) == ("A", "B")
        assert dist == {1: 1}

    def test_delta_gt_2_excluded(self):
        skills = [_skill("A", 1), _skill("B", 4)]
        pairs, _ = candidate_pairs(skills)
        assert len(pairs) == 0

    def test_delta_0_included_both_directions(self):
        skills = [_skill("A", 1), _skill("B", 1)]
        pairs, dist = candidate_pairs(skills)
        assert len(pairs) == 2
        assert dist == {0: 2}

    def test_self_pair_excluded(self):
        pairs, _ = candidate_pairs([_skill("A", 1)])
        assert len(pairs) == 0

    def test_dist_counts_all_deltas(self):
        # A(1)→B(2): delta=1; B(2)→C(3): delta=1; A(1)→C(3): delta=2
        skills = [_skill("A", 1), _skill("B", 2), _skill("C", 3)]
        _, dist = candidate_pairs(skills)
        assert dist.get(1) == 2
        assert dist.get(2) == 1
        assert 0 not in dist
        assert 3 not in dist


# ---------------------------------------------------------------------------
# apply_consistency  (run_sample.py)
# ---------------------------------------------------------------------------

def _result(score, label="INCERTO", source="pass1"):
    return {"score": score, "label": label, "source": source, "reasoning": "x"}


def _row(ca, ano_a, cb, ano_b):
    return {"codigo_a": ca, "ano_a": str(ano_a), "codigo_b": cb, "ano_b": str(ano_b),
            "habilidade_a": "x", "habilidade_b": "x"}


class TestApplyConsistency:
    def test_symmetry_property_holds(self):
        # Propriedade fundamental: corrected(A→B) + corrected(B→A) = 1.0
        rows = [_row("A", 1, "B", 1), _row("B", 1, "A", 1)]
        results = {"A__B": _result(0.75), "B__A": _result(0.75)}
        c, _ = apply_consistency(rows, results)
        assert abs(c["A__B"]["score"] + c["B__A"]["score"] - 1.0) < 1e-9

    def test_inconsistent_pair_corrected(self):
        # Ambos altos (soma > 1) → correção aplicada
        rows = [_row("A", 1, "B", 1), _row("B", 1, "A", 1)]
        results = {"A__B": _result(0.75), "B__A": _result(0.75)}
        c, _ = apply_consistency(rows, results)
        assert c["A__B"]["score"] == 0.5
        assert c["A__B"].get("consistency") == "corrected"

    def test_consistent_pair_not_corrected(self):
        # raw(A→B)=0.75, raw(B→A)=0.25 → soma=1.0 ≤ 1.0, sem correção
        rows = [_row("A", 1, "B", 1), _row("B", 1, "A", 1)]
        results = {"A__B": _result(0.75), "B__A": _result(0.25)}
        c, _ = apply_consistency(rows, results)
        assert c["A__B"]["score"] == 0.75
        assert "consistency" not in c["A__B"]

    def test_both_low_not_corrected(self):
        # raw(A→B)=0.25, raw(B→A)=0.25 → soma=0.5 ≤ 1.0, sem correção
        rows = [_row("A", 1, "B", 1), _row("B", 1, "A", 1)]
        results = {"A__B": _result(0.25), "B__A": _result(0.25)}
        c, _ = apply_consistency(rows, results)
        assert c["A__B"]["score"] == 0.25
        assert "consistency" not in c["A__B"]

    def test_different_years_skipped(self):
        rows = [_row("A", 1, "B", 2)]
        results = {"A__B": _result(1.0)}
        c, _ = apply_consistency(rows, results)
        assert "consistency" not in c["A__B"]

    def test_label_removed_after_correction(self):
        rows = [_row("A", 1, "B", 1), _row("B", 1, "A", 1)]
        results = {"A__B": _result(0.75), "B__A": _result(0.75)}
        c, _ = apply_consistency(rows, results)
        assert "label" not in c["A__B"]
