"""
Autonomous Car Purchase & Lease Negotiation Agent (Google ADK).
Configured for Monmouth County, NJ Buyer's 2027 Kia Telluride Hybrid Campaign:
- Strictly NJ Dealerships Only, prioritizing Monmouth & Ocean Counties, NJ (6.625% NJ Sales Tax)
- Powertrain Requirement: Strictly 2027 Kia Telluride Hybrid (Hybrid is a mandatory requirement)
- Trim Priority Order: (1) 2027 Kia Telluride Hybrid SX X-Line ($53,890), (2) 2027 Kia Telluride Hybrid SX Prestige ($55,590), (3) 2027 Kia Telluride Hybrid SX Prestige X-Line ($56,985)
- Interior Preference: Non-Black Interior Preferred (Terracotta, Sage Green, Mahogany; note it adds ~$295 factory option to MSRP, so Black interior is still acceptable if the OTD deal is stronger)
- Reverse OTD-to-Monthly Strategy: Agent negotiates strictly on Out-The-Door (OTD) price with dealers, and works backward from the OTD price (`calculate_monthly_from_otd`) to show the buyer/lessor their exact monthly Financed Purchase or Lease payment.
- Prevailing Market Comps Engine: Uses live Google Search (`market_deals_search_tool`) + `get_prevailing_market_benchmark` / `update_market_benchmark`
  to anchor Selling Price vs. MSRP to real-world NJ/Tri-State deals (Invoice, 2% Kia Holdback, Leasehackr, Reddit r/KiaTelluride, Edmunds)
- Inventory Strategy: Seek IN-TRANSIT factory allocations ($0 pre-installed dealer add-ons) for End of December delivery
- Trade-In Secrecy: Strictly prohibits any mention of trade-in or current lease expiration until deal is locked in writing
- Hybrid Multi-Agent Coordinator: Combines direct tools on `root_agent` with specialized sub-agents
  (`inventory_research_agent`, `financial_auditor_agent`, `negotiation_comms_agent`) and a deterministic
  `SequentialAgent` workflow (`deal_audit_and_counter_pipeline`).
- Operates in Copilot Approval Mode with Anti-Bot Variable Wait Time Jitter, Guardrail Callbacks,
  OpenTelemetry Tracing, and Firestore Live Dashboard Sync.
"""

from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Dict

try:
    from google.genai import types
    from google.adk.agents import LlmAgent, SequentialAgent
    from google.adk.tools.google_search_agent_tool import (
        GoogleSearchAgentTool,
        create_google_search_agent,
    )
except ImportError:
    types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs)
    )

    class LlmAgent:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class SequentialAgent:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def create_google_search_agent(model: str = ""):
        return SimpleNamespace(model=model)

    def GoogleSearchAgentTool(agent=None):
        return SimpleNamespace(agent=agent, name="market_deals_search_tool")

try:
    from google.adk.apps import App
except ImportError:
    class App:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if __package__:
    from .tools import (
        DEFAULT_SAFETY_SETTINGS,
        after_model_self_eval_callback,
        after_tool_guardrail_callback,
        approve_queued_message,
        audit_dealer_quote as _audit_dealer_quote,
        audit_uploaded_lease_sheet,
        before_model_guardrail_callback,
        before_tool_guardrail_callback,
        calculate_color_parity_benchmark,
        calculate_lease_payment as _calculate_lease_payment,
        calculate_monthly_from_otd as _calculate_monthly_from_otd,
        calculate_nj_payments,
        calculate_otd_price,
        calculate_trade_in_impact,
        create_or_update_campaign,
        dealer_initiated_accounting_jargon,
        dispatch_ready_messages,
        generate_natural_buyer_counter,
        get_best_competing_offer,
        get_dealer_conversation_thread,
        get_live_comms_configuration_status,
        get_negotiation_dashboard,
        get_prevailing_market_benchmark as _get_prevailing_market_benchmark,
        ingest_live_dealer_message,
        instrument_tool,
        parse_and_log_dealer_reply,
        poll_personal_gmail_inbox,
        queue_dealer_message as _queue_dealer_message,
        record_dealer_quote,
        run_autonomous_multi_dealer_negotiation,
        scrape_monmouth_ocean_kia_inventory,
        search_negotiation_memory,
        seed_sample_negotiation_campaign,
        set_dealer_trade_in_gate,
        set_transport_mode,
        simulate_dealer_reply,
        update_dealer_contact_info,
        update_market_benchmark as _update_market_benchmark,
        upsert_dealer_lead,
    )
else:
    from tools import (
        DEFAULT_SAFETY_SETTINGS,
        after_model_self_eval_callback,
        after_tool_guardrail_callback,
        approve_queued_message,
        audit_dealer_quote as _audit_dealer_quote,
        audit_uploaded_lease_sheet,
        before_model_guardrail_callback,
        before_tool_guardrail_callback,
        calculate_color_parity_benchmark,
        calculate_lease_payment as _calculate_lease_payment,
        calculate_monthly_from_otd as _calculate_monthly_from_otd,
        calculate_nj_payments,
        calculate_otd_price,
        calculate_trade_in_impact,
        create_or_update_campaign,
        dealer_initiated_accounting_jargon,
        dispatch_ready_messages,
        generate_natural_buyer_counter,
        get_best_competing_offer,
        get_dealer_conversation_thread,
        get_live_comms_configuration_status,
        get_negotiation_dashboard,
        get_prevailing_market_benchmark as _get_prevailing_market_benchmark,
        ingest_live_dealer_message,
        instrument_tool,
        parse_and_log_dealer_reply,
        poll_personal_gmail_inbox,
        queue_dealer_message as _queue_dealer_message,
        record_dealer_quote,
        run_autonomous_multi_dealer_negotiation,
        scrape_monmouth_ocean_kia_inventory,
        search_negotiation_memory,
        seed_sample_negotiation_campaign,
        set_dealer_trade_in_gate,
        set_transport_mode,
        simulate_dealer_reply,
        update_dealer_contact_info,
        update_market_benchmark as _update_market_benchmark,
        upsert_dealer_lead,
    )


@instrument_tool
def calculate_monthly_from_otd(
    otd_price: float,
    msrp: float,
    doc_fee: float,
    gov_fees: float,
    tax_rate_percent: float,
    rebates: float,
    cash_down_or_trade_equity: float,
    loan_apr_percent: float,
    loan_term_months: int,
    lease_term_months: int,
    lease_annual_miles: int,
    residual_percent: float,
    money_factor: float,
) -> Dict[str, Any]:
    """Works backward from an Out-The-Door (OTD) price to compute Implied Selling Price, Monthly Financed Purchase Payments (48/60/72mo), and 36-Month Lease Payments.

    Args:
        otd_price (float): Quoted or target Out-The-Door total price in USD.
        msrp (float): Vehicle window sticker MSRP in USD.
        doc_fee (float): Dealership documentation fee in USD.
        gov_fees (float): NJ MVC registration/title fees in USD (typically 385.0).
        tax_rate_percent (float): Applicable state sales tax rate percentage (6.625 for NJ).
        rebates (float): Applicable manufacturer rebates or lease cash in USD.
        cash_down_or_trade_equity (float): Cash down payment or net trade equity in USD.
        loan_apr_percent (float): Annual percentage rate (APR) for financed purchase comparison.
        loan_term_months (int): Primary loan term in months (e.g., 60).
        lease_term_months (int): Lease term in months (e.g., 36).
        lease_annual_miles (int): Annual lease mileage allowance (e.g., 12000).
        residual_percent (float): Residual value percentage of MSRP (e.g., 62.0).
        money_factor (float): Lease money factor (e.g., 0.00190).

    Returns:
        Dict[str, Any]: Dictionary containing `reverse_otd_breakdown`, `finance_purchase_from_otd`,
        and `lease_from_otd_cap_cost`.
    """
    return _calculate_monthly_from_otd(
        otd_price=otd_price,
        msrp=msrp,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        tax_rate_percent=tax_rate_percent,
        rebates=rebates,
        cash_down_or_trade_equity=cash_down_or_trade_equity,
        loan_apr_percent=loan_apr_percent,
        loan_term_months=loan_term_months,
        lease_term_months=lease_term_months,
        lease_annual_miles=lease_annual_miles,
        residual_percent=residual_percent,
        money_factor=money_factor,
        acquisition_fee=650.0,
    )


@instrument_tool
def audit_dealer_quote(
    deal_type: str,
    msrp: float,
    selling_price: float,
    dealer_quoted_otd: float,
    dealer_quoted_monthly: float,
    doc_fee: float,
    gov_fees: float,
    dealer_addons_total: float,
    rebates: float,
    tax_rate_percent: float,
    residual_percent: float = 62.0,
    money_factor: float = 0.00190,
    base_buy_rate_mf: float = 0.00190,
    term_months: int = 36,
    annual_miles: int = 12000,
) -> Dict[str, Any]:
    """Audits a dealer's PURCHASE or LEASE quote against itemized NJ math and prevailing market benchmarks.

    For deal_type='PURCHASE', omit residual_percent, money_factor, base_buy_rate_mf, term_months, and annual_miles.
    For deal_type='LEASE', provide residual_percent, money_factor, base_buy_rate_mf, term_months, and annual_miles.

    Args:
        deal_type (str): Deal structure (`'PURCHASE'` or `'LEASE'`).
        msrp (float): Vehicle window sticker MSRP in USD.
        selling_price (float): Quoted selling price before taxes and fees in USD.
        dealer_quoted_otd (float): Total OTD price quoted by the dealer (`0` if not stated).
        dealer_quoted_monthly (float): Monthly payment quoted by the dealer (`0` if not stated).
        doc_fee (float): Dealer documentation fee in USD.
        gov_fees (float): NJ MVC registration/title fees in USD (385.0).
        dealer_addons_total (float): Total dealer-installed add-ons in USD.
        rebates (float): Manufacturer rebates or lease cash in USD.
        tax_rate_percent (float): Applicable state sales tax percentage (6.625 for NJ).
        residual_percent (float): Residual value percentage for LEASE audits. Defaults to 62.0.
        money_factor (float): Quoted lease money factor for LEASE audits. Defaults to 0.00190.
        base_buy_rate_mf (float): Kia Finance Tier 1 base buy-rate money factor. Defaults to 0.00190.
        term_months (int): Lease term in months. Defaults to 36.
        annual_miles (int): Annual lease mileage allowance. Defaults to 12000.

    Returns:
        Dict[str, Any]: Comprehensive audit result including `otd_total`, `red_flags`,
        `recommended_counter_selling_price`, and `recommended_counter_otd`.
    """
    return _audit_dealer_quote(
        deal_type=deal_type,
        msrp=msrp,
        selling_price=selling_price,
        dealer_quoted_otd=dealer_quoted_otd,
        dealer_quoted_monthly=dealer_quoted_monthly,
        dealer_quoted_drive_off=0.0,
        residual_percent=residual_percent,
        money_factor=money_factor,
        base_buy_rate_mf=base_buy_rate_mf,
        term_months=term_months,
        annual_miles=annual_miles,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        dealer_addons_total=dealer_addons_total,
        rebates=rebates,
        tax_rate_percent=tax_rate_percent,
    )


@instrument_tool
def calculate_lease_payment(
    msrp: float,
    selling_price: float,
    residual_percent: float,
    money_factor: float,
    term_months: int,
    annual_miles: int,
    tax_rate_percent: float,
    acquisition_fee: float,
    doc_fee: float,
    gov_fees: float,
    dealer_addons_total: float,
    rebates_and_lease_cash: float,
    cap_cost_reduction_down: float,
    base_buy_rate_mf: float,
) -> Dict[str, Any]:
    """Computes full 36-month lease breakdown, Effective Monthly Cost, 1% MSRP Rule ratio, and Leasehackr Score.

    Args:
        msrp (float): Vehicle window sticker MSRP in USD.
        selling_price (float): Agreed vehicle selling price (gross cap cost base) in USD.
        residual_percent (float): Residual percentage of MSRP (e.g., 62.0).
        money_factor (float): Lease money factor (e.g., 0.00190).
        term_months (int): Lease term in months (e.g., 36).
        annual_miles (int): Annual mileage allowance (e.g., 12000).
        tax_rate_percent (float): State sales tax percentage (6.625 for NJ).
        acquisition_fee (float): Lessor acquisition fee in USD (650.0 for Kia Finance).
        doc_fee (float): Dealership documentation fee in USD.
        gov_fees (float): NJ MVC registration/title fees in USD (385.0).
        dealer_addons_total (float): Total mandatory/optional dealer add-ons in USD.
        rebates_and_lease_cash (float): Applicable lease cash or rebates in USD.
        cap_cost_reduction_down (float): Cash down payment or trade equity in USD.
        base_buy_rate_mf (float): Base Tier 1 buy-rate money factor (0.00190).

    Returns:
        Dict[str, Any]: Complete lease breakdown including `total_monthly_payment`, `drive_off_total`,
        `effective_monthly_cost`, `one_percent_rule_ratio`, and `leasehackr_score_years`.
    """
    return _calculate_lease_payment(
        msrp=msrp,
        selling_price=selling_price,
        residual_percent=residual_percent,
        money_factor=money_factor,
        term_months=term_months,
        annual_miles=annual_miles,
        tax_rate_percent=tax_rate_percent,
        acquisition_fee=acquisition_fee,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        dealer_addons_total=dealer_addons_total,
        rebates_and_lease_cash=rebates_and_lease_cash,
        cap_cost_reduction_down=cap_cost_reduction_down,
        base_buy_rate_mf=base_buy_rate_mf,
    )


@instrument_tool
def get_prevailing_market_benchmark(trim_level: str, msrp: float) -> Dict[str, Any]:
    """Returns prevailing NJ/Tri-State market comps, invoice price (-3.8%), holdback (2%), and stretch target (-5.0%).

    Args:
        trim_level (str): 2027 Kia Telluride Hybrid trim name (e.g., `'2027 Kia Telluride Hybrid SX X-Line'`).
        msrp (float): Vehicle window sticker MSRP in USD.

    Returns:
        Dict[str, Any]: Market benchmark dictionary containing `estimated_invoice_price`,
        `dealer_holdback_amount`, `prevailing_market_selling_price`, and `stretch_counter_selling_price`.
    """
    return _get_prevailing_market_benchmark(trim_level=trim_level, msrp=msrp)


@instrument_tool
def update_market_benchmark(
    prevailing_market_discount_pct: float,
    stretch_target_discount_pct: float,
    invoice_below_msrp_pct: float,
    dealer_holdback_pct: float,
    kia_incentives_rebates: float,
    market_intel_notes: str,
) -> Dict[str, Any]:
    """Updates the campaign's prevailing market benchmark percentages and recalculates all trim targets.

    Args:
        prevailing_market_discount_pct (float): Prevailing NJ/Tri-State market discount off MSRP percentage.
        stretch_target_discount_pct (float): Aggressive stretch counter-offer discount off MSRP percentage.
        invoice_below_msrp_pct (float): Estimated dealer invoice percentage below MSRP.
        dealer_holdback_pct (float): Manufacturer dealer holdback percentage of MSRP.
        kia_incentives_rebates (float): Applicable Kia Finance rebate/incentive in USD.
        market_intel_notes (str): Optional notes summarizing new market comps.

    Returns:
        Dict[str, Any]: Updated market benchmark configuration and recalculated trim targets.
    """
    return _update_market_benchmark(
        prevailing_market_discount_pct=prevailing_market_discount_pct,
        stretch_target_discount_pct=stretch_target_discount_pct,
        invoice_below_msrp_pct=invoice_below_msrp_pct,
        dealer_holdback_pct=dealer_holdback_pct,
        kia_incentives_rebates=kia_incentives_rebates,
        market_intel_notes=market_intel_notes,
    )


@instrument_tool
def queue_dealer_message(dealer_id: str, body: str, channel: str) -> Dict[str, Any]:
    """Queues an outbound negotiation SMS or Email in PENDING_APPROVAL with Trade-In Secrecy guardrail and stealth wait timer.

    Args:
        dealer_id (str): Dealership ID (e.g., `'dlr-raceway-kia-of-freehold'`).
        body (str): Outbound message text to send to the dealership.
        channel (str): Communication channel (`'SMS_PERSONAL'`, `'SMS_TWILIO'`, or `'EMAIL'`).

    Returns:
        Dict[str, Any]: Queued message record or guardrail rejection with recovery instructions.
    """
    return _queue_dealer_message(dealer_id=dealer_id, body=body, channel=channel)


market_deals_search_tool = GoogleSearchAgentTool(
    agent=create_google_search_agent(model="gemini-2.5-flash")
)

# ---------------------------------------------------------------------------
# Specialized Sub-Agents & Deterministic Sequential Workflow Pipeline
# ---------------------------------------------------------------------------

inventory_research_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="inventory_research_agent",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        safety_settings=DEFAULT_SAFETY_SETTINGS,
    ),
    description=(
        "Specialized Monmouth & Ocean County, NJ Kia Dealership Inventory & Market Benchmark Research Agent. "
        "Scrapes live DealerOn Cosmos inventory feeds, retrieves semantic memory, and updates prevailing Tri-State comps."
    ),
    instruction=(
        "You are the Inventory & Market Benchmark Research Specialist for a Monmouth County, NJ buyer (ZIP 07724). "
        "Focus strictly on New Jersey Kia dealerships (prioritizing Raceway Kia of Freehold, Sansone Jr's 66 Automall Kia, "
        "Matt Blatt Kia of Toms River, DiFeo Kia of Lakewood, and Manahawkin Kia) and 2027 Kia Telluride Hybrid trims "
        "(SX X-Line, SX Prestige, SX Prestige X-Line)."
    ),
    tools=[
        market_deals_search_tool,
        scrape_monmouth_ocean_kia_inventory,
        get_prevailing_market_benchmark,
        update_market_benchmark,
        search_negotiation_memory,
    ],
    before_model_callback=before_model_guardrail_callback,
    after_model_callback=after_model_self_eval_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

financial_auditor_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="financial_auditor_agent",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        safety_settings=DEFAULT_SAFETY_SETTINGS,
    ),
    description=(
        "Specialized Automotive Financial & Reverse-OTD Auditor Agent. "
        "Performs precision 6.625% NJ sales tax OTD audits, Reverse OTD -> Monthly loan/lease math, "
        "Color-Parity normalization, and Two-Stage Trade-In tax credit calculations."
    ),
    instruction=(
        "You are the Precision Automotive Financial Auditor for a Monmouth County, NJ buyer (ZIP 07724, 6.625% NJ sales tax). "
        "Verify every dealer worksheet line-by-line, strip out dealer add-ons and Money Factor markups above 0.00190, "
        "and work backward from Out-The-Door (OTD) prices to compute exact monthly purchase and lease payments."
    ),
    tools=[
        calculate_otd_price,
        calculate_monthly_from_otd,
        calculate_lease_payment,
        calculate_color_parity_benchmark,
        calculate_trade_in_impact,
        audit_dealer_quote,
        calculate_nj_payments,
        audit_uploaded_lease_sheet,
    ],
    before_model_callback=before_model_guardrail_callback,
    after_model_callback=after_model_self_eval_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

negotiation_comms_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="negotiation_comms_agent",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
        safety_settings=DEFAULT_SAFETY_SETTINGS,
    ),
    description=(
        "Specialized Dealership Communications & Copilot Stealth Queue Agent. "
        "Drafts 2-3 sentence Zero-Buzzword natural buyer counter-offers (or Reactive Accounting Mirror replies), "
        "enforces the Phase 1 Trade-In Secrecy Gag Order, and queues messages in PENDING_APPROVAL."
    ),
    instruction=(
        "You are the Dealership Communications Specialist for a Monmouth County, NJ buyer (ZIP 07724). "
        "Enforce the Zero-Buzzword Default tone (no 'invoice', 'holdback', or 'floorplan' jargon unless the dealer initiated it first), "
        "enforce the Silent Color Rule (never mention interior color preferences, monthly budgets, or lease return dates to dealers), "
        "and strictly prohibit any mention of a trade-in or expiring lease during Phase 1 OTD negotiation."
    ),
    tools=[
        dealer_initiated_accounting_jargon,
        generate_natural_buyer_counter,
        queue_dealer_message,
        approve_queued_message,
        dispatch_ready_messages,
        simulate_dealer_reply,
        ingest_live_dealer_message,
        poll_personal_gmail_inbox,
        update_dealer_contact_info,
        set_transport_mode,
        get_live_comms_configuration_status,
        get_dealer_conversation_thread,
    ],
    before_model_callback=before_model_guardrail_callback,
    after_model_callback=after_model_self_eval_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

_pipeline_audit_step = LlmAgent(
    model="gemini-2.5-flash",
    name="pipeline_quote_audit_step",
    description="Step 1 of Deal Audit & Counter Pipeline: Parses and audits the inbound dealer quote.",
    instruction="Parse and audit the dealer quote against 6.625% NJ tax and prevailing market benchmarks.",
    tools=[parse_and_log_dealer_reply, audit_dealer_quote, calculate_monthly_from_otd, calculate_nj_payments],
)

_pipeline_counter_step = LlmAgent(
    model="gemini-2.5-flash",
    name="pipeline_counter_queue_step",
    description="Step 2 of Deal Audit & Counter Pipeline: Generates and queues a compliant counter-offer in PENDING_APPROVAL.",
    instruction="Generate a compliant 2-3 sentence counter-offer and queue it in PENDING_APPROVAL for human confirmation.",
    tools=[generate_natural_buyer_counter, queue_dealer_message],
)

deal_audit_and_counter_pipeline = SequentialAgent(
    name="deal_audit_and_counter_pipeline",
    description=(
        "Deterministic 2-step SequentialAgent workflow that (1) audits an inbound dealer quote "
        "and (2) drafts and queues a compliant counter-offer in Copilot PENDING_APPROVAL mode."
    ),
    sub_agents=[_pipeline_audit_step, _pipeline_counter_step],
)

# ---------------------------------------------------------------------------
# Root Coordinator Agent (Herbie V3 — Hybrid Coordinator Pattern)
# ---------------------------------------------------------------------------

root_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="car_negotiator",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        safety_settings=DEFAULT_SAFETY_SETTINGS,
    ),
    description=(
        "Herbie (V3): Autonomous Car Purchase & Lease Negotiation Agent & Real-Time Deal Coach for a Monmouth County, NJ buyer (ZIP 07724) "
        "negotiating on a 2027 Kia Telluride Hybrid across the 5 Monmouth & Ocean County dealerships "
        "(Matt Blatt Kia, Raceway Kia, DiFeo Kia, Manahawkin Kia, and unified Sansone Jr's Kia [Neptune & Keyport])."
    ),
    instruction="""
    You are **Herbie**, an elite, quantitative Automotive Buying Agent & Real-Time Deal Coach representing a buyer in **Monmouth & Ocean County, New Jersey** (ZIP 07724, NJ Sales Tax: 6.625%).

    === 1. TARGET VEHICLE, 2027 MSRP ANCHORS & SILENT COLOR RULE ===
    - **Strictly 2027 Kia Telluride Hybrid (2.5L Turbo Hybrid AWD)**. Reject or flag any non-hybrid V6 quotes.
    - **2027 Official Base MSRPs (including $1,545 destination)**:
      1. **X-Line SX Hybrid**: $56,035 (*Allowed Priority Non-Black Interiors*: Saddle Brown & Millstone, Black & Millstone)
      2. **SX-Prestige Hybrid**: $58,135 (*Allowed Priority Non-Black Interior*: Deep Navy & Tuscan Umber)
      3. **X-Line SX-Prestige Hybrid**: $59,135 (*Allowed Priority Non-Black Interior*: Black & Millstone)
    - **Silent Color Rule**: Prioritize Non-Black interiors (`★ Priority Non-Black`), while keeping All-Black interiors visible in inventory so the buyer can choose to negotiate on them too. **NEVER mention interior color preferences, monthly budgets, or lease return dates in outbound dealer emails or texts.** Negotiate strictly by Stock # and 17-character VIN.

    === 2. THE 5 MONMOUTH & OCEAN COUNTY, NJ DEALERSHIPS (SANSONE JR'S UNIFIED) ===
    Treat **Sansone Jr's Neptune & Keyport** as **1 unified dealership** (`Sansone Jr's 66 Automall Kia`, $895 doc fee). Total of **5 dealerships**:
    1. **Matt Blatt Kia of Toms River** — Doc Fee: **$689** | Mandatory Add-Ons: **$0** (Ocean County baseline anchor)
    2. **Raceway Kia of Freehold** — Doc Fee: **$799** | Mandatory Add-Ons: **$0** (Monmouth County volume anchor)
    3. **DiFeo Kia of Lakewood** — Doc Fee: **$749** (baked into online price!) | Mandatory Add-Ons: **~$205**
    4. **Manahawkin Kia** — Doc Fee: **$749** (baked into online price!) | Watch for $995–$1,995 protection packages
    5. **Sansone Jr's 66 Automall Kia (Neptune & Keyport)** — Doc Fee: **$895** | Mandatory Add-Ons: **~$200**

    === 3. THE TWO-STAGE SQUEEZE & 4-MODE STATE MACHINE ===
    - **Stage 1 (In-Transit Pre-Sold Reserve)**: Target $1,500–$2,200 below MSRP (~2.5%–3.8% off), $0 dealer add-ons (PDI Hold), refundable deposit.
    - **Stage 2 (On-the-Lot Raid)**: Once any competing VIN lands ON_LOT, target $2,800–$3,800 below MSRP (~5.0%–6.4% off) for immediate 24-hour pickup.
    - **4 Operating Modes**:
      * **Mode 1 (`INITIAL_OUTREACH`)**: Clinical 3-sentence OTD inquiry citing Stock # and VIN, 07724 ZIP, and requesting itemized Selling Price, Doc Fee, and OTD.
      * **Mode 2 (`DEALER_OFFER_RECEIVED`)**: Run `calculate_nj_payments`, separate Pure Dealer Discount from the $750 Kia Loyalty/Conquest rebate, compute True Deal Score `(Selling Price - MSRP) + Doc Fee + Mandatory Add-Ons`, and draft a counter demanding removal of add-ons or beating the competing dealer's True Deal Score by $500.
      * **Mode 3 (`DEALER_DEFLECTION`)**: Track strikes (`0/2`, `1/2`, `DEAD`). Strike 1 sends a polite remote-work boundary pivot; Strike 2 marks the dealer `DEAD` and pivots to the next best Monmouth/Ocean dealer.
      * **Mode 4 (`WORKSHEET_AUDIT`)**: Run `audit_uploaded_lease_sheet` on uploaded/pasted dealer worksheets to reverse-engineer implied Money Factor vs. `0.00245` Tier 1 buy rate and flag 36-month reserve padding.

    === 4. DUAL-VIEW OUTPUT FORMAT (PRIVATE HUD + READY-TO-SEND REPLY) ===
    Always call `calculate_nj_payments` or `calculate_monthly_from_otd` to compute both **$3,000 Cash Down** and **$0 Sign & Drive** monthly numbers side-by-side (60-mo Loan @ 4.49% APR and 36-mo / 12k NJ Lease @ 0.00245 base MF, 62% residual, 6.625% NJ tax, $400 MV/tire fees, $750 target budget). Keep outbound dealer replies clinical, peer-to-peer, and under 120 words.

    === 5. DIRECT TOOL ROUTING RULES (AVOID UNNECESSARY DASHBOARD CALLS) ===
    - When the user provides the numbers in their prompt to work backward from an OTD price, call `calculate_monthly_from_otd` directly (do NOT call `get_negotiation_dashboard` first). If lease annual miles is not stated, pass `lease_annual_miles=12000`; if cash down is not stated, pass `cash_down_or_trade_equity=0`.
    - When the user asks to audit a dealer's purchase or lease quote and provides the numbers in the prompt, call `audit_dealer_quote` directly (do NOT call `get_negotiation_dashboard` first):
      * For `deal_type="PURCHASE"`, pass ONLY the 10 required fields (`deal_type="PURCHASE"`, `msrp`, `selling_price`, `dealer_quoted_otd` [0 if not explicitly stated], `dealer_quoted_monthly=0`, `doc_fee`, `gov_fees`, `dealer_addons_total`, `rebates`, `tax_rate_percent=6.625`) and do NOT pass `residual_percent`, `money_factor`, `base_buy_rate_mf`, `term_months`, or `annual_miles`.
      * For `deal_type="LEASE"`, pass all 10 required fields (`dealer_quoted_otd=0`) PLUS `residual_percent`, `money_factor`, `base_buy_rate_mf`, `term_months`, and `annual_miles`.
    - When the user asks for lease metrics including Effective Monthly Cost, 1% MSRP Rule ratio, and Leasehackr Score on a specific selling price, call `calculate_lease_payment` directly.
    - When the user asks to check our negotiation dashboard and best competing offer across dealerships (or asks whether to take/record an out-of-state or non-hybrid V6 offer), call ONLY `get_negotiation_dashboard()` (do NOT call `get_best_competing_offer` separately).
    - When the user asks to update the campaign's prevailing market benchmark percentages, call `update_market_benchmark` directly and leave `market_intel_notes=""` unless a verbatim quoted note string is specified.
    - When the user asks to draft/queue a message to a specific `dealer_id` with target numbers or a quoted body already specified in the prompt, call `queue_dealer_message` directly (do NOT call `get_negotiation_dashboard` first) and strictly omit any mention of a trade-in or expiring lease.
    """,
    tools=[
        market_deals_search_tool,
        scrape_monmouth_ocean_kia_inventory,
        get_prevailing_market_benchmark,
        update_market_benchmark,
        search_negotiation_memory,
        create_or_update_campaign,
        upsert_dealer_lead,
        record_dealer_quote,
        get_best_competing_offer,
        get_negotiation_dashboard,
        set_dealer_trade_in_gate,
        parse_and_log_dealer_reply,
        run_autonomous_multi_dealer_negotiation,
        calculate_otd_price,
        calculate_monthly_from_otd,
        calculate_lease_payment,
        calculate_color_parity_benchmark,
        calculate_trade_in_impact,
        audit_dealer_quote,
        calculate_nj_payments,
        audit_uploaded_lease_sheet,
        dealer_initiated_accounting_jargon,
        generate_natural_buyer_counter,
        queue_dealer_message,
        approve_queued_message,
        dispatch_ready_messages,
        simulate_dealer_reply,
        seed_sample_negotiation_campaign,
        ingest_live_dealer_message,
        poll_personal_gmail_inbox,
        update_dealer_contact_info,
        set_transport_mode,
        get_live_comms_configuration_status,
        get_dealer_conversation_thread,
    ],
    sub_agents=[
        inventory_research_agent,
        financial_auditor_agent,
        negotiation_comms_agent,
        deal_audit_and_counter_pipeline,
    ],
    before_model_callback=before_model_guardrail_callback,
    after_model_callback=after_model_self_eval_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)
setattr(root_agent, "agent_display_name", "Herbie")

app = App(
    name="car_negotiator",
    root_agent=root_agent,
)
