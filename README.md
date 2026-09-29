# irreversible-reach

**Which of your agent's model-produced values can reach an irreversible action?**

Static reachability analysis for agent graphs. You declare what your agent can
do and where each value comes from. It tells you what the model can cause.

Your agent framework knows what tools exist. It does not know which of them a
model can cause, through how many hops, with nothing in the way. `irreversible-reach`
computes that from a manifest you write, prints the map, and fails CI when a
model-produced value reaches an irreversible action ungated.

```
$ irreversible-reach check reach.yaml

FAIL. 2 model-produced value(s) reach an irreversible action with nothing in the way:

  invoice_state -> escalate_to_signatory
      invoice_state is produced by the model, escalate_to_signatory is irreversible
  invoice_state -> suppress_chasing
      invoice_state is produced by the model, suppress_chasing is irreversible

  Each one is fixed by ONE of three things, and they are not equal:
    a deterministic gate on the action, which is best when the
      evidence can be computed rather than inferred
    a human gate on the action, which is right when it cannot
    making the source deterministic, which is the real fix and is
      usually cheaper than it looks
```

## The bug this exists for

A spec said, in capitals, that the model could not escalate.

It was false. It read as true to the person who wrote it, because the model
emitted only a **label**. The label then selected the action. One value meant
escalate to the client's finance director. Another meant stop chasing entirely.

**The model wrote no action and caused one.**

A reviewer found it. No test found it, and no test could have, because every
test asserted on what the model **emitted**, and the defect was in what the
emission **reached**.

**A guarantee about an output is not a guarantee about an effect. A label is an
action with one level of indirection.**

## Why existing tools do not catch this

Evaluation and observability tools answer *how did my agent perform on this
dataset* and *what happened in this trace*. Both are worth having and neither
asks this question.

This one is closer to taint analysis than to evals: **before you ship, which
model-controlled values can reach a side effect, and what stands in between.**

## Install

```
pip install "irreversible-reach[yaml] @ git+https://github.com/IvanSFlowGit/irreversible-reach"
```

That puts `irreversible-reach` on your path. Or copy `irreversible_reach.py`
into your repo and run `python3 irreversible_reach.py check reach.yaml`; it needs
PyYAML only if your manifest is YAML.

One file, no framework, no runtime, nothing to instrument. It reads a manifest
and exits.

## The manifest

```yaml
actions:
  send_reminder:
    reversible: true
  escalate_to_signatory:
    reversible: false        # emails the client's finance director
  suppress_chasing:
    reversible: false        # stops all follow-up on this invoice

sources:
  invoice_state:
    produced_by: model       # model | deterministic | human
  payment_lag:
    produced_by: deterministic

flows:
  - {from: invoice_state, to: send_reminder}
  - {from: invoice_state, to: escalate_to_signatory}
  - {from: payment_lag,   to: suppress_chasing}

gates:
  - {on: escalate_to_signatory, kind: human}   # human | deterministic
```

```
$ irreversible-reach map reach.yaml

  [model        ] invoice_state -> escalate_to_signatory  [IRREVERSIBLE]
  [model        ] invoice_state -> send_reminder  [reversible]
  [model        ] invoice_state -> suppress_chasing  [IRREVERSIBLE]
  [deterministic] payment_lag -> suppress_chasing  [IRREVERSIBLE]
```

## Three decisions in it, and each one is deliberate

**`reversible` has no default and never will.** Defaulting it to true
understates every irreversible reach in the file, which is the thing this exists to
catch. Defaulting it to false teaches people to write `reversible: true`
without thinking, which is the same failure wearing a seatbelt. You say, per
action, because only you know whether undoing it is a database rollback or an
apology to somebody's finance director.

**A gate on the source does not count.** Asking a human to confirm the label
the model produced, and then letting that label drive the action, is the
original defect with a click in front of it. A gate has to sit downstream of
the model value.

**A malformed manifest refuses rather than reporting clean.** A flow naming a
node that does not exist is a typo that silently deletes a path from the graph,
and a graph with a missing edge reports fewer violations than exist. Every load
and validation failure exits 2.

## Fixing a violation

Three options, and they are not equal.

1. **Make the source deterministic.** The real fix, and usually cheaper than it
   looks. A bounce is a flag. A payment lag is arithmetic. Check how many of
   your model-produced values actually need a model before you reach for a gate.
2. **A deterministic gate on the action.** Right when the evidence can be
   computed rather than inferred.
3. **A human gate on the action.** Right when it cannot, and the honest answer
   more often than teams like.

**The useful move is usually to arrange the graph so the model only reaches
actions whose worst case is a person reading something.** If your model owns
the two states that both route to a human, a wrong label costs somebody five
minutes. If it owns the state that stops all follow-up, a wrong label loses the
money and nothing tells you.

## What it does not do

**It does not check that the manifest describes the system you shipped.** A
manifest is a claim. This checks that the claim is coherent, never that it is
true. Keep it next to the code, review it in the same pull request, and treat a
manifest change like a schema change.

It does not read your source, instrument your runtime, or talk to a model.

## Status

Early. The graph analysis and the refusals are tested with a control in both
directions for every rule, because a check that passes everything and a
check that is not running look identical.

Issues and manifests from real agents are the most useful thing you can send.

MIT.
