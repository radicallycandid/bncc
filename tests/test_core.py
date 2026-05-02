"""
Testes das funções puras do pipeline.
Sem chamadas de API, sem leitura de arquivos de dados de produção.
"""
import json

import pytest

import batch_utils
from batch_utils import (
    LABEL_TO_SCORE,
    PROMPT_FILE_SYM,
    custom_id,
    decide,
    fill_user,
    load_prompt,
    load_state,
    make_request,
    make_request_sym,
    parse_response,
    parse_sym_response,
    resolve_pair,
    save_state,
    sym_custom_id,
)
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

    def test_substituted_value_is_not_re_substituted(self):
        # Se uma habilidade contém literalmente '{{ANO_B}}', a substituição em uma
        # passada deve preservar o literal — .replace() encadeado faria a re-substituição
        # e quebraria silenciosamente.
        tpl = "{{HABILIDADE_A}} | ano={{ANO_B}}"
        row = {
            "ano_a": "1", "codigo_a": "X", "habilidade_a": "texto com {{ANO_B}} no meio",
            "ano_b": "9", "codigo_b": "Y", "habilidade_b": "y",
        }
        assert fill_user(tpl, row) == "texto com {{ANO_B}} no meio | ano=9"


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
# custom_id
# ---------------------------------------------------------------------------

class TestCustomId:
    def test_format(self):
        row = {"codigo_a": "EF01MA01", "codigo_b": "EF02MA01"}
        assert custom_id(row) == "EF01MA01__EF02MA01"

    def test_separator_is_double_underscore(self):
        # custom_id é a chave usada pra correlacionar passes — o separador "__"
        # precisa ser estável e não ambíguo (não pode aparecer dentro de um código BNCC).
        row = {"codigo_a": "X", "codigo_b": "Y"}
        assert custom_id(row).count("__") == 1


# ---------------------------------------------------------------------------
# make_request
# ---------------------------------------------------------------------------

def _request_row():
    return {
        "codigo_a": "EF01MA01", "ano_a": "1", "habilidade_a": "contar",
        "codigo_b": "EF02MA01", "ano_b": "2", "habilidade_b": "somar",
    }


class TestMakeRequest:
    def test_top_level_shape(self):
        req = make_request(_request_row(), "S", "u", "haiku", 0.5)
        assert set(req) == {"custom_id", "params"}
        assert req["custom_id"] == "EF01MA01__EF02MA01"

    def test_params_have_required_keys(self):
        req = make_request(_request_row(), "S", "u", "haiku", 0.5)
        params = req["params"]
        assert params["model"] == "haiku"
        assert params["temperature"] == 0.5
        assert params["max_tokens"] == 512
        assert "system" in params and "messages" in params

    def test_system_is_cached_content_block(self):
        # Locks in o fix de prompt caching: se alguém reverter para
        # system=string_nua, este teste falha.
        req = make_request(_request_row(), "SYSTEM PROMPT", "u", "haiku", 0)
        sys_blocks = req["params"]["system"]
        assert isinstance(sys_blocks, list) and len(sys_blocks) == 1
        block = sys_blocks[0]
        assert block["type"] == "text"
        assert block["text"] == "SYSTEM PROMPT"
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_user_template_is_filled(self):
        req = make_request(_request_row(), "S", "{{CODIGO_A}} -> {{CODIGO_B}}", "h", 0)
        assert req["params"]["messages"] == [
            {"role": "user", "content": "EF01MA01 -> EF02MA01"}
        ]

    def test_context_note_prepended(self):
        req = make_request(_request_row(), "S", "{{CODIGO_A}}", "h", 0,
                           context_note="[contexto]")
        content = req["params"]["messages"][0]["content"]
        assert content == "[contexto]\n\nEF01MA01"

    def test_no_context_note_no_prepend(self):
        req = make_request(_request_row(), "S", "{{CODIGO_A}}", "h", 0)
        assert req["params"]["messages"][0]["content"] == "EF01MA01"


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------

class TestLoadPrompt:
    def test_extracts_two_fenced_blocks(self, tmp_path, monkeypatch):
        prompt_file = tmp_path / "p.md"
        prompt_file.write_text(
            "# título\n\n## SYSTEM\n\n```\nconteúdo do system\n```\n\n"
            "## USER\n\n```\nconteúdo do user\n```\n"
        )
        monkeypatch.setattr(batch_utils, "PROMPT_FILE", prompt_file)
        sys_block, user_block = load_prompt()
        assert sys_block == "conteúdo do system"
        assert user_block == "conteúdo do user"

    def test_raises_when_only_one_block(self, tmp_path, monkeypatch):
        prompt_file = tmp_path / "p.md"
        prompt_file.write_text("# título\n\n```\nsó um bloco\n```\n")
        monkeypatch.setattr(batch_utils, "PROMPT_FILE", prompt_file)
        with pytest.raises(ValueError, match="SYSTEM e USER"):
            load_prompt()

    def test_raises_when_no_blocks(self, tmp_path, monkeypatch):
        prompt_file = tmp_path / "p.md"
        prompt_file.write_text("# título sem fences\n")
        monkeypatch.setattr(batch_utils, "PROMPT_FILE", prompt_file)
        with pytest.raises(ValueError):
            load_prompt()


# ---------------------------------------------------------------------------
# load_state / save_state
# ---------------------------------------------------------------------------

class TestLoadState:
    def test_missing_file_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(batch_utils, "STATE_FILE", tmp_path / "absent.json")
        assert load_state() == {"batches": []}

    def test_valid_state_loads(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        payload = {"batches": [{"pass": 1, "batch_id": "abc", "status": "in_progress"}]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(batch_utils, "STATE_FILE", path)
        assert load_state() == payload

    def test_list_instead_of_dict_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        path.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(batch_utils, "STATE_FILE", path)
        with pytest.raises(ValueError, match="corrompido"):
            load_state()

    def test_missing_batches_key_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        monkeypatch.setattr(batch_utils, "STATE_FILE", path)
        with pytest.raises(ValueError, match="corrompido"):
            load_state()

    def test_batches_not_list_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"batches": "not a list"}), encoding="utf-8")
        monkeypatch.setattr(batch_utils, "STATE_FILE", path)
        with pytest.raises(ValueError, match="corrompido"):
            load_state()


# ---------------------------------------------------------------------------
# parse_sym_response
# ---------------------------------------------------------------------------

class TestParseSymResponse:
    def test_valid(self):
        text = '{"reasoning": "X.", "label_ab": "PROVAVELMENTE_SIM", "label_ba": "DEFINITIVAMENTE_NÃO"}'
        r = parse_sym_response(text)
        assert r["label_ab"] == "PROVAVELMENTE_SIM"
        assert r["label_ba"] == "DEFINITIVAMENTE_NÃO"
        assert r["score_ab"] == 0.75
        assert r["score_ba"] == 0.0

    def test_all_label_combinations_produce_correct_scores(self):
        for lab_ab, s_ab in LABEL_TO_SCORE.items():
            for lab_ba, s_ba in LABEL_TO_SCORE.items():
                text = f'{{"reasoning": "x", "label_ab": "{lab_ab}", "label_ba": "{lab_ba}"}}'
                r = parse_sym_response(text)
                assert r["score_ab"] == s_ab
                assert r["score_ba"] == s_ba

    def test_both_low_is_valid(self):
        text = '{"reasoning": "x", "label_ab": "DEFINITIVAMENTE_NÃO", "label_ba": "DEFINITIVAMENTE_NÃO"}'
        r = parse_sym_response(text)
        assert r["score_ab"] == 0.0 and r["score_ba"] == 0.0

    def test_sum_above_one_still_returns(self, capsys):
        # Modelo violou a restrição — retorna mesmo assim, mas emite aviso
        text = '{"reasoning": "x", "label_ab": "DEFINITIVAMENTE_SIM", "label_ba": "DEFINITIVAMENTE_SIM"}'
        r = parse_sym_response(text)
        assert r is not None
        assert "⚠️" in capsys.readouterr().out

    def test_missing_label_ab(self):
        assert parse_sym_response('{"reasoning": "x", "label_ba": "INCERTO"}') is None

    def test_missing_label_ba(self):
        assert parse_sym_response('{"reasoning": "x", "label_ab": "INCERTO"}') is None

    def test_missing_reasoning(self):
        assert parse_sym_response('{"label_ab": "INCERTO", "label_ba": "INCERTO"}') is None

    def test_invalid_label_ab(self):
        assert parse_sym_response('{"reasoning": "x", "label_ab": "TALVEZ", "label_ba": "INCERTO"}') is None

    def test_invalid_label_ba(self):
        assert parse_sym_response('{"reasoning": "x", "label_ab": "INCERTO", "label_ba": "TALVEZ"}') is None

    def test_no_json(self):
        assert parse_sym_response("sem JSON") is None

    def test_json_buried_in_prose(self):
        text = 'Análise:\n{"reasoning": "ok", "label_ab": "INCERTO", "label_ba": "PROVAVELMENTE_NÃO"}\nFim.'
        r = parse_sym_response(text)
        assert r["score_ab"] == 0.5 and r["score_ba"] == 0.25

    def test_last_valid_json_wins(self):
        # Modelo auto-corrigiu — o segundo bloco é o veredito
        text = (
            '{"reasoning": "rascunho", "label_ab": "INCERTO", "label_ba": "INCERTO"}'
            ' texto '
            '{"reasoning": "final", "label_ab": "DEFINITIVAMENTE_SIM", "label_ba": "DEFINITIVAMENTE_NÃO"}'
        )
        r = parse_sym_response(text)
        assert r["label_ab"] == "DEFINITIVAMENTE_SIM"
        assert r["label_ba"] == "DEFINITIVAMENTE_NÃO"


# ---------------------------------------------------------------------------
# sym_custom_id e make_request_sym
# ---------------------------------------------------------------------------

def _sym_row():
    return {
        "codigo_a": "EF01MA01", "ano_a": "1", "habilidade_a": "contar",
        "codigo_b": "EF01MA02", "ano_b": "1", "habilidade_b": "somar",
    }


class TestSymCustomId:
    def test_smaller_code_first(self):
        row = {"codigo_a": "EF01MA02", "codigo_b": "EF01MA01"}
        cid = sym_custom_id(row)
        assert cid.startswith("EF01MA01__EF01MA02")

    def test_already_canonical_unchanged(self):
        row = {"codigo_a": "EF01MA01", "codigo_b": "EF01MA02"}
        assert sym_custom_id(row) == "EF01MA01__EF01MA02__sym"

    def test_sym_suffix_present(self):
        assert sym_custom_id(_sym_row()).endswith("__sym")

    def test_no_extra_underscores(self):
        cid = sym_custom_id(_sym_row())
        # Exatamente dois separadores "__" (entre codigos e antes de "sym")
        assert cid.count("__") == 2


class TestMakeRequestSym:
    def test_custom_id_has_sym_suffix(self):
        req = make_request_sym(_sym_row(), "S", "u", "sonnet", 0)
        assert req["custom_id"].endswith("__sym")

    def test_custom_id_is_canonical(self):
        row = {**_sym_row(), "codigo_a": "EF01MA02", "codigo_b": "EF01MA01"}
        req = make_request_sym(row, "S", "u", "sonnet", 0)
        assert req["custom_id"] == "EF01MA01__EF01MA02__sym"

    def test_params_shape_matches_make_request(self):
        req = make_request_sym(_sym_row(), "S", "{{CODIGO_A}}", "sonnet", 0)
        params = req["params"]
        assert params["model"] == "sonnet"
        assert params["temperature"] == 0
        assert "system" in params and "messages" in params

    def test_system_is_cached(self):
        req = make_request_sym(_sym_row(), "SYS", "u", "sonnet", 0)
        block = req["params"]["system"][0]
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_user_template_filled(self):
        req = make_request_sym(_sym_row(), "S", "{{CODIGO_A}}->{{CODIGO_B}}", "sonnet", 0)
        content = req["params"]["messages"][0]["content"]
        assert "EF01MA01" in content and "EF01MA02" in content


# ---------------------------------------------------------------------------
# load_prompt com path customizado (PROMPT_FILE_SYM)
# ---------------------------------------------------------------------------

class TestLoadPromptSym:
    def test_sym_prompt_has_two_blocks(self):
        system, user = load_prompt(PROMPT_FILE_SYM)
        assert len(system) > 0
        assert len(user) > 0

    def test_sym_user_has_all_placeholders(self):
        _, user = load_prompt(PROMPT_FILE_SYM)
        for ph in ["{{ANO_A}}", "{{CODIGO_A}}", "{{HABILIDADE_A}}",
                   "{{ANO_B}}", "{{CODIGO_B}}", "{{HABILIDADE_B}}"]:
            assert ph in user

    def test_sym_system_mentions_label_ab_and_label_ba(self):
        system, _ = load_prompt(PROMPT_FILE_SYM)
        assert "label_ab" in system and "label_ba" in system

    def test_sym_system_mentions_constraint(self):
        system, _ = load_prompt(PROMPT_FILE_SYM)
        assert "≤ 1" in system or "<= 1" in system or "≤ 1.0" in system


class TestSaveState:
    def test_round_trip(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        monkeypatch.setattr(batch_utils, "STATE_FILE", path)
        original = {
            "batches": [
                {"pass": 1, "batch_id": "msgbatch_abc", "status": "in_progress",
                 "submitted_at": "2026-04-26T00:00:00+00:00", "n_requests": 100,
                 "results_file": None},
            ]
        }
        save_state(original)
        assert load_state() == original

    def test_preserves_unicode(self, tmp_path, monkeypatch):
        # ensure_ascii=False no save_state — verifica que acentos sobrevivem.
        path = tmp_path / "state.json"
        monkeypatch.setattr(batch_utils, "STATE_FILE", path)
        save_state({"batches": [], "nota": "habilidade com não e ç"})
        # Mesmo com a chave extra "nota", a leitura crua preserva o conteúdo
        raw = path.read_text(encoding="utf-8")
        assert "não" in raw and "ç" in raw


# ---------------------------------------------------------------------------
# resolve_pair
# ---------------------------------------------------------------------------

class TestResolvePair:
    def _call(self, **overrides):
        defaults = dict(
            raw_ab=0.5,
            raw_ba=0.3,
            same_year=True,
            cid_ab="A__B",
            current_source="pass1",
            sym_results={},
        )
        defaults.update(overrides)
        return resolve_pair(**defaults)

    def test_source_missing_when_raw_ab_none(self):
        score, source, flag = self._call(raw_ab=None)
        assert score is None
        assert flag == "source_missing"
        assert source == "pass1"  # preservado

    def test_no_symmetric_possible_when_different_years(self):
        score, source, flag = self._call(same_year=False, raw_ab=0.7)
        assert score == 0.7
        assert flag == "no_symmetric_possible"
        assert source == "pass1"

    def test_symmetric_pair_missing_when_raw_ba_none(self):
        score, source, flag = self._call(raw_ba=None, raw_ab=0.6)
        assert score == 0.6
        assert flag == "symmetric_pair_missing"
        assert source == "pass1"

    def test_consistent_when_sum_le_one(self):
        score, source, flag = self._call(raw_ab=0.4, raw_ba=0.5)
        assert score == 0.4
        assert flag == "symmetric_consistent"
        assert source == "pass1"

    def test_consistent_at_exact_one(self):
        score, _, flag = self._call(raw_ab=0.5, raw_ba=0.5)
        assert flag == "symmetric_consistent"
        assert score == 0.5

    def test_sym_scored_when_inconsistent_and_sym_available(self):
        sym = {"A__B": {"score": 0.2, "source": "sym"}}
        score, source, flag = self._call(raw_ab=0.75, raw_ba=0.5, sym_results=sym)
        assert score == 0.2
        assert source == "sym"
        assert flag == "sym_scored"

    def test_corrected_when_inconsistent_and_no_sym(self):
        # raw_ab=0.75, raw_ba=0.5 → soma 1.25 (inconsistente)
        # corrigido = (0.75 + (1 - 0.5)) / 2 = 0.625
        score, source, flag = self._call(raw_ab=0.75, raw_ba=0.5)
        assert score == 0.625
        assert flag == "symmetric_corrected"
        assert source == "pass1"

    def test_corrected_pair_sums_to_one(self):
        # Aplicando a fórmula nas duas direções, score(A→B) + score(B→A) = 1.0
        ab, _, _ = self._call(raw_ab=0.75, raw_ba=0.5)
        ba, _, _ = self._call(raw_ab=0.5, raw_ba=0.75)
        assert ab + ba == pytest.approx(1.0)

    def test_sym_overrides_corrected_only_when_inconsistent(self):
        # Mesmo com sym disponível, par consistente NÃO é tocado.
        sym = {"A__B": {"score": 0.99, "source": "sym"}}
        score, source, flag = self._call(raw_ab=0.4, raw_ba=0.5, sym_results=sym)
        assert flag == "symmetric_consistent"
        assert score == 0.4
        assert source == "pass1"
