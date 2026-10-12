"""Synthetic-only tests for the local translation-quality evaluation journal."""

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import translation_evaluation as evaluation


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id():
    return uuid.uuid4().hex


def _case(group_id, conditions, *, missing_condition=None):
    case_id = _id()
    candidates = []
    for condition in conditions:
        missing = condition == missing_condition
        candidates.append({
            "blind_candidate_id": _id(),
            "condition_id": condition,
            "candidate_sha256": None if missing else _sha("candidate:" + case_id + condition),
            "proposal_sha256": None if missing else _sha("proposal:" + case_id + condition),
        })
    return {
        "case_id": case_id,
        "group_id": group_id,
        "context_sha256": _sha("context:" + case_id),
        "source_review_status": "accepted",
        "source_review_event_id": _id(),
        "source_text_sha256": _sha("source:" + case_id),
        "crop_sha256": _sha("crop:" + case_id),
        "srt_sha256": _sha("srt:" + case_id),
        "candidates": candidates,
    }


def _manifest(*, dataset_kind="synthetic", split_leak=False, alias_leak=False,
              missing_condition=False, coverage_floor=0.75):
    conditions = [_id(), _id()]
    source_id = _id()
    episode_a, episode_b, episode_dev = _id(), _id(), _id()
    group_a = evaluation.canonical_group_id(source_id, episode_a)
    group_b = evaluation.canonical_group_id(source_id, episode_b)
    group_dev = evaluation.canonical_group_id(source_id, episode_dev)
    groups = [
        {"group_id": group_a, "source_id": source_id, "episode_id": episode_a, "split": "held_out"},
        {"group_id": group_b, "source_id": source_id, "episode_id": episode_b, "split": "held_out"},
        {"group_id": group_dev, "source_id": source_id, "episode_id": episode_dev, "split": "development"},
    ]
    if split_leak:
        groups[1] = {**groups[0], "split": "development"}
    if alias_leak:
        groups[1] = {**groups[1], "episode_id": episode_a}
    cases = [
        _case(group_a, conditions, missing_condition=conditions[0] if missing_condition else None),
        _case(group_b, conditions),
        _case(group_dev, conditions),
    ]
    return {
        "schema_version": evaluation.SCHEMA_VERSION,
        "evaluation_id": _id(),
        "dataset_kind": dataset_kind,
        "source_language": "ja",
        "target_language": "tr",
        "style_profile_sha256": _sha("neutral style profile v1"),
        "glossary_sha256": _sha("synthetic glossary v1"),
        "rubric_version": evaluation.RUBRIC["version"],
        "rubric_sha256": evaluation.RUBRIC_SHA256,
        "primary_metric_version": evaluation.PRIMARY_METRIC_VERSION,
        "split_policy": "source_episode_group_v1",
        "group_identity_basis": ("synthetic_fixture_ids_v1" if dataset_kind == "synthetic"
                                  else "caller_verified_source_episode_ids_v1"),
        "coverage_floor": {"minimum_rated_fraction_per_held_out_group": coverage_floor},
        "groups": groups,
        "cases": cases,
    }


def _source_states(manifest):
    result = {}
    for case in manifest["cases"]:
        for candidate in case["candidates"]:
            result[(case["case_id"], candidate["blind_candidate_id"])] = {
                "active": True,
                "context_sha256": case["context_sha256"],
                "source_review_status": case["source_review_status"],
                "source_review_event_id": case["source_review_event_id"],
                "source_text_sha256": case["source_text_sha256"],
                "crop_sha256": case["crop_sha256"],
                "srt_sha256": case["srt_sha256"],
                "candidate_sha256": candidate["candidate_sha256"],
                "proposal_sha256": candidate["proposal_sha256"],
            }
    return result


def _resolver(states, *, missing=None, overrides=None):
    missing = missing or set()
    overrides = overrides or {}

    def resolve(case_id, candidate_id):
        key = (case_id, candidate_id)
        if case_id in missing:
            return None
        return overrides.get(key, states.get(key))
    return resolve


class TranslationEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "synthetic-evaluation"
        self.manifest = _manifest()
        evaluation.create_evaluation(self.root, self.manifest)
        self.sources = _source_states(self.manifest)
        self.resolver = _resolver(self.sources)
        self.case = self.manifest["cases"][0]
        self.candidate = self.case["candidates"][0]
        self.condition_a = self.candidate["condition_id"]
        self.state = self.sources[(self.case["case_id"], self.candidate["blind_candidate_id"])]

    def tearDown(self):
        self.temp.cleanup()

    def record(self, *, status="rated", dimensions=None, issues=None, outcome="accepted",
               reviewer="reviewer-a", reason=None, supersedes=None, resolver=None,
               candidate=None):
        candidate = candidate or self.candidate
        return evaluation.record_rating(
            self.root, candidate["blind_candidate_id"], reviewer, status,
            dimensions or {key: 2 for key in evaluation.DIMENSIONS},
            [] if issues is None else issues, outcome,
            source_resolver=resolver or self.resolver,
            exclusion_reason=reason, supersedes_event_id=supersedes,
        )

    def test_manifest_and_rubric_are_sealed_before_ratings(self):
        manifest_path = self.root / "translation-evaluation-manifest-v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["rubric_version"] = "changed-after-freeze"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.aggregate_report(self.root, self.resolver)

    def test_split_leakage_is_rejected_at_freeze(self):
        leaking = _manifest(split_leak=True)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.create_evaluation(Path(self.temp.name) / "leaking", leaking)

    def test_same_source_episode_cannot_be_aliased_into_a_second_group(self):
        leaking = _manifest(alias_leak=True)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.create_evaluation(Path(self.temp.name) / "aliased", leaking)

    def test_coverage_floor_is_frozen_and_required(self):
        weak = _manifest()
        weak.pop("coverage_floor")
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.create_evaluation(Path(self.temp.name) / "no-floor", weak)
        out_of_range = _manifest(coverage_floor=1.1)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.create_evaluation(Path(self.temp.name) / "bad-floor", out_of_range)

    def test_condition_ids_must_be_opaque_and_no_mapping_is_stored(self):
        unsafe = _manifest()
        unsafe["cases"][0]["candidates"][0]["condition_id"] = "provider-gpt"
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.create_evaluation(Path(self.temp.name) / "named-condition", unsafe)

    def test_manifest_rejects_paths_and_subtitle_text_fields(self):
        unsafe = _manifest()
        unsafe["cases"][0]["source_text"] = "synthetic source cue"
        unsafe["cases"][0]["pack_path"] = r"C:\Users\example\Videos\episode.review-pack"
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.create_evaluation(Path(self.temp.name) / "unsafe", unsafe)

    def test_rating_requires_current_source_and_candidate_hashes(self):
        stale_source = dict(self.state, crop_sha256=_sha("changed crop"))
        stale_resolver = _resolver(self.sources, overrides={
            (self.case["case_id"], self.candidate["blind_candidate_id"]): stale_source,
        })
        with self.assertRaises(evaluation.EvaluationError):
            self.record(resolver=stale_resolver)
        stale_candidate = dict(self.state, candidate_sha256=_sha("changed target"))
        candidate_resolver = _resolver(self.sources, overrides={
            (self.case["case_id"], self.candidate["blind_candidate_id"]): stale_candidate,
        })
        with self.assertRaises(evaluation.EvaluationError):
            self.record(resolver=candidate_resolver)

    def test_browser_mapping_is_not_accepted_as_source_resolver(self):
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.aggregate_report(self.root, self.state)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.record_rating(
                self.root, self.candidate["blind_candidate_id"], "reviewer-a", "rated",
                {key: 2 for key in evaluation.DIMENSIONS}, [], "accepted",
                source_resolver=self.state,
            )

    def test_current_server_resolver_allows_rating_and_is_called_with_opaque_ids(self):
        observed = []

        def resolver(case_id, candidate_id):
            observed.append((case_id, candidate_id))
            return self.sources[(case_id, candidate_id)]

        event = self.record(resolver=resolver)
        self.assertEqual(event["status"], "rated")
        self.assertEqual(observed, [(self.case["case_id"], self.candidate["blind_candidate_id"])])

    def test_report_excludes_a_stale_candidate_and_reports_the_reason(self):
        self.record()
        altered = dict(self.state, proposal_sha256=_sha("changed proposal event"))
        resolver = _resolver(self.sources, overrides={
            (self.case["case_id"], self.candidate["blind_candidate_id"]): altered,
        })
        report = evaluation.aggregate_report(self.root, resolver)
        coverage = report["conditions"][self.condition_a]["coverage"]["held_out"]
        self.assertEqual(coverage["stale_candidate"], 1)
        self.assertEqual(coverage["excluded"], 1)
        self.assertEqual(coverage["rated"], 0)

    def test_missing_source_is_unavailable_and_cannot_receive_scores(self):
        missing_resolver = _resolver(self.sources, missing={self.case["case_id"]})
        with self.assertRaises(evaluation.EvaluationError):
            self.record(resolver=missing_resolver)
        self.record(status="unevaluable", dimensions={key: None for key in evaluation.DIMENSIONS},
                    issues=[], outcome=None, reason="source_unavailable", resolver=missing_resolver)
        report = evaluation.aggregate_report(self.root, missing_resolver)
        coverage = report["conditions"][self.condition_a]["coverage"]["held_out"]
        self.assertEqual(coverage["source_unavailable"], 1)
        self.assertEqual(coverage["excluded"], 1)
        self.assertEqual(report["conditions"][self.condition_a]["exclusion_reasons"]["held_out"]["source_unavailable"], 1)
        self.assertEqual(coverage["rated"], 0)

    def test_duplicate_rating_requires_append_only_superseding_event(self):
        first = self.record()
        with self.assertRaises(evaluation.EvaluationError):
            self.record(dimensions={key: 1 for key in evaluation.DIMENSIONS})
        corrected = self.record(dimensions={key: 1 for key in evaluation.DIMENSIONS},
                                outcome="edited", supersedes=first["event_id"])
        log = (self.root / "translation-evaluation-events.jsonl").read_text(encoding="utf-8")
        self.assertIn(first["event_id"], log)
        self.assertIn(corrected["event_id"], log)
        self.assertEqual(log.count("\n"), 3)  # seal + original rating + correction

    def test_condition_mapping_reveal_is_terminal_for_later_corrections(self):
        mapping_sha = _sha("private condition mapping bytes")
        with self.assertRaisesRegex(evaluation.EvaluationError, "only after all blind ratings"):
            evaluation.reveal_condition_mapping(self.root, "reviewer-a", self.condition_a, mapping_sha)
        for case in self.manifest["cases"]:
            for candidate in case["candidates"]:
                self.record(candidate=candidate)
        active = evaluation._active_ratings(evaluation._load(self.root)[3])
        reveal = evaluation.reveal_condition_mapping(
            self.root, "reviewer-a", self.condition_a, mapping_sha)
        self.assertEqual(reveal["event_type"], "condition_mapping_revealed")
        events = evaluation._load(self.root)[3]
        self.assertEqual(events[-1]["event_id"], reveal["event_id"])
        self.assertNotIn("private_label", events[-1])
        prior = active[(self.candidate["blind_candidate_id"], "reviewer-a")]
        with self.assertRaisesRegex(evaluation.EvaluationError, "terminally closed"):
            self.record(candidate=self.candidate, supersedes=prior["event_id"])
        self.assertEqual(len(evaluation._load(self.root)[3]), len(events))
        self.assertFalse(evaluation.aggregate_report(self.root, self.resolver)["phase6_eligible"])

    def test_hash_chain_tamper_fails_closed(self):
        self.record()
        path = self.root / "translation-evaluation-events.jsonl"
        rows = path.read_text(encoding="utf-8").splitlines()
        event = json.loads(rows[-1])
        event["dimensions"]["semantic_adequacy"] = 0
        rows[-1] = json.dumps(event, ensure_ascii=False)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.aggregate_report(self.root, self.resolver)

    def test_unresolved_independent_rater_disagreement_is_excluded(self):
        self.record(reviewer="reviewer-a")
        self.record(reviewer="reviewer-b", dimensions={key: 1 for key in evaluation.DIMENSIONS}, outcome="edited")
        report = evaluation.aggregate_report(self.root, self.resolver)
        coverage = report["conditions"][self.condition_a]["coverage"]["held_out"]
        self.assertEqual(coverage["disputed"], 1)
        self.assertEqual(coverage["excluded"], 1)
        self.assertEqual(coverage["rated"], 0)

    def test_missing_output_is_counted_but_never_scored(self):
        missing_manifest = _manifest(missing_condition=True)
        root = Path(self.temp.name) / "missing-output"
        evaluation.create_evaluation(root, missing_manifest)
        report = evaluation.aggregate_report(root, _resolver(_source_states(missing_manifest)))
        condition = missing_manifest["cases"][0]["candidates"][0]["condition_id"]
        coverage = report["conditions"][condition]["coverage"]["held_out"]
        self.assertEqual(coverage["missing_output"], 1)
        self.assertEqual(coverage["excluded"], 1)
        self.assertEqual(coverage["rated"], 0)

    def test_unevaluable_ratings_have_no_dimension_scores(self):
        self.record(status="unevaluable", dimensions={key: None for key in evaluation.DIMENSIONS},
                    issues=[], outcome=None, reason="context_missing")
        report = evaluation.aggregate_report(self.root, self.resolver)
        coverage = report["conditions"][self.condition_a]["coverage"]["held_out"]
        self.assertEqual(coverage["unevaluable"], 1)
        self.assertEqual(coverage["excluded"], 1)
        self.assertEqual(coverage["rated"], 0)
        self.assertEqual(
            report["conditions"][self.condition_a]["exclusion_reasons"]["held_out"]["unevaluable:context_missing"], 1)

    def test_exclusion_reason_counts_are_reason_incidence_not_unique_items(self):
        self.record(status="unevaluable", dimensions={key: None for key in evaluation.DIMENSIONS},
                    issues=[], outcome=None, reason="context_missing", reviewer="reviewer-a")
        self.record(status="unevaluable", dimensions={key: None for key in evaluation.DIMENSIONS},
                    issues=[], outcome=None, reason="language_uncertain", reviewer="reviewer-b")
        report = evaluation.aggregate_report(self.root, self.resolver)
        condition = report["conditions"][self.condition_a]
        coverage = condition["coverage"]["held_out"]
        reasons = condition["exclusion_reasons"]["held_out"]
        self.assertEqual(condition["exclusion_reason_semantics"], "reason_incidence_unique_per_candidate")
        self.assertEqual(coverage["unevaluable"], 1)
        self.assertEqual(coverage["excluded"], 1)
        self.assertEqual(reasons["unevaluable:context_missing"], 1)
        self.assertEqual(reasons["unevaluable:language_uncertain"], 1)
        unevaluable_incidence = sum(value for reason, value in reasons.items()
                                    if reason.startswith("unevaluable:"))
        self.assertEqual(unevaluable_incidence, 2)
        self.assertGreater(sum(reasons.values()), coverage["excluded"])

    def test_coverage_floor_blocks_held_out_metric_and_exclusions_stay_visible(self):
        manifest = _manifest(dataset_kind="human_held_out", coverage_floor=0.75)
        root = Path(self.temp.name) / "coverage-gated"
        evaluation.create_evaluation(root, manifest)
        sources = _source_states(manifest)
        case, candidate = manifest["cases"][0], manifest["cases"][0]["candidates"][0]
        evaluation.record_rating(
            root, candidate["blind_candidate_id"], "reviewer-a", "rated",
            {key: 2 for key in evaluation.DIMENSIONS}, [], "accepted",
            source_resolver=_resolver(sources),
        )
        report = evaluation.aggregate_report(root, _resolver(sources))
        condition = candidate["condition_id"]
        arm = report["conditions"][condition]
        self.assertFalse(arm["coverage_gate"]["passes"])
        self.assertEqual(arm["coverage_gate"]["groups_below_floor"], 1)
        self.assertIsNone(arm["held_out_metric"])
        self.assertEqual(arm["coverage"]["held_out"]["expected"], 2)
        self.assertEqual(arm["exclusion_reasons"]["held_out"]["pending_rating"], 1)

    def test_synthetic_report_is_redacted_and_has_no_quality_result(self):
        self.record(dimensions={
            "semantic_adequacy": 2, "omissions": 0, "additions": 0,
            "terminology": 2, "style_register": 2,
        })
        report = evaluation.aggregate_report(self.root, self.resolver)
        arm = report["conditions"][self.condition_a]
        self.assertEqual(arm["metric_status"], "SYNTHETIC_FIXTURE_ONLY")
        self.assertIsNone(arm["held_out_metric"])
        self.assertIsNone(arm["rubric_dimensions"])
        self.assertFalse(report["phase6_eligible"])
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("verified synthetic source text", serialized)
        self.assertNotIn("synthetic target candidate", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(self.case["case_id"], serialized)
        for group in self.manifest["groups"]:
            self.assertNotIn(group["source_id"], serialized)
            self.assertNotIn(group["episode_id"], serialized)
        self.assertTrue(all(len(condition_id) == 32 for condition_id in report["conditions"]))


if __name__ == "__main__":
    unittest.main()
