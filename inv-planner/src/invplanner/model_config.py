"""How the parity-faithful workbook model is simplified into an optimization model.

The engine reproduces the workbook exactly, quirks included, because that is what
makes it trustworthy. The optimizer needs something else: a model with no
double-counted material, no dead flows, and no accounting splits that carry no
decision. Those differences live here as explicit, reversible configuration
rather than being quietly baked into the solver.

Every entry states why, so the simplification can be argued with.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional


#: Products merged into one for optimization purposes.
#:
#: `members` are the workbook product codes that become the aggregate.
#: `pool` names a workbook block that is a *derived sum* of the members rather
#: than a tank of its own - it is dropped, because modelling both the pool and
#: its members counts the same material twice.
AGGREGATIONS: List[Dict[str, Any]] = [
    {
        "id": "DSL",
        "title": "Finished diesel",
        "pool": "DDDD",
        "members": ["8170", "8175", "8135", "8105", "8136", "8177",
                    "8120", "8165", "8125", "8115"],
        "capacity_override": 1_600_000.0,
        "why": (
            "The workbook splits finished diesel into ten grades and then sums "
            "them back into the DDDD pool, so the same material is represented "
            "twice. The split carries no decision either: all demand sits on "
            "8175, which has no tank and no production, while all production "
            "lands on 8105 and 8170, which have no demand. Collapsing to one "
            "product removes the double count and the mismatch at once."),
        "capacity_note": (
            "Members sum to 948,825 gal but six of them have no tank recorded, "
            "so the pool's own 1,600,000 figure is used. Confirm with "
            "operations - this is the aggregate's only invented number."),
    },
]

#: Dollars of objective the solver may leave on the table before it stops.
#:
#: Absolute, not relative, because the cost objective measures value *given up*
#: and so shrinks as the model improves - a percentage of it is a moving target
#: that tightens every time anything is fixed, which is not a decision anyone
#: made. In dollars the question is answerable: a changeover costs $2,000, so
#: this is "do not spend an hour proving something worth five changeovers".
#:
#: Raise it if runs stop proving; lower it if a schedule looks obviously
#: improvable. Both are judgements about the plant, which is the point.
DEFAULT_GAP_ABS: float = 10_000.0

#: Flows that exist in the workbook but are dead, and would otherwise give the
#: optimizer objectives it can never satisfy.
RETIRED: Dict[str, Any] = {
    "products": {
        "9202": (
            "Obsolete tolling flow. Never charged at MEK, never produced (the "
            "yield rule looks up 9118 in a charge range holding 9202 and "
            "silently returns zero), yet 1,167,600 gal of Sonneborn demand is "
            "still booked against it - a permanent lost sale nothing could "
            "avoid."),
        "4325": (
            "Kendex 165HTLV is no longer made or sold. It survived as an EXTRACT "
            "charge line that was never used and had no place on the transition "
            "ladder, so the optimizer could never have scheduled it anyway."),
    },
    "units": {
        "TOLLING": "Never used in the plan; its only product, 9202, is retired.",
    },
    "charge_lines": {
        # Finished diesel charged back into the hydrotreater to make itself.
        # With the grades collapsed this becomes a 2% self-consuming loop that
        # represents no real decision.
        "HYDRO#78": "8170 charged to the hydrotreater to produce 8170.",
        "HYDRO#79": "8175 charged to the hydrotreater to produce 8175.",
        "TRANSFER_DIESEL#117": (
            "Kensol 30 to the diesel *charge* pool. Confirmed with operations "
            "that K-30 goes to *finished* diesel instead, on TRANSFER_FINDSL#135 "
            "- which carries 3.2 M gal against this line's 13 k. Keeping both "
            "gave 4111 two outlets, which the plant does not have: a downgrade "
            "route is a line to one destination, not a choice."),
        # The platformer's two self-charging rows. Both consume a product and
        # produce nothing, which is the same shape as the diesel loops above.
        "PLATFORMER#106": (
            "Isomerate run charged back to the platformer. Confirmed with "
            "operations that this is not a run the plant makes - it is how the "
            "workbook wrote down isomerate leaving. Its feed 9501 has no supply "
            "in the model anyway: the isomerate run's yield is booked straight "
            "into 1128's tank, so the line drew on a product that is never "
            "made."),
        "PLATFORMER#107": (
            "Platformate charged back to the platformer. Not a real run either: "
            "it is where the workbook recorded platformate going to gasoline. "
            "Kept alongside TRANSFER_GASOLINE it gave 9511 two outlets to the "
            "same place, and the model would take whichever priced better."),
    },
}

#: Demand that is a sale at its own margin, not disposal of something already
#: made - so `lost_cost` must not put the downgrade-discount floor under it.
#:
#: That floor exists because #6 oil carries a *negative* gross profit, which
#: taken literally makes failing to serve its demand profitable. The answer was
#: to say a lost sale is never a reward: declining #6 oil does not make the oil
#: vanish, it leaves it in a tank to be got rid of some other way.
#:
#: Gasoline is the opposite case. Platformate and isomerate take their forecast
#: from product 5020 (E10 gasoline), and not making them leaves *naphtha*
#: unreformed rather than platformate undisposed-of. Operations confirm gasoline
#: nets crude cost plus $0.20/gal and sells as far as the plant can make it, so
#: the margin given up by not reforming is exactly that $0.20 - not the $0.50
#: disposal floor, which is two and a half times too much and had the model
#: reforming to avoid a cost that was not there.
NOT_DISPOSAL: Dict[str, str] = {
    "9511": "Platformate to gasoline is a sale at the gasoline margin.",
    "1128": "Isomerate to gasoline is a sale at the gasoline margin.",
}

#: Blocks whose `Production Out` row names no product at all, so nothing can
#: ever leave the tank however many lines point at it.
#:
#: The workbook never modelled a route off these three, so its VLOOKUP had
#: nothing to look up and the cell reads zero. `_add_sink_lines` then invents an
#: outlet for them - and the invented line moved material in the model while the
#: replay moved none, which creates oil on the planner side. Latent until now
#: only because no volume had ever been routed there.
#:
#: Repairing the hook is parity-safe: the workbook charges nothing on any line
#: carrying these codes, so the engine still computes the same zero it always
#: did against the workbook's own plan.
MISSING_OUTLET_HOOK: Dict[str, str] = {
    "Napthas:59": "9511",     # platformate  -> gasoline
    "Napthas:75": "1128",     # isomerate    -> gasoline
    "Cstock:141": "4586",     # Kendex 0866  -> #6 oil
}

#: The hydrotreater makes one diesel product from the diesel charge stock.
#: 9713 stays a separate product: it is the *feed*, not the finished grade.
DIESEL_CHARGE_PRODUCT = "9713"
DIESEL_PRODUCT = "DSL"
DIESEL_YIELD = 0.98


#: Downgrade sinks — the overflow valves.
#:
#: These are where material goes when a tank would otherwise overfill. They are
#: modelled as *unlimited*: no capacity, no inventory balance, no demand ceiling.
#: In reality each has some limit, but relaxing them is deliberate — a sink that
#: can fill up turns an overflow into an infeasibility, which is exactly the
#: failure mode that makes these models unsolvable. The cost of using them is
#: carried entirely by the netback in `economics.py`, so the optimizer is
#: restrained by price rather than by a constraint.
#:
#: `product` is the workbook block that represents the sink, where one exists.
SINKS: Dict[str, Dict[str, Any]] = {
    "SIX_OIL": {
        "title": "#6 oil",
        "product": "8201",
        "outlet": "TRANSFER_6OIL",
    },
    "CAT": {
        "title": "Cat cracker",
        "product": "8221",
        "outlet": "TRANSFER_CAT",
    },
    "DIESEL": {
        "title": "Diesel",
        "product": "DSL",
        "outlet": "TRANSFER_DIESEL",
        "note": ("Diesel is both a sold product and a downgrade sink. As a sink "
                 "it absorbs without limit; its own demand is still served "
                 "normally."),
    },
    "FINDSL": {
        "title": "Finished diesel",
        "product": "DSL",
        "outlet": "TRANSFER_FINDSL",
        "note": ("Kensol 30 goes to finished diesel, not to the charge pool - "
                 "confirmed with operations. There are exactly two diesel "
                 "products: the charge stock 9713 and the finished pool DSL, "
                 "and this outlet is the second of them. Held as a fixed "
                 "obligation rather than a decision, the plan's 3.2 M gal of "
                 "K-30 transfer outran the K-30 available after demand and the "
                 "optimizer shorted customers to fund it."),
    },
    "GASOLINE": {
        "title": "Gasoline",
        "product": None,
        "outlet": "TRANSFER_GASOLINE",
        "note": ("No route exists in the workbook - this is a real downgrade "
                 "path the spreadsheet never modelled. Takes Kensol 17, "
                 "platformate and isomerate, confirmed with operations."),
    },
}

#: Which products may be pushed to which sink. Routes marked `observed` come from
#: the workbook's own transfer lines; `proposed` ones do not exist in the
#: spreadsheet and need confirming.
SINK_ROUTES: Dict[str, Dict[str, Any]] = {
    "GASOLINE": {
        "status": "confirmed",
        # Empty, and that is the point. The route these two take to gasoline is
        # already in the model as their 5020 forecast; a second line to the same
        # customer is not incremental offtake, it is the same barrels again.
        # With the forecast free to go unserved (see ELASTIC_DEMAND) the model
        # will happily short it to feed the sink, while the simulator serves
        # demand first and finds nothing left - a 50,000 gal feed shortfall on a
        # schedule that is fine.
        #
        # Worth restoring the day platformer production outruns the blending
        # pull, because then there really is surplus with nowhere to go. It does
        # not today: 5.7 M gal made against a 7.5 M gal forecast over 100 days.
        "products": [],
        "why": ("Confirmed with operations: platformate and isomerate blend to "
                "gasoline. Kensol 30 goes to diesel instead - a route the "
                "workbook already carries and uses (3.2 M gal a year through the "
                "K-30 to finished diesel line). The light straight run and "
                "isomerate *run* are untanked intermediates inside the platformer "
                "cascade, so they cannot overflow and need no outlet."),
        "caution": (
            "Gasoline demand is ALREADY in the model. Platformate and isomerate "
            "take their forecast from product 5020 (E10 gasoline) split 75/25 "
            "between them, which is the expected gasoline blending pull. The sink "
            "is therefore *incremental* offtake on top of that forecast, not a "
            "replacement for it - modelling it as the whole gasoline demand would "
            "count the same barrels twice."),
        "removed": (
            "Kensol 17 (4107) was on this list and has been taken off. It is the "
            "reformer's own feed - the same material as the platformer charge, "
            "under the code it carries when sold as a solvent - and it reaches "
            "gasoline as platformate, not directly. The direct route only existed "
            "because the reformer leg was missing from the workbook, which left "
            "3.6 M gal of heart cut a window with nowhere to go. Left in, it lets "
            "the optimizer blend heart cut straight to gasoline instead of "
            "reforming it: cheaper in the model, wrong in the plant."),
    },
    "SIX_OIL": {
        "status": "confirmed",
        "products": ["4579", "4586"],
        "why": ("Kendex D&N and Kendex 0866 both go to #6 oil. Confirmed with "
                "operations; the workbook carries a transfer line for neither. "
                "Both are small side cuts of extraction with no demand, no route "
                "and no tank movement anywhere in the plan, so on paper they "
                "simply accumulated until their tanks filled and the model went "
                "infeasible - 4579 on day 125, short by 17 gallons, and 4586 due "
                "to do the same around day 287. A product that is made and can "
                "never leave is not a scheduling problem, it is a missing route, "
                "and it fails as an arithmetic impossibility rather than as an "
                "expensive answer."),
    },
}

#: Allowed changeovers per unit, measured from the plan (see
#: analyze_transitions.py) and to be confirmed with operations. Absent units are
#: unrestricted.
TRANSITIONS: Dict[str, Dict[str, Any]] = {
    "MEK": {
        "ladder": ["9116", "9117", "9119", "4317"],
        "why": ("All 56 observed changeovers move exactly one rung on the "
                "viscosity ladder. With 9202 retired the ladder is complete."),
    },
    "EXTRACT": {
        "arcs": [("9302", "9303"), ("9303", "9302"),
                 ("9303", "9305"), ("9305", "9303"),
                 ("9305", "9302")],
        "why": ("Ladder 9302-9303-9305 plus a reset arc from 9305 back to 9302. "
                "Asymmetric: 9305->9302 observed 3x, 9302->9305 never. Confirm "
                "this is a rule and not a coincidence."),
    },
}

#: Transitions keyed on the **charge line**, for v1 onward.
#:
#: `TRANSITIONS` above is keyed on the product, and that is not expressive enough
#: for a unit that runs one feed two ways. Extraction changes over between normal
#: and deep extraction *without changing feed* - #90 -> #91 six times, #91 -> #90
#: once - and at product level that is 9305 -> 9305, a self-loop the arc
#: formulation cannot represent at all. So the setup state has to be the line,
#: not the product, and these are the arcs between lines.
#:
#: Measured from the plan; 9 of 12 possible pairs are used. The three never
#: observed sharpen the asymmetry already flagged on this unit: **deep extract
#: can return to 9302, normal extract cannot**, and nothing goes directly from
#: 9302 to either 9305 mode.
TRANSITIONS_BY_LINE: Dict[str, Dict[str, Any]] = {
    "EXTRACT": {
        "arcs": [
            ("EXTRACT#87", "EXTRACT#89"),   # 9302 -> 9303          11x
            ("EXTRACT#89", "EXTRACT#87"),   # 9303 -> 9302           6x
            ("EXTRACT#89", "EXTRACT#90"),   # 9303 -> 9305 normal    6x
            ("EXTRACT#90", "EXTRACT#89"),   # 9305 normal -> 9303    1x
            ("EXTRACT#89", "EXTRACT#91"),   # 9303 -> 9305 deep      4x
            ("EXTRACT#91", "EXTRACT#89"),   # 9305 deep -> 9303      4x
            ("EXTRACT#90", "EXTRACT#91"),   # normal -> deep         5x
            ("EXTRACT#91", "EXTRACT#90"),   # deep -> normal         1x
            ("EXTRACT#91", "EXTRACT#87"),   # 9305 deep -> 9302      3x
        ],
        "never_observed": [
            ("EXTRACT#87", "EXTRACT#90"), ("EXTRACT#87", "EXTRACT#91"),
            ("EXTRACT#90", "EXTRACT#87"),
        ],
        "why": ("Ladder 9302 - 9303 - 9305, with the two 9305 modes adjacent to "
                "each other, plus a one-way return from deep extract to 9302. "
                "Confirm the three never-observed pairs are rules rather than "
                "coincidence - in particular that normal extract really cannot "
                "return to 9302 when deep extract can."),
    },
}

#: Minimum campaign length per line, where the line-level figure differs from the
#: product-level one in MIN_RUN_DAYS. Extraction's 9305 carries a single minimum
#: of 3 days at product level, but the two modes behave differently.
MIN_RUN_DAYS_BY_LINE: Dict[str, Dict[str, int]] = {
    "EXTRACT": {
        "EXTRACT#87": 2,    # 12 campaigns, median 7 days
        "EXTRACT#89": 1,    # 17 campaigns, median 2 days
        "EXTRACT#90": 2,    # 7 campaigns, median 3 days  (9305 normal)
        "EXTRACT#91": 1,    # 9 campaigns, median 4 days  (9305 deep)
    },
    #: From operations, not from the plan. The workbook's own HYDRO campaigns run
    #: a median of 1-2 days per charge, and that was read for a long time as the
    #: plant switching the unit daily - `MARGIN-AND-CAMPAIGNS.md` says so, citing
    #: "93 of its 98 changeovers are back to back". That figure answers a
    #: different question: it comes from the idle-gap section of
    #: `switching-analysis.md` and means a changeover with no idle day inside it,
    #: not a campaign one day long. The unit is not run in one-day campaigns.
    #:
    #: This is the second half of the reactor rule, not a replacement for it.
    #: `min_visit_days` holds the unit on a reactor; this holds it on a charge.
    #: With only the first, a 4-day R1 visit was free to bounce between 9704 and
    #: 9705 and R2 was unconstrained entirely - the 100-day run came back with 59
    #: campaigns at a median of 1 day, worse line-level churn than the 43 it had
    #: before the reactor rule went in.
    "HYDRO": {
        "HYDRO#77": 3,      # 9705 Kendex 0847 - R1
        "HYDRO#84": 3,      # 9704 Kendex 0150 - R1
        "HYDRO#76": 2,      # 9713 No.2 diesel charge
        "HYDRO#78": 2,      # 8170 onroad diesel
        "HYDRO#79": 2,      # 8175 NRLM diesel
        "HYDRO#80": 2,      # 9711 Kensol 48
        "HYDRO#81": 2,      # 9712 Kensol 50
        "HYDRO#82": 2,      # 9703 Kensol 61
        "HYDRO#83": 2,      # 9720 dewaxed unext LN
    },
}

#: v2. The hydrotreater has no ladder: 36 of its 42 possible transitions appear
#: in the plan, so there is no structure for the arc formulation to exploit.
#:
#: **Decision: restrict to the 36 observed.** Tighter relaxation than an
#: unrestricted set, at a cost worth stating - the model can never propose a
#: transition the plant did not happen to make in this one year, so any genuine
#: improvement that needs an unused pair is out of reach by construction. The six
#: unused pairs should be checked before v2 runs in anger: if any of them is
#: merely unused rather than forbidden, it belongs in the set.
HYDRO_ARCS_FROM_OBSERVED = True

#: Units where the model chooses what to charge, and therefore needs setup
#: binaries, changeover arcs and a maximum rate per charge product.
DECISION_UNITS = ["MEK", "HYDRO", "EXTRACT", "ROSE"]

#: Units that run several streams in parallel rather than choosing between them.
#: Throughput follows from the feed and the yields, so the individual stages need
#: no setup binary and no independent rate.
CASCADE_UNITS = ["PLATFORMER"]

#: Downgrade and transfer routes. Not unit charges: their volume is bounded by
#: the sink's offtake and its netback, not by a unit's capacity.
TRANSFER_UNIT_PREFIXES = ("TRANSFER", "SONNEBORN", "RAILCAR")

#: Units that run one feed at a time, so a rate set on one of their lines clears
#: the unit's other lines on the same days (Set a rate across the window). Not
#: the Platformer's parallel stages, the hydrotreater's mix or the transfers: in
#: the plans on 2026-09-13 the Platformer had two or more lines on 3,573 of 5,968
#: charged days, HYDRO on 230 of 2,810 and TRANSFER_DIESEL on 288 of 2,246, and
#: clearing the others there wiped schedules that were right. MEK (4 of 1,528),
#: EXTRACT (21 of 1,516) and ROSE (0 of 4,995) are where a second line on a day
#: is a mistake to clean up - though a changeover day the optimizer split
#: between two feeds looks the same, which is why a typed cell clears nothing.
ONE_FEED_UNITS = ["MEK", "EXTRACT", "ROSE"]


def runs_one_feed(unit: str) -> bool:
    return unit in ONE_FEED_UNITS


def is_decision_unit(unit: str) -> bool:
    return unit in DECISION_UNITS


def needs_max_rate(unit: str) -> bool:
    """Only units that schedule a charge need a rate ceiling."""
    return is_decision_unit(unit)


#: Time lost to a changeover, as a fraction of a day. The interface goes back to
#: charge, so the material is not lost - the *unit time* is, which is why this
#: belongs in the day's time budget rather than in the objective as a cash cost.
#:
#: Measured independently from the plan by comparing the first day of a campaign
#: with the days that follow: MEK 12%, EXTRACT 13%, HYDRO 13%. Those land on
#: ~1/8 of a day, which is the default below. ROSE's sample is too small (6
#: campaigns) to read.
SWITCH_LOSS_DAYS: Dict[str, float] = {
    "MEK": 0.125,
    "EXTRACT": 0.125,
    "HYDRO": 0.125,
    "ROSE": 0.125,
    "PLATFORMER": 0.0,     # a continuous cascade; it does not change over
}
DEFAULT_SWITCH_LOSS_DAYS = 0.125

#: Units built from more than one reactor, where the choice of reactor is itself
#: a decision and crossing between them costs a flush.
UNIT_REACTORS: Dict[str, Dict[str, Any]] = {
    "HYDRO": {
        "flush_days": 0.25,
        "why": (
            "Two reactors on one unit. 4315 and 4319 are made on the dedicated "
            "reactor and everything else on the other, so the plant groups those "
            "two products together to avoid crossing. A crossing costs a quarter "
            "of a day while the reactor is flushed back to charge."),
        "reactors": {
            "R1": {"charges": ["9704", "9705"], "makes": ["4315", "4319"]},
            "R2": {"charges": ["9713", "9705x", "8170", "8175", "9711", "9712",
                               "9703", "9720"]},
        },
        #: Days the unit stays on a reactor once it goes there, from operations.
        #:
        #: This is a rule about the *reactor*, not about a charge line, and the
        #: difference is the whole point. `MIN_RUN_DAYS` says "once 9704 starts,
        #: run it three days" - which lengthens a visit and does nothing to stop
        #: the unit leaving R1 for a Kensol and coming straight back. Tried at
        #: R1 3 / R2 2 over 42 days it did exactly that: visits got longer *and*
        #: more numerous, 6 crossings against 3 with no minimum at all.
        #:
        #: Four days is the plan's own median R1 visit measured as reactor time
        #: (7 visits over 100 days), and operations confirm the unit is not run
        #: in shorter ones. Holding the reactor is what puts 9704 and 9705 in the
        #: same visit, which is the grouping the plant describes - as structure,
        #: rather than hoping a changeover price buys it.
        #:
        #: R2 carries no minimum: it is the general reactor and the state the
        #: unit sits in, and the plan really does turn its Kensol grades quickly.
        "min_visit_days": {"R1": 4},
        "evidence": (
            "Derived independently from the yield rules: 9704 makes 4315 and "
            "9705 makes 4319. The plan crosses reactors 20 times a year (1.6 a "
            "month, 5.0 days of flush time), and reactor 1 runs in 11 campaigns "
            "of median 4 days. First-day charge on 9704 and 9705 is 42-44% below "
            "their later days, against ~13% for everything else - the flush plus "
            "an ordinary switch, showing up in the data."),
    },
}

#: Maximum charge rate per unit and product, in **barrels per day**.
#:
#: The model is `charge = max_rate x time_fraction`, so what is needed is an
#: absolute rate. The workbook's monthly run rate is a *planning* figure, not a
#: ceiling - ROSE runs 4313 at 1.3x its planning rate and the platformer runs
#: 9103 at 1.1x - so using it as the maximum would cap the optimizer below what
#: the plant already does.
#:
#: `basis` says where the number came from:
#:   clean day   the largest charge on a day with no changeover either side
#:   grossed up  no clean day exists, so the observed maximum was necessarily a
#:               partial day and has been grossed up by one changeover's loss
#:
#: Every figure is a **floor on the true maximum**, never a specification. The
#: four hydrotreater charges on a grossed-up basis are the ones to check first.
#: Where a maximum rate came from, and therefore how much weight it carries.
#:
#: The distinction is the whole point of recording a basis: a rate read off the
#: plan is evidence of what the unit *has done*, never of what it *can do*, and
#: the two get confused the moment nobody writes it down. Only a confirmed rate
#: is a specification. Everything else is a floor, and the build says so.
RATE_BASIS_IS_FLOOR: Dict[str, bool] = {
    "confirmed with operations": False,
    # A planner typing a rate is stating a limit, not reporting an observation,
    # so it binds even against a plan that exceeds it. Defaulting it to a floor
    # like the measured bases is what made the edit look accepted and do
    # nothing: the ceiling reached the spec correctly and was then read as
    # "the largest we have seen", so `max(override, what the plan ran)` handed
    # the original number straight back.
    "planner override": False,
    "clean day": True,
    "grossed up": True,
    "derived from the fractionator": True,
    "deep extraction mode": True,
}

MAX_RATE_BBL_PER_DAY: Dict[str, Dict[str, Dict[str, Any]]] = {
    "MEK": {
        "9117": {"bbl": 4000, "basis": "clean day", "clean_days": 22},
        "9119": {"bbl": 2100, "basis": "clean day", "clean_days": 3},
        "9116": {"bbl": 4000, "basis": "clean day", "clean_days": 2},
        "4317": {"bbl": 2400, "basis": "clean day", "clean_days": 24},
    },
    "HYDRO": {
        "9713": {"bbl": 5000, "basis": "clean day", "clean_days": 9},
        # Confirmed with operations. The plan runs it at 3,000 on its best day,
        # so the 2,500 read off a single clean day was the observation, not the
        # capability - the model was capping reactor 1's feed 17% below what the
        # unit can take.
        "9705": {"bbl": 3000, "basis": "confirmed with operations",
                 "clean_days": 1},
        # 5,200 was read off 4 clean days, and operations say the unit does not
        # go there. The observation loses: the plan is known to carry charge
        # figures the unit cannot run - its single 8,500 HYDRO day is a crude
        # number on a hydrotreater row - so a clean day is evidence of what was
        # *typed*, not of what the unit can take.
        "9704": {"bbl": 5000, "basis": "confirmed with operations",
                 "clean_days": 4, "was": 5200},
        # Confirmed with operations: the hydrotreater tops out at 5,000 bbl/day
        # for the *unit*, whatever mix it runs. These four had no clean day to
        # read, so they were grossed up from partial days by one changeover's
        # loss - and grossing up an observation is only sound if the result stays
        # inside the unit's real limit, which these did not: 5,943 is 19% above a
        # unit that tops out at 5,000. The arithmetic was right and the answer was
        # still wrong, which is exactly what `basis: grossed up` was meant to flag.
        #
        # Held at the unit limit pending per-feed figures. Each of these is a
        # *unit* limit standing in for a product rate, so any of them may still be
        # too high individually - none can be too low.
        #
        # **Why no separate unit-level cap is needed.** The day-time budget makes
        # a unit's daily total a convex combination of its line rates:
        # `sum(chg[k]) <= sum(rate[k] * tfrac[k]) <= max(rate) * sum(tfrac) <= max(rate)`.
        # So the total is bounded by the *highest* line ceiling, and with every
        # HYDRO line at or below 5,000 the unit constraint holds for free. That
        # stops being true the moment any HYDRO line is raised above 5,000, at
        # which point an explicit absolute cap becomes necessary.
        "9711": {"bbl": 5000, "basis": "confirmed with operations",
                 "clean_days": 0, "was": 5943},
        "9712": {"bbl": 5000, "basis": "confirmed with operations",
                 "clean_days": 0, "was": 5943},
        "9703": {"bbl": 5000, "basis": "confirmed with operations",
                 "clean_days": 0, "was": 5714},
        "9720": {"bbl": 5000, "basis": "confirmed with operations",
                 "clean_days": 0, "was": 5486},
    },
    "EXTRACT": {
        "9302": {"bbl": 2800, "basis": "clean day", "clean_days": 47},
        # Confirmed with operations. The plan itself exceeds the old 1,800 on its
        # best day, which is also why the v1 warm start could only seed binaries:
        # the planner's own levels were infeasible against a ceiling that was
        # wrong. At 2,100 the plan is admissible and the levels can be seeded too.
        "9303": {"bbl": 2100, "basis": "confirmed with operations",
                 "clean_days": 1},
        "9305": {"bbl": 2200, "basis": "clean day", "clean_days": 21},
    },
    "ROSE": {
        "4313": {"bbl": 1300, "basis": "clean day", "clean_days": 139},
        "4555": {"bbl": 1000, "basis": "clean day", "clean_days": 2},
    },
    "PLATFORMER": {
        "9103": {"bbl": 4000, "basis": "clean day", "clean_days": 132},
        # The reformer. Never scheduled in the workbook, so there is no observed
        # day to read a rate from; derived instead from what the fractionator
        # hands it - 3,690 bbl/day of naphtha at the 0.5512 heart-cut yield,
        # less the small solvent draw. A floor, not a specification: the plan is
        # evidence the reformer takes at least this much, not that it can take
        # no more.
        #
        # NAMING TRAP, and almost certainly the origin of the missing leg:
        # the workbook names 9103 "PLATFORMER CHARGE (NAPHTHA)", but 9103
        # charges the *fractionator*. The real platformer charge is 4107 - the
        # heart cut, sold as Kensol 17 when it is sold. Read the product names
        # rather than the yield rules and you conclude the complex is fully
        # modelled by line #102, which is the mistake the spreadsheet embeds.
        "4107": {"bbl": 2024, "basis": "derived from the fractionator",
                 "clean_days": 0},
    },
}

#: Rates that belong to a *line* rather than to the unit-and-feed pair.
#:
#: A unit running the same feed two different ways has two different ceilings,
#: and keying on the feed alone silently gives both the higher one.
#:
#: Extraction runs 9305 Dewaxed Bright Stock in two modes:
#:
#:   EXTRACT#90  normal    -> 4318 Argold Legacy, which can only be sold
#:   EXTRACT#91  deep      -> 9705, which can be sold *or* charged to the
#:                           hydrotreater - and the hydrotreater will only take
#:                           deep extract, so this line is the sole route into
#:                           reactor 1 for that stream
#:
#: Deep extraction is the more severe cut and runs slower: 1,300 bbl/day against
#: 2,200. The plan never exceeds it. The model, keying on 9305 alone, gave the
#: deep line the shallow line's 2,200 and scheduled it there on 4 days of 42 -
#: 69% more deep extract than the unit can produce, feeding a hydrotreater
#: campaign that could not have run.
#:
#: This is a product-mix decision, not a duplicated row: the two modes make
#: different products from the same feed, the same shape as the crude R/L mode.
MAX_RATE_BY_LINE: Dict[str, Dict[str, Any]] = {
    "EXTRACT#91": {"bbl": 1300, "basis": "deep extraction mode",
                   "clean_days": 33,
                   "note": "Deep extract. The only feed the hydrotreater will "
                           "take from extraction. Sellable in the plant, but "
                           "deliberately charge-only in the model - see "
                           "DECIDED_NOT_MODELLED."},
    "EXTRACT#90": {"bbl": 2200, "basis": "clean day", "clean_days": 21,
                   "note": "Normal extract, to 4318 Argold. Sales only - it "
                           "cannot be charged to the hydrotreater."},
}

#: Where each sink's transfer lands, so a downgrade can be priced as the
#: difference between two gross profits rather than as an invented discount.
#: Derived from the workbook's own transfer yield rules; written down because the
#: pricing depends on it and a silent change would misprice every downgrade.
SINK_LANDS_IN: Dict[str, Optional[str]] = {
    "SIX_OIL": "8201",      # TRANSFER_6OIL   -> #6 oil
    "CAT": "8221",          # TRANSFER_CAT    -> cat cracker feed
    "DIESEL": "9713",       # TRANSFER_DIESEL -> diesel *charge* pool
    "FINDSL": "DSL",        # TRANSFER_FINDSL -> *finished* diesel
    "GASOLINE": None,       # no product in the workbook; priced as the outlet
}

#: Products whose demand is a blend pool's pull, not a market of their own.
#:
#: Platformate and isomerate are never sold to anyone. Their forecast comes from
#: product 5020, E10 gasoline, split 75/25 between them - it is the blending pull
#: for gasoline wearing their codes. Pricing them separately would invent a
#: market that does not exist and let the model trade one against the other as
#: though a customer preferred one.
#:
#: So their netback is the pool's. Between them they carry 4.2 M gal, 27% of all
#: demand in a 42-day window, which makes the gasoline netback the single
#: highest-leverage price in the model.
BLEND_POOL_PRODUCTS: Dict[str, str] = {
    "9511": "GASOLINE",     # platformate, 75% of the 5020 pull
    "1128": "GASOLINE",     # isomerate, 25%
}

#: Production code -> the code the same material is sold under.
#:
#: Five products carry two codes: one for the stream inside the plant, one for
#: the grade on the invoice. They are the same oil. Kensol 17 and the platformer
#: charge were the same duality and cost 67% of the objective before anyone
#: noticed, so these are written down rather than inferred.
#:
#: **The model keeps the production code as its key, deliberately.** Every arc,
#: rate, minimum-run and product list in this file is written in production
#: codes, and so is the workbook's own charge grid; renaming the keys would move
#: all of that through the alias machinery at once, which is where a wrong answer
#: is most expensive. The sales code is what a *price* attaches to - nothing
#: else - so it is carried here and used at the point prices are read.
SALES_CODES: Dict[str, Dict[str, str]] = {
    # the workbook records these itself, in each block's sold code
    "9711": {"sold_as": "4105", "name": "Kensol 48 UNHT", "source": "workbook"},
    "9718": {"sold_as": "9116", "name": "Waxy Light Neutral", "source": "workbook"},
    "4329": {"sold_as": "4327", "name": "Kendex 0060H", "source": "workbook"},
    "4554": {"sold_as": "9207", "name": "Kendex 0834", "source": "workbook"},
    # the workbook does not: its block calls 9704 both things
    "9704": {"sold_as": "4305", "name": "Kendex 0150",
             "source": "operations",
             "note": "9704 is Kendex 0150 UNHT as a stream and 4305 as a grade. "
                     "Hydrotreating it makes 4315 Kendex 0150H instead, so the "
                     "trade is 4315's netback against 4305's, less the "
                     "hydrotreater time. Both sides of that now have a price."},
}

#: Products that can be sold as they are, without limit, in addition to whatever
#: the plant normally does with them.
#:
#: Distinct from a downgrade *sink*, which is a destination other products are
#: routed **to**. This is offtake a product has of its **own** - no transfer
#: line, no receiving pool, no unit time. The material is lifted as-is.
#:
#: The price is expressed against another product rather than in absolute terms,
#: which means it can be set today instead of waiting for the netback list: the
#: cost of using the outlet is the cost of that product's own outlet plus the
#: discount. Diesel charge sold raw therefore costs the diesel downgrade cost
#: plus $0.60/gal, and the model will process it into finished diesel whenever
#: the hydrotreater has the time, selling it only when it genuinely cannot.
#:
#: **This differential is a placeholder.** 9713 is on the netback list in its own
#: right, and when an absolute arrives it replaces `relative_to` and
#: `discount_per_gal` outright - one mechanism for every outlet, rather than one
#: product priced a different way from the rest. Keep the differential only until
#: then: it is right, but it is the odd one out.
UNLIMITED_OFFTAKE: Dict[str, Dict[str, Any]] = {
    "9713": {
        "relative_to": "DIESEL",
        "discount_per_gal": 0.60,
        "why": ("Diesel charge stock lifts as-is at $0.60/gal under finished "
                "diesel, unlimited offtake, confirmed with operations. 9713 is "
                "the bottom of the plant - every hydrotreater line returns its "
                "yield loss there and every diesel downgrade lands there - so "
                "without an outlet of its own the pool simply backs up, and the "
                "excess is real rather than an artefact."),
    },
}

#: Charge lines the model runs even though the planner never schedules them.
#:
#: v0 takes the planner's assignments as given, which normally means "a line with
#: no barrels on it is a line the plant chose not to run". The reformer is the
#: exception: it runs every day, but the workbook has no row that says so, so the
#: schedule shows zero and the model inferred a shutdown.
#:
#: The cost of that inference was the largest number in the objective. Kensol 17
#: *is* the platformer charge - the reformer's feed, under the code it carries
#: when sold as a solvent. Confirmed with operations:
#:
#:     crude -> 9103 whole naphtha -> FRACTIONATOR -> LSR + K17 + K30
#:                                                     |
#:                                                     K17 -> REFORMER -> platformate -> gasoline
#:
#: The fractionator makes 3.59 M gal of K17 in a 42-day window against 17 k gal
#: of solvent demand and a 273 k gal tank, so it plainly does not accumulate.
#: With no reformer leg the model had to dump it, and then booked the platformate
#: it would have made as 3.15 M gal of lost sales: 67% of the objective, on both
#: sides of the same barrels.
#:
#: These lines get a charge variable regardless of the schedule, bounded by the
#: unit's maximum rate. They are flow-through, not a decision - the material has
#: nowhere else to go, so the tank constraint alone makes the unit run.
FLOW_THROUGH_LINES: Dict[str, str] = {
    "PLATFORMER#104": (
        "The reformer: Kensol 17 to platformate at 0.84. Runs continuously - the "
        "Platformer is a parallel cascade, not a choice - but the workbook never "
        "carried the row, so the plan shows it idle for all 366 days."),
    "HYDRO#76": (
        "The diesel charge draw: 9713 to finished diesel at 0.98. 9713 is the "
        "bottom of the plant - every hydrotreater line returns its yield loss "
        "there, and every downgrade routed to diesel lands there. The plan "
        "schedules this line on only 12 days of a 42-day window, which is a "
        "planner writing down the days they expected to need rather than a "
        "restriction: held to those 12 days the pool backs up, and freeing MEK "
        "and extraction pushed 58,136 gal into it that the model could not "
        "dispose of.\n\n"
        "Available every day it is an option, not an obligation. The unit can "
        "draw the pool down at full diesel value, or the pool can be sold as-is "
        "at $0.60/gal under finished diesel (see UNLIMITED_OFFTAKE) - and the "
        "hydrotreater's day budget makes running it compete with solvent "
        "production, which is the trade the optimizer is there to make."),
}

#: Minimum stable charge rate, as a fraction of the maximum.
#:
#: Products do have real minimum rates, but the model starts at zero deliberately:
#: a minimum rate is a *restriction*, so starting without it means v0 always has a
#: feasible answer, and adding the real numbers later can only tighten the plan
#: rather than break it. Fill this in per unit and product once operations supply
#: them - `min_rate_fraction()` is already wired through.
MIN_RATE_FRACTION: Dict[str, Dict[str, float]] = {}

#: Measured from the plan, not assumed. Across every line with enough
#: mid-campaign days to read - days with a running day either side, so no
#: changeover is clipping them - the lowest rate the plant has ever held is
#: **0.64 of the line's maximum**, and the median is 0.77:
#:
#:     MEK#69   0.85    EXTRACT#87  0.64    HYDRO#76  0.90
#:     MEK#71   0.71    EXTRACT#90  0.68    HYDRO#84  0.87
#:     MEK#73   0.75    EXTRACT#91  1.00    ROSE#97   0.77
#:
#: The plant runs a line properly or not at all; it does not trickle. Starting
#: at zero was deliberate - a minimum is a restriction, and starting without one
#: guaranteed a feasible answer - but zero turned out to be the wrong kind of
#: wrong: must-run binds the day's *time*, nothing bound volume, and the model
#: allocated days it then charged nothing through. Six of extraction's 42 days
#: came back nominally running and empty, which is not a schedule anyone would
#: work to.
#:
#: 0.60 sits just under the lowest figure the plant has actually held, so it
#: forbids the trickle without forbidding anything the plant does.
DEFAULT_MIN_RATE_FRACTION = 0.60

#: The crude unit's charge rate is a **fixed input**, not a decision.
#:
#: Crude rate is set by refinery economics - the crude-to-product spread - not by
#: downstream tank logistics. The plant runs it flat out and only turns it down in
#: a poor-margin scenario, which is a different decision made at a different
#: level and on a different clock.
#:
#: Leaving it as a variable would actively harm the model. Crude margin dwarfs any
#: downgrade cost, so an optimizer free to cut crude would "solve" every inventory
#: problem by making less oil - the cheapest answer in the model and the most
#: expensive one in reality. Fixing it points the optimizer at the question worth
#: asking: given this crude plan, what is the best downstream schedule?
CRUDE_RATE_IS_INPUT = True

#: Whether the crude unit's R/L mode stays a decision.
#:
#: Unlike the rate, mode is a product-mix choice with a direct downstream effect:
#: R makes 9117 Waxy Medium Neutral, which feeds the MEK dewaxer; L makes 9118 Low
#: Volatility Medium Neutral, which goes to Sonneborn. The plan runs R on 313 days
#: and L on 47, switching 38 times a year - so it is campaigned deliberately.
#:
#: Left as a decision it costs one binary a day and lets the model say "run L a
#: day earlier so the MEK does not run dry on 9117". Set to False if Sonneborn
#: volumes are contractual and the mode calendar is really fixed.
CRUDE_MODE_IS_DECISION = True

#: Units run every day unless a turnaround stops them.
#:
#: Confirmed by the plan: inside the region it actually schedules, MEK runs 160 of
#: 162 days, ROSE 153 of 162, extraction 151 of 162, the hydrotreater 151 of 160 -
#: and the missing days cluster into a handful of outages rather than scattering.
#: So idling is the exception, not a free choice.
#:
#: This is a real constraint, not a convenience. Without it the optimizer can
#: relieve any inventory problem by simply not running, which is never what the
#: plant does and would make every schedule it produces useless.
MUST_RUN_WHEN_AVAILABLE = True

#: Planned outages. **User input** - these are real dates from the maintenance
#: calendar, and nothing may be produced on them.
#:
#: Seeded below with the outages implicit in the current plan (blank stretches
#: rather than declared shutdowns), so they can be checked against the real
#: calendar rather than started from nothing. Every one is marked `detected` until
#: someone confirms it.
TURNAROUNDS: Dict[str, List[Dict[str, Any]]] = {
    "MEK": [
        {"start": "2026-07-27", "end": "2026-07-28", "status": "detected"},
    ],
    "HYDRO": [
        {"start": "2026-10-11", "end": "2026-10-15", "status": "detected"},
    ],
    "EXTRACT": [
        {"start": "2026-10-02", "end": "2026-10-09", "status": "detected"},
    ],
    "ROSE": [
        {"start": "2026-10-15", "end": "2026-10-23", "status": "detected"},
    ],
    "PLATFORMER": [
        {"start": "2026-12-18", "end": "2027-02-08", "status": "detected",
         "note": "53 days - long enough that it is either a major turnaround or "
                 "a deliberate shutdown. Worth confirming which."},
    ],
}

#: Single idle days in the plan that are not part of any outage above:
#: hydrotreater 4, extraction 3. Under a must-run rule the model will schedule
#: through them, making it slightly tighter than the plan. Either they are
#: one-day outages worth declaring, or they were slack.
UNEXPLAINED_IDLE_DAYS = {
    "HYDRO": ["2026-08-18", "2026-08-29", "2026-09-02", "2026-10-01"],
    "EXTRACT": ["2026-07-27", "2026-08-05", "2026-08-15"],
}

#: Product recycled back into a unit as charge - rare, and not yet specified.
#:
#: Note this is *not* the interface material that returns to charge on a
#: changeover: that is already handled as lost time rather than lost material, so
#: it needs no flow here. This hook is for the genuine cases where finished or
#: off-spec product is re-charged. Each entry needs a source product, a
#: destination unit, and whether the volume is a decision or a fixed rate.
RECYCLE_ROUTES: List[Dict[str, Any]] = []

#: Things the plant does that the model deliberately does not.
#:
#: Distinct from an open item. An open item is missing - someone still has to
#: answer it. These have been answered, and the answer was "not yet". They are
#: recorded so nobody reads the absence as an oversight and quietly "fixes" it,
#: and so the cost of the simplification is written down next to it rather than
#: rediscovered.
DECIDED_NOT_MODELLED: List[Dict[str, str]] = [
    {
        "what": "Blend pull priced above ordinary forecast",
        "decision": "One netback per output product. Demand is summed across "
                    "Sales, Forecast and Blends, so the model cannot tell which "
                    "kind it failed to serve and values them alike.",
        "cost": "Blends do not outrank forecast when something has to give, "
                "though operations rank them highest. Blend pull is spread "
                "across nine products - 9704 carries 456 k gal of it, 4329 "
                "245 k, 4315 188 k - and 4309 is entirely blend, so a short "
                "product may be shorted on the blend rather than on the "
                "forecast, which is the wrong way round.",
        "when": "Split demand by row and price each separately. The rows are "
                "already kept apart in `spec.demand_rows`; it is the optimizer "
                "that sums them. Contained change, and the netback file has a "
                "column ready for the second number whenever it is wanted.",
    },
    {
        "what": "Interface material lost on a changeover",
        "decision": "Changeovers are modelled as clean: the arc carries lost "
                    "unit time (0.125 d, measured three ways independently) and "
                    "no material. In reality each charge product has its own "
                    "split between interface returned to charge and interface "
                    "genuinely slopped or blended off - confirmed with "
                    "operations - but that split is not needed at this level.",
        "cost": "The model undercounts what a changeover costs, because it "
                "charges for the time and not for the giveaway. It will "
                "therefore switch somewhat more often than is truly economic, "
                "and campaigns may come out shorter than the plant would "
                "choose. Nothing becomes infeasible or unexecutable - the error "
                "is in the objective, not the physics.",
        "when": "The correction already has a home: `switch_cost`, the cash term "
                "per changeover, which exists precisely for what lost time does "
                "not capture. It needs no interface measurements to set - run "
                "v1 at zero and raise it per unit until campaign lengths match "
                "the ones the plant actually runs (MEK 3 d median, HYDRO 1 d, "
                "EXTRACT 3 d, ROSE 40 d). That calibrates against observed "
                "behaviour rather than a quoted number.",
    },
    {
        "what": "Deep extract (9705) can be sold, not only charged",
        "decision": "Charge-only. 9705 carries no demand anywhere in the "
                    "workbook, so there is no volume or price to model a sale "
                    "with - and a half-modelled outlet with invented demand "
                    "would distort the one real trade-off on the unit.",
        "cost": "Extraction's deep mode looks slightly less valuable than it "
                "is, and the model has one fewer relief valve when the "
                "hydrotreater is down: the plant can sell deep extract, the "
                "model can only back it up or downgrade it. It will therefore "
                "understate deep extraction and may run it below what the "
                "plant would - the conservative direction.",
        "when": "Once product netbacks exist (see docs/archive/MARGIN-OBJECTIVE.md), "
        "model "
                "it as a transfer that moves 9705 to its sale product at no "
                "cost and no yield loss - the same shape as RECYCLE_ROUTES, "
                "not a new demand row. That keeps one physical stream with two "
                "outlets rather than inventing a second product.",
    },
]


def turnaround_days(unit: str) -> set:
    """Every date on which `unit` may not produce."""
    import datetime as _dt

    out = set()
    for window in TURNAROUNDS.get(unit, []):
        y1, m1, d1 = (int(x) for x in window["start"].split("-"))
        y2, m2, d2 = (int(x) for x in window["end"].split("-"))
        day = _dt.date(y1, m1, d1)
        last = _dt.date(y2, m2, d2)
        while day <= last:
            out.add(day)
            day += _dt.timedelta(days=1)
    return out


def next_outage(days, after, reach_days: int = 90) -> List[Any]:
    """The next unbroken outage starting after `after`, from a set of down days.

    Takes the days rather than a unit name **so the planner's own turnaround
    dates drive it**. Downtime is scenario data, edited on the planning side and
    carried to the optimizer per run; `TURNAROUNDS` here is only the fallback
    inferred from the workbook when a scenario carries none. Reading the table
    directly would have meant a planner moving a turnaround saw the units stop
    on the new dates while the tanks were still being drained for the old ones -
    and the two only agree today because both were seeded from the same
    inference.

    Empty if there is none inside `reach_days`. Only the *next* one matters: a
    tank has to survive the outage it meets first, and by the time the second
    arrives the plan has had a whole outage to rearrange itself.
    """
    import datetime as _d

    ahead = [d for d in sorted(days)
             if after < d <= after + _d.timedelta(days=reach_days)]
    if not ahead:
        return []
    run = [ahead[0]]
    for d in ahead[1:]:
        if (d - run[-1]).days == 1:
            run.append(d)
        else:
            break
    return run


def must_run(unit: str, day) -> bool:
    """True when the unit is required to produce something on this day."""
    if not MUST_RUN_WHEN_AVAILABLE:
        return False
    return day not in turnaround_days(unit)


#: Safety stock in gallons, per product, where operations have given a real one.
#:
#: **Empty on purpose**, like every other table here that would otherwise carry an
#: invented number. `RefKind.TARGET_LCL` is the eventual home - the reference layer
#: already has a slot for a lower control limit per product - but the workbook sets
#: none, and `modelprep` does not carry the field through to the spec yet. Until it
#: does, the floor is derived from days of cover (`safety_stock_days`), which is a
#: statement about demand rather than about the tank.
#:
#: An entry here overrides the derived figure for that product, so the first real
#: number operations supply can land without waiting for the rest.
SAFETY_STOCK_GAL: Dict[str, float] = {}


#: The last day the workbook's inputs can be believed. Confirmed with operations.
#:
#: The scenario carries 366 dates, 2026-07-23 to 2027-07-23, and the workbook has
#: columns for all of them - so nothing about the data announces where it stops
#: being real. Three things end at different points and only one of them matters:
#:
#:     2026-10-15   last firm Open Order (57 days); forecast beyond
#:     2026-12-31   last day the planner filled in a charge  <- the boundary
#:     2027-07-17   extent of the Charge Schedule date columns
#:
#: **Past 2026-12-31 the demand inputs are wrong**, and the schedule is empty for
#: MEK, extraction, ROSE and the hydrotreater. Crude is a fixed input and keeps
#: running, so the side streams keep arriving with nothing charging them and the
#: feed tanks fill until they burst - 9117 alone by 9.8 M gal/day, which is what
#: made a full-year run infeasible at every charge floor including zero.
#:
#: That infeasibility was a symptom. The cause is that the question was asked of
#: days the workbook cannot answer for, and no parameter can fix it: it needs
#: schedule and demand data that do not exist yet.
#:
#: **The practical limit is two days shorter still: 160 days, ending
#: 2026-12-29.** 161 is infeasible, and for a much smaller reason than the
#: full-year case - the elastic diagnostic returns exactly one violation, 9103
#: platformer charge overflowing its tank by 81,759 gal on 2026-12-30. That is an
#: edge-of-window effect on a single product, not a structural break, which is
#: worth knowing before anyone goes looking for a deep cause. It is also why
#: every report in `data/reports` is named `*-160`: the edge was found
#: empirically before it was explained.
#:
#: **The date used to be written here as 2026-12-31 and is now derived.** The
#: boundary was never a fact about the calendar - it is a fact about how far the
#: planner has filled the schedule in, and it moves the moment they fill in more.
#: Hard-coding it meant a planner who extended their plan by a month got the same
#: silent truncation and no way to see why, and it would have had to be edited by
#: hand on every input refresh. `schedule_valid_through` reads it off the data
#: instead. On the workbook this comment was written against, it returns
#: 2026-12-31 - the same day, now for a reason the code can restate.
#:
#: Set this to a real date to override the derivation; `None` means derive.
DATA_VALID_THROUGH: Optional[_dt.date] = None

#: The longest horizon that actually solves. See above for what the 161st day
#: costs. Kept separate from the believable-data boundary because they are
#: different claims: one is about the data, the other about this plan's tank
#: levels, and a revised plan could move the second without touching the first.
MAX_SOLVABLE_DAYS = 160


def schedule_valid_through(scn: Any) -> Optional[_dt.date]:
    """The last day the planner filled in a charge on a unit that decides things.

    This is the boundary, and the reason is the asymmetry between what stops and
    what does not. Crude is a fixed input: it keeps arriving whatever the
    schedule says. So on the first day past the end of the planner's grid the
    side streams still land, nothing is scheduled to consume them, and the feed
    tanks fill until they burst - 9117 alone by 9.8 M gal/day. That is what made
    a full-year run infeasible at every charge floor including zero.

    Demand is not the binding half, despite what the comment above used to imply.
    The sales forecast, blend demand and base-oil transfers are all *monthly*
    rates, so they cover any day the model asks about; only the firm open orders
    are dated, and they stop earlier still (2026-10-15) with forecast covering
    the rest by design. It is the schedule that runs out, so the schedule is what
    is measured.

    Transfer and cascade lines are ignored deliberately. A transfer line carries
    a downgrade the planner wrote, not a charge decision, and the platformer runs
    to the end of the date columns because it is fed by crude rather than
    scheduled - counting either would push the boundary out past the point where
    anything is actually charging the lube units.

    Returns `None` when the scenario carries no charges at all, which means "do
    not clamp" rather than "clamp to nothing": a caller with an empty grid has a
    different problem, and truncating its horizon to zero would hide it.
    """
    lines = getattr(scn, "charge_lines", None) or {}
    last = None
    for key, series in lines.items():
        if not is_decision_unit(str(key).split("#")[0]):
            continue
        for iso, bbl in (series or {}).items():
            if not bbl or bbl <= 0:
                continue
            if last is None or iso > last:
                last = iso
    if last is None:
        return None
    y, m, d = (int(x) for x in str(last).split("-"))
    return _dt.date(y, m, d)


def valid_through(scn: Any = None) -> Optional[_dt.date]:
    """The boundary in force: the override if one is set, else the derivation."""
    if DATA_VALID_THROUGH is not None:
        return DATA_VALID_THROUGH
    return schedule_valid_through(scn) if scn is not None else None


def clamp_horizon(dates: List[Any], scn: Any = None) -> List[Any]:
    """Cut a horizon back to the last day the inputs can be believed.

    Silently truncating is the wrong instinct in general, but here the
    alternative is worse: the model does not fail on bad days, it produces a
    confident schedule built on demand nobody stands behind, or an infeasibility
    whose real cause is three layers away from the error.

    With no scenario and no override there is nothing to measure the horizon
    against, so the dates pass through untouched. That is the honest answer -
    inventing a boundary would be worse than having none - and it is why every
    caller inside the optimizer passes `scn`.
    """
    through = valid_through(scn)
    if through is None:
        return list(dates)
    return [d for d in dates if d <= through]


#: A unit runs at most this many charge products in a day.
#:
#: Confirmed across all 366 days: MEK and ROSE never exceed one, the hydrotreater
#: reaches two on 9 days and extraction on 1, and the platformer runs exactly two
#: every day - though there the pair is a parallel cascade rather than a
#: changeover. Nothing anywhere runs three.
MAX_PRODUCTS_PER_DAY = 2

#: With at most two products in a day, at most two setup changes can occur: the
#: unit may switch off whatever it carried in without running it, then switch
#: again mid-day. Observed twice in the year.
MAX_CHANGEOVERS_PER_DAY = 2


def min_rate_fraction(unit: str, product: str) -> float:
    return MIN_RATE_FRACTION.get(unit, {}).get(
        product, DEFAULT_MIN_RATE_FRACTION)


def min_rate_bbl(unit: str, product: str) -> float:
    rate = max_rate(unit, product)
    return 0.0 if rate is None else rate * min_rate_fraction(unit, product)


def max_rate_info(unit: str, product: str,
                  line_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The charge ceiling, per line where the line is what carries it.

    Rates are normally a property of the unit and the feed. They are not when a
    unit runs the same feed in two modes: pass `line_key` and the line's own
    rate wins. Keyed on feed alone, extraction's deep mode inherits the shallow
    mode's 2,200 bbl/day and the optimizer schedules 69% more deep extract than
    the unit can make.
    """
    if line_key:
        entry = MAX_RATE_BY_LINE.get(line_key)
        if entry:
            return dict(entry)
    entry = MAX_RATE_BBL_PER_DAY.get(unit, {}).get(product)
    return dict(entry) if entry else None


def max_rate(unit: str, product: str) -> Optional[float]:
    info = max_rate_info(unit, product)
    return info["bbl"] if info else None


def switch_loss_days(unit: str) -> float:
    return SWITCH_LOSS_DAYS.get(unit, DEFAULT_SWITCH_LOSS_DAYS)


def reactor_of(unit: str, product: str) -> Optional[str]:
    spec = UNIT_REACTORS.get(unit)
    if not spec:
        return None
    for name, r in spec["reactors"].items():
        if product in r["charges"]:
            return name
    # Anything not named explicitly runs on the general reactor.
    return "R2" if "R2" in spec["reactors"] else None


def min_reactor_visit_days(unit: str, reactor: str) -> int:
    """Days a unit must stay on a reactor once it goes there.

    1 means no constraint - the same convention `min_run_days` uses, and the
    value that leaves the formulation untouched for every unit but the one that
    names a figure.
    """
    spec = UNIT_REACTORS.get(unit)
    if not spec:
        return 1
    return int((spec.get("min_visit_days") or {}).get(reactor, 1))


def changeover_loss_days(unit: str, frm: str, to: str) -> float:
    """Time lost moving a unit from one charge to another.

    A reactor crossing costs the flush on top of the ordinary switch, which is
    what makes the optimizer group 4315 and 4319 together without being told to.
    """
    if frm == to:
        return 0.0
    loss = switch_loss_days(unit)
    spec = UNIT_REACTORS.get(unit)
    if spec and reactor_of(unit, frm) != reactor_of(unit, to):
        loss += spec["flush_days"]
    return loss


#: $ per changeover, per unit - the cash twin of `changeover_loss_days`.
#:
#: One global figure cannot carry this. `DECIDED_NOT_MODELLED` prescribes the
#: calibration as "raise it per unit until campaign lengths match the ones the
#: plant actually runs", and the targets are a factor of forty apart: MEK 3 d
#: median, HYDRO 1 d, EXTRACT 3 d, ROSE 40 d. The number that gives ROSE its
#: campaigns would freeze the hydrotreater solid.
#:
#: `MIP-FORMULATION.md` section 6 specified this as `c_sw[u,(p,q)]` from the
#: start; the scalar in `OptimizerParams` was the shortcut, and it remains the
#: default for any unit not named here.
#:
#: **Empty on purpose.** Every entry is a claim about what a changeover costs
#: beyond its lost time, and none has been measured yet - the interface split
#: between material returned to charge and material genuinely slopped off differs
#: per charge product and was confirmed with operations only in the qualitative.
#: Fill it by running v1 and comparing campaign length against the medians above,
#: one unit at a time. Until then this is empty and the model prices exactly as
#: it did before, which is the honest answer rather than an invented one.
SWITCH_COST_BY_UNIT: Dict[str, float] = {}

#: $ per changeover on one specific transition, where the arc differs from the
#: rest of its unit. The reactor-crossing arcs are the reason this exists: they
#: already cost more *time* than an ordinary switch (`changeover_loss_days` adds
#: the flush), and the giveaway that time does not capture scales the same way.
#:
#: Keyed by workbook product pair, matching `changeover_loss_days`. That pairing
#: cannot separate extraction's two 9305 modes, which appear here as
#: `("9305", "9305")` - the one place this table is coarser than the arc set it
#: prices. Give the unit a figure rather than the arc if the modes need to
#: differ, or key this by line once anything actually needs to.
SWITCH_COST_BY_ARC: Dict[str, Dict[tuple, float]] = {}


def switch_cost_for(unit: str, frm: str, to: str, default: float = 0.0,
                    by_unit: Optional[Dict[str, float]] = None) -> float:
    """What a changeover costs in cash, beyond the time it already loses.

    Most specific wins, the same precedence `max_rate_info` and
    `min_run_days_for_line` use: the arc's own figure, then the unit's, then the
    global default. `by_unit` is the scenario's override - a planner calibrating
    against campaign lengths should not have to edit code - and outranks the
    committed table at the same specificity.

    **`frm == to` is not free here**, which is where this parts company with
    `changeover_loss_days`. Arcs run between distinct *lines*, so an arc always
    represents a real changeover - and extraction changes between normal and deep
    mode without changing feed, which is 9305 -> 9305 at product level and a
    physical changeover on the unit. Zeroing the self-pair would make that mode
    flip free and the model would flip it every day.
    """
    arc = SWITCH_COST_BY_ARC.get(unit, {}).get((frm, to))
    if arc is not None:
        return float(arc)
    if by_unit and unit in by_unit and by_unit[unit] is not None:
        return float(by_unit[unit])
    if unit in SWITCH_COST_BY_UNIT:
        return float(SWITCH_COST_BY_UNIT[unit])
    return float(default)


def net_rate_bbl(unit: str, product: str, time_fraction: float) -> Optional[float]:
    """Barrels obtainable from a product given the share of the day it gets.

    This is the whole rate model: `charge = max_rate x time`. Time lost to
    changeovers never appears here - it is subtracted from the day's budget
    before the remainder is shared out, which is what makes the net rate fall.
    """
    rate = max_rate(unit, product)
    return None if rate is None else rate * time_fraction


def day_time_budget(unit: str, changeovers: List[tuple]) -> float:
    """Time left for production after the day's changeovers.

    `changeovers` is a list of (from_product, to_product). Each costs the unit's
    interface loss, plus the reactor flush where the pair crosses reactors.
    """
    used = sum(changeover_loss_days(unit, a, b) for a, b in changeovers)
    return max(0.0, 1.0 - used)


#: Minimum campaign length in days, per unit and product, from observed minima.
#:
#: **A minimum, not a typical length.** ROSE's 4313 is the trap: the plan never
#: runs it for less than 20 days and its median campaign is 41, so the table looks
#: wrong at 3 and has been queried once already. It is not. Operations confirmed
#: the rule is *three days on 4555 (KENDEX 0897)*; 4313 is the c-stock, it carries
#: most of the unit's days, and it runs long because that is what the economics
#: ask for - not because anything forbids a short run.
#:
#: Raising it to the observed 20 would hard-code an outcome the model is supposed
#: to derive, and would forbid the short c-stock campaign the plant is free to run
#: whenever demand calls for one. Emergent behaviour does not belong in a
#: structural constraint. The same caution applies to every figure here: where the
#: observed minimum and the real rule disagree, the rule wins.
#:
#: ROSE runs essentially continuously and the absence of idle days in the plan
#: carries no information either way - it is not a constraint to reproduce.
MIN_RUN_DAYS: Dict[str, Dict[str, int]] = {
    "MEK": {"4317": 3, "9116": 2, "9117": 2, "9119": 1},
    "EXTRACT": {"9302": 2, "9303": 1, "9305": 3},
    #: 4555 is KENDEX 0897 - the workbook's charge line calls it "LR RESINS",
    #: which is product 9210's name and misleading here. 4313 is the c-stock.
    "ROSE": {"4313": 3, "4555": 3},
}


def ladder_arcs(ladder: List[str]) -> List[tuple]:
    """Adjacent pairs in both directions - the only transitions a ladder allows."""
    arcs = []
    for a, b in zip(ladder, ladder[1:]):
        arcs.append((a, b))
        arcs.append((b, a))
    return arcs


def allowed_arcs(unit: str, products: List[str],
                 resolve=None) -> Optional[List[tuple]]:
    """Allowed (from, to) changeovers, or None when the unit is unrestricted.

    The config is written in workbook product codes, because that is how
    operations names the grades. `resolve` maps those onto model products, which
    differ wherever a block pools a charge code with a sold code — the MEK
    charges 9116 but its inventory is tracked under 9718.
    """
    spec = TRANSITIONS.get(unit)
    if spec is None:
        return None
    r = resolve or (lambda c: c)
    if "ladder" in spec:
        ladder = [r(p) for p in spec["ladder"]]
        ladder = [p for p in ladder if p in products]
        return ladder_arcs(ladder)
    arcs = [(r(a), r(b)) for a, b in spec["arcs"]]
    return [(a, b) for a, b in arcs if a in products and b in products]


def line_arcs(unit: str, lines: List[Dict[str, Any]]) -> Optional[List[tuple]]:
    """Allowed changeovers between **charge lines**, the setup state from v1 on.

    A unit that runs one feed two ways has two setup states on one product, and
    a product-keyed arc set cannot say so: extraction's normal-to-deep switch is
    9305 -> 9305 at product level, a self-loop the formulation cannot represent.
    Where a line-level set is configured it wins; otherwise the product-level
    ladder is projected onto the lines that carry those products.

    Returns None when the unit is unrestricted, matching `allowed_arcs`.
    """
    keys = [l["key"] for l in lines]
    spec = TRANSITIONS_BY_LINE.get(unit)
    if spec is not None:
        return [(a, b) for a, b in spec["arcs"] if a in keys and b in keys]

    by_product: Dict[str, List[str]] = {}
    for l in lines:
        by_product.setdefault(l["workbook_product"], []).append(l["key"])
    product_arcs = allowed_arcs(unit, list(by_product))
    if product_arcs is None:
        # Unrestricted: every ordered pair of distinct lines.
        return [(a, b) for a in keys for b in keys if a != b]
    out = []
    for a, b in product_arcs:
        for ka in by_product.get(a, []):
            for kb in by_product.get(b, []):
                if ka != kb:
                    out.append((ka, kb))
    return out


def min_run_days_for_line(unit: str, line_key: str, product: str,
                          resolve=None) -> int:
    """Minimum campaign length for a line, falling back to its product's."""
    table = MIN_RUN_DAYS_BY_LINE.get(unit, {})
    if line_key in table:
        return table[line_key]
    return min_run_days(unit, product, resolve)


def min_run_days(unit: str, product: str, resolve=None) -> int:
    """Minimum campaign length, looked up through the same code translation."""
    r = resolve or (lambda c: c)
    table = MIN_RUN_DAYS.get(unit, {})
    for code, days in table.items():
        if r(code) == product:
            return days
    return 1


def aggregate_of(code: str) -> Optional[str]:
    """The aggregate a product belongs to, if any."""
    for agg in AGGREGATIONS:
        if code == agg.get("pool") or code in agg["members"]:
            return agg["id"]
    return None


def sink_products() -> Dict[str, str]:
    """Workbook product code -> sink id, for the sinks that have a block."""
    return {s["product"]: sid for sid, s in SINKS.items() if s.get("product")}


def is_sink(code: Optional[str]) -> bool:
    return code is not None and code in sink_products()


def is_retired(code: Optional[str] = None, unit: Optional[str] = None,
               line_key: Optional[str] = None) -> bool:
    if code and code in RETIRED["products"]:
        return True
    if unit and unit in RETIRED["units"]:
        return True
    if line_key and line_key in RETIRED["charge_lines"]:
        return True
    return False


def is_disposal(code: Optional[str]) -> bool:
    """False where unserved demand is a forgone sale rather than undisposed material."""
    return not (code and code in NOT_DISPOSAL)


def live_blocks(blocks: Any) -> Any:
    """The product blocks a planner should be shown.

    Retired blocks are hidden here rather than deleted, but not because
    anything depends on them - deleting 9202 and 4325 from the reference and
    re-simulating moves no row anywhere, 9118 included. The reason is what the
    engine is for: it reproduces the workbook cell for cell so the parity
    harness can compare against the workbook's own cached values, and a block
    the engine does not carry is a block parity stops checking.

    So the simplification belongs a layer up, which is where it already is:
    `modelprep` drops them from the model and this drops them from the screen.

    The near-miss worth remembering is that 9202's Sales row reads charge row
    121 directly - the same Sonneborn lift that is 9118's Production Out. When
    the double-count guard kept one global set of accounted-for lines, 9202
    claimed that line and 9118 stopped being drawn down at all, appearing to
    overflow by 707,233 gal. The guard is per block now, which is what makes
    these two blocks inert rather than load-bearing.
    """
    return [b for b in blocks if not is_retired(code=b.get("charge_code"))]


def live_charge_lines(lines: Any) -> Any:
    """The charge lines a planner should be shown, on the same terms.

    A retired line is one the optimizer already refuses to schedule, so leaving
    it on the grid offers an edit that the next run silently discards.
    """
    return [l for l in lines
            if not is_retired(code=l.get("code"), unit=l.get("unit"),
                              line_key=l.get("key"))]
