"""Observed changeover behaviour per unit.

Sequence-dependent transitions are the part of this problem that resists a clean
model: a unit cannot go from any feed to any other feed. Rather than guess the
allowed set, measure which transitions the planner actually uses, and whether the
unit goes down in between - a transition that only ever happens across an idle gap
is a different constraint from one that happens back to back.

Writes data/reports/transition-analysis.md.
"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.engine import Reference, Scenario  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

UNITS = ["MEK", "HYDRO", "EXTRACT", "ROSE", "PLATFORMER"]


def campaigns(scn, lines, dates):
    """[(product_key, start_index, length)] with idle gaps skipped but recorded."""
    seq = []
    for d in dates:
        on = sorted(l["code"] for l in lines if scn.charge_bbl(l["key"], d) > 0)
        seq.append("+".join(on) if on else None)
    out = []
    i = 0
    while i < len(seq):
        if seq[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(seq) and seq[j + 1] == seq[i]:
            j += 1
        out.append((seq[i], i, j - i + 1))
        i = j + 1
    return out, seq


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    dates = scn.dates
    lines_by_unit = defaultdict(list)
    for line in ref.charge_lines:
        lines_by_unit[line["unit"]].append(line)

    L = []

    def w(s=""):
        L.append(s)

    w("# Observed changeovers\n")
    w("Measured over {} days of the planner's own schedule. `gap` is the number of "
      "idle days between one campaign ending and the next starting.\n"
      .format(len(dates)))

    for unit in UNITS:
        lines = lines_by_unit.get(unit, [])
        if not lines:
            continue
        camps, seq = campaigns(scn, lines, dates)
        if len(camps) < 2:
            continue
        products = sorted({c[0] for c in camps})
        name = {l["code"]: (l["name"] or "")[:26] for l in lines}

        w("## {}\n".format(unit))
        w("Charge options: {}\n".format(", ".join(
            "{} {}".format(p, name.get(p, "")) for p in products)))

        direct = Counter()
        gapped = Counter()
        gap_len = defaultdict(list)
        for a, b in zip(camps, camps[1:]):
            frm, to = a[0], b[0]
            gap = b[1] - (a[1] + a[2])
            if gap == 0:
                direct[(frm, to)] += 1
            else:
                gapped[(frm, to)] += 1
            gap_len[(frm, to)].append(gap)

        seen_pairs = set(direct) | set(gapped)
        w("**Transition matrix** — rows = from, columns = to. "
          "`n` back-to-back / `n*` across an idle gap.\n")
        header = "| from \\ to | " + " | ".join(products) + " |"
        w(header)
        w("|---" * (len(products) + 1) + "|")
        for frm in products:
            cells = []
            for to in products:
                d = direct.get((frm, to), 0)
                g = gapped.get((frm, to), 0)
                if not d and not g:
                    cells.append("·")
                else:
                    parts = []
                    if d:
                        parts.append(str(d))
                    if g:
                        parts.append("{}*".format(g))
                    cells.append(" / ".join(parts))
            w("| **{}** | {} |".format(frm, " | ".join(cells)))
        w("")

        n_pairs = len(products) * (len(products) - 1)
        used = len([p for p in seen_pairs if p[0] != p[1]])
        w("- {} of {} possible product-to-product transitions are ever used "
          "({:.0f}%).".format(used, n_pairs, 100.0 * used / n_pairs if n_pairs else 0))
        never = [(a, b) for a in products for b in products
                 if a != b and (a, b) not in seen_pairs]
        if never:
            w("- **Never observed:** {}".format(
                ", ".join("{}->{}".format(a, b) for a, b in never)))
        only_gapped = [p for p in seen_pairs
                       if p[0] != p[1] and p not in direct and p in gapped]
        if only_gapped:
            w("- **Only ever across an idle gap** (never back to back): {}".format(
                ", ".join("{}->{} (gaps {})".format(
                    a, b, ",".join(str(g) for g in sorted(set(gap_len[(a, b)]))))
                    for a, b in only_gapped)))
        direct_only = [p for p in direct if p[0] != p[1] and p not in gapped]
        if direct_only:
            w("- Always back to back: {}".format(
                ", ".join("{}->{}".format(a, b) for a, b in direct_only)))
        w("")

        lens = defaultdict(list)
        for p, _, ln in camps:
            lens[p].append(ln)
        w("**Campaign length by product**\n")
        w("| Product | Campaigns | Min | Median | Max |")
        w("|---|---:|---:|---:|---:|")
        for p in products:
            v = sorted(lens[p])
            w("| {} {} | {} | {} | {} | {} |".format(
                p, name.get(p, ""), len(v), v[0], v[len(v) // 2], v[-1]))
        w("")

        gaps = [b[1] - (a[1] + a[2]) for a, b in zip(camps, camps[1:])]
        idle = [g for g in gaps if g > 0]
        w("- {} of {} changeovers happen back to back; {} cross an idle gap "
          "(median {} days).\n".format(
              len(gaps) - len(idle), len(gaps), len(idle),
              sorted(idle)[len(idle) // 2] if idle else 0))

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "transition-analysis.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
