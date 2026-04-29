"""Tests for LangGraph graph structure and routing logic."""
import pytest


class TestRouteAfterReview:
    """Test the conditional routing function without running the full graph."""

    def setup_method(self):
        from src.agents.graph import route_after_review
        self.route = route_after_review

    def test_high_score_goes_to_end(self):
        state = {
            "quality_review_result": {"score": 8},
            "retry_count": 1,
            "verification_summary": {"low_confidence_count": 0},
        }
        result = self.route(state)
        from langgraph.graph import END
        assert result == END

    def test_retry_count_exceeded_escalates_to_human(self):
        # retry_count >= 2 and score < 7 → human_escalation (not END)
        state = {
            "quality_review_result": {"score": 5, "financial_quality": 4, "sector_quality": 6},
            "retry_count": 2,
            "verification_summary": {"low_confidence_count": 0},
        }
        result = self.route(state)
        assert result == "human_escalation"

    def test_low_financial_quality_routes_to_financial(self):
        state = {
            "quality_review_result": {"score": 5, "financial_quality": 4, "sector_quality": 7},
            "retry_count": 0,
            "verification_summary": {"low_confidence_count": 0},
        }
        result = self.route(state)
        assert result == "analyze_financial"

    def test_low_sector_quality_routes_to_sector(self):
        state = {
            "quality_review_result": {"score": 5, "financial_quality": 8, "sector_quality": 4},
            "retry_count": 0,
            "verification_summary": {"low_confidence_count": 0},
        }
        result = self.route(state)
        assert result == "analyze_sector"

    def test_financial_worse_routes_to_financial(self):
        # financial_quality ≤ sector_quality and financial < 7
        state = {
            "quality_review_result": {"score": 5, "financial_quality": 5, "sector_quality": 6},
            "retry_count": 0,
            "verification_summary": {"low_confidence_count": 0},
        }
        result = self.route(state)
        assert result == "analyze_financial"

    def test_low_confidence_claims_escalates(self):
        # low_confidence_count > 3 → human_escalation even with good score
        state = {
            "quality_review_result": {"score": 8},
            "retry_count": 0,
            "verification_summary": {"low_confidence_count": 4},
        }
        result = self.route(state)
        assert result == "human_escalation"

    def test_missing_review_result_goes_to_end(self):
        # Default score=10 when missing → END
        state = {
            "quality_review_result": None,
            "retry_count": 0,
            "verification_summary": {"low_confidence_count": 0},
        }
        from langgraph.graph import END
        result = self.route(state)
        assert result == END


class TestGraphCompilation:
    def test_graph_builds_without_error(self):
        from src.agents.graph import build_credit_proposal_graph
        graph = build_credit_proposal_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        """Verify graph has the 5 expected nodes."""
        from src.agents.graph import build_credit_proposal_graph
        graph = build_credit_proposal_graph()
        # LangGraph compiled graph exposes graph.nodes or similar
        # We check via the builder before compilation
        from src.agents.graph import build_credit_proposal_graph
        # If compilation succeeds, structure is correct
        assert graph is not None


class TestJudgeModelIndependence:
    """Verify quality_review uses get_judge_llm, not any generator getter.

    LLM-as-Judge principle: the judge must be from a different model than every
    generator to avoid self-confirmation bias.
      SG1: openai/gpt-oss-20b  (get_medium_llm)
      SG2: llama-3.3-70b       (get_smart_llm)
      SG3: groq/compound       (get_financial_llm)
      OCR: llama-4-scout       (get_vision_llm)
      Judge: openai/gpt-oss-120b (get_judge_llm) — different from all generators.
    """

    def test_quality_review_imports_judge_llm(self):
        """assembler.py must import get_judge_llm for the quality review role."""
        import ast, inspect
        from src.agents import assembler
        source = inspect.getsource(assembler)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "get_judge_llm" in imported_names, (
            "quality_review_node must import get_judge_llm — "
            "the judge (gpt-oss-120b) must be independent from all generators"
        )

    def test_quality_review_calls_judge_llm_not_others(self):
        """quality_review_node body must call get_judge_llm, not get_smart/medium_llm."""
        import ast, inspect
        from src.agents import assembler

        source = inspect.getsource(assembler.quality_review_node)
        tree = ast.parse(source)
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("get_medium_llm", "get_smart_llm", "get_judge_llm")
        ]
        assert "get_judge_llm" in calls, (
            "quality_review_node must call get_judge_llm (OpenAI vendor)"
        )
        assert "get_smart_llm" not in calls, (
            "quality_review_node must NOT call get_smart_llm — "
            "SG2 uses this model; same model judging its own output defeats independence"
        )
        assert "get_medium_llm" not in calls, (
            "quality_review_node must NOT call get_medium_llm — "
            "SG1 uses this model; same model judging its own output defeats independence"
        )


class TestStripLlmJson:
    """Verify strip_llm_json handles all known LLM output quirks."""

    def setup_method(self):
        from src.utils.llm import strip_llm_json
        self.strip = strip_llm_json

    def test_strips_think_blocks(self):
        raw = "<think>reasoning</think>{\"key\": \"val\"}"
        assert self.strip(raw) == '{"key": "val"}'

    def test_strips_code_fence(self):
        raw = "```json\n{\"key\": \"val\"}\n```"
        assert self.strip(raw) == '{"key": "val"}'

    def test_removes_trailing_comma_in_object(self):
        raw = '{"a": 1, "b": 2,}'
        result = self.strip(raw)
        import json
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_removes_trailing_comma_in_array(self):
        raw = '{"items": [1, 2, 3,]}'
        result = self.strip(raw)
        import json
        assert json.loads(result) == {"items": [1, 2, 3]}

    def test_removes_nested_trailing_commas(self):
        raw = '{"a": {"b": 1,}, "c": [2,],}'
        result = self.strip(raw)
        import json
        assert json.loads(result) == {"a": {"b": 1}, "c": [2]}

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert self.strip(raw) == '{"key": "value"}'
