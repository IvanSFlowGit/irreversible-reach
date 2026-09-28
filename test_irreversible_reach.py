#!/usr/bin/env python3
"""Controls in both directions for every rule this tool enforces.

A CHECK THAT PASSES EVERYTHING AND A CHECK THAT IS NOT RUNNING LOOK IDENTICAL,
so every positive case here has a negative twin that differs in ONE field. If a
pair ever stops disagreeing, the rule between them has stopped firing.

    python3 test_irreversible_reach.py
"""

import io
import contextlib
import json
import os
import tempfile
import unittest

import irreversible_reach as br


def m(**over):
    """The defect this tool was built from: a label drives an irreversible action."""
    doc = {
        "actions": {
            "send_reminder": {"reversible": True},
            "escalate_to_signatory": {"reversible": False},
            "suppress_chasing": {"reversible": False},
        },
        "sources": {
            "invoice_state": {"produced_by": "model"},
            "payment_lag": {"produced_by": "deterministic"},
        },
        "flows": [
            {"from": "invoice_state", "to": "send_reminder"},
            {"from": "invoice_state", "to": "escalate_to_signatory"},
            {"from": "payment_lag", "to": "suppress_chasing"},
        ],
        "gates": [],
    }
    doc.update(over)
    return doc


class Reachability(unittest.TestCase):

    def test_model_reaching_irreversible_is_a_violation(self):
        v = br.check(br.validate(m()))
        self.assertEqual([["invoice_state", "escalate_to_signatory"]], v)

    def test_same_graph_with_a_human_gate_is_clean(self):
        doc = m(gates=[{"on": "escalate_to_signatory", "kind": "human"}])
        self.assertEqual([], br.check(br.validate(doc)))

    def test_same_graph_with_a_deterministic_gate_is_clean(self):
        doc = m(gates=[{"on": "escalate_to_signatory", "kind": "deterministic"}])
        self.assertEqual([], br.check(br.validate(doc)))

    def test_making_the_source_deterministic_is_clean(self):
        """The real fix, and the one the tool recommends third because it is
        the one people forget is available."""
        doc = m(sources={"invoice_state": {"produced_by": "deterministic"},
                         "payment_lag": {"produced_by": "deterministic"}})
        self.assertEqual([], br.check(br.validate(doc)))

    def test_model_reaching_a_REVERSIBLE_action_is_clean(self):
        """Isolates reversibility. Same source, same producer, one flag."""
        doc = m(flows=[{"from": "invoice_state", "to": "send_reminder"}])
        self.assertEqual([], br.check(br.validate(doc)))

    def test_deterministic_source_reaching_irreversible_is_clean(self):
        """Isolates the producer. payment_lag -> suppress_chasing is
        irreversible and must NOT be reported."""
        for path in br.check(br.validate(m())):
            self.assertNotEqual("payment_lag", path[0])

    def test_a_gate_on_the_SOURCE_does_not_count(self):
        """THE MOST IMPORTANT CASE IN THIS FILE.

        A human who confirms the label the model produced, and then lets that
        label drive the action, has reproduced the original defect with a click
        in front of it. A gate must sit downstream of the model value, never on
        it.
        """
        doc = m(gates=[{"on": "invoice_state", "kind": "human"}])
        self.assertEqual([["invoice_state", "escalate_to_signatory"]],
                         br.check(br.validate(doc)))

    def test_indirection_through_a_middle_node_is_still_reachable(self):
        """A label that picks a strategy that picks an action is the same
        defect with one more hop, and hops are exactly what hid it."""
        doc = m(
            actions={"strategy": {"reversible": True},
                     "escalate_to_signatory": {"reversible": False}},
            sources={"invoice_state": {"produced_by": "model"}},
            flows=[{"from": "invoice_state", "to": "strategy"},
                   {"from": "strategy", "to": "escalate_to_signatory"}])
        self.assertIn(["invoice_state", "strategy", "escalate_to_signatory"],
                      br.check(br.validate(doc)))

    def test_a_gate_on_the_middle_node_clears_it(self):
        doc = m(
            actions={"strategy": {"reversible": True},
                     "escalate_to_signatory": {"reversible": False}},
            sources={"invoice_state": {"produced_by": "model"}},
            flows=[{"from": "invoice_state", "to": "strategy"},
                   {"from": "strategy", "to": "escalate_to_signatory"}],
            gates=[{"on": "strategy", "kind": "deterministic"}])
        self.assertEqual([], br.check(br.validate(doc)))

    def test_a_cycle_terminates(self):
        doc = m(
            actions={"a": {"reversible": True}, "b": {"reversible": False}},
            sources={"s": {"produced_by": "model"}},
            flows=[{"from": "s", "to": "a"}, {"from": "a", "to": "b"},
                   {"from": "b", "to": "a"}])
        self.assertTrue(br.check(br.validate(doc)))


class Refusals(unittest.TestCase):
    """A manifest that cannot be trusted must REFUSE, never report clean."""

    def bad(self, doc, fragment):
        with self.assertRaises(br.ManifestError) as cm:
            br.validate(doc)
        self.assertIn(fragment, str(cm.exception).lower())

    def test_missing_reversible_refuses(self):
        self.bad(m(actions={"x": {}}), "reversible")

    def test_reversible_must_be_boolean(self):
        self.bad(m(actions={"x": {"reversible": "yes"}}), "true or false")

    def test_unknown_producer_refuses(self):
        self.bad(m(sources={"s": {"produced_by": "vibes"}}), "produced_by")

    def test_flow_naming_nothing_refuses(self):
        """A typo silently deletes a path, which is a false clean result."""
        self.bad(m(flows=[{"from": "invoice_state", "to": "esclate"}]),
                 "names nothing declared")

    def test_gate_naming_nothing_refuses(self):
        self.bad(m(gates=[{"on": "nope", "kind": "human"}]),
                 "names nothing declared")

    def test_unknown_gate_kind_refuses(self):
        self.bad(m(gates=[{"on": "send_reminder", "kind": "probably"}]),
                 "kind is")

    def test_no_actions_refuses_rather_than_passing(self):
        self.bad({"actions": {}, "sources": {"s": {"produced_by": "model"}}},
                 "nothing can be checked")


class DocumentedYamlInterface(unittest.TestCase):
    """THE SUITE COVERED THE CODE AND NOT THE INTERFACE, AND THAT IS HOW THE
    ONLY FUNCTIONAL BUG IN THIS TOOL SURVIVED TWENTY TWO PASSING TESTS.

    Every case above builds a Python dict. The README documents YAML. Nothing
    crossed the bridge, so nobody noticed that YAML 1.1 resolves the bare key
    `on` to boolean True and that every gate written exactly as documented
    arrived as {True: ...}, failed validation, and was refused with a message
    blaming the user.

    These cases are copied from the README rather than written fresh. A test
    that paraphrases the documentation tests the paraphrase.
    """

    def yaml_load(self, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "reach.yaml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return br.load(p)

    def test_the_README_gate_block_verbatim_is_honoured(self):
        doc = self.yaml_load(
            "actions:\n"
            "  send_reminder:\n"
            "    reversible: true\n"
            "  escalate_to_signatory:\n"
            "    reversible: false\n"
            "sources:\n"
            "  invoice_state:\n"
            "    produced_by: model\n"
            "flows:\n"
            "  - {from: invoice_state, to: escalate_to_signatory}\n"
            "gates:\n"
            "  - {on: escalate_to_signatory, kind: human}\n")
        self.assertEqual([{"on": "escalate_to_signatory", "kind": "human"}],
                         doc["gates"])
        self.assertEqual([], br.check(doc))

    def test_without_the_gate_the_same_yaml_is_a_violation(self):
        """The twin. If both were clean the gate block would be doing nothing
        and this file would still look green."""
        doc = self.yaml_load(
            "actions:\n  escalate_to_signatory:\n    reversible: false\n"
            "sources:\n  invoice_state:\n    produced_by: model\n"
            "flows:\n  - {from: invoice_state, to: escalate_to_signatory}\n"
            "gates: []\n")
        self.assertEqual([["invoice_state", "escalate_to_signatory"]],
                         br.check(doc))

    def test_reversible_true_is_still_a_boolean_not_a_string(self):
        """The fix removes YAML 1.1's extra booleans. It must not remove the
        one boolean this format actually depends on."""
        doc = self.yaml_load(
            "actions:\n  a: {reversible: true}\n  b: {reversible: false}\n"
            "sources:\n  s: {produced_by: model}\n")
        self.assertIs(True, doc["actions"]["a"]["reversible"])
        self.assertIs(False, doc["actions"]["b"]["reversible"])

    def test_nodes_named_no_yes_and_off_survive_as_strings(self):
        """Somebody will name an action `no` one day. Under YAML 1.1 it becomes
        False and the graph silently loses a node."""
        doc = self.yaml_load(
            "actions:\n  no: {reversible: false}\n  yes: {reversible: true}\n"
            "sources:\n  off: {produced_by: model}\n"
            "flows:\n  - {from: off, to: no}\n")
        self.assertEqual(["no", "yes"], sorted(doc["actions"]))
        self.assertEqual(["off"], list(doc["sources"]))

    def test_the_shipped_example_file_parses_and_fails_as_documented(self):
        doc = br.load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "examples", "reach.yaml"))
        self.assertEqual(4, len(br.paths_to_actions(doc)))
        self.assertEqual(2, len(br.check(doc)))


class ReadmeTranscripts(unittest.TestCase):
    """THE README SHOWED OUTPUT THIS TOOL HAS NEVER PRODUCED.

    It was hand-written: one violation where the shipped example produces two,
    and the remediation block silently dropped. In a repository whose entire
    thesis is that a check which passes everything and a check that is not
    running look identical, a fabricated transcript is the worst possible
    defect, and it was found by a reviewer rather than by this suite.

    So the transcripts are now asserted against live output. A change to either
    the wording or the example manifest fails here until the README is
    regenerated.
    """

    def readme_block(self, command_line):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        marker = "$ irreversible-reach " + command_line + "\n"
        start = text.index(marker) + len(marker)
        end = text.index("```", start)
        return text[start:end].strip("\n")

    def live(self, *argv):
        here = os.path.dirname(os.path.abspath(__file__))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            br.main([argv[0], os.path.join(here, "examples", "reach.yaml")])
        return buf.getvalue().strip("\n")

    def test_check_transcript_matches_live_output(self):
        self.assertEqual(self.live("check"),
                         self.readme_block("check reach.yaml"))

    def test_map_transcript_matches_live_output(self):
        self.assertEqual(self.live("map"),
                         self.readme_block("map reach.yaml"))


class Cli(unittest.TestCase):

    def run_cli(self, doc, cmd="check"):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "reach.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = br.main([cmd, p])
        return rc, buf.getvalue()

    def test_violation_exits_1_and_prints_the_path(self):
        rc, out = self.run_cli(m())
        self.assertEqual(1, rc)
        self.assertIn("invoice_state -> escalate_to_signatory", out)

    def test_clean_exits_0_and_says_what_it_did_not_check(self):
        rc, out = self.run_cli(
            m(gates=[{"on": "escalate_to_signatory", "kind": "human"}]))
        self.assertEqual(0, rc)
        self.assertIn("NOT CHECKED HERE", out)

    def test_missing_file_exits_2_not_0(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = br.main(["check", "/nonexistent/reach.yaml"])
        self.assertEqual(2, rc)

    def test_broken_manifest_exits_2_not_0(self):
        rc, out = self.run_cli({"actions": {"x": {}}, "sources": {}})
        self.assertEqual(2, rc)
        self.assertIn("FAIL", out)

    def test_map_marks_irreversible_paths(self):
        rc, out = self.run_cli(m(), cmd="map")
        self.assertEqual(0, rc)
        self.assertIn("IRREVERSIBLE", out)
        self.assertIn("[model", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
