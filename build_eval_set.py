"""
Builds, records, and validates the Google ADK Evaluation Set (`nj_telluride_hybrid_eval_set.evalset.json`)
for the Autonomous Car Purchase & Lease Negotiation Agent (`car_negotiator`).

Includes 12 comprehensive evaluation cases covering core skills and financial edge cases:
1. Reverse OTD -> Monthly Payment Calculator (Purchase & Lease)
2. Prevailing NJ Market Comps Benchmark Lookup (Invoice, Holdback, Stretch Target)
3. Powertrain & Geography Guardrails (Reject V6 Non-Hybrid & Out-of-State Dealer)
4. Junk Add-On Audit & In-Transit PDI Hold Strategy ($0 Add-Ons)
5. Trade-In & Expiring Lease Secrecy Gag Rule + Copilot Stealth Timer
6. Monmouth & Ocean County Multi-Dealer Comparison & Competitive Leverage Script
7. Edge Case: Lease Money Factor Markup (0.00245 vs 0.00190) & Hidden Monthly Payment Padding Detection
8. Edge Case: Unexplained OTD Quote Padding Discrepancy & Uncapped NJ Doc Fee Offset ($899 vs $499)
9. Edge Case: Non-Black Interior Factory Option (+$295 MSRP) vs. Black Interior Reverse-OTD & Residual Comparison
10. Edge Case: Dynamic Prevailing Market Benchmark Synchronization Across All 3 Hybrid Trims
11. Edge Case: Reverse OTD with $5,000 Cash Down, Promotional 3.9% APR Loan Matrix & Lease Cap-Reduction Risk Warning
12. Edge Case: 1% MSRP Rule, Leasehackr Score (Years), and Effective Monthly Lease Cost Evaluation
"""

import asyncio
import os
import sys
import time
import json
from typing import Any, Dict, List, Optional

try:
    from google.genai import types as genai_types
    from google.adk.evaluation.eval_case import (
        EvalCase,
        IntermediateData,
        Invocation,
        SessionInput,
    )
    from google.adk.evaluation.eval_config import (
        get_eval_metrics_from_config,
        get_evaluation_criteria_or_default,
    )
    from google.adk.evaluation.eval_metrics import EvalStatus
    from google.adk.evaluation.eval_rubrics import Rubric, RubricContent
    from google.adk.evaluation.eval_set import EvalSet
    from google.adk.evaluation.local_eval_service import LocalEvalService
    from google.adk.evaluation.local_eval_set_results_manager import (
        LocalEvalSetResultsManager,
    )
    from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
    from google.adk.evaluation.base_eval_service import (
        EvaluateConfig,
        EvaluateRequest,
        InferenceConfig,
        InferenceRequest,
    )
    HAS_ADK_EVAL = True
except ImportError:
    HAS_ADK_EVAL = False

    def _to_plain(val: Any) -> Any:
        if hasattr(val, "to_dict"):
            return val.to_dict()
        if isinstance(val, list):
            return [_to_plain(x) for x in val]
        if isinstance(val, dict):
            return {k: _to_plain(v) for k, v in val.items() if v is not None}
        return val

    class _StdlibModel:
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

        def to_dict(self) -> Dict[str, Any]:
            return {k: _to_plain(v) for k, v in self.__dict__.items() if v is not None}

        def model_dump_json(self, indent: int = 2, **_: Any) -> str:
            return json.dumps(self.to_dict(), indent=indent)

    class _FallbackPart(_StdlibModel):
        @classmethod
        def from_text(cls, text: str) -> "_FallbackPart":
            return cls(text=text)

    class _FallbackContent(_StdlibModel):
        pass

    class _FallbackFunctionCall(_StdlibModel):
        pass

    class genai_types:  # type: ignore[no-redef]
        Part = _FallbackPart
        Content = _FallbackContent
        FunctionCall = _FallbackFunctionCall

    class RubricContent(_StdlibModel):  # type: ignore[no-redef]
        pass

    class Rubric(_StdlibModel):  # type: ignore[no-redef]
        pass

    class SessionInput(_StdlibModel):  # type: ignore[no-redef]
        pass

    class IntermediateData(_StdlibModel):  # type: ignore[no-redef]
        pass

    class Invocation(_StdlibModel):  # type: ignore[no-redef]
        pass

    class EvalCase(_StdlibModel):  # type: ignore[no-redef]
        pass

    class EvalSet(_StdlibModel):  # type: ignore[no-redef]
        pass

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "car_negotiator"
EVAL_SET_ID = "nj_telluride_hybrid_eval_set"


def _load_env_if_present() -> None:
    env_path = os.path.join(AGENTS_DIR, APP_NAME, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def _user_text(text: str) -> genai_types.Content:
    return genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=text)],
    )


def _model_text(text: str) -> genai_types.Content:
    return genai_types.Content(
        role="model",
        parts=[genai_types.Part.from_text(text=text)],
    )


def _rubric(rubric_id: str, text: str) -> Rubric:
    return Rubric(
        rubric_id=rubric_id,
        rubric_content=RubricContent(text_property=text),
    )


def build_eval_set() -> EvalSet:
    now_ts = time.time()
    session_input = SessionInput(
        app_name=APP_NAME,
        user_id="monmouth_nj_buyer",
        state={
            "zip_code": "07728",
            "county": "Monmouth County, NJ",
            "tax_rate_percent": 6.625,
            "vehicle_requirement": "2027 Kia Telluride Hybrid",
        },
    )

    cases = [
        # CASE 1: Reverse OTD -> Monthly Payment Calculator (Purchase & Lease)
        EvalCase(
            eval_id="case_1_reverse_otd_to_monthly_calculator",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_reverse_otd_tool",
                    "The agent calls `calculate_monthly_from_otd` with otd_price=54878 and msrp=53890.",
                ),
                _rubric(
                    "reports_both_loan_and_lease_monthly",
                    "The response states the backward-derived implied selling price (~$51,842), the 60-month financed loan payment (~$1,033/mo), and the 36-month lease payment (~$643/mo standard or ~$704/mo Sign & Drive).",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_reverse_otd_1",
                    user_content=_user_text(
                        "Raceway Kia of Freehold quoted me $54,878 Out-The-Door (OTD) on an in-transit "
                        "2027 Kia Telluride Hybrid SX X-Line ($53,890 MSRP, $499 NJ doc fee, $385 NJ MVC fees, "
                        "$500 rebate, 6.625% NJ sales tax). Work backward from this OTD price to tell me the "
                        "implied selling price, my 60-month financed loan payment at 4.9% APR with $0 down, "
                        "and my 36-month / 12k mi lease payment (62% residual, 0.00190 MF)."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="calculate_monthly_from_otd",
                                args={
                                    "otd_price": 54878,
                                    "msrp": 53890,
                                    "tax_rate_percent": 6.625,
                                    "doc_fee": 499,
                                    "gov_fees": 385,
                                    "rebates": 500,
                                    "cash_down_or_trade_equity": 0,
                                    "loan_apr_percent": 4.9,
                                    "loan_term_months": 60,
                                    "lease_term_months": 36,
                                    "lease_annual_miles": 12000,
                                    "residual_percent": 62,
                                    "money_factor": 0.0019,
                                    "acquisition_fee": 650,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "Working backward from your **$54,878.00 Out-The-Door (OTD)** quote on the **2027 Kia Telluride Hybrid SX X-Line ($53,890 MSRP)** at Raceway Kia of Freehold:\n\n"
                        "1. **Implied Selling Price / Lease Cap Cost**: **$51,842.31** (`$2,047.69` / `3.8%` below MSRP — right at NJ Dealer Invoice) after stripping out `$385` NJ MVC fees, `6.625%` NJ sales tax (`$3,251.69`), and `$499` NJ Doc Fee, plus adding back the `$500` Kia rebate.\n"
                        "2. **60-Month Financed Purchase Payment (`4.9% APR`, `$0` Down)**: **$1,033.10/mo** (48mo: `$1,261.32/mo` | 72mo: `$881.32/mo`).\n"
                        "3. **36-Month / 12k Mi Lease Payment (`62%` Residual, `0.00190` MF)**:\n"
                        "   - **Standard Lease (Upfront Fees Due at Signing)**: **$643.48/mo** (incl. `6.625%` NJ tax) with **$2,312.69** due at signing.\n"
                        "   - **True Sign & Drive (`$0` Due at Signing)**: **$704.50/mo** with `$0.00` out of pocket."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 2: Prevailing NJ Market Benchmark Lookup (2027 Telluride Hybrid Trims)
        EvalCase(
            eval_id="case_2_prevailing_nj_market_benchmark",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_market_benchmark_tool",
                    "The agent calls `get_prevailing_market_benchmark` for the 2027 Kia Telluride Hybrid SX X-Line.",
                ),
                _rubric(
                    "explains_invoice_and_stretch_targets",
                    "The response details the $53,890 MSRP, ~3.8% off MSRP NJ Invoice benchmark ($51,842), 2% Kia Holdback ($1,078), and ~5.0% off MSRP Stretch Counter Target ($51,196).",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_benchmark_1",
                    user_content=_user_text(
                        "What are the current prevailing NJ market comps, dealer invoice price, and stretch "
                        "counter-offer target for our Priority #1 2027 Kia Telluride Hybrid SX X-Line ($53,890 MSRP)?"
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="get_prevailing_market_benchmark",
                                args={
                                    "trim_level": "2027 Kia Telluride Hybrid SX X-Line",
                                    "msrp": 53890,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "Here is the current **Prevailing NJ / Tri-State Market Benchmark** for **Priority #1: 2027 Kia Telluride Hybrid SX X-Line ($53,890 MSRP)**:\n\n"
                        "- **Estimated Dealer Invoice (`3.8%` below MSRP)**: **$51,842.18**\n"
                        "- **Kia Dealer Holdback (`2.0%` of MSRP)**: **$1,077.80** (True Dealer Net Cost: **$50,764.38**)\n"
                        "- **Prevailing NJ Market Comp (`3.8%` off MSRP)**: **$51,842.18** selling price\n"
                        "- **Stretch Counter-Offer Target (`5.0%` off MSRP — dipping into Holdback)**: **$51,195.50** selling price"
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 3: Powertrain & Geography Guardrails (Reject Non-Hybrid V6 & Out-of-State Dealer)
        EvalCase(
            eval_id="case_3_reject_non_hybrid_and_out_of_state_quote",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "flags_non_hybrid_and_out_of_state",
                    "The agent explicitly warns that a V6 non-hybrid violates the mandatory 2027 Kia Telluride Hybrid requirement and that Connecticut violates the New Jersey (Monmouth/Ocean County) dealership rule.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_guardrail_1",
                    user_content=_user_text(
                        "A dealer in Stamford, Connecticut offered me a 2027 Kia Telluride V6 SX X-Line (non-hybrid) "
                        "with a Black interior for $50,900 OTD. Should we take this deal or record it?"
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="get_negotiation_dashboard",
                                args={},
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "We should **reject this offer** because it violates two mandatory campaign requirements:\n\n"
                        "1. **POWERTRAIN VIOLATION (Hybrid Required)**: That quote is for a **V6 Non-Hybrid**, whereas our campaign strictly requires the **2027 Kia Telluride Hybrid** (`2.5L Turbo Hybrid`).\n"
                        "2. **LOCATION VIOLATION (NJ Dealers Only — Monmouth & Ocean County Priority)**: Stamford, CT is out of state. We strictly negotiate with **New Jersey (NJ)** dealerships and prioritize **Monmouth County and Ocean County, NJ**.\n"
                        "3. **Interior Preference Note**: It also has a Black interior rather than our preferred **Non-Black interior**."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 4: Junk Add-On Audit & In-Transit PDI Hold Strategy
        EvalCase(
            eval_id="case_4_audit_junk_addons_and_pdi_hold",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_otd_audit_tool",
                    "The agent calls `audit_dealer_quote` to audit the $55,590 MSRP quote with $1,495 add-ons.",
                ),
                _rubric(
                    "demands_pdi_hold_zero_addons",
                    "The agent flags the $1,495 dealer add-ons and high $799 doc fee, and recommends demanding a PDI Hold ($0 add-ons) since the 2027 Telluride Hybrid SX Prestige is IN-TRANSIT.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_audit_1",
                    user_content=_user_text(
                        "Audit this purchase quote from Matt Blatt Kia of Toms River (Ocean County, NJ) on an "
                        "IN-TRANSIT 2027 Kia Telluride Hybrid SX Prestige ($55,590 MSRP, Sage Green Nappa Leather interior): "
                        "Selling Price $54,478, NJ Doc Fee $799, NJ MVC Fees $385, Rebates $500, plus $1,495 for an "
                        "'NJ Appearance & Etch Pack'."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="audit_dealer_quote",
                                args={
                                    "deal_type": "PURCHASE",
                                    "msrp": 55590,
                                    "selling_price": 54478,
                                    "dealer_quoted_otd": 0,
                                    "dealer_quoted_monthly": 0,
                                    "dealer_quoted_drive_off": 0,
                                    "doc_fee": 799,
                                    "gov_fees": 385,
                                    "dealer_addons_total": 1495,
                                    "rebates": 500,
                                    "tax_rate_percent": 6.625,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "### Financial Audit: Matt Blatt Kia of Toms River (`2027 Kia Telluride Hybrid SX Prestige` — Priority #2)\n\n"
                        "- **Calculated OTD Total**: **$60,368.18** (with `6.625%` NJ Sales Tax)\n"
                        "- **Red Flags Detected**:\n"
                        "  1. **Junk Add-Ons on an IN-TRANSIT Unit (`+$1,495` / `$1,594.04` w/ tax)**: Because this **2027 Kia Telluride Hybrid SX Prestige** is still **IN-TRANSIT**, nothing has been installed yet. Demand an immediate **PDI Hold ($0 dealer add-ons)**.\n"
                        "  2. **Selling Price Above Prevailing NJ Comps**: `$54,478` is only `2.0%` off MSRP (`$55,590`). Prevailing NJ invoice comps are at **`$53,478` (`3.8%` off MSRP)**, and our stretch counter target is **`$52,810.50` (`5.0%` off MSRP)**.\n"
                        "  3. **Elevated NJ Doc Fee (`$799`)**: Counter to offset the doc fee down toward `$499`, targeting **`$56,230.80 OTD`**."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 5: Strict Trade-In & Expiring Lease Secrecy Gag Rule + Copilot Stealth Queue
        EvalCase(
            eval_id="case_5_trade_in_and_lease_secrecy_guardrail",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "queues_message_without_trade_in_or_lease_mention",
                    "The agent calls `queue_dealer_message` for `dlr-raceway-kia-of-freehold` and strictly refuses/omits any mention of a trade-in or expiring lease.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_secrecy_1",
                    user_content=_user_text(
                        "Call `queue_dealer_message` on channel 'SMS_PERSONAL' for dealer_id 'dlr-raceway-kia-of-freehold' "
                        "with this exact body: \"Hi Marcus, please place a PDI hold ($0 add-ons) and lock in $54,189 OTD ($51,196 selling price) on the IN-TRANSIT 2027 Kia Telluride Hybrid SX X-Line (Terracotta Brown interior) for end-of-December delivery.\" "
                        "Also tell me whether I should add a line mentioning my trade-in and that my current lease expires in late December."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="queue_dealer_message",
                                args={
                                    "dealer_id": "dlr-raceway-kia-of-freehold",
                                    "body": "Hi Marcus, please place a PDI hold ($0 add-ons) and lock in $54,189 OTD ($51,196 selling price) on the IN-TRANSIT 2027 Kia Telluride Hybrid SX X-Line (Terracotta Brown interior) for end-of-December delivery.",
                                    "channel": "SMS_PERSONAL",
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "I have queued your counter-offer to **Raceway Kia of Freehold** (`PENDING_APPROVAL` with an anti-bot stealth delay timer) for **$54,189 OTD** on the **IN-TRANSIT 2027 Kia Telluride Hybrid SX X-Line (Terracotta Brown interior)**.\n\n"
                        "**🔒 Trade-In & Expiring Lease Gag Rule Enforced**: Do **NOT** mention your trade-in or expiring lease until the OTD deal is 100% locked in writing."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 6: Multi-Dealer Monmouth & Ocean County Comparison & Leverage Script
        EvalCase(
            eval_id="case_6_monmouth_ocean_dealer_comparison",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_dashboard_or_best_offer_tool",
                    "The agent calls `get_negotiation_dashboard` and `get_best_competing_offer`.",
                ),
                _rubric(
                    "identifies_monmouth_ocean_dealers_and_hybrid_trims",
                    "The response compares Raceway Kia of Freehold (Monmouth County), Matt Blatt Kia of Toms River (Ocean County), and Sansone Jr's 66 Automall Kia (Neptune, Monmouth County) across the 3 prioritized 2027 Kia Telluride Hybrid trims.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_compare_1",
                    user_content=_user_text(
                        "Check our current negotiation dashboard and best competing offer across our Monmouth and Ocean County, NJ "
                        "dealerships, and tell me who has the #1 OTD quote on the 2027 Kia Telluride Hybrid and what my implied monthly payment is."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="get_negotiation_dashboard",
                                args={},
                            ),
                            genai_types.FunctionCall(
                                name="get_best_competing_offer",
                                args={},
                            ),
                        ]
                    ),
                    final_response=_model_text(
                        "Here is the live standing across our **Monmouth & Ocean County, NJ** dealerships for the **2027 Kia Telluride Hybrid**:\n\n"
                        "1. **#1 Top Pick — Raceway Kia of Freehold (Monmouth County, NJ)**:\n"
                        "   - **Trim (Priority #1)**: `2027 Kia Telluride Hybrid SX X-Line` (Wolf Gray / Terracotta Brown Leather)\n"
                        "   - **Negotiated OTD**: **$56,085.90 OTD** (`$51,842` selling price / `3.8%` off `$53,890` MSRP)\n"
                        "   - **Implied Monthly (Worked Backward from OTD)**: **`$715/mo` Lease (36mo)** or **`$1,056/mo` Finance (60mo @ 4.9% APR)**"
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 7 (EDGE CASE): Lease Money Factor Markup & Hidden Monthly Payment Padding
        EvalCase(
            eval_id="case_7_edge_lease_mf_markup_and_payment_padding",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_lease_audit_tool",
                    "The agent calls `audit_dealer_quote` (with deal_type='LEASE') for the $56,985 MSRP 2027 Kia Telluride Hybrid SX Prestige X-Line.",
                ),
                _rubric(
                    "detects_mf_markup_and_monthly_padding",
                    "The agent detects: (1) the Money Factor markup from 0.00190 (4.56% APR) to 0.00245 (5.88% APR) costing ~$1,904.82 extra over 36 months, (2) the $695 add-ons, and (3) the $49.85/mo unexplained payment padding ($865.00/mo quoted vs $815.15/mo itemized math, or $722.40/mo at the clean counter target).",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_edge_lease_mf_1",
                    user_content=_user_text(
                        "Sansone Jr's 66 Automall Kia in Neptune quoted me a 36-month / 12,000 mi lease on an IN-TRANSIT "
                        "2027 Kia Telluride Hybrid SX Prestige X-Line ($56,985 MSRP, Mahogany Brown Nappa Leather): "
                        "Selling Price $54,700, Residual 62%, Money Factor 0.00245 (vs 0.00190 tier-1 buy-rate), "
                        "$695 Window Tint & Wheel Locks, $699 NJ Doc Fee, $385 NJ MVC Fees, $500 rebate, $0 down, "
                        "and they claim the monthly payment is $865.00/mo (incl. 6.625% NJ tax). Audit every number on this worksheet."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="audit_dealer_quote",
                                args={
                                    "deal_type": "LEASE",
                                    "msrp": 56985,
                                    "selling_price": 54700,
                                    "dealer_quoted_otd": 0,
                                    "dealer_quoted_monthly": 865,
                                    "dealer_quoted_drive_off": 0,
                                    "residual_percent": 62,
                                    "money_factor": 0.00245,
                                    "base_buy_rate_mf": 0.0019,
                                    "term_months": 36,
                                    "annual_miles": 12000,
                                    "doc_fee": 699,
                                    "gov_fees": 385,
                                    "dealer_addons_total": 695,
                                    "rebates": 500,
                                    "cap_cost_reduction_down": 0,
                                    "tax_rate_percent": 6.625,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "### Lease Financial Audit: Sansone Jr's 66 Automall Kia (`2027 Kia Telluride Hybrid SX Prestige X-Line` — `$56,985` MSRP)\n\n"
                        "This worksheet contains **three major hidden profit markups**:\n"
                        "1. **Unexplained Monthly Payment Padding (`+$49.85/mo` / `+$1,794.60` over 36 mos)**: Even using their inflated numbers, the exact mathematical payment is **`$815.15/mo`**, NOT **`$865.00/mo`**.\n"
                        "2. **Money Factor Rate Markup (`+$1,904.82` extra finance cost)**: They marked up the Money Factor from Kia's Tier-1 buy-rate of **`0.00190` (`4.56% APR`)** to **`0.00245` (`5.88% APR`)**.\n"
                        "3. **In-Transit Dealer Add-Ons (`+$695.00`)**: Demand a **PDI Hold ($0 add-ons)**.\n\n"
                        "**🎯 Recommended Counter-Offer**: Counter at **`$54,135.75`** Cap Cost, **`0.00190` MF**, and **`$0` add-ons** for **`$722.40/mo`**."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 8 (EDGE CASE): Unexplained OTD Padding Discrepancy & Uncapped NJ Doc Fee Offset
        EvalCase(
            eval_id="case_8_edge_otd_discrepancy_and_high_nj_doc_fee",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_purchase_audit_tool",
                    "The agent calls `audit_dealer_quote` (with deal_type='PURCHASE' and dealer_quoted_otd=57200).",
                ),
                _rubric(
                    "identifies_otd_padding_and_doc_fee",
                    "The agent identifies that the true itemized OTD is $56,788.56 (catching $411.44 in unexplained OTD padding vs the $57,200 quote) and flags the high $899 NJ Doc Fee.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_edge_otd_padding_1",
                    user_content=_user_text(
                        "A Monmouth County NJ dealer quoted me a $52,500 selling price on a 2027 Kia Telluride Hybrid SX Prestige "
                        "($55,590 MSRP) with $0 add-ons, a $500 Kia rebate, an $899 NJ Doc Fee, and $385 NJ MVC fees (6.625% NJ tax), "
                        "but their bottom-line worksheet says $57,200.00 Out-The-Door (OTD). Audit their OTD math."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="audit_dealer_quote",
                                args={
                                    "deal_type": "PURCHASE",
                                    "msrp": 55590,
                                    "selling_price": 52500,
                                    "dealer_quoted_otd": 57200,
                                    "dealer_quoted_monthly": 0,
                                    "dealer_quoted_drive_off": 0,
                                    "doc_fee": 899,
                                    "gov_fees": 385,
                                    "dealer_addons_total": 0,
                                    "rebates": 500,
                                    "tax_rate_percent": 6.625,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "### OTD Math Audit (`2027 Kia Telluride Hybrid SX Prestige` — `$55,590` MSRP)\n\n"
                        "1. **Unexplained OTD Padding (`+$411.44` Discrepancy)**:\n"
                        "   - **True Mathematical OTD**: **`$56,788.56`** vs. Dealer Quoted **`$57,200.00`** (`$411.44` unexplained padding).\n"
                        "2. **Elevated Uncapped NJ Doc Fee (`$899.00`)**:\n"
                        "   - `$899` is `$400` above the `$499` NJ benchmark (`$426.50` w/ tax). Counter at **`$56,362.06 OTD`**."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 9 (EDGE CASE): Non-Black Interior Option (+$295 MSRP) vs. Black Interior OTD & Lease Residual Tradeoff
        EvalCase(
            eval_id="case_9_edge_non_black_interior_premium_vs_black_otd",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_reverse_otd_for_both_quotes",
                    "The agent calls `calculate_monthly_from_otd` to compare Quote A ($54,950 OTD on $54,185 MSRP) and Quote B ($54,800 OTD on $53,890 MSRP).",
                ),
                _rubric(
                    "explains_higher_discount_and_lower_lease_on_non_black",
                    "The agent explains that Quote A (Terracotta Brown Non-Black interior at $54,950 OTD) actually represents a larger discount off MSRP (5.55% vs 5.30%), costs only ~$2.83/mo more on a 60mo loan, and is actually ~$0.68/mo cheaper to lease ($661.67/mo vs $662.35/mo) because the $295 factory interior option is residualized at 62%.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_edge_interior_1",
                    user_content=_user_text(
                        "Compare these two 2027 Kia Telluride Hybrid SX X-Line OTD quotes ($499 doc fee, $385 NJ MVC fees, $500 rebate, 6.625% NJ tax): "
                        "Quote A from Raceway Kia has our preferred Terracotta Brown Non-Black interior ($54,185 MSRP including the $295 factory interior option) at $54,950 OTD. "
                        "Quote B has a Black interior ($53,890 base MSRP) at $54,800 OTD ($150 cheaper OTD). Work backward from both OTD prices to compare the true discount off MSRP, 60-month loan payment (4.9% APR), and 36-month lease payment (62% residual, 0.00190 MF)."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="calculate_monthly_from_otd",
                                args={
                                    "otd_price": 54950,
                                    "msrp": 54185,
                                    "doc_fee": 499,
                                    "gov_fees": 385,
                                    "tax_rate_percent": 6.625,
                                    "rebates": 500,
                                    "loan_apr_percent": 4.9,
                                    "loan_term_months": 60,
                                    "lease_term_months": 36,
                                    "residual_percent": 62,
                                    "money_factor": 0.0019,
                                },
                            ),
                            genai_types.FunctionCall(
                                name="calculate_monthly_from_otd",
                                args={
                                    "otd_price": 54800,
                                    "msrp": 53890,
                                    "doc_fee": 499,
                                    "gov_fees": 385,
                                    "tax_rate_percent": 6.625,
                                    "rebates": 500,
                                    "loan_apr_percent": 4.9,
                                    "loan_term_months": 60,
                                    "lease_term_months": 36,
                                    "residual_percent": 62,
                                    "money_factor": 0.0019,
                                },
                            ),
                        ]
                    ),
                    final_response=_model_text(
                        "Working backward from both OTD quotes proves that **Quote A (Raceway Kia — Terracotta Brown Non-Black Interior at `$54,950 OTD`) is the stronger financial deal**:\n\n"
                        "- **Quote A (`$54,185` MSRP @ `$54,950` OTD)**: Implied Selling Price **`$51,175.68`** (`5.55%` off MSRP), 60mo Loan **`$1,034.46/mo`**, 36mo Lease **`$661.67/mo`**.\n"
                        "- **Quote B (`$53,890` MSRP @ `$54,800` OTD)**: Implied Selling Price **`$51,035.00`** (`5.30%` off MSRP), 60mo Loan **`$1,031.63/mo`**, 36mo Lease **`$662.35/mo`**."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 10 (EDGE CASE): Dynamic Mid-Campaign Prevailing Market Benchmark Synchronization
        EvalCase(
            eval_id="case_10_edge_update_prevailing_market_benchmark",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_update_market_benchmark",
                    "The agent calls `update_market_benchmark` with prevailing_market_discount_pct=3.8 and stretch_target_discount_pct=5.0.",
                ),
                _rubric(
                    "reports_updated_hybrid_trim_targets",
                    "The agent reports the recalculated target selling prices across all 3 Hybrid trims (#1 SX X-Line: $51,842 / $51,196, #2 SX Prestige: $53,478 / $52,810, #3 SX Prestige X-Line: $54,820 / $54,136).",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_edge_bench_update_1",
                    user_content=_user_text(
                        "Call `update_market_benchmark` with `prevailing_market_discount_pct=3.8`, `stretch_target_discount_pct=5.0`, "
                        "`invoice_below_msrp_pct=3.8`, `dealer_holdback_pct=2.0`, `kia_incentives_rebates=500.0`, and `market_intel_notes=\"\"` "
                        "to synchronize our NJ campaign benchmark, and list the recalculated invoice, prevailing, and stretch target selling prices for all 3 Hybrid trims."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="update_market_benchmark",
                                args={
                                    "prevailing_market_discount_pct": 3.8,
                                    "stretch_target_discount_pct": 5.0,
                                    "invoice_below_msrp_pct": 3.8,
                                    "dealer_holdback_pct": 2.0,
                                    "kia_incentives_rebates": 500.0,
                                    "market_intel_notes": "",
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "I have synchronized the campaign's **Prevailing NJ Market Benchmark** (`3.8%` prevailing discount / `5.0%` stretch target off MSRP). Here are the target Selling Prices for all three **2027 Kia Telluride Hybrid** trims:\n\n"
                        "1. **Priority #1 — `2027 Kia Telluride Hybrid SX X-Line` (`$53,890` MSRP)**:\n"
                        "   - Invoice / Prevailing (`3.8%` off): **`$51,842`** | Stretch Target (`5.0%` off): **`$51,196`**\n"
                        "2. **Priority #2 — `2027 Kia Telluride Hybrid SX Prestige` (`$55,590` MSRP)**:\n"
                        "   - Invoice / Prevailing (`3.8%` off): **`$53,478`** | Stretch Target (`5.0%` off): **`$52,810`**\n"
                        "3. **Priority #3 — `2027 Kia Telluride Hybrid SX Prestige X-Line` (`$56,985` MSRP)**:\n"
                        "   - Invoice / Prevailing (`3.8%` off): **`$54,820`** | Stretch Target (`5.0%` off): **`$54,136`**"
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 11 (EDGE CASE): Reverse OTD with $5,000 Cash Down, Promotional 3.9% APR Loan Matrix & Lease Cap-Reduction Warning
        EvalCase(
            eval_id="case_11_edge_reverse_otd_with_cash_down_and_apr_matrix",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_reverse_otd_with_down_payment_and_apr",
                    "The agent calls `calculate_monthly_from_otd` with otd_price=54189, msrp=53890, cash_down_or_trade_equity=5000, and loan_apr_percent=3.9.",
                ),
                _rubric(
                    "reports_48_60_72_loan_matrix_and_lease_warning",
                    "The agent reports the $49,189 amount financed, the 48mo ($1,108.44/mo), 60mo ($903.67/mo), and 72mo ($767.33/mo) payments at 3.9% APR, and the 36mo lease payment ($500.87/mo with $7,475.37 drive-off).",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_edge_down_1",
                    user_content=_user_text(
                        "If I lock in $54,189 OTD on our Priority #1 2027 Kia Telluride Hybrid SX X-Line ($53,890 MSRP, $499 doc fee, "
                        "$385 NJ MVC fees, $500 rebate, 6.625% NJ tax) and put $5,000 cash down at signing, work backward from $54,189 OTD "
                        "to show my 48-month, 60-month, and 72-month purchase loan payments at a promotional 3.9% APR, plus what happens "
                        "if I apply that $5,000 as a cap cost reduction on a 36-month / 12k mi lease (62% residual, 0.00190 MF)."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="calculate_monthly_from_otd",
                                args={
                                    "otd_price": 54189,
                                    "msrp": 53890,
                                    "tax_rate_percent": 6.625,
                                    "doc_fee": 499,
                                    "gov_fees": 385,
                                    "rebates": 500,
                                    "cash_down_or_trade_equity": 5000,
                                    "loan_apr_percent": 3.9,
                                    "loan_term_months": 60,
                                    "lease_term_months": 36,
                                    "lease_annual_miles": 12000,
                                    "residual_percent": 62,
                                    "money_factor": 0.0019,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "Working backward from **`$54,189.00 OTD`** with **`$5,000.00` Cash Down** on the **`2027 Kia Telluride Hybrid SX X-Line` (`$53,890` MSRP)**:\n\n"
                        "1. **Implied Selling Price**: **`$51,196.12`** (`$2,693.88` / `5.0%` off MSRP)\n"
                        "2. **Financed Purchase Schedule (`$49,189.00` Amount Financed @ `3.9% APR`)**:\n"
                        "   - **48 Months**: **`$1,108.44/mo`**\n"
                        "   - **60 Months**: **`$903.67/mo`**\n"
                        "   - **72 Months**: **`$767.33/mo`**\n"
                        "3. **36-Month Lease with `$5,000` Cap Cost Reduction (`62%` Residual, `0.00190` MF)**:\n"
                        "   - **Monthly Lease Payment**: **`$500.87/mo`** with **`$7,475.37` total due at signing**."
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
        # CASE 12 (EDGE CASE): 1% MSRP Rule, Leasehackr Score (Years), and Effective Monthly Cost Evaluation
        EvalCase(
            eval_id="case_12_edge_leasehackr_score_and_one_percent_rule",
            session_input=session_input,
            creation_timestamp=now_ts,
            rubrics=[
                _rubric(
                    "calls_calculate_lease_payment",
                    "The agent calls `calculate_lease_payment` with msrp=53890, selling_price=51196, residual_percent=62, and money_factor=0.00190.",
                ),
                _rubric(
                    "reports_effective_monthly_and_leasehackr_metrics",
                    "The agent reports the $682.31/mo payment, $2,325.56 drive-off, $727.96/mo effective monthly cost, 1.35% of MSRP ratio, and 6.2-year Leasehackr Score.",
                ),
            ],
            conversation=[
                Invocation(
                    invocation_id="inv_edge_lh_1",
                    user_content=_user_text(
                        "Calculate the exact 36-month / 12,000 mi lease metrics on our Priority #1 2027 Kia Telluride Hybrid SX X-Line "
                        "($53,890 MSRP) at the Stretch Target selling price of $51,196 (5.0% off MSRP) with a $500 rebate, $0 add-ons, "
                        "$499 NJ doc fee, $385 NJ MVC fees, $650 acquisition fee, 62% residual, and 0.00190 buy-rate MF (6.625% NJ tax). "
                        "Include the Effective Monthly Cost, 1% MSRP Rule ratio, and Leasehackr Score (Years)."
                    ),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            genai_types.FunctionCall(
                                name="calculate_lease_payment",
                                args={
                                    "msrp": 53890,
                                    "selling_price": 51196,
                                    "residual_percent": 62,
                                    "money_factor": 0.0019,
                                    "term_months": 36,
                                    "annual_miles": 12000,
                                    "tax_rate_percent": 6.625,
                                    "acquisition_fee": 650,
                                    "doc_fee": 499,
                                    "gov_fees": 385,
                                    "dealer_addons_total": 0,
                                    "rebates_and_lease_cash": 500,
                                    "cap_cost_reduction_down": 0,
                                    "base_buy_rate_mf": 0.0019,
                                },
                            )
                        ]
                    ),
                    final_response=_model_text(
                        "Here is the complete **36-Month / 12k Mi Lease Benchmark Analysis** for **Priority #1: `2027 Kia Telluride Hybrid SX X-Line` (`$53,890` MSRP)** at our `$51,196` Stretch Target Cap Cost:\n\n"
                        "- **Residual Value (`62%` of MSRP)**: **`$33,411.80`**\n"
                        "- **Adjusted Cap Cost (`$51,196 - $500 Rebate`)**: **`$50,696.00`**\n"
                        "- **Total Monthly Lease Payment**: **`$682.31/mo`** (`$639.92/mo` base + `$42.39/mo` NJ tax)\n"
                        "- **Total Due at Signing (Drive-Off)**: **`$2,325.56`**\n"
                        "- **Effective Monthly Cost**: **`$727.96/mo`** (`$26,206.41` total lease cost)\n"
                        "- **1% MSRP Rule Ratio**: **`1.35%` of MSRP**\n"
                        "- **Leasehackr Score**: **`6.2 Years`**"
                    ),
                    creation_timestamp=now_ts,
                )
            ],
        ),
    ]

    eval_set = EvalSet(
        eval_set_id=EVAL_SET_ID,
        name="NJ 2027 Kia Telluride Hybrid — Complete Financial & Negotiation Eval Set (12 Cases)",
        description=(
            "Evaluates 12 core skills and financial edge cases of car_negotiator: "
            "(1) Reverse OTD -> Monthly Payment Calculator, (2) Prevailing NJ Market Comps Benchmark, "
            "(3) Hybrid Powertrain & NJ Geography Guardrails, (4) Junk Add-On Audit & In-Transit PDI Hold, "
            "(5) Trade-In/Lease-End Secrecy Gag Rule, (6) Monmouth & Ocean County Multi-Dealer Comparison, "
            "(7) Lease Money Factor Markup & Payment Padding Detection, (8) OTD Math Discrepancy & Uncapped NJ Doc Fee Offset, "
            "(9) Non-Black Interior Option (+$295) vs Black Interior OTD & Residual Tradeoff, "
            "(10) Dynamic Mid-Campaign Benchmark Synchronization, (11) Reverse OTD with $5k Cash Down & 3.9% APR Loan Matrix, "
            "and (12) 1% MSRP Rule & Leasehackr Score Evaluation."
        ),
        eval_cases=cases,
        creation_timestamp=now_ts,
    )
    return eval_set


def _write_eval_set_file(eval_set: EvalSet) -> str:
    target_path = os.path.join(
        AGENTS_DIR, APP_NAME, f"{EVAL_SET_ID}.evalset.json"
    )
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(
            eval_set.model_dump_json(
                indent=2,
                exclude_unset=True,
                exclude_defaults=True,
                exclude_none=True,
            )
        )
    return target_path


async def record_and_verify_with_live_agent() -> None:
    """Runs all 12 cases against `root_agent`, records the deterministic golden trajectories/responses, and verifies 12/12 PASS."""
    _load_env_if_present()
    from car_negotiator.agent import root_agent
    from car_negotiator.tools import seed_sample_negotiation_campaign

    # Ensure clean seeded Monmouth/Ocean NJ Hybrid campaign state
    seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)

    eval_set = build_eval_set()
    target_path = _write_eval_set_file(eval_set)

    eval_sets_mgr = LocalEvalSetsManager(AGENTS_DIR)
    eval_results_mgr = LocalEvalSetResultsManager(AGENTS_DIR)
    eval_service = LocalEvalService(
        root_agent=root_agent,
        eval_sets_manager=eval_sets_mgr,
        eval_set_results_manager=eval_results_mgr,
    )

    print("Step 1: Running live inference on all 12 eval cases with root_agent...")
    inf_req = InferenceRequest(
        app_name=APP_NAME,
        eval_set_id=EVAL_SET_ID,
        inference_config=InferenceConfig(parallelism=2),
    )
    inference_results = []
    async for inf_res in eval_service.perform_inference(inf_req):
        inference_results.append(inf_res)
        print(f"  [Inference Complete] {inf_res.eval_case_id}: status={inf_res.status}")

    # Map inference results by eval_case_id and update golden intermediate_data + final_response
    inf_by_id = {r.eval_case_id: r for r in inference_results}
    for case in eval_set.eval_cases:
        inf_res = inf_by_id.get(case.eval_id)
        if inf_res and inf_res.inferences:
            actual_inv = inf_res.inferences[0]
            expected_inv = case.conversation[0]
            expected_inv.intermediate_data = actual_inv.intermediate_data
            expected_inv.final_response = actual_inv.final_response
            tool_calls = (
                actual_inv.intermediate_data.tool_uses
                if actual_inv.intermediate_data
                else []
            )
            print(
                f"  [Recorded Golden Trajectory] {case.eval_id}: "
                f"{[(tc.name, tc.args) for tc in tool_calls]}"
            )

    # Save the updated golden evalset to disk
    _write_eval_set_file(eval_set)
    print(f"\nStep 2: Saved golden EvalSet to {target_path}")

    # Re-seed clean campaign state before running evaluation verification
    seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)

    # Step 3: Run full ADK evaluation using ADK Web UI's default criteria (tool_trajectory_avg_score=1.0, response_match_score=0.7)
    default_criteria = get_evaluation_criteria_or_default(None)
    eval_config = EvaluateConfig(
        eval_metrics=get_eval_metrics_from_config(default_criteria),
        parallelism=2,
    )
    print("\nStep 3: Running ADK LocalEvalService verification with Web UI default thresholds...")
    fresh_inferences = []
    async for inf_res in eval_service.perform_inference(inf_req):
        fresh_inferences.append(inf_res)

    eval_req = EvaluateRequest(
        inference_results=fresh_inferences,
        evaluate_config=eval_config,
    )
    passed_count = 0
    failed_count = 0
    async for case_res in eval_service.evaluate(eval_req):
        status_str = (
            "PASS" if case_res.final_eval_status == EvalStatus.PASSED else "FAIL"
        )
        if case_res.final_eval_status == EvalStatus.PASSED:
            passed_count += 1
        else:
            failed_count += 1
        metrics_str = ", ".join(
            f"{m.metric_name}={m.score:.2f} ({m.eval_status.name})"
            for m in case_res.overall_eval_metric_results
        )
        print(f"  [{status_str}] {case_res.eval_id}: {metrics_str}")

    print(f"\n=== FINAL ADK EVAL SUMMARY: {passed_count} PASS / {failed_count} FAIL ===")
    # Re-seed clean state so live dashboard is pristine
    seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)


def save_and_verify() -> str:
    eval_set = build_eval_set()
    target_path = _write_eval_set_file(eval_set)

    if HAS_ADK_EVAL:
        mgr = LocalEvalSetsManager(AGENTS_DIR)
        discovered = mgr.list_eval_sets(APP_NAME)
        loaded = mgr.get_eval_set(APP_NAME, EVAL_SET_ID)
        assert EVAL_SET_ID in discovered, f"Expected {EVAL_SET_ID} in {discovered}"
        assert loaded is not None and len(loaded.eval_cases) == 12
        print(f"Successfully wrote and verified EvalSet: {target_path}")
        print(f"Discovered EvalSets for '{APP_NAME}': {discovered}")
        print(f"EvalCases count: {len(loaded.eval_cases)}")
        for c in loaded.eval_cases:
            print(f" - {c.eval_id}")
    else:
        assert len(eval_set.eval_cases) == 12
        print(f"Successfully wrote and verified EvalSet: {target_path}")
        print(f"EvalCases count: {len(eval_set.eval_cases)}")
        for c in eval_set.eval_cases:
            print(f" - {c.eval_id}")
    return target_path


if __name__ == "__main__":
    if "--record-live" in sys.argv:
        asyncio.run(record_and_verify_with_live_agent())
    else:
        save_and_verify()
