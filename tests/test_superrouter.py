#!/usr/bin/env python3
"""
The test suite. Every test here guards a bug that actually happened.

    python3 -m unittest discover tests -v
    python3 -m unittest tests.test_superrouter.RefusalsAreNotWrongAnswers -v

**Why `unittest` and not pytest.** pytest is a dependency, and this project's
whole install story is that there isn't one. `unittest` ships with Python.

**Why these tests and not coverage.** Six bugs reached this project between
2026-08-19 and 2026-08-23, and not one of them was a case a coverage target
would have caught — the code was reachable, ran, and returned a plausible
number. Every one was the instrument scoring its own failure against the model
it measured, or comparing two things that were not comparable. So each class
below is named for the rule it defends, and the docstring says which real
failure it exists to stop coming back.

Nothing here calls a model or spends money. Anything needing the network is
skipped rather than mocked into a shape that could drift from the real one.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from superrouter import anthropic_api, providers  # noqa: E402
from superrouter.evals import read_verdict as verdict  # noqa: E402
from superrouter.evals import wilson


class VerdictParsing(unittest.TestCase):
    """A model's answer must be read the way a person would read it.

    The parser has to survive prose, markdown, and a bare word, and it must
    return None rather than guessing when there is no verdict in the text.
    """

    def test_bare_words(self):
        self.assertIs(verdict("TRUE"), True)
        self.assertIs(verdict("FALSE"), False)
        self.assertIs(verdict("  true  "), True)

    def test_leading_markdown_and_punctuation(self):
        self.assertIs(verdict("**FALSE**"), False)
        self.assertIs(verdict("`TRUE`"), True)
        self.assertIs(verdict("# TRUE"), True)

    def test_answer_with_reasoning_attached(self):
        self.assertIs(verdict("TRUE\n\nThe claim is a direct copy of the source"), True)

    def test_no_verdict_is_none_not_a_guess(self):
        # Guessing here is how an unanswerable case became a wrong answer.
        self.assertIsNone(verdict(""))
        self.assertIsNone(verdict("I am not sure about this one"))
        self.assertIsNone(verdict("TRUE or FALSE"))     # both present, ambiguous


class IntervalsAreHonest(unittest.TestCase):
    """A rate without an interval is an opinion with a decimal point."""

    def test_small_samples_are_wide(self):
        lo, hi = wilson(6, 6)
        self.assertLess(lo, 70, "6/6 must not read as near-certainty")
        self.assertEqual(hi, 100)

    def test_large_samples_are_tight(self):
        lo, hi = wilson(90, 100)
        self.assertGreater(lo, 80)
        self.assertLess(hi, 96)

    def test_zero_denominator_does_not_explode(self):
        self.assertEqual(wilson(0, 0), (0, 0))


class ProvidersReachMoreThanOpenRouter(unittest.TestCase):
    """Until 2026-08-23 three files named openrouter.ai directly, so anyone on
    Azure, Bedrock or a self-hosted vLLM could measure nothing at all."""

    def setUp(self):
        self.cfg = {
            "providers": {
                "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                               "api_key_env": ["OPENROUTER_API_KEY"]},
                "azure": {"base_url": "https://acme.openai.azure.com/openai/v1",
                          "api_key_env": ["T_AZ1", "T_AZ2"]},
            },
            "models": {
                "azure/gpt-4o": {"provider": "azure", "model_id": "dep-gpt4o",
                                 "in_per_m": 2.5, "out_per_m": 10.0,
                                 "max_tokens": 16384, "context": 128000},
            },
        }
        os.environ["T_AZ1"], os.environ["T_AZ2"] = "k1", "k2"
        providers._ROTATION.clear()

    def test_declared_model_goes_to_its_own_provider(self):
        r = providers.resolve("azure/gpt-4o", self.cfg)
        self.assertEqual(r["provider"], "azure")
        self.assertTrue(r["url"].startswith("https://acme.openai.azure.com"))
        self.assertEqual(r["wire"], "dep-gpt4o",
                         "the deployment name, not the routing alias, goes on the wire")

    def test_unknown_model_still_falls_through_to_openrouter(self):
        # Nothing already running may change when this file is introduced.
        r = providers.resolve("anthropic/claude-sonnet-5", self.cfg)
        self.assertEqual(r["provider"], "openrouter")

    def test_keys_rotate(self):
        got = [providers.resolve("azure/gpt-4o", self.cfg)["key"] for _ in range(4)]
        self.assertEqual(got, ["k1", "k2", "k1", "k2"])

    def test_max_tokens_is_clamped_to_the_model_ceiling(self):
        capped, note = providers.clamp("azure/gpt-4o", 99999, self.cfg)
        self.assertEqual(capped, 16384)
        self.assertIn("clamped", note, "an adjustment made silently is a lie")

    def test_undeclared_ceiling_is_unknown_not_unlimited(self):
        capped, note = providers.clamp("anthropic/claude-sonnet-5", 99999, self.cfg)
        self.assertEqual(capped, 99999)
        self.assertIsNone(note)

    def test_oversized_prompt_is_caught_before_it_is_sent(self):
        ok, why = providers.fits("azure/gpt-4o", 4 * 200_000, self.cfg)
        self.assertFalse(ok)
        self.assertIn("window", why)


class AnthropicTranslation(unittest.TestCase):
    """Claude Code speaks a different dialect, and two of its fields cost money
    if they are quietly dropped."""

    def test_system_prompt_becomes_a_system_message(self):
        o, _ = anthropic_api.to_openai(
            {"model": "m", "max_tokens": 10, "system": "Be terse.",
             "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(o["messages"][0]["role"], "system")
        self.assertEqual(o["messages"][0]["content"], "Be terse.")

    def test_prompt_caching_is_forwarded_not_stripped(self):
        # Stripping it turned a cached prompt into an uncached bill while the
        # response still reported success.
        o, notes = anthropic_api.to_openai(
            {"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": [
                {"type": "text", "text": "big", "cache_control": {"type": "ephemeral"}}]}]})
        self.assertIn("cache_control", json.dumps(o["messages"]))
        self.assertNotIn("cache_control", notes)

    def test_unhonoured_fields_are_named_rather_than_hidden(self):
        _, notes = anthropic_api.to_openai(
            {"model": "m", "max_tokens": 10, "thinking": {"type": "enabled"},
             "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn("thinking", notes,
                      "accepting a field and ignoring it silently is worse than refusing it")

    def test_images_survive_the_round_trip(self):
        o, _ = anthropic_api.to_openai(
            {"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png", "data": "AAA"}}]}]})
        self.assertIn("image_url", json.dumps(o["messages"]))

    def test_stop_reasons_map_to_anthropic_vocabulary(self):
        for openai_reason, expected in (("stop", "end_turn"),
                                        ("length", "max_tokens"),
                                        ("tool_calls", "tool_use")):
            out = anthropic_api.from_openai(
                {"choices": [{"message": {"content": "x"}, "finish_reason": openai_reason}],
                 "usage": {}}, "m")
            self.assertEqual(out["stop_reason"], expected)

    def test_stream_translator_emits_a_complete_event_sequence(self):
        # A client written against Anthropic's stream hangs if the frames do not
        # arrive in order, so this is a state machine and not a field rename.
        t = anthropic_api.StreamTranslator("m")
        body = t.feed('data: {"choices":[{"delta":{"content":"he"}}]}')
        body += t.feed('data: {"choices":[{"delta":{"content":"llo"},'
                       '"finish_reason":"stop"}]}')
        body += t.finish()
        text = body.decode()
        for event in ("message_start", "content_block_start", "content_block_delta",
                      "content_block_stop", "message_delta", "message_stop"):
            self.assertIn(event, text, f"{event} missing from the stream")
        self.assertLess(text.index("content_block_start"), text.index("content_block_delta"))
        self.assertLess(text.index("content_block_stop"), text.index("message_stop"))


class CascadeEscalation(unittest.TestCase):
    """A cascade's saving is arithmetic on its escalation rate. What has to be
    right is *which* queries it escalates, and that the ladder is monotonic."""

    def test_no_answer_escalates_at_every_level_above_zero(self):
        from superrouter.serve import doubtful
        self.assertFalse(doubtful("", 0), "level 0 never escalates")
        for lvl in (1, 3, 5):
            self.assertTrue(doubtful("", lvl))

    def test_a_clean_answer_is_only_escalated_at_the_top(self):
        from superrouter.serve import doubtful
        self.assertFalse(doubtful("TRUE", 1))
        self.assertFalse(doubtful("TRUE", 3))
        self.assertTrue(doubtful("TRUE", 5), "level 5 escalates everything by definition")

    def test_ladder_is_monotonic(self):
        # Each level must escalate a superset of the level below, or the levels
        # stop being a left-to-right series on the deferral curve.
        from superrouter.serve import doubtful
        answers = ["", "TRUE", "I am not sure", "possibly FALSE", "  "]
        counts = [sum(doubtful(a, lvl) for a in answers) for lvl in (0, 1, 3, 5)]
        self.assertEqual(counts, sorted(counts), f"not monotonic: {counts}")

    def test_double_payment_is_charged(self):
        from superrouter.cascade import evaluate
        cheap = {"a": {"id": "a", "correct": False}, "b": {"id": "b", "correct": True}}
        ref = {"a": {"id": "a", "correct": True}, "b": {"id": "b", "correct": True}}
        res = evaluate(cheap, ref, ["a", "b"], lambda r: not r["correct"], 1.0, 10.0)
        # both cases pay the cheap tier; the escalated one also pays the reference
        self.assertEqual(res["cost"], 2 * 1.0 + 1 * 10.0)


class AToolCallIsAnAnswer(unittest.TestCase):
    """The fifth instance of one class: the instrument scoring its own inability
    to read an answer against the model that gave one.

    OpenAI returns `content: null` for a tool call and puts the decision in
    `tool_calls`. Every reader here took `content`, so an agent request looked
    like a model that had said nothing — and "said nothing" is the strongest
    escalation signal there is."""

    def test_a_tool_call_is_not_an_empty_answer(self):
        from superrouter.serve import answer_of
        msg = {"content": None, "tool_calls": [
            {"function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]}
        self.assertTrue(answer_of(msg).strip(), "a tool call must read as an answer")
        self.assertIn("get_weather", answer_of(msg))

    def test_tool_calls_do_not_escalate_every_request(self):
        from superrouter.serve import answer_of, doubtful
        msg = {"content": None, "tool_calls": [
            {"function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]}
        for lvl in (1, 3):
            self.assertFalse(doubtful(answer_of(msg), lvl),
                             "a tool call escalating at every level means a 100% "
                             "escalation rate on agent traffic, with both tiers paid")

    def test_same_tool_and_arguments_is_the_same_decision(self):
        from superrouter.serve import answer_of, same_decision
        mk = lambda n, a: {"content": None,
                           "tool_calls": [{"function": {"name": n, "arguments": a}}]}
        same = same_decision(answer_of(mk("f", '{"x":1}')), answer_of(mk("f", '{"x":1}')))
        diff = same_decision(answer_of(mk("f", '{"x":1}')), answer_of(mk("g", '{}')))
        self.assertTrue(same, "identical tool calls must not read as drift")
        self.assertFalse(diff, "different tools must read as disagreement")

    def test_a_genuinely_empty_answer_still_escalates(self):
        from superrouter.serve import answer_of, doubtful
        self.assertTrue(doubtful(answer_of({"content": None}), 1))
        self.assertTrue(doubtful(answer_of({}), 1))

    def test_content_parts_are_flattened(self):
        from superrouter.serve import answer_of
        self.assertEqual(answer_of({"content": [{"type": "text", "text": "TRUE"}]}), "TRUE")


class PerQueryRoutingIsOurOwn(unittest.TestCase):
    """Standard library only, and it must be able to say "do not route"."""

    def _corpus(self):
        from superrouter.perquery import PerQueryRouter
        rows, queries = [], {}
        # Two clearly separable families of query. `cheap` is right on family A
        # and wrong on family B; `dear` is right on both and costs 100x.
        for i in range(40):
            fam = "login screen password field" if i % 2 else "chart axis label tick"
            queries[i] = {"embedding_id": i, "query": f"[f{i}] {fam} number {i}"}
            for m, ok, c in (("cheap", i % 2 == 1, 0.0001), ("dear", True, 0.01)):
                rows.append({"embedding_id": i, "model_name": m, "correct": ok,
                             "cost_usd": c, "query": queries[i]["query"]})
        return PerQueryRouter(k=5).fit(rows, queries), rows, queries

    def test_it_needs_no_third_party_import(self):
        """Allowlist, not denylist — the same lesson as the corpus filter.

        A list of banned package names only bans the ones somebody remembered.
        This asserts every import resolves to the standard library or to this
        package, so a dependency added later fails here rather than shipping.
        """
        import ast
        import sys
        import superrouter.perquery as pq
        tree = ast.parse(open(pq.__file__).read())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
        allowed = set(sys.stdlib_module_names)
        outside = sorted(roots - allowed)
        self.assertEqual(outside, [],
                         f"{outside} is outside the standard library and breaks "
                         f"the promise that this installs with nothing")

    def test_a_low_bar_prefers_the_cheap_model(self):
        r, _, _ = self._corpus()
        m, _, cleared = r.route("[f1] login screen password field number 1", bar=0.5)
        self.assertEqual(m, "cheap")
        self.assertTrue(cleared)

    def test_a_bar_nothing_clears_falls_back_to_the_most_accurate(self):
        r, _, _ = self._corpus()
        m, _, cleared = r.route("chart axis label tick number 2", bar=0.999)
        self.assertFalse(cleared, "nothing should clear a bar of 0.999 here")
        self.assertEqual(m, "dear", "the fallback must be the most accurate, not the cheapest")

    def test_the_frame_id_separates_identical_sentences(self):
        # The bug this whole project found the hard way: the same assertion
        # asked of two screens has two different answers.
        from superrouter.perquery import cosine, features
        a = features("[frame-a] The heading is legible.")
        b = features("[frame-b] The heading is legible.")
        self.assertLess(cosine(a, b), 0.999, "two frames must not be the same point")

    def test_it_refuses_to_route_when_a_fixed_choice_dominates(self):
        from superrouter.perquery import dominated
        fixed = {"accuracy": 96, "cost": 0.00039}
        routed = {"accuracy": 96, "cost": 0.01127}
        self.assertTrue(dominated(routed, fixed),
                        "matching accuracy at 29x the price is not routing, it is waste")
        self.assertFalse(dominated({"accuracy": 98, "cost": 0.02},
                                   {"accuracy": 100, "cost": 0.09}),
                         "cheaper at a stated accuracy cost is a real trade, not domination")


class ColdStartIsAPathNobodyHereWalks(unittest.TestCase):
    """The first command a new user runs must not be a traceback.

    The exams ship; their screenshots deliberately do not, because one person's
    product proves nothing about yours. That combination made the README's own
    getting-started line crash with a bare FileNotFoundError — found by cloning
    the published repo and running it as a stranger."""

    def test_missing_frames_are_counted_not_opened(self):
        from superrouter.evals import frames_missing
        cases = [{"frame": "definitely-not-a-real-frame", "answer": True}]
        # Must return a count rather than raising — the caller turns it into
        # an instruction, and an exception here is the bug being guarded.
        self.assertEqual(frames_missing("qa-vision-assert", cases), 1)

    def test_a_text_task_has_no_frames_to_miss(self):
        from superrouter.evals import frames_missing
        self.assertEqual(frames_missing("text-faithful", [{"assert": "x"}]), 0)


class TheInstallPathIsCheckable(unittest.TestCase):
    """An agent driving the install needs states it can branch on, and a
    default that cannot quietly charge somebody."""

    def test_doctor_reports_a_state_and_a_fix_for_every_check(self):
        from superrouter.doctor import run_all
        for c in run_all():
            self.assertIn(c["state"], ("ok", "missing", "broken", "optional"), c)
            self.assertTrue(c["name"])
            if c["state"] in ("missing", "broken"):
                self.assertTrue(c["fix"], f"{c['name']} fails with no fix to offer")

    def test_ffmpeg_is_checked_by_behaviour_not_presence(self):
        """The worst bug this project had was a tool that was installed and
        silently not doing its job. Presence is not the check."""
        import inspect
        from superrouter import doctor
        src = inspect.getsource(doctor.check_ffmpeg)
        self.assertIn("YAVG", src, "must verify the filter actually reports, "
                                   "not merely that the binary exists")

    def test_quickstart_does_not_spend_by_default(self):
        """Behavioural, not textual. An earlier version of this test read the
        source and matched the *dry-run* invocation — which is free — so it
        failed on correct code. What matters is which commands actually run."""
        import sys
        from unittest import mock
        from superrouter import quickstart

        ran = []

        def fake_step(name, cmd, cwd=None, capture=True):
            ran.append(cmd)
            return True, '{"ok": true, "checks": [], "blocking": []}'

        argv = ["quickstart", "--origin", "https://example.invalid", "--name", "t"]
        with mock.patch.object(quickstart, "step", fake_step), \
                mock.patch.object(sys, "argv", argv):
            quickstart.main()

        spent = [c for c in ran if "--model" in c]
        self.assertEqual(spent, [], f"ran a scoring command without --spend: {spent}")
        self.assertTrue(any("--dry-run" in c for c in ran),
                        "the free run must still price what scoring would cost")


class DeferralCurveMaths(unittest.TestCase):
    """The oracle is the ceiling and random is the line to beat. If those two
    are wrong, every claim about routing judgement is measured against nothing."""

    def test_oracle_spends_only_on_what_the_cheap_tier_got_wrong(self):
        from superrouter.deferral import curve
        ids = [f"c{i}" for i in range(10)]
        cheap = {i: {"correct": n >= 5} for n, i in enumerate(ids)}   # 5 wrong
        ref = {i: {"correct": True} for i in ids}
        rows, base, ceiling = curve(cheap, ref, ids, points=11)
        self.assertAlmostEqual(base, 0.5)
        self.assertAlmostEqual(ceiling, 1.0)
        at_half = next(r for r in rows if abs(r["rate"] - 0.5) < 1e-9)
        self.assertAlmostEqual(at_half["oracle"], 1.0,
                               msg="escalating exactly the wrong half must reach the ceiling")

    def test_random_sits_below_the_oracle_in_the_middle(self):
        from superrouter.deferral import curve
        ids = [f"c{i}" for i in range(20)]
        cheap = {i: {"correct": n >= 10} for n, i in enumerate(ids)}
        ref = {i: {"correct": True} for i in ids}
        rows, _, _ = curve(cheap, ref, ids, points=11)
        mid = rows[5]
        self.assertGreater(mid["oracle"], mid["random"],
                           "if random matched the oracle there would be nothing to route")

    def test_both_ends_of_the_curve_agree(self):
        from superrouter.deferral import curve
        ids = [f"c{i}" for i in range(8)]
        cheap = {i: {"correct": n % 2 == 0} for n, i in enumerate(ids)}
        ref = {i: {"correct": True} for i in ids}
        rows, base, ceiling = curve(cheap, ref, ids, points=11)
        self.assertAlmostEqual(rows[0]["random"], base, places=6)
        self.assertAlmostEqual(rows[-1]["random"], ceiling, places=6)
        self.assertAlmostEqual(rows[-1]["oracle"], ceiling, places=6)


class AuditRefusesToPassOnNothing(unittest.TestCase):
    """Run cold on an empty repo the audit reported "0 failing checks", which a
    reader takes as verified. A check with nothing to check has not passed."""

    def test_exam_mixing_is_detected(self):
        from superrouter.audit import check_exams_not_mixed
        rows = [
            ("/x/runs/a.json", {"summary": {"model": "m1", "cases": 100,
                                            "exam_fingerprint": "aaa"}}),
            ("/x/runs/b.json", {"summary": {"model": "m2", "cases": 100,
                                            "exam_fingerprint": "bbb"}}),
        ]
        self.assertTrue(check_exams_not_mixed(rows),
                        "two exams in one directory must be reported")

    def test_one_exam_is_clean(self):
        from superrouter.audit import check_exams_not_mixed
        rows = [
            ("/x/runs/a.json", {"summary": {"model": "m1", "cases": 100,
                                            "exam_fingerprint": "aaa"}}),
            ("/x/runs/b.json", {"summary": {"model": "m2", "cases": 100,
                                            "exam_fingerprint": "aaa"}}),
        ]
        self.assertFalse(check_exams_not_mixed(rows))

    def test_a_scored_error_is_reported(self):
        from superrouter.audit import check_failures_not_scored
        rows = [("/x/runs/a.json", {
            "summary": {"model": "m", "cases": 50, "reached": 49,
                        "refusals": 1, "refusal_pct": 2},
            "results": [{"id": "1", "error": "timeout", "said": True}],
        })]
        self.assertTrue(check_failures_not_scored(rows),
                        "a request that never arrived must not carry a verdict")


class ExamIdentity(unittest.TestCase):
    """Comparing runs from two versions of a set ranks the exams, not the
    models — and it fails silently with a confident-looking answer."""

    def test_fingerprint_changes_when_an_answer_changes(self):
        from superrouter.evals import fingerprint
        a = [{"id": "1", "answer": True, "corruption": None, "variant": "x", "defect": None}]
        b = [{"id": "1", "answer": False, "corruption": None, "variant": "x", "defect": None}]
        self.assertNotEqual(fingerprint(a), fingerprint(b))

    def test_fingerprint_is_order_independent(self):
        from superrouter.evals import fingerprint
        a = [{"id": "1", "answer": True, "corruption": None, "variant": None, "defect": None},
             {"id": "2", "answer": False, "corruption": None, "variant": None, "defect": None}]
        self.assertEqual(fingerprint(a), fingerprint(list(reversed(a))))


class ProvidersFileIsOptional(unittest.TestCase):
    """Introducing providers.json must not change a machine that has none."""

    def test_absent_file_gives_the_built_in_two(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["SUPERROUTER_PROVIDERS"] = os.path.join(d, "nope.json")
            cfg = providers.load()
            self.assertIn("openrouter", cfg["providers"])
            self.assertIn("local", cfg["providers"])
            self.assertEqual(cfg["models"], {})
        os.environ.pop("SUPERROUTER_PROVIDERS", None)

    def test_malformed_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "providers.json")
            with open(p, "w") as f:
                f.write("{not json")
            os.environ["SUPERROUTER_PROVIDERS"] = p
            with self.assertRaises(SystemExit):
                providers.load()
        os.environ.pop("SUPERROUTER_PROVIDERS", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
