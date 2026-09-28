#!/usr/bin/env python3
"""irreversible-reach: which model-produced values can reach an irreversible action?

Agent frameworks let you declare tools and let a model choose between them.
None of them produce an artefact saying WHICH MODEL OUTPUTS CAN CAUSE WHICH
SIDE EFFECTS. This computes that, from a manifest you write, and fails CI when
a model-produced value reaches an irreversible action with nothing deterministic
or human in the path.

    irreversible-reach check reach.yaml     exit 1 on a violation
    irreversible-reach map   reach.yaml     print the reachability map

THE BUG THIS EXISTS FOR, which is a real one and not an illustration.

A spec said, in capitals, that the model could not escalate. It was false, and
it read as true to the person who wrote it, because the model emitted only a
LABEL. The label then selected the action: one value meant escalate to the
signatory, another meant stop chasing entirely. The model wrote no action and
caused one.

A reviewer found it. No test found it, and no test could have, because every
test asserted on what the model EMITTED and the defect was in what the emission
REACHED. A guarantee about an output is not a guarantee about an effect, and a
label is an action with one level of indirection.
"""

import argparse
import json
import os
import re
import sys

try:
    import yaml
except ImportError:                                        # pragma: no cover
    yaml = None


if yaml is not None:
    class _Loader(yaml.SafeLoader):
        """SafeLoader with YAML 1.1's extra booleans removed.

        THIS EXISTS BECAUSE THE DOCUMENTED SYNTAX DID NOT WORK. A gate is
        written `- {on: escalate, kind: human}`, exactly as the README says,
        and YAML 1.1 resolves the bare key `on` to boolean True. The mapping
        arrives as {True: 'escalate', 'kind': 'human'}, the validator finds no
        'on', and it refuses with a message blaming the user for a file that
        matches the documentation.

        Every test in the suite passed throughout, because every test builds a
        Python dict and none of them went through YAML. THE SUITE COVERED THE
        CODE AND NOT THE DOCUMENTED INTERFACE, which is the only interface a
        user has.

        `on`, `off`, `yes`, `no`, `y` and `n` are now plain strings. `true` and
        `false` still resolve to booleans, because `reversible: true` has to
        keep working and that is YAML 1.2 behaviour anyway.
        """

    # ADDING A RESOLVER DOES NOT REPLACE ONE. The first version of this fix
    # called add_implicit_resolver and changed nothing, because PyYAML appends
    # to the inherited table and the 1.1 bool resolver stayed registered under
    # o, O, y, Y, n and N. The old entries are stripped first, by hand.
    _Loader.yaml_implicit_resolvers = {
        first: [(tag, rx) for tag, rx in resolvers
                if tag != "tag:yaml.org,2002:bool"]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _Loader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"))

PRODUCERS = ("model", "deterministic", "human")
GATE_KINDS = ("human", "deterministic")


class ManifestError(ValueError):
    """The manifest cannot be read, which is a refusal and never a pass.

    A malformed manifest that returned "no violations" would be indistinguishable
    from a safe system. Every load failure raises.
    """


def load(path):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if path.endswith((".yaml", ".yml")):
        if yaml is None:
            raise ManifestError(
                "this manifest is YAML and PyYAML is not installed. Install it, "
                "or write the manifest as JSON. Guessing is not an option here: "
                "a half-parsed manifest reports fewer paths than exist.")
        doc = yaml.load(raw, Loader=_Loader)
    else:
        doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ManifestError(f"{path} is not a mapping")
    return validate(doc, path)


def validate(doc, path="<manifest>"):
    """Refuse anything ambiguous. A default here is a lie with a shrug.

    THERE IS NO DEFAULT FOR `reversible` AND THERE NEVER WILL BE. Defaulting it
    to true understates every irreversible reach in the file, which is the failure
    this tool exists to catch. Defaulting it to false trains people to write
    `reversible: true` without thinking, which is the same failure wearing a
    seatbelt.
    """
    actions = doc.get("actions") or {}
    sources = doc.get("sources") or {}
    flows = doc.get("flows") or []
    gates = doc.get("gates") or []

    if not actions:
        raise ManifestError(f"{path}: no actions declared, so nothing can be "
                            f"checked and a clean result would be meaningless")
    if not sources:
        raise ManifestError(f"{path}: no sources declared")

    for name, a in actions.items():
        if not isinstance(a, dict) or "reversible" not in a:
            raise ManifestError(
                f"{path}: action {name!r} does not say whether it is "
                f"reversible. There is no default. Undoing an email to a "
                f"client's finance director is not the same as retrying a "
                f"webhook, and only you know which this is")
        if not isinstance(a["reversible"], bool):
            raise ManifestError(f"{path}: action {name!r} reversible must be "
                                f"true or false, got {a['reversible']!r}")

    for name, s in sources.items():
        if not isinstance(s, dict) or "produced_by" not in s:
            raise ManifestError(f"{path}: source {name!r} has no produced_by")
        if s["produced_by"] not in PRODUCERS:
            raise ManifestError(
                f"{path}: source {name!r} produced_by is {s['produced_by']!r}, "
                f"expected one of {list(PRODUCERS)}")

    known = set(actions) | set(sources)
    for i, f in enumerate(flows):
        if not isinstance(f, dict) or "from" not in f or "to" not in f:
            raise ManifestError(f"{path}: flow {i} needs 'from' and 'to'")
        for end in ("from", "to"):
            if f[end] not in known:
                raise ManifestError(
                    f"{path}: flow {i} {end}={f[end]!r} names nothing declared. "
                    f"A typo here silently removes a path from the graph, which "
                    f"is a false clean result")

    for i, g in enumerate(gates):
        if not isinstance(g, dict) or "on" not in g or "kind" not in g:
            raise ManifestError(f"{path}: gate {i} needs 'on' and 'kind'")
        if g["on"] not in known:
            raise ManifestError(f"{path}: gate {i} on={g['on']!r} names nothing "
                                f"declared")
        if g["kind"] not in GATE_KINDS:
            raise ManifestError(f"{path}: gate {i} kind is {g['kind']!r}, "
                                f"expected one of {list(GATE_KINDS)}")
    return doc


def paths_to_actions(doc):
    """Every path from a source to an action, as a list of node names.

    Depth first, cycles cut. An agent graph that loops is normal and a loop
    carries no new reachability, so revisiting a node on the same path adds
    nothing and would not terminate.
    """
    actions, sources = doc["actions"], doc["sources"]
    adj = {}
    for f in doc.get("flows", []):
        adj.setdefault(f["from"], []).append(f["to"])

    found = []

    def walk(node, path):
        if node in actions and len(path) > 1:
            found.append(list(path))
            # Do not stop. An action can flow onward into another action, and
            # the second one may be the irreversible one.
        for nxt in adj.get(node, []):
            if nxt in path:
                continue
            walk(nxt, path + [nxt])

    for s in sources:
        walk(s, [s])
    return found


def gated(doc, path):
    """Is anything on this path a gate, other than the source itself?

    A GATE ON THE SOURCE IS NOT A GATE ON THE PATH. Confirming the label a model
    produced, and then letting that label drive an irreversible action, is the
    same defect with a click in front of it. The gate has to sit on or before
    the action, downstream of the model value.
    """
    gates = {g["on"]: g["kind"] for g in doc.get("gates", [])}
    return [(n, gates[n]) for n in path[1:] if n in gates]


def check(doc):
    """Violations: a model-produced value reaching an irreversible action ungated."""
    actions, sources = doc["actions"], doc["sources"]
    violations = []
    for path in paths_to_actions(doc):
        src, dst = path[0], path[-1]
        if sources[src]["produced_by"] != "model":
            continue
        if actions[dst]["reversible"]:
            continue
        if gated(doc, path):
            continue
        violations.append(path)
    return violations


def render_map(doc):
    lines = []
    for path in sorted(paths_to_actions(doc)):
        src, dst = path[0], path[-1]
        produced = doc["sources"][src]["produced_by"]
        rev = "reversible" if doc["actions"][dst]["reversible"] else \
            "IRREVERSIBLE"
        g = gated(doc, path)
        gtxt = f"  gated by {', '.join(f'{n} ({k})' for n, k in g)}" if g else ""
        lines.append(f"  [{produced:13}] {' -> '.join(path)}  [{rev}]{gtxt}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["check", "map"])
    ap.add_argument("manifest")
    args = ap.parse_args(argv)

    if not os.path.exists(args.manifest):
        print(f"FAIL. {args.manifest} does not exist, so nothing was checked.")
        return 2
    try:
        doc = load(args.manifest)
    except (ManifestError, json.JSONDecodeError) as e:
        print(f"FAIL. {e}")
        return 2

    if args.command == "map":
        print(render_map(doc) or "  no paths from any source to any action")
        return 0

    violations = check(doc)
    if not violations:
        print(f"ok. no model-produced value reaches an irreversible action "
              f"ungated, across {len(paths_to_actions(doc))} path(s).")
        print()
        print("  NOT CHECKED HERE: whether the manifest describes the system "
              "you actually shipped.")
        print("  A manifest is a claim. This checks the claim is coherent, "
              "never that it is true.")
        return 0

    print(f"FAIL. {len(violations)} model-produced value(s) reach an "
          f"irreversible action with nothing in the way:")
    print()
    for p in violations:
        print(f"  {' -> '.join(p)}")
        print(f"      {p[0]} is produced by the model, {p[-1]} is irreversible")
    print()
    print("  Each one is fixed by ONE of three things, and they are not equal:")
    print("    a deterministic gate on the action, which is best when the")
    print("      evidence can be computed rather than inferred")
    print("    a human gate on the action, which is right when it cannot")
    print("    making the source deterministic, which is the real fix and is")
    print("      usually cheaper than it looks")
    return 1


if __name__ == "__main__":
    sys.exit(main())
