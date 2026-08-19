"""Write or check the committed baseline of what the model answers.

Usage:  python scripts/snapshot.py            # check against the committed file
        python scripts/snapshot.py --update   # accept the current answers

`--update` is the whole point of the workflow and also its only danger: it says
"this change was intended". Read the diff it prints before you run it, and put the
reason in the commit message - a baseline nobody argued with is just a record of
whatever the code happened to do.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.baseline import diff, snapshot          # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
PATH = os.path.join(ROOT, "tests", "baseline.json")


def main() -> int:
    if not os.path.exists(os.path.join(SEED, "reference.json")):
        print("no seed data; run scripts/seed.py first")
        return 1

    update = "--update" in sys.argv
    from invplanner.baseline import CASES
    print("solving {} cases...".format(len(CASES)), end="", flush=True)
    new = snapshot(SEED)
    print(" done")

    if os.path.exists(PATH):
        with open(PATH) as fh:
            old = json.load(fh)
        changes = diff(old, new)
        if not changes:
            print("baseline unchanged ({} cases)".format(len(new)))
            return 0
        print("\n{} field(s) changed:".format(len(changes)))
        for c in changes:
            print("  " + c)
        if not update:
            print("\nRun with --update to accept, and say why in the commit.")
            return 1
    elif not update:
        print("no baseline at {}; run with --update to create it".format(PATH))
        return 1

    with open(PATH, "w") as fh:
        json.dump(new, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("\nwrote {}".format(PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
