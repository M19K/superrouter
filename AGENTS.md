# Installing and running SuperRouter — the contract

**This file is for an agent doing the work, not for a person reading about it.**
The README explains what SuperRouter is and why. This says what to run, in what
order, what each step needs beforehand, and what a failure at each step means.

If you are a person: `python3 -m superrouter.quickstart --origin https://your.site --name yours`
does all of it, and stops before spending anything.

---

## What you are installing

A measuring instrument and a proxy. It photographs a product you name, breaks
copies of those screenshots in ways it chose, asks candidate models about both,
and produces a table of which cheap models are good enough **for that product**.
Then it serves that table as an OpenAI-compatible proxy.

**The measurement is per-product and does not transfer.** Measured across two
products on the same day, one model caught 52% of planted defects on the first
and 85% on the second. Do not reuse another product's table. Do not present a
number measured elsewhere as applying here.

---

## Before anything

```bash
python3 -m superrouter.doctor --json
```

Exit `0` means every **required** check passed. Exit `1` means at least one did
not, and `blocking` names them. Every check carries a `fix` string; use it
rather than guessing.

| Check | Required | If it fails |
|---|---|---|
| `python` | yes | Needs 3.9+. Nothing else installs — there are no dependencies. |
| `core modules` | yes | Report it. The core is standard library only; a failure here is a bug in the repo, not in the machine. |
| `ffmpeg` | yes | Not a Python package. Used to decide whether a planted defect is actually visible. **State `broken` matters more than `missing`** — it means ffmpeg runs but prints no `signalstats` metadata, and the defect gate silently passes everything. Do not proceed. |
| `agent-browser` | yes | Not a Python package. Drives the browser that photographs the product. |
| `provider key` | no | Needed only to **score**. Building an exam costs nothing and needs no key. |
| `local models` | no | Optional. A local vision model can be scored alongside hosted ones for free. |
| `an exam to score` | no | Expected to be `missing` on a fresh clone — manifests ship, screenshots do not. |

---

## The sequence

### 1 · Build an exam from the product — free, about ten minutes

```bash
python3 golden/qa-vision/build_generic.py --origin https://your.site --name yours
```

`--origin` repeats. A product is more than one screen, and an exam built on
whichever screen you happened to pass measures a model on a fraction of it.

**Preconditions:** `ffmpeg` and `agent-browser` both `ok`. The URL must serve a
page **you** control.

**What can go wrong, and what it means:**

- *"REFUSING to write this exam: only N planted defects"* — the page is too
  sparse to carry enough defect classes. **This refusal is correct.** An exam
  with no real faults scores every model near 100% and measures nothing. Give it
  a richer page, or more `--origin` screens.
- *"asked for X while capturing Y, and the browser is at Z"* — something else is
  driving the same browser session. Set `AGENT_BROWSER_SESSION` to a unique name.
- *"page exposed no roles"* — the page rendered nothing the builder could find.
  Check the URL loads without a login.

**Do not point this at a site you do not own.** It takes screenshots and mutates
the DOM.

### 2 · Price the run before spending — free

```bash
python3 -m superrouter.evals --set yours --dry-run
```

Prints an estimate per candidate model and spends nothing. **Show this to the
human before step 3.** Scoring calls real models on their account.

### 3 · Score the ladder — this costs money

```bash
python3 -m superrouter.evals --set yours --model <a> --model <b> --model <c>
```

**Requires a provider key.** Three to seven candidates spanning the price range
is the useful shape. Include a free local model if `doctor` found one — it costs
nothing and it is often the model a QA lane would otherwise use unmeasured.

Each model reports accuracy, catch rate, false-alarm rate and cost. **Read all
three rates.** A model with a perfect catch rate and a high false-alarm rate
produces a report nobody trusts, which is the same as no report.

### 4 · Turn measurement into a decision — free

```bash
python3 -m superrouter.route_table
```

### 5 · Serve it

```bash
python3 -m superrouter.serve --shadow 20
```

Point the client at `http://localhost:8787/v1` and ask for model
`superrouter/auto`. `--shadow 20` sends one call in twenty to the reference
model as well, so drift surfaces while it is happening.

---

## All of it, in one command

```bash
python3 -m superrouter.quickstart --origin https://your.site --name yours --json
```

Runs steps 1, 2 and stops. Add `--spend` to continue through 3 and 4.

The `--json` result is `{ok, product, steps[], estimate, ...}`. Each step
carries `ok`, `detail` and `fix`. **`stopped: "before spending"` is a success,
not a failure** — it means everything free completed and the next step needs a
human's decision about money.

---

## Rules for an agent working in this repository

1. **Never present a number you did not just measure.** Every published figure
   here is re-derived from run records by `python3 -m superrouter.audit`. If you
   change a number in a document, the audit must still pass.
2. **A finding is not a blocker; an unexamined finding is.** This applies to
   defect reports and to your own output.
3. **Do not add a dependency.** The core is standard library only and a test
   fails if that stops being true. Optional modules must degrade with a message,
   never a traceback.
4. **`--dry-run` before anything that spends.** Every path that costs money has
   a free path that prices it first. Use it, and show the human.
5. **Say which model judged a result.** A passing check from an unmeasured model
   is weak evidence and must be reported as weak.
