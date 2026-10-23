"""
Custom Tailwind Web Dashboard & Direct Google ADK Agent API Server.
Replaces the standard ADK Dev UI with a purpose-built Car Negotiator Web Application (`public/negotiator.html`)
that connects directly to:
- `car_negotiator.agent.root_agent` via Google ADK `Runner` & `InMemorySessionService` (`/api/agent_chat`)
- Real-time Car / VIN Telemetry, OTD Auditing, and Reverse OTD -> Monthly Payment Calculator (`/api/dashboard`, `/api/calculate_from_otd`, `/api/audit_quote`, `/api/upsert_vin`)
- Copilot Approval Queue, Anti-Bot Variable Stealth Timers, and NJ Dealer Simulator (`/api/draft_counter`, `/api/approve`, `/api/dispatch_ready`, `/api/simulate_reply`)
"""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import urlparse

try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None  # type: ignore[assignment]

try:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
except ImportError:
    Runner = None  # type: ignore[assignment]
    InMemorySessionService = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if __package__:
    from .agent import root_agent
    from .tools.comms_tools import (
        approve_queued_message,
        dealer_initiated_accounting_jargon,
        dispatch_ready_messages,
        generate_natural_buyer_counter,
        queue_dealer_message,
    )
    from .tools.context_memory import (
        PersistentFirestoreSessionService,
    )
    from .tools.deal_tracker import (
        build_enriched_dealer_inventory,
        create_or_update_campaign,
        ensure_dealer_lead_from_inventory,
        get_best_competing_offer,
        get_negotiation_dashboard,
        parse_and_log_dealer_reply,
        record_dealer_quote,
        run_autonomous_multi_dealer_negotiation,
        set_dealer_trade_in_gate,
        upsert_dealer_lead,
    )
    from .tools.dealer_simulator import simulate_dealer_reply
    from .tools.demo_seed import seed_sample_negotiation_campaign
    from .tools.finance_tools import (
        audit_dealer_quote,
        audit_uploaded_lease_sheet,
        calculate_color_parity_benchmark,
        calculate_lease_payment,
        calculate_monthly_from_otd,
        calculate_nj_payments,
        calculate_otd_price,
        calculate_trade_in_impact,
    )
    from .tools.guardrails import (
        DEFAULT_SAFETY_SETTINGS,
        evaluate_agent_response_policy,
    )
    from .tools.inventory_scraper import (
        MONMOUTH_OCEAN_KIA_DEALERS,
        scrape_monmouth_ocean_kia_inventory,
    )
    from .tools.live_comms_connector import (
        configure_personal_comms_credentials,
        get_dealer_conversation_thread,
        get_live_comms_configuration_status,
        ingest_live_dealer_message,
        poll_personal_gmail_inbox,
        set_transport_mode,
        update_dealer_contact_info,
    )
    from .tools.market_research_tools import (
        get_prevailing_market_benchmark,
        update_market_benchmark,
    )
    from .tools.observability import log_structured_event
    from .tools.secret_manager import get_secret, load_local_env_file
else:
    from agent import root_agent
    from tools.comms_tools import (
        approve_queued_message,
        dealer_initiated_accounting_jargon,
        dispatch_ready_messages,
        generate_natural_buyer_counter,
        queue_dealer_message,
    )
    from tools.context_memory import (
        PersistentFirestoreSessionService,
    )
    from tools.deal_tracker import (
        build_enriched_dealer_inventory,
        create_or_update_campaign,
        ensure_dealer_lead_from_inventory,
        get_best_competing_offer,
        get_negotiation_dashboard,
        parse_and_log_dealer_reply,
        record_dealer_quote,
        run_autonomous_multi_dealer_negotiation,
        set_dealer_trade_in_gate,
        upsert_dealer_lead,
    )
    from tools.dealer_simulator import simulate_dealer_reply
    from tools.demo_seed import seed_sample_negotiation_campaign
    from tools.finance_tools import (
        audit_dealer_quote,
        audit_uploaded_lease_sheet,
        calculate_color_parity_benchmark,
        calculate_lease_payment,
        calculate_monthly_from_otd,
        calculate_nj_payments,
        calculate_otd_price,
        calculate_trade_in_impact,
    )
    from tools.guardrails import (
        DEFAULT_SAFETY_SETTINGS,
        evaluate_agent_response_policy,
    )
    from tools.inventory_scraper import (
        MONMOUTH_OCEAN_KIA_DEALERS,
        scrape_monmouth_ocean_kia_inventory,
    )
    from tools.live_comms_connector import (
        configure_personal_comms_credentials,
        get_dealer_conversation_thread,
        get_live_comms_configuration_status,
        ingest_live_dealer_message,
        poll_personal_gmail_inbox,
        set_transport_mode,
        update_dealer_contact_info,
    )
    from tools.market_research_tools import (
        get_prevailing_market_benchmark,
        update_market_benchmark,
    )
    from tools.observability import log_structured_event
    from tools.secret_manager import get_secret, load_local_env_file

_LOCAL_PUBLIC_DIR = BASE_DIR / "public"
_PARENT_PUBLIC_DIR = BASE_DIR.parent / "public"
PUBLIC_DIR = str(
    _LOCAL_PUBLIC_DIR
    if (_LOCAL_PUBLIC_DIR / "negotiator.html").is_file()
    and (_LOCAL_PUBLIC_DIR / "negotiator.js").is_file()
    else _PARENT_PUBLIC_DIR
)


def _load_env_if_present() -> None:
    load_local_env_file()


_load_env_if_present()
os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")

import re

try:
    from google import genai
except ImportError:
    genai = None  # type: ignore[assignment]

# Persistent Firestore-synced SessionService & ADK Runner for direct UI -> Agent API calls
_PERSISTENT_SESSION_SERVICE = PersistentFirestoreSessionService()
_SESSION_SERVICE = InMemorySessionService() if InMemorySessionService else None
_ADK_RUNNER = (
    Runner(
        app_name="car_negotiator",
        agent=root_agent,
        session_service=_SESSION_SERVICE,
    )
    if Runner and _SESSION_SERVICE
    else None
)

# Multi-turn conversational memory cache backed by PersistentFirestoreSessionService
_CHAT_HISTORY_BY_SESSION: Dict[str, List[Dict[str, str]]] = {}


def _resolve_target_dealer_from_prompt(
    prompt: str,
    selected_vin_context: Dict[str, Any] | None,
    dealers_ranked: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Finds if the user explicitly asked about a specific dealer or VIN in their prompt; otherwise uses selected_vin_context."""
    p_low = prompt.lower()
    # Check explicit VIN match first
    for d in dealers_ranked:
        vin_str = str(d.get("vin", "")).lower()
        if vin_str and vin_str in p_low:
            return d

    # Check dealer keyword match if user asks about a specific dealership
    dealer_keywords = [
        ("manahawkin", "manahawkin"),
        ("difeo", "difeo"),
        ("lakewood", "difeo"),
        ("matt blatt", "matt blatt"),
        ("toms river", "matt blatt"),
        ("sansone", "sansone"),
        ("neptune", "sansone"),
        ("raceway", "raceway"),
        ("freehold", "raceway"),
    ]
    for kw, match_token in dealer_keywords:
        if kw in p_low:
            for d in dealers_ranked:
                if match_token in str(d.get("dealer_name", "")).lower():
                    return d

    if selected_vin_context and selected_vin_context.get("vin"):
        for d in dealers_ranked:
            if d.get("vin") == selected_vin_context.get("vin"):
                return d
        return selected_vin_context

    return dealers_ranked[0] if dealers_ranked else (selected_vin_context or {})


def _build_live_campaign_context_block(
    target_dealer: Dict[str, Any],
    dealers_ranked: List[Dict[str, Any]],
    best_offer: Dict[str, Any],
    compared_dealer_ids: List[str] | None = None,
) -> str:
    """Creates a live situational awareness block for the LLM so it can give specific deal & negotiation advice."""
    v = target_dealer or {}
    trim = str(v.get("trim_level") or "2027 Kia Telluride Hybrid SX X-Line")
    msrp_val = float(v.get("msrp") or 53890.0)
    bench = get_prevailing_market_benchmark(trim_level=trim, msrp=msrp_val)
    top_summary_lines = []
    for idx, d in enumerate(dealers_ranked[:9], start=1):
        top_summary_lines.append(
            f"  #{idx}: {d.get('dealer_name')} ({d.get('location_label')}) | VIN {d.get('vin')} | "
            f"{d.get('trim_level')} ({d.get('interior_color')}, {d.get('stock_status')}) | "
            f"MSRP ${float(d.get('msrp', 0)):,.0f} | Selling ${float(d.get('selling_price', 0)):,.0f} "
            f"({d.get('discount_off_msrp_pct', 0)}% off) | OTD ${float(d.get('otd_total', 0)):,.2f} | "
            f"Color-Parity OTD ${float(d.get('color_parity_otd', d.get('otd_total', 0))):,.2f} | "
            f"Doc ${float(d.get('doc_fee', 0)):,.0f} | Add-Ons ${float(d.get('dealer_addons_total', 0)):,.0f} | "
            f"MF {d.get('money_factor')} | 36m Lease ${float(d.get('lease_monthly_36m', 0)):,.2f}/mo"
        )
    leaderboard_text = "\n".join(top_summary_lines)
    best_lead = (best_offer or {}).get("best_lead") or {}

    comp_ids = compared_dealer_ids or [d.get("id") for d in dealers_ranked[:3]]
    comp_dealers = [d for d in dealers_ranked if d.get("id") in comp_ids][:3]
    comp_lines = []
    for cd in comp_dealers:
        comp_lines.append(
            f"  * {cd.get('dealer_name')}: {cd.get('trim_level')} ({cd.get('interior_color')}, {cd.get('stock_status')}) | "
            f"MSRP ${float(cd.get('msrp', 0)):,.0f} | {cd.get('discount_off_msrp_pct', 0)}% off MSRP | "
            f"OTD ${float(cd.get('otd_total', 0)):,.2f} | Color-Parity OTD ${float(cd.get('color_parity_otd', cd.get('otd_total', 0))):,.2f} | "
            f"60m Loan ${float(cd.get('finance_monthly_60m', 0)):,.2f}/mo | 36m Lease ${float(cd.get('lease_monthly_36m', 0)):,.2f}/mo"
        )
    comp_block = "\n".join(comp_lines) if comp_lines else "  * None selected"

    return (
        f"=== LIVE CAMPAIGN & SELECTED VEHICLE CONTEXT (MONMOUTH & OCEAN COUNTY, NJ • ZIP 07724) ===\n"
        f"- Buyer Strategy: Mandatory IN_TRANSIT December Delivery Lock (lock in written OTD deal now, take delivery in December when current lease ends; NEVER mention expiring lease to dealers).\n"
        f"- Outbound Tone Policy: Zero-Buzzword Default (natural 2-3 sentence busy buyer texts pitching deposit today + 24hr pickup when truck lands in Dec, NO 'invoice'/'holdback'/'floorplan' jargon) + Reactive Mirror Exception (only use insider accounting math if dealer brings up invoice/margin excuses first).\n"
        f"- Interior Policy: Non-Black Interior is a nice-to-have tiebreaker (adds only ~$295 factory option / ~$300 OTD / ~$3.10/mo lease; zero scarcity premium tolerated).\n"
        f"- Active / Target Vehicle: VIN {v.get('vin')} | {trim} | Interior: {v.get('interior_color')} | Status: {v.get('stock_status')}\n"
        f"- Dealership: {v.get('dealer_name')} ({v.get('location_label')}, ID: '{v.get('id')}', Contact: {v.get('contact_name', 'Internet Sales Desk')})\n"
        f"- Quote Numbers: MSRP ${msrp_val:,.2f} | Quoted Selling Price ${float(v.get('selling_price', 51842)):,.2f} "
        f"({v.get('discount_off_msrp_pct', 3.8)}% off MSRP) | Quoted OTD Total (w/ 6.625% NJ Tax): ${float(v.get('otd_total', 55303.11)):,.2f} | "
        f"Color-Parity OTD: ${float(v.get('color_parity_otd', v.get('otd_total', 55303.11))):,.2f}\n"
        f"- Fees & Lease Terms: NJ Doc Fee ${float(v.get('doc_fee', 499)):,.2f} | NJ MVC ${float(v.get('gov_fees', 385)):,.2f} | "
        f"Dealer Add-Ons ${float(v.get('dealer_addons_total', 0)):,.2f} ({v.get('addon_descriptions') or 'None'}) | "
        f"KMF Rebate ${float(v.get('rebates', 500)):,.2f} | Money Factor {v.get('money_factor', 0.00190)} (Buy Rate: 0.00190 = 4.56% APR) | Residual {v.get('residual_percent', 62.0)}%\n"
        f"- Prevailing NJ Market Benchmarks for {trim}: Invoice Selling Price ${float(bench.get('prevailing_market_selling_price', 51842)):,.2f} (-3.8% off MSRP) | "
        f"Stretch Target Selling Price ${float(bench.get('stretch_counter_selling_price', 51196)):,.2f} (-5.0% off MSRP, dipping into 2% Kia Holdback) | "
        f"Recommended Stretch Counter OTD: ${float(v.get('recommended_counter_otd') or 54614.29):,.2f}\n"
        f"- Up to 3 Selected Competing Vehicles (Side-by-Side-by-Side Deck):\n{comp_block}\n"
        f"- #1 Best Competing Offer in Campaign: {best_lead.get('dealer_name', 'Raceway Kia of Freehold')} "
        f"(VIN {best_lead.get('vin', '5XYP5DGC8VG041822')}) at ${float(best_lead.get('otd_total', 55303.11)):,.2f} OTD "
        f"(${float(best_lead.get('selling_price', 51842)):,.2f} selling price, $0 add-ons)\n"
        f"- All Ranked Monmouth & Ocean County Dealer VINs:\n{leaderboard_text}\n"
    )


async def _run_adk_agent_turn(
    prompt: str,
    session_id: str = "web-dashboard-session",
    selected_vin_context: Dict[str, Any] | None = None,
    agent_mode: str = "strategist",
    compared_dealer_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Executes a dynamic conversational turn against `root_agent` with persistent session history."""
    user_id = "monmouth_nj_buyer"
    dash = get_negotiation_dashboard()
    dealers_ranked = dash.get("dealer_inventory") or dash.get("dealers_ranked") or []
    best_offer = get_best_competing_offer()
    target_dealer = _resolve_target_dealer_from_prompt(
        prompt, selected_vin_context, dealers_ranked
    )
    context_block = _build_live_campaign_context_block(
        target_dealer, dealers_ranked, best_offer, compared_dealer_ids
    )

    persistent_session = _PERSISTENT_SESSION_SERVICE.get_or_create_session(
        session_id=session_id,
        user_id=user_id,
        initial_state={
            "zip_code": "07724",
            "county": "Monmouth County, NJ",
            "tax_rate_percent": 6.625,
            "vehicle_requirement": "2027 Kia Telluride Hybrid",
        },
    )
    history = _CHAT_HISTORY_BY_SESSION.setdefault(
        session_id, list(persistent_session.get("history", []))
    )
    recent_turns = history[-4:]

    recent_turns_text = ""
    if recent_turns:
        formatted_turns = [
            f"User: {h['user']}\nAgent: {h['agent']}" for h in recent_turns
        ]
        recent_turns_text += (
            "=== RECENT CONVERSATION HISTORY ===\n"
            + "\n---\n".join(formatted_turns)
            + "\n\n"
        )

    is_strategist = str(agent_mode).lower() != "auditor"
    target_temp = 0.65 if is_strategist else 0.1
    routed_model = "gemini-2.5-flash"
    mode_directive = (
        "MODE: CREATIVE STRATEGIST (Conversational Deal Coach, Temperature=0.65). "
        "Have a natural, direct, back-and-forth conversation with the buyer. Answer ONLY what they ask—never dump a canned scorecard or unrequested sections."
        if is_strategist
        else "MODE: STRICT DEAL AUDITOR (Precision Financial Auditor, Temperature=0.1). "
        "Directly answer the buyer's exact math, fee, or audit question with precise numbers—no unrequested boilerplate."
    )

    # Pre-compute live math for any numbers in the prompt so the LLM has exact figures in 1 round-trip
    v = target_dealer or {}
    v_msrp = float(v.get("msrp") or 53890.0)
    v_selling = float(v.get("selling_price") or 51842.0)
    v_otd = float(v.get("otd_total") or 55303.11)
    v_doc = float(v.get("doc_fee") or 499.0)
    v_gov = float(v.get("gov_fees") or 385.0)
    v_rebates = float(v.get("rebates") or 500.0)
    v_addons = float(v.get("dealer_addons_total") or 0.0)
    v_mf = float(v.get("money_factor") or 0.00190)
    v_rv = float(v.get("residual_percent") or 62.0)

    parsed_nums = _extract_custom_numbers(prompt, v_otd, v_selling)
    eff_otd = float(parsed_nums["custom_otd"] or v_otd)
    eff_down = float(parsed_nums["custom_down"] or 0.0)
    eff_apr = float(parsed_nums["custom_apr"] or 4.9)

    live_rev = calculate_monthly_from_otd(
        otd_price=eff_otd,
        msrp=v_msrp,
        doc_fee=v_doc,
        gov_fees=v_gov,
        tax_rate_percent=6.625,
        rebates=v_rebates,
        cash_down_or_trade_equity=eff_down,
        loan_apr_percent=eff_apr,
        loan_term_months=60,
        residual_percent=v_rv,
        money_factor=v_mf,
        lease_term_months=36,
    )
    live_b = live_rev["reverse_otd_breakdown"]
    live_fin = live_rev["finance_purchase_from_otd"]
    live_lse = live_rev["lease_from_otd_cap_cost"]

    tool_calls_log: List[Dict[str, Any]] = [
        {
            "type": "call",
            "name": "calculate_monthly_from_otd",
            "args": {
                "otd_price": eff_otd,
                "msrp": v_msrp,
                "doc_fee": v_doc,
                "cash_down_or_trade_equity": eff_down,
                "loan_apr_percent": eff_apr,
            },
        },
        {
            "type": "response",
            "name": "calculate_monthly_from_otd",
            "response": live_rev,
        },
    ]

    live_math_context = (
        f"=== PRE-COMPUTED NJ FINANCIAL TELEMETRY (USE ONLY IF RELEVANT TO THE USER'S QUESTION) ===\n"
        f"- Evaluated OTD: ${eff_otd:,.2f} (Cash Down: ${eff_down:,.0f}, APR: {eff_apr}%)\n"
        f"- Implied Selling Price / Lease Cap Cost from ${eff_otd:,.2f} OTD: ${float(live_b['implied_selling_price_cap_cost']):,.2f} ({live_b['implied_discount_pct']}% off ${v_msrp:,.0f} MSRP)\n"
        f"- Financed Purchase Monthly (${eff_down:,.0f} down @ {eff_apr}% APR): 48mo=${float(live_fin['monthly_48mo']):,.2f}/mo | 60mo=${float(live_fin['monthly_60mo']):,.2f}/mo | 72mo=${float(live_fin['monthly_72mo']):,.2f}/mo\n"
        f"- 36mo/12k Hybrid Lease Monthly ({v_rv}% RV, {v_mf:.5f} MF): ${float(live_lse['total_monthly_payment']):,.2f}/mo (${float(live_lse['drive_off_due_at_signing']):,.2f} drive-off) or ${float(live_lse['sign_and_drive_zero_due_monthly']):,.2f}/mo Sign & Drive ($0 due)\n"
        f"- Dealer Add-Ons on Worksheet: ${v_addons:,.0f} ({v.get('addon_descriptions') or 'None'})\n\n"
    )

    conversational_system_instruction = (
        "You are the live, generative AI brain of `car_negotiator`, a personal Car Buying & Negotiation Coach "
        "helping a buyer in Monmouth County, NJ (ZIP 07724, 6.625% NJ sales tax) buy or lease a 2027 Kia Telluride Hybrid.\n\n"
        "CRITICAL CONVERSATIONAL RULES:\n"
        "1. Be 100% conversational, natural, and generative. Directly answer the exact question or request the user just typed.\n"
        "2. NEVER output a canned report template, never repeat the same fixed headings (like '### 🎯 Deal Evaluation' or '### 💬 Advisor Response'), "
        "and NEVER dump unrequested sections (do not list 48/60/72-month payments, lease math, or dealer scripts unless the user's question actually asks for them or benefits from them).\n"
        "3. Match the length and style of your reply to what the user asked: if they ask a quick question, give a crisp, direct conversational answer; "
        "if they ask for deep strategy, math, or a draft email/text, provide a thoughtful, tailored response grounded in the live NJ campaign data.\n"
        "4. Domain guardrails to keep in mind when drafting dealer messages: negotiate on Out-The-Door (OTD) price (never monthly payments with dealers), "
        "never mention the buyer's trade-in or expiring December lease to a dealer before OTD is locked in writing, and avoid unprompted dealer-accounting jargon in outbound buyer messages unless the dealer brought up invoice/margin excuses first."
    )

    enriched_prompt = (
        f"{context_block}\n"
        f"{live_math_context}"
        f"{recent_turns_text}"
        f"=== AGENT OPERATING MODE ===\n{mode_directive}\n\n"
        f"=== USER'S MESSAGE ===\n"
        f"{prompt}\n\n"
        f"Reply directly and naturally to the user's message above."
    )

    def _persist_turn(reply_text: str) -> Dict[str, Any]:
        eval_report = evaluate_agent_response_policy(
            response_text=reply_text,
            dealer_initiated_jargon=dealer_initiated_accounting_jargon(prompt),
            otd_locked_in_writing=bool(target_dealer.get("otd_locked_in_writing", False)),
        )
        safe_reply = str(eval_report["sanitized_text"])
        history.append({"user": prompt, "agent": safe_reply})
        _PERSISTENT_SESSION_SERVICE.append_turn(
            session_id=session_id,
            user_message=prompt,
            agent_reply=safe_reply,
            user_id=user_id,
        )
        return eval_report

    import time

    global _LLM_QUOTA_COOLDOWN_UNTIL
    now_ts = time.time()
    api_key = get_secret("GOOGLE_API_KEY") or get_secret("GEMINI_API_KEY")
    has_api_key = bool(api_key)
    if (
        genai_types is None
        or (genai is None and _ADK_RUNNER is None)
        or not has_api_key
        or now_ts < globals().get("_LLM_QUOTA_COOLDOWN_UNTIL", 0.0)
    ):
        advisor_res = _dynamic_conversational_deal_advisor(
            prompt=prompt,
            target_dealer=target_dealer,
            dealers_ranked=dealers_ranked,
            best_offer=best_offer,
            session_id=session_id,
            agent_mode=agent_mode,
            compared_dealer_ids=compared_dealer_ids,
        )
        eval_report = _persist_turn(advisor_res["reply"])
        advisor_res["reply"] = eval_report["sanitized_text"]
        advisor_res["self_evaluation"] = eval_report
        return advisor_res

    if _SESSION_SERVICE is not None:
        existing = await _SESSION_SERVICE.get_session(
            app_name="car_negotiator", user_id=user_id, session_id=session_id
        )
        if not existing:
            await _SESSION_SERVICE.create_session(
                app_name="car_negotiator",
                user_id=user_id,
                session_id=session_id,
                state={
                    "zip_code": "07724",
                    "county": "Monmouth County, NJ",
                    "tax_rate_percent": 6.625,
                    "vehicle_requirement": "2027 Kia Telluride Hybrid",
                },
            )

    content = genai_types.Content(
        role="user", parts=[genai_types.Part.from_text(text=enriched_prompt)]
    )

    reply_chunks: List[Dict[str, Any] | str] = []

    # 1. Primary Live Generative Gemini Call (`gemini-flash-latest` -> `gemini-flash-lite-latest`)
    if genai is not None:
        cascade_models = [
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            routed_model,
        ]
        seen_models = set()
        ordered_models = []
        for m in cascade_models:
            if m and m not in seen_models:
                seen_models.add(m)
                ordered_models.append(m)
        try:
            saved_proj = os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
            saved_loc = os.environ.pop("GOOGLE_CLOUD_LOCATION", None)
            try:
                client = genai.Client(api_key=api_key)
            finally:
                if saved_proj is not None:
                    os.environ["GOOGLE_CLOUD_PROJECT"] = saved_proj
                if saved_loc is not None:
                    os.environ["GOOGLE_CLOUD_LOCATION"] = saved_loc
            for alt_model in ordered_models:
                try:
                    resp = client.models.generate_content(
                        model=alt_model,
                        contents=enriched_prompt,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=conversational_system_instruction,
                            temperature=target_temp,
                            safety_settings=DEFAULT_SAFETY_SETTINGS,
                        ),
                    )
                    if resp and resp.text:
                        reply_text = resp.text.strip()
                        eval_report = _persist_turn(reply_text)
                        return {
                            "ok": True,
                            "mode": f"ADK_LIVE_LLM ({alt_model} • temp={target_temp})",
                            "agent_mode": agent_mode,
                            "temperature": target_temp,
                            "model": alt_model,
                            "self_evaluation": eval_report,
                            "reply": eval_report["sanitized_text"],
                            "tool_events": tool_calls_log,
                        }
                except Exception as model_exc:
                    log_structured_event(
                        "dashboard.gemini_cascade_retry",
                        level="WARNING",
                        model=alt_model,
                        error=str(model_exc)[:200],
                    )
                    continue
        except Exception as client_exc:
            log_structured_event(
                "dashboard.gemini_client_warning",
                level="WARNING",
                error=str(client_exc)[:200],
            )

    # 2. Try primary Google ADK Runner (`root_agent`)
    try:
        root_agent.generate_content_config = genai_types.GenerateContentConfig(
            temperature=target_temp,
            safety_settings=DEFAULT_SAFETY_SETTINGS,
        )
        async for event in _ADK_RUNNER.run_async(
            user_id=user_id, session_id=session_id, new_message=content
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.function_call:
                        tool_calls_log.append(
                            {
                                "type": "call",
                                "name": part.function_call.name,
                                "args": dict(part.function_call.args or {}),
                            }
                        )
                    if part.function_response:
                        tool_calls_log.append(
                            {
                                "type": "response",
                                "name": part.function_response.name,
                                "response": part.function_response.response,
                            }
                        )
                    if part.text:
                        reply_chunks.append(part.text)
        final_text = "\n".join(t for t in reply_chunks if t.strip()).strip()
        if final_text:
            eval_report = _persist_turn(final_text)
            return {
                "ok": True,
                "mode": f"ADK_LIVE_AGENT (temp={target_temp})",
                "agent_mode": agent_mode,
                "temperature": target_temp,
                "model": root_agent.model,
                "self_evaluation": eval_report,
                "reply": eval_report["sanitized_text"],
                "tool_events": tool_calls_log,
            }
    except Exception as primary_exc:
        _LLM_QUOTA_COOLDOWN_UNTIL = time.time() + 45.0
        log_structured_event(
            "dashboard.adk_runner_fallback",
            level="WARNING",
            error=str(primary_exc)[:200],
        )
        advisor_res = _dynamic_conversational_deal_advisor(
            prompt=prompt,
            target_dealer=target_dealer,
            dealers_ranked=dealers_ranked,
            best_offer=best_offer,
            session_id=session_id,
            agent_mode=agent_mode,
            compared_dealer_ids=compared_dealer_ids,
        )
        advisor_res["fallback_reason"] = str(primary_exc)
        eval_report = _persist_turn(advisor_res["reply"])
        advisor_res["reply"] = eval_report["sanitized_text"]
        advisor_res["self_evaluation"] = eval_report
        return advisor_res

    advisor_res = _dynamic_conversational_deal_advisor(
        prompt=prompt,
        target_dealer=target_dealer,
        dealers_ranked=dealers_ranked,
        best_offer=best_offer,
        session_id=session_id,
        agent_mode=agent_mode,
        compared_dealer_ids=compared_dealer_ids,
    )
    eval_report = _persist_turn(advisor_res["reply"])
    advisor_res["reply"] = eval_report["sanitized_text"]
    advisor_res["self_evaluation"] = eval_report
    return advisor_res


def _extract_custom_numbers(prompt: str, default_otd: float, default_selling: float) -> Dict[str, float | None]:
    """Extracts any user-mentioned OTD price, selling price, down payment, APR, or monthly target from natural language."""
    p_low = prompt.lower()
    # Find dollar amounts like $54,189 or $3,000 or 54500
    dollar_matches = re.findall(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,6}(?:\.[0-9]{1,2})?)", prompt)
    raw_nums = [float(m.replace(",", "")) for m in dollar_matches]
    if not raw_nums:
        # Also check bare 5-digit numbers like 54500 or 55000
        bare_5digit = re.findall(r"\b(4[5-9][0-9]{3}|5[0-9]{4}|6[0-5][0-9]{3})\b", prompt)
        raw_nums = [float(x) for x in bare_5digit]

    custom_otd: float | None = None
    custom_selling: float | None = None
    custom_down: float = 0.0
    custom_monthly: float | None = None

    # Check explicit down payment pattern e.g. "$3,000 down" or "3000 down"
    down_match = re.search(r"\$?\s*([0-9]{1,2},?[0-9]{3}|[0-9]{3,4})\s*(?:down|due at signing|cash down)", p_low)
    if down_match:
        custom_down = float(down_match.group(1).replace(",", ""))

    for val in raw_nums:
        if abs(val - custom_down) < 1e-2 and custom_down > 0:
            continue
        if 45000 <= val <= 68000:
            if "selling" in p_low or "cap cost" in p_low or "before tax" in p_low:
                custom_selling = val
            else:
                custom_otd = val
        elif 400 <= val <= 1600 and ("/mo" in p_low or "month" in p_low or "payment" in p_low):
            custom_monthly = val

    # Check APR mention e.g. "3.9% apr" or "5.5%"
    apr_match = re.search(r"([0-9]+\.[0-9]+|[0-9]+)\s*%\s*(?:apr|interest|rate)", p_low)
    custom_apr = float(apr_match.group(1)) if apr_match else 4.9

    return {
        "custom_otd": custom_otd,
        "custom_selling": custom_selling,
        "custom_down": custom_down,
        "custom_monthly": custom_monthly,
        "custom_apr": custom_apr,
    }


def _score_deal_quality(
    msrp: float,
    selling_price: float,
    otd_total: float,
    addons: float,
    doc_fee: float,
    gov_fees: float,
    rebates: float,
    mf: float,
    stock_status: str,
    bench: Dict[str, Any],
) -> Dict[str, Any]:
    """Computes a comprehensive Deal Quality Scorecard (Grade A+ to D) and dollar savings/overpayment vs NJ benchmark."""
    discount_pct = round(((msrp - selling_price) / msrp) * 100.0, 2) if msrp > 0 else 0.0
    stretch_selling = float(bench.get("stretch_counter_selling_price") or round(msrp * 0.95, 2))
    invoice_selling = float(bench.get("prevailing_market_selling_price") or round(msrp * 0.962, 2))

    invoice_otd_res = calculate_otd_price(
        msrp=msrp,
        selling_price=invoice_selling,
        doc_fee=499.0,
        gov_fees=gov_fees,
        dealer_addons_total=0.0,
        rebates=rebates,
        tax_rate_percent=6.625,
    )
    stretch_otd_res = calculate_otd_price(
        msrp=msrp,
        selling_price=stretch_selling,
        doc_fee=499.0,
        gov_fees=gov_fees,
        dealer_addons_total=0.0,
        rebates=rebates,
        tax_rate_percent=6.625,
    )
    invoice_otd = float(invoice_otd_res.get("otd_total") or round(msrp * 1.041, 2))
    stretch_otd = float(stretch_otd_res.get("otd_total") or round(msrp * 1.028, 2))

    mf_markup_bps = max(0.0, round((mf - 0.00190) * 2400.0, 2))
    excess_doc = max(0.0, round(doc_fee - 499.0, 2))
    over_invoice_otd = round(otd_total - invoice_otd, 2)
    potential_savings_to_stretch = max(0.0, round(otd_total - stretch_otd, 2))

    if discount_pct >= 4.5 and addons == 0 and mf <= 0.00195:
        grade = "A+ (Unicorn / Holdback Deal)"
        verdict_short = "EXCEPTIONAL DEAL — LOCK THIS IN IMMEDIATELY"
        summary = (
            f"You are getting **{discount_pct}% off MSRP** (`${msrp - selling_price:,.0f}` discount) with **`$0` dealer add-ons** "
            f"and base buy-rate financing, beating NJ Dealer Invoice (`3.8%` off) and dipping directly into Kia's `2.0%` dealer holdback."
        )
    elif discount_pct >= 3.5 and addons == 0 and mf <= 0.00200:
        grade = "A (Strong Below-Invoice Deal)"
        verdict_short = "GOOD DEAL — AT NJ DEALER INVOICE WITH $0 ADD-ONS"
        summary = (
            f"This quote is right at **NJ Dealer Invoice (`{discount_pct}%` off MSRP)** with **`$0` forced add-ons** and clean money factor (`{mf:.5f}`). "
            f"You can either take this deal today or push for an extra **`${potential_savings_to_stretch:,.0f}` off** to hit our `5.0%` stretch target (`${stretch_otd:,.0f}` OTD)."
        )
    elif discount_pct >= 2.2 and addons <= 500:
        grade = "B (Fair / Average Market Quote — Room to Negotiate)"
        verdict_short = "DECENT OPENING QUOTE, BUT DO NOT SIGN YET"
        summary = (
            f"At **{discount_pct}% off MSRP** (`${otd_total:,.2f}` OTD), the dealer is giving a modest discount but still holding back "
            f"**`${potential_savings_to_stretch:,.0f}`** compared to our `5.0%` stretch target (`${stretch_otd:,.2f}` OTD)."
        )
    elif addons > 0 or mf > 0.00210 or discount_pct < 2.2:
        grade = "C- (Dealer Profit Padding Detected — Counter Aggressively)"
        verdict_short = "NOT A GOOD DEAL YET — DEALER PADDING DETECTED"
        reasons = []
        if discount_pct < 3.0:
            reasons.append(f"weak `{discount_pct}%` MSRP discount vs `3.8%–5.0%` NJ benchmark")
        if addons > 0:
            reasons.append(f"`${addons:,.0f}` in high-margin dealer add-ons")
        if excess_doc > 0:
            reasons.append(f"`${doc_fee:,.0f}` NJ Doc Fee (`${excess_doc:,.0f}` higher than Raceway Kia's `$499`)")
        if mf_markup_bps > 0:
            reasons.append(f"Money Factor marked up to `{mf:.5f}` (`+{mf_markup_bps}%` hidden APR markup over `0.00190` buy rate)")
        summary = (
            f"You are currently overpaying by **`${max(over_invoice_otd, 0):,.0f}` to `${potential_savings_to_stretch:,.0f}`** due to: "
            + ", ".join(reasons)
            + "."
        )
    else:
        grade = "D (Walk-Away / Sticker Trap)"
        verdict_short = "BAD DEAL — DO NOT ACCEPT THIS WORKSHEET"
        summary = f"This quote is `$${potential_savings_to_stretch:,.0f}` above our Monmouth/Ocean County target OTD."

    return {
        "grade": grade,
        "verdict_short": verdict_short,
        "summary": summary,
        "discount_pct": discount_pct,
        "invoice_otd": invoice_otd,
        "stretch_otd": stretch_otd,
        "stretch_selling": stretch_selling,
        "invoice_selling": invoice_selling,
        "over_invoice_otd": over_invoice_otd,
        "potential_savings_to_stretch": potential_savings_to_stretch,
        "mf_markup_bps": mf_markup_bps,
        "excess_doc": excess_doc,
    }


def _dynamic_conversational_deal_advisor(
    prompt: str,
    target_dealer: Dict[str, Any],
    dealers_ranked: List[Dict[str, Any]],
    best_offer: Dict[str, Any],
    session_id: str = "web-dashboard-session",
    agent_mode: str = "strategist",
    compared_dealer_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """
    Dynamic Conversational Deal Coach & Negotiation Strategist (`Creative Strategist` vs `Strict Deal Auditor`).
    Executes the real ADK tools (`audit_dealer_quote`, `calculate_monthly_from_otd`, `calculate_lease_payment`,
    `calculate_color_parity_benchmark`, `generate_natural_buyer_counter`, `get_prevailing_market_benchmark`)
    and synthesizes non-repetitive, context-aware answers across the up-to-3 selected vehicles.
    """
    import random

    v = target_dealer or {}
    msrp = float(v.get("msrp") or 53890.0)
    selling = float(v.get("selling_price") or 51842.0)
    otd = float(v.get("otd_total") or 55303.11)
    doc_fee = float(v.get("doc_fee") or 499.0)
    gov_fees = float(v.get("gov_fees") or 385.0)
    rebates = float(v.get("rebates") or 500.0)
    addons = float(v.get("dealer_addons_total") or 0.0)
    addon_desc = str(v.get("addon_descriptions") or "")
    mf = float(v.get("money_factor") or 0.00190)
    rv = float(v.get("residual_percent") or 62.0)
    trim = str(v.get("trim_level") or "2027 Kia Telluride Hybrid SX X-Line")
    dealer_name = str(v.get("dealer_name") or "Raceway Kia of Freehold")
    vin = str(v.get("vin") or "5XYP5DGC8VG041822")
    interior = str(v.get("interior_color") or "Terracotta Brown Leather")
    stock_status = str(v.get("stock_status") or "IN_TRANSIT")
    contact_name = str(v.get("contact_name") or "Internet Sales Manager")

    parsed = _extract_custom_numbers(prompt, otd, selling)
    effective_otd = float(parsed["custom_otd"] or otd)
    cash_down = float(parsed["custom_down"] or 0.0)
    loan_apr = float(parsed["custom_apr"] or 4.9)

    tool_events: List[Dict[str, Any]] = []

    bench = get_prevailing_market_benchmark(trim_level=trim, msrp=msrp)
    tool_events.append(
        {
            "type": "call",
            "name": "get_prevailing_market_benchmark",
            "args": {"trim_level": trim, "msrp": msrp},
        }
    )
    tool_events.append(
        {
            "type": "response",
            "name": "get_prevailing_market_benchmark",
            "response": bench,
        }
    )

    rev = calculate_monthly_from_otd(
        otd_price=effective_otd,
        msrp=msrp,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        tax_rate_percent=6.625,
        rebates=rebates,
        cash_down_or_trade_equity=cash_down,
        loan_apr_percent=loan_apr,
        loan_term_months=60,
        residual_percent=rv,
        money_factor=mf,
        lease_term_months=36,
    )
    tool_events.append(
        {
            "type": "call",
            "name": "calculate_monthly_from_otd",
            "args": {
                "otd_price": effective_otd,
                "msrp": msrp,
                "doc_fee": doc_fee,
                "cash_down_or_trade_equity": cash_down,
                "loan_apr_percent": loan_apr,
                "residual_percent": rv,
                "money_factor": mf,
            },
        }
    )
    tool_events.append(
        {
            "type": "response",
            "name": "calculate_monthly_from_otd",
            "response": rev,
        }
    )

    b_math = rev["reverse_otd_breakdown"]
    fin_math = rev["finance_purchase_from_otd"]
    lse_math = rev["lease_from_otd_cap_cost"]

    effective_selling = (
        float(parsed["custom_selling"])
        if parsed["custom_selling"]
        else (
            float(b_math["implied_selling_price_cap_cost"])
            if parsed["custom_otd"]
            else selling
        )
    )

    audit_res = audit_dealer_quote(
        deal_type="PURCHASE",
        msrp=msrp,
        selling_price=effective_selling,
        dealer_quoted_otd=effective_otd,
        residual_percent=rv,
        money_factor=mf,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        dealer_addons_total=addons if not parsed["custom_otd"] else 0.0,
        rebates=rebates,
        tax_rate_percent=6.625,
    )
    tool_events.append(
        {
            "type": "call",
            "name": "audit_dealer_quote",
            "args": {
                "deal_type": "PURCHASE",
                "msrp": msrp,
                "selling_price": effective_selling,
                "dealer_quoted_otd": effective_otd,
                "dealer_addons_total": addons if not parsed["custom_otd"] else 0.0,
                "doc_fee": doc_fee,
                "money_factor": mf,
            },
        }
    )
    tool_events.append(
        {
            "type": "response",
            "name": "audit_dealer_quote",
            "response": audit_res,
        }
    )

    color_parity = calculate_color_parity_benchmark(
        msrp=msrp,
        selling_price=effective_selling,
        interior_color=interior,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        dealer_addons_total=addons if not parsed["custom_otd"] else 0.0,
        rebates=rebates,
        tax_rate_percent=6.625,
    )
    tool_events.append(
        {
            "type": "call",
            "name": "calculate_color_parity_benchmark",
            "args": {
                "msrp": msrp,
                "selling_price": effective_selling,
                "interior_color": interior,
            },
        }
    )

    score = _score_deal_quality(
        msrp=msrp,
        selling_price=effective_selling,
        otd_total=effective_otd,
        addons=addons if not parsed["custom_otd"] else 0.0,
        doc_fee=doc_fee,
        gov_fees=gov_fees,
        rebates=rebates,
        mf=mf,
        stock_status=stock_status,
        bench=bench,
    )

    best_lead = (best_offer or {}).get("best_lead") or (dealers_ranked[0] if dealers_ranked else v)
    best_dealer_name = best_lead.get("dealer_name", "Raceway Kia of Freehold")
    best_otd = float(best_lead.get("otd_total") or 55303.11)
    best_vin = best_lead.get("vin", "5XYP5DGC8VG041822")

    p_low = prompt.lower().strip()
    prompt_clean = prompt.strip()
    prompt_snippet = prompt_clean[:90] + ("..." if len(prompt_clean) > 90 else "")
    is_auditor = str(agent_mode).lower() == "auditor"

    # If the user specified a custom target OTD in a draft/counter request, honor their target OTD!
    custom_target_otd = float(parsed["custom_otd"]) if parsed["custom_otd"] else float(score["stretch_otd"])
    custom_target_selling = (
        float(parsed["custom_selling"])
        if parsed["custom_selling"]
        else (
            float(b_math["implied_selling_price_cap_cost"])
            if parsed["custom_otd"]
            else float(score["stretch_selling"])
        )
    )

    # Build Natural Counter Script via generate_natural_buyer_counter
    counter_gen = generate_natural_buyer_counter(
        dealer_name=dealer_name,
        contact_name=contact_name,
        vin=vin,
        trim_level=trim,
        interior_color=interior,
        stock_status=stock_status,
        target_otd=custom_target_otd,
        target_selling_price=custom_target_selling,
        competing_dealer_name=best_dealer_name if best_dealer_name != dealer_name else "Matt Blatt Kia of Toms River",
        competing_otd=best_otd,
        round_num=2,
        dealer_inbound_text=prompt,
        has_addons=addons > 0,
        addon_descriptions=addon_desc,
        has_mf_markup=mf > 0.00195,
        quoted_mf=mf,
        doc_fee=doc_fee,
    )
    tool_events.append(
        {
            "type": "call",
            "name": "generate_natural_buyer_counter",
            "args": {
                "dealer_name": dealer_name,
                "vin": vin,
                "target_otd": custom_target_otd,
                "tone_mode": counter_gen["tone_mode"],
            },
        }
    )

    is_greeting_or_ping = p_low in {
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "test",
        "testing",
        "help",
        "status",
        "ping",
        "are you there",
        "are you working",
        "what can you do",
        "who are you",
    } or (len(p_low.split()) <= 3 and any(w in p_low for w in ["hi", "hello", "hey", "test", "help"]))

    asks_gmail_or_sms = any(
        w in p_low
        for w in [
            "gmail",
            "app password",
            "password",
            "oauth",
            "inbox",
            "sync",
            "thread",
            "sms",
            "text message",
            "my phone",
            "mobile",
            "chrome",
            "settings",
            "connect email",
        ]
    )
    asks_draft_or_queue = any(
        w in p_low
        for w in [
            "draft",
            "write",
            "send",
            "queue",
            "email",
            "text ",
            "reply",
            "respond",
            "what should i say",
            "message",
            "counter",
            "offer",
        ]
    )
    asks_fee_or_addon = any(
        w in p_low
        for w in [
            "doc fee",
            "documentation",
            "add-on",
            "addon",
            "etch",
            "nitrogen",
            "lojack",
            "ceramic",
            "pinstripe",
            "protection",
            "mvc",
            "registration",
            "tax",
            "money factor",
            "mf",
            "residual",
        ]
    )
    has_custom_numbers = any(
        [
            parsed["custom_otd"] is not None,
            parsed["custom_selling"] is not None,
            parsed["custom_down"] > 0,
            parsed["custom_monthly"] is not None,
            "apr" in p_low,
        ]
    )

    asks_compare_dealers = any(
        w in p_low
        for w in [
            "compare",
            "which dealer",
            "best dealer",
            "monmouth",
            "ocean",
            "rank",
            "all dealers",
            "3 vehicles",
            "3 dealers",
            "side by side",
            "trim",
            "versus",
            " vs ",
        ]
    )
    asks_lease_vs_buy = any(
        w in p_low
        for w in [
            "lease or",
            "lease vs",
            "buy or lease",
            "finance or lease",
            "should i lease",
            "should i buy",
            "1% rule",
            "leasehackr",
        ]
    )
    asks_trade_in = any(
        w in p_low for w in ["trade", "trade-in", "carmax", "carvana", "current lease", "my car", "shell game"]
    )
    asks_color_or_intransit = any(
        w in p_low
        for w in [
            "interior",
            "terracotta",
            "sage",
            "mahogany",
            "non-black",
            "color",
            "in-transit",
            "in transit",
            "december",
            "buzzword",
            "floorplan",
        ]
    )
    asks_objection = any(
        w in p_low
        for w in [
            "showroom",
            "come in",
            "in person",
            "won't put",
            "losing money",
            "already at invoice",
            "fake quote",
            "send me the quote",
            "objection",
        ]
    )
    asks_negotiation_advice = any(
        w in p_low
        for w in [
            "negotiate",
            "negotiat",
            "guidance",
            "strategy",
            "how do i",
            "how can i",
            "what should i say",
            "counter",
            "script",
            "talk them",
            "lower",
            "leverage",
            "tips",
            "coach",
            "help me",
            "next step",
            "what now",
            "what next",
        ]
    )
    asks_good_deal = any(
        w in p_low
        for w in [
            "good deal",
            "bad deal",
            "fair",
            "worth",
            "overpay",
            "audit",
            "what do you think",
            "opinion",
            "advice",
            "evaluate",
            "rate",
            "is this",
            "am i getting",
            "mark up",
            "markup",
            "invoice",
            "holdback",
        ]
    )
    asks_payment_math = any(
        w in p_low
        for w in [
            "monthly",
            "payment",
            "work backward",
            "reverse otd",
            "down",
            "48",
            "60",
            "72",
            "apr",
        ]
    ) or has_custom_numbers

    sections: List[str] = []

    # -------------------------------------------------------------------------
    # SECTION 0: DIRECT, INPUT-SPECIFIC CONVERSATIONAL RESPONSE
    # Guarantees every single user prompt receives a unique, tailored response
    # -------------------------------------------------------------------------
    if is_greeting_or_ping:
        nj_pay_greeting = calculate_nj_payments(
            msrp=msrp,
            selling_price=effective_selling,
            doc_fee=doc_fee,
            mandatory_addons=addons,
            rebates=rebates if rebates > 0 else 750.0,
            cash_down=cash_down if cash_down > 0 else 3000.0,
            target_monthly_budget=750.0,
        )
        sections.append(
            f"### 🤖 Herbie — Live NJ Deal Coach Connected (`{dealer_name}` • ZIP `07724`)\n"
            f"- **You said**: *\"{prompt_snippet}\"*\n"
            f"- **Active Focused Vehicle**: **{dealer_name}** — `{trim}` (VIN `{vin}`, `{interior}`, `{stock_status}`) quoted at **`${otd:,.2f}` OTD** (True Deal Score: **`${nj_pay_greeting['true_deal_score']:,.0f}`**) vs. our **`${score['stretch_otd']:,.2f}` Stretch Target OTD** (saves **`${score['potential_savings_to_stretch']:,.2f}`**).\n"
            f"- **Current #1 Ranked Leader**: **{best_dealer_name}** (VIN `{best_vin}`) at **`${best_otd:,.2f}` OTD**.\n"
            f"- **Ask Herbie anything in real time, paste a dealer text/email, or drop a worksheet**:\n"
            f"  1. *\"What if {dealer_name} offers $54,750 OTD with $3,000 down?\"*\n"
            f"  2. *\"Draft and queue a counter email to {contact_name} at $54,614 OTD\"*\n"
            f"  3. *\"Salesperson says 'come into the showroom to talk numbers' — what do I text back?\"*\n"
            f"  4. *\"How do I set up my 16-letter Gmail App Password for single-thread emails?\"*"
        )
        reply = "\n\n".join(sections)
        return {
            "ok": True,
            "agent_name": "Herbie",
            "mode": f"HERBIE_DEAL_COACH ({agent_mode.upper()})",
            "agent_mode": agent_mode,
            "temperature": 0.65 if not is_auditor else 0.1,
            "model": root_agent.model,
            "reply": reply,
            "hud_metrics": nj_pay_greeting,
            "ready_to_send_reply": {
                "body": counter_gen["body"],
                "subject": f"2027 Telluride {trim.replace('2027 Kia Telluride ', '')} - Stock #{v.get('stock_number', vin[-6:])} - OTD Price Request",
                "tone_mode": counter_gen["tone_mode"],
                "operating_mode": "MODE_1_INITIAL_OUTREACH",
                "dealer_name": dealer_name,
                "vin": vin,
            },
            "tool_events": tool_events,
        }

    if asks_gmail_or_sms:
        comms_cfg = get_live_comms_configuration_status()
        gmail_state = (
            f"✅ **Configured (`{comms_cfg.get('gmail_user_email')}` via `{comms_cfg.get('gmail_auth_method')}`)**"
            if comms_cfg.get("gmail_ready")
            else "⚙️ **Ready for 60-Second Setup in `⚙️ Settings`** (Enter your Gmail + 16-character Google App Password)"
        )
        sections.append(
            f"### 📬 Live Personal Gmail Threading & Mobile SMS Setup (Re: *\"{prompt_snippet}\"*)\n"
            f"- **Current Gmail Status**: {gmail_state}\n"
            f"- **How the 60-Second App Password Works**: In your Google Account (`myaccount.google.com/apppasswords`), create a 16-letter **App Password** for this app and paste it into **⚙️ Settings**. You never share your main Gmail password.\n"
            f"- **Single-Thread Dealer Continuity (`In-Reply-To` / `References`)**: Every email sent to `{contact_name}` at **{dealer_name}** (`{v.get('contact_email') or 'sales@dealer.com'}`) uses subject `Re: 2027 Kia Telluride Hybrid (VIN {vin}) — Written OTD Quote Request (ZIP 07724)` and RFC-5322 Message-ID threading so your entire back-and-forth stays in **one natural Gmail thread**.\n"
            f"- **Mobile Chrome 1-Tap SMS (`sms:`)**: On your phone, tap **📲 Text from My Phone** on any queued card to open your native Messages app pre-addressed to `{v.get('contact_phone') or '(732) 555-0142'}` with your counter-offer pre-filled."
        )

    if has_custom_numbers and not is_auditor:
        diff_vs_current = round(otd - effective_otd, 2)
        diff_vs_stretch = round(effective_otd - float(score["stretch_otd"]), 2)
        comparison_note = (
            f"**`${abs(diff_vs_current):,.2f}` lower** than `{dealer_name}`'s current `${otd:,.2f}` OTD quote"
            if diff_vs_current >= 0
            else f"**`${abs(diff_vs_current):,.2f}` higher** than `{dealer_name}`'s current `${otd:,.2f}` OTD quote"
        )
        stretch_note = (
            f"within **`${abs(diff_vs_stretch):,.2f}`** of our `${score['stretch_otd']:,.2f}` Stretch Holdback Target!"
            if diff_vs_stretch <= 250
            else f"still **`${diff_vs_stretch:,.2f}` above** our `${score['stretch_otd']:,.2f}` Stretch Holdback Target (`-5.0%` off MSRP)."
        )
        custom_Monthly_note = ""
        if parsed["custom_monthly"]:
            target_mo = float(parsed["custom_monthly"])
            custom_Monthly_note = (
                f"\n- **Your Target Monthly Payment (`${target_mo:,.0f}/mo`)**: At `{loan_apr}%` APR for 60 months, `${effective_otd:,.2f}` OTD works out to **`${fin_math['monthly_60mo']:,.2f}/mo`** (or **`${lse_math['total_monthly_payment']:,.2f}/mo`** on a 36-month/12k lease)."
            )
        sections.append(
            f"### 💡 Herbie Custom Number Analysis for *\"{prompt_snippet}\"* — `{dealer_name}`\n"
            f"- **Your Scenario**: **`${effective_otd:,.2f}` OTD** (Implied Selling Price: **`${effective_selling:,.2f}`** = **`{score['discount_pct']}%` off `${msrp:,.0f}` MSRP** • Cash Down: **`${cash_down:,.0f}`** • APR: **`{loan_apr}%`**).\n"
            f"- **How It Compares**: This is {comparison_note}, and is {stretch_note}\n"
            f"- **Deal Verdict at `${effective_otd:,.2f}` OTD**: **{score['grade']}** — {score['verdict_short']}.{custom_Monthly_note}\n"
            f"- **Ready-to-Send Counter at `${custom_target_otd:,.0f}` OTD**:\n"
            f'> *"{counter_gen["body"]}"*'
        )

    if asks_fee_or_addon and not is_auditor:
        addon_status = (
            f"⚠️ **`${addons:,.0f}` in dealer add-ons detected** (`{addon_desc or 'Protection/Etch package'}`). Demand `$0` PDI hold before arrival!"
            if addons > 0
            else "✅ **`$0` mandatory add-ons** on this worksheet."
        )
        doc_status = (
            f"⚠️ **`${doc_fee:,.0f}` Doc Fee** (`${doc_fee - 499:,.0f}` higher than Raceway Kia's `$499` NJ benchmark — reduce selling price by `${doc_fee - 499:,.0f}` to offset)."
            if doc_fee > 500
            else f"✅ **`${doc_fee:,.0f}` Doc Fee** (lowest tier in Monmouth/Ocean County)."
        )
        mf_status = (
            f"⚠️ **Money Factor Marked Up to `{mf:.5f}`** (`{mf * 2400:.2f}%` APR vs `0.00190` / `4.56%` KMF Tier-1 buy rate)."
            if mf > 0.00195
            else f"✅ **Clean Buy-Rate Money Factor (`{mf:.5f}` = `{mf * 2400:.2f}%` APR)** and **`{rv}%` Residual**."
        )
        sections.append(
            f"### 🔍 Line-Item Fee, Tax & Markup Audit for `{dealer_name}` (Re: *\"{prompt_snippet}\"*)\n"
            f"- **Vehicle**: `{trim}` (VIN `{vin}` • MSRP `${msrp:,.0f}` • Selling `${effective_selling:,.2f}`)\n"
            f"- **NJ Doc Fee**: {doc_status}\n"
            f"- **Dealer Add-Ons**: {addon_status}\n"
            f"- **NJ Sales Tax (`6.625%` ZIP `07724`) + MVC (`${gov_fees:,.0f}`)**: Tax is calculated on `(Selling Price + Doc Fee + Add-Ons - Rebates)`.\n"
            f"- **Lease Money Factor & Residual**: {mf_status}"
        )

    if asks_draft_or_queue and not is_auditor:
        should_queue = any(w in p_low for w in ["queue", "send", "approve", "schedule"])
        queue_note = ""
        if should_queue and v.get("id"):
            q_res = queue_dealer_message(
                dealer_id=str(v.get("id")),
                dealer_name=dealer_name,
                channel="EMAIL" if "email" in p_low else "SMS_PERSONAL",
                recipient=str(v.get("contact_email") if "email" in p_low else v.get("contact_phone") or ""),
                subject=counter_gen.get("subject", f"Re: 2027 Kia Telluride Hybrid (VIN {vin}) — Written OTD Quote Request"),
                body=counter_gen["body"],
                autonomy_mode="COPILOT",
            )
            tool_events.append(
                {
                    "type": "call",
                    "name": "queue_dealer_message",
                    "args": {"dealer_id": v.get("id"), "target_otd": custom_target_otd},
                }
            )
            queue_note = f"\n- ✅ **Queued in Copilot Approval Queue**: Draft `{q_res.get('message', {}).get('id', 'msg-live')}` is ready for your 1-click approval or `📲 Text from My Phone` in the right panel!"

        sections.append(
            f"### ✉️ Custom Outbound Draft for `{contact_name}` at `{dealer_name}` (Target: **`${custom_target_otd:,.0f}` OTD**)\n"
            f"- **Subject (Threaded)**: `Re: 2027 Kia Telluride Hybrid (VIN {vin}) — Written OTD Quote Request (ZIP 07724)`\n"
            f"- **Tone Policy**: `{counter_gen['tone_mode']}` (Zero unprompted accounting buzzwords; trade-in kept 100% secret){queue_note}\n"
            f"- **Copy/Send Message**:\n"
            f'> *"{counter_gen["body"]}"*'
        )

    # -------------------------------------------------------------------------
    # AUDITOR MODE: Crisp, high-signal mathematical verification table
    # -------------------------------------------------------------------------
    if is_auditor:
        flags = audit_res.get("red_flags") or [
            "PASS: $0 mandatory add-ons and base 0.00190 KMF buy-rate Money Factor."
        ]
        auditor_lines = [
            f"### 📐 Strict Deal Audit for *\"{prompt_snippet}\"* — `{dealer_name}` (`{trim}` • VIN `{vin}`)",
            f"- **Audit Verdict**: **{score['grade']}** (`{score['verdict_short']}`)",
            f"- **Reverse OTD Math (`6.625%` NJ Tax, ZIP `07724`)**:",
            f"  * **Evaluated OTD**: **`${effective_otd:,.2f}`** → **Implied Selling Price / Cap Cost**: **`${effective_selling:,.2f}`** (`{score['discount_pct']}%` off `${msrp:,.0f}` MSRP)",
            f"  * **Color-Parity OTD (Stripping `$295` `{interior}` Option)**: **`${color_parity['color_parity_otd']:,.2f}`** (`+${color_parity['otd_color_delta']:,.2f}` OTD / `+${color_parity['lease_monthly_36m_color_delta']:,.2f}/mo` lease impact)",
            f"  * **NJ Invoice OTD (`-3.8%`)**: `${score['invoice_otd']:,.2f}` | **Stretch Holdback Target OTD (`-5.0%`)**: **`${score['stretch_otd']:,.2f}`** (`${score['potential_savings_to_stretch']:,.2f}` recoverable)",
            f"  * **Implied Payments from `${effective_otd:,.2f}` OTD**: 60mo Loan @ `{loan_apr}%` = **`${fin_math['monthly_60mo']:,.2f}/mo`** | 36mo/12k Lease = **`${lse_math['total_monthly_payment']:,.2f}/mo`** (`${lse_math['sign_and_drive_zero_due_monthly']:,.2f}/mo` Sign & Drive)",
            f"- **Red-Flag Line-Item Checks**:",
        ]
        for f in flags:
            auditor_lines.append(f"  * {f}")
        auditor_lines.append(
            f"- **Outbound Counter (`{counter_gen['tone_mode']}`)**:\n> *\"{counter_gen['body']}\"*"
        )
        sections.append("\n".join(auditor_lines))

    # -------------------------------------------------------------------------
    # SECTION 1: SIDE-BY-SIDE-BY-SIDE (UP TO 3 VEHICLES) & CROSS-TRIM COMPARISON
    # -------------------------------------------------------------------------
    if (asks_compare_dealers or "3" in p_low) and dealers_ranked and not is_auditor:
        comp_ids = compared_dealer_ids or [d.get("id") for d in dealers_ranked[:3]]
        comp_list = [d for d in dealers_ranked if d.get("id") in comp_ids][:3]
        if len(comp_list) < 3:
            comp_list = dealers_ranked[:3]
        lines = [
            f"### ⚖️ Side-by-Side-by-Side Comparison (Re: *\"{prompt_snippet}\"*)",
            f"Comparing your **{len(comp_list)} selected Monmouth & Ocean County vehicles** normalized by **% Off Trim MSRP**, **$ vs. Trim Invoice**, and **Color-Parity OTD** (so higher trims like `SX Prestige` and `SX Prestige X-Line` can be judged apples-to-apples against `SX X-Line`):",
        ]
        for idx, d in enumerate(comp_list, start=1):
            d_otd = float(d.get("otd_total", 0))
            d_cp_otd = float(d.get("color_parity_otd", d_otd))
            vs_inv = float(d.get("price_vs_trim_invoice", 0))
            vs_inv_str = f"`${abs(vs_inv):,.0f}` below Invoice" if vs_inv <= 0 else f"`+${vs_inv:,.0f}` above Invoice"
            lines.append(
                f"* **Slot #{idx}: {d.get('dealer_name')}** (`{d.get('trim_level')}` • VIN `{d.get('vin')}` • `{d.get('interior_color')}` • `{d.get('stock_status')}`)\n"
                f"  * **MSRP**: `${float(d.get('msrp', 0)):,.0f}` | **Selling**: `${float(d.get('selling_price', 0)):,.0f}` (**`{d.get('discount_off_msrp_pct', 0)}%` off MSRP**, {vs_inv_str})\n"
                f"  * **Actual OTD**: **`${d_otd:,.2f}`** | **Color-Parity OTD**: **`${d_cp_otd:,.2f}`** | **60mo Loan**: `${float(d.get('finance_monthly_60m', 0)):,.0f}/mo` | **36mo Lease**: `${float(d.get('lease_monthly_36m', 0)):,.0f}/mo`"
            )
        sections.append("\n".join(lines))

    # -------------------------------------------------------------------------
    # SECTION 2: IN-TRANSIT DECEMBER LOCK, ZERO-BUZZWORD TONE & COLOR-PARITY STRATEGY
    # -------------------------------------------------------------------------
    if asks_color_or_intransit or asks_objection or (asks_negotiation_advice and not is_auditor and not asks_draft_or_queue):
        strat_angles = [
            "Pitching the pre-sale in plain buyer English (*'deposit today, zero lot prep needed, 24-hour pickup when the truck lands in December'*) lets the GSM do the `$0` floorplan math in their head without feeling lectured.",
            "Because your current lease ends in December, locking an `IN_TRANSIT` factory allocation now protects you from late-December showroom desperation while keeping your expiring lease 100% invisible to the dealer.",
            "Treating Non-Black interior as a nice-to-have tiebreaker eliminates the dealer's 'rare color' markup — the factory option is only `$295` (`~$300` OTD or `$3.10/mo` on a 36-month lease).",
        ]
        chosen_angle = random.choice(strat_angles)
        tone_badge = (
            "🔥 **REACTIVE MIRROR MODE TRIGGERED** (Dealer used accounting/invoice jargon first — countering with exact `3.8%` invoice + `2%` holdback + `$0` floorplan carry math)"
            if counter_gen["tone_mode"] == "REACTIVE_ACCOUNTING_MIRROR"
            else "🤫 **ZERO-BUZZWORD NATURAL BUYER MODE** (No dealer-accounting jargon in outbound text; pitching frictionless December pre-sale speed & certainty)"
        )
        tactic_lines = [
            f"### 🧠 Herbie Strategist Insights for *\"{prompt_snippet}\"* — `{dealer_name}`",
            f"- **Strategic Angle**: {chosen_angle}",
            f"- **Non-Black Interior Color-Parity Math (`{interior}`)**: Factory option is **`${color_parity['factory_color_option_msrp']:,.0f}`** (`+${color_parity['otd_color_delta']:,.2f}` OTD w/ `6.625%` NJ tax, or **`+${color_parity['lease_monthly_36m_color_delta']:,.2f}/mo`** on a 36mo lease). {color_parity['leverage_script']}",
            f"- **Active Outbound Tone Policy**: {tone_badge}",
            f"- **Ready-to-Send 2–3 Sentence Buyer Reply for {contact_name} at {dealer_name}**:",
            f'> *"{counter_gen["body"]}"*',
        ]
        sections.append("\n".join(tactic_lines))

    # -------------------------------------------------------------------------
    # SECTION 3: DEAL VERDICT & PLAYBOOK (When explicitly asked or when answering a general question)
    # -------------------------------------------------------------------------
    if not is_auditor and (asks_good_deal or not sections):
        verdict_lines = [
            f"### 🤖 Herbie Deal Coach Analysis for *\"{prompt_snippet}\"* — `{dealer_name}` (`{trim}` • VIN `{vin}`)",
            f"- **Direct Takeaway**: For **{dealer_name}** (`{interior}`, `{stock_status}`), their current **`${effective_otd:,.2f}` OTD** quote (`${effective_selling:,.2f}` selling = `{score['discount_pct']}%` off `${msrp:,.0f}` MSRP) earns a **{score['grade']}** (`{score['verdict_short']}`). {score['summary']}",
            f"- **Target Numbers to Beat (ZIP `07724` • `6.625%` NJ Tax)**:",
            f"  * **NJ Invoice (`-3.8%`)**: `${score['invoice_selling']:,.2f}` selling → **`${score['invoice_otd']:,.2f}` OTD**",
            f"  * **Stretch Holdback Target (`-5.0%` • `$0` Add-Ons)**: `${score['stretch_selling']:,.2f}` selling → **`${score['stretch_otd']:,.2f}` OTD** (Recovers **`${score['potential_savings_to_stretch']:,.2f}`**)",
            f"  * **Monthly Equivalent at `${effective_otd:,.2f}` OTD**: **`${fin_math['monthly_60mo']:,.2f}/mo`** (60mo @ `{loan_apr}%` APR) or **`${lse_math['total_monthly_payment']:,.2f}/mo`** (36mo/12k Hybrid lease)",
            f"- **Recommended Reply to `{contact_name}` (`{counter_gen['tone_mode']}`)**:",
            f'> *"{counter_gen["body"]}"*',
        ]
        sections.append("\n".join(verdict_lines))

    # -------------------------------------------------------------------------
    # SECTION 4: LEASE VS. BUY / REVERSE OTD -> MONTHLY PAYMENT ANALYSIS
    # -------------------------------------------------------------------------
    if (asks_lease_vs_buy or asks_payment_math) and not is_auditor:
        lease_detail = calculate_lease_payment(
            msrp=msrp,
            selling_price=effective_selling,
            residual_percent=rv,
            money_factor=mf,
            term_months=36,
            annual_miles=12000,
            rebates_and_lease_cash=rebates,
            cap_cost_reduction_down=cash_down,
            doc_fee=doc_fee,
            gov_fees=gov_fees,
            tax_rate_percent=6.625,
        )
        one_pct_ratio = lease_detail.get("pct_of_msrp", round((lse_math["total_monthly_payment"] / msrp) * 100, 2))
        lh_score = lease_detail.get("leasehackr_score_years", 7.4)
        down_note = f" (with **`${cash_down:,.0f}` Cash Down**)" if cash_down > 0 else " (`$0` Down Payment)"

        pay_lines = [
            f"### 🧮 Reverse OTD (`${effective_otd:,.2f}`) → Monthly Purchase vs. Lease Breakdown{down_note}",
            f"- **Worked Backward from `${effective_otd:,.2f}` OTD**: Strips out `$385` NJ MVC, `${doc_fee:,.0f}` Doc Fee, and `6.625%` NJ Sales Tax → **`${b_math['implied_selling_price_cap_cost']:,.2f}` Implied Selling Price / Lease Cap Cost** (`{b_math['implied_discount_pct']}%` off `${msrp:,.0f}` MSRP).",
            f"- **Financed Purchase (`{loan_apr}%` APR{down_note})**: 48mo: `${fin_math['monthly_48mo']:,.2f}/mo` | **60mo: `${fin_math['monthly_60mo']:,.2f}/mo`** | 72mo: `${fin_math['monthly_72mo']:,.2f}/mo`",
            f"- **36-Month / 12k Mi Hybrid Lease (`{rv}%` Residual, `{mf:.5f}` MF)**: **`${lse_math['total_monthly_payment']:,.2f}/mo`** (`${lse_math['drive_off_due_at_signing']:,.2f}` drive-off) or **`${lse_math['sign_and_drive_zero_due_monthly']:,.2f}/mo` Sign & Drive (`$0` due)** • `{one_pct_ratio}%` of MSRP • Leasehackr Score: **`{lh_score} yrs`**",
        ]
        sections.append("\n".join(pay_lines))

    # -------------------------------------------------------------------------
    # SECTION 5: TWO-STAGE AFFIRMATIVE TRADE-IN WORKFLOW COACHING
    # -------------------------------------------------------------------------
    if asks_trade_in:
        trade_demo = calculate_trade_in_impact(
            locked_vehicle_otd=effective_otd,
            msrp=msrp,
            doc_fee=doc_fee,
            gov_fees=gov_fees,
            rebates=rebates,
            trade_in_value=18000.0,
            trade_in_payoff=0.0,
            carmax_offer_floor=18500.0,
            tax_rate_percent=6.625,
            private_preview_confirmed=True,
            otd_locked_confirmed=False,
        )
        sections.append(
            "### 🔒 Two-Stage Affirmative Trade-In & NJ Sales Tax Credit Strategy\n"
            "- **Gate #1 (Private Monthly & NJ Tax Credit Preview)**: Check **Box #1** in the Trade-In Bar to privately model how your trade-in reduces your Net OTD and monthly payments (including NJ's **`6.625%` sales tax credit**: e.g. an `$18,000` trade saves an extra **`$"
            + f"{trade_demo['nj_sales_tax_credit_savings']:,.2f}"
            + "`** in NJ sales tax, so a dealer offer of **`$"
            + f"{trade_demo['carmax_tax_equivalent_dealer_floor']:,.0f}"
            + "`** equals an `$18,500` CarMax check). Outbound dealer messages still keep your trade-in **100% secret**.\n"
            "- **Gate #2 (Post-OTD Lock Dealer Trade Negotiation)**: Only check **Box #2** *after* the dealer signs a written OTD buyers order on the `IN_TRANSIT` Telluride Hybrid. That unlocks our Phase 2 trade-in counter script and activates the **Post-Trade Shell-Game Detector** (which flags if the dealer tries to claw back your new-car discount or inflate doc/add-on fees)."
        )

    nj_hud = calculate_nj_payments(
        msrp=msrp,
        selling_price=effective_selling,
        doc_fee=doc_fee,
        mandatory_addons=addons if not parsed["custom_otd"] else 0.0,
        rebates=rebates if rebates > 0 else 750.0,
        cash_down=cash_down if cash_down > 0 else 3000.0,
        target_monthly_budget=float(parsed["custom_monthly"] or 750.0),
        loan_apr_percent=loan_apr if loan_apr != 4.9 else 4.49,
        residual_percent=rv,
        base_mf=mf if mf >= 0.00200 else 0.00245,
        mv_tire_fees=gov_fees if gov_fees > 0 else 400.0,
    )
    tool_events.append(
        {
            "type": "call",
            "name": "calculate_nj_payments",
            "args": {
                "msrp": msrp,
                "selling_price": effective_selling,
                "doc_fee": doc_fee,
                "mandatory_addons": addons if not parsed["custom_otd"] else 0.0,
                "cash_down": cash_down if cash_down > 0 else 3000.0,
            },
        }
    )

    operating_mode = (
        "MODE_3_DEALER_DEFLECTION"
        if asks_objection
        else ("MODE_2_DEALER_OFFER_RECEIVED" if has_custom_numbers or asks_good_deal else "MODE_1_INITIAL_OUTREACH")
    )

    reply = "\n\n".join(sections)
    return {
        "ok": True,
        "agent_name": "Herbie",
        "mode": f"HERBIE_DEAL_COACH ({agent_mode.upper()})",
        "agent_mode": agent_mode,
        "temperature": 0.65 if not is_auditor else 0.1,
        "model": root_agent.model,
        "reply": reply,
        "hud_metrics": nj_hud,
        "ready_to_send_reply": {
            "body": counter_gen["body"],
            "subject": f"Re: 2027 Kia Telluride Hybrid (VIN {vin}) — Written OTD Quote Request (ZIP 07724)",
            "tone_mode": counter_gen["tone_mode"],
            "operating_mode": operating_mode,
            "dealer_name": dealer_name,
            "vin": vin,
        },
        "tool_events": tool_events,
    }



class NegotiatorDashboardHandler(SimpleHTTPRequestHandler):
    """Serves static files from /public and JSON API routes under /api/*."""

    timeout = 15

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    def end_headers(self) -> None:
        self.send_header(
            "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
        )
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send_json({"ok": True})

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/negotiator"):
            self.path = "/negotiator.html"
        elif parsed.path == "/otd-calculator":
            self.path = "/otd-calculator.html"
        elif parsed.path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        return super().do_HEAD()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/negotiator"):
            self.path = "/negotiator.html"
            return super().do_GET()
        if parsed.path == "/otd-calculator":
            self.path = "/otd-calculator.html"
            return super().do_GET()
        if parsed.path.startswith("/__/firebase/"):
            stub = b"/* Firebase Hosting SDK local stub (using /api/* REST backend) */\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(stub)))
            self.end_headers()
            self.wfile.write(stub)
            return

        if parsed.path == "/api/dashboard":
            dispatch_ready_messages(force_send_all_queued=False)
            dash = get_negotiation_dashboard()
            dealer_ids = {d.get("id") for d in dash.get("dealers_ranked", [])}
            camp_vehicle = (dash.get("campaign") or {}).get("vehicle", "")
            if (
                not dash.get("campaign")
                or "dlr-raceway-kia-of-freehold" not in dealer_ids
                or "hybrid" not in camp_vehicle.lower()
            ):
                dash = seed_sample_negotiation_campaign(deal_type="PURCHASE")
            dash["best_offer"] = get_best_competing_offer()
            dash["monmouth_ocean_dealers"] = MONMOUTH_OCEAN_KIA_DEALERS
            dash["comms_status"] = get_live_comms_configuration_status()
            dash["agent_metadata"] = {
                "name": root_agent.name,
                "display_name": getattr(root_agent, "agent_display_name", "Herbie"),
                "model": root_agent.model,
                "tools_count": len(root_agent.tools),
            }
            self._send_json(dash)
            return

        if parsed.path == "/api/comms_status":
            self._send_json(get_live_comms_configuration_status())
            return

        if parsed.path == "/api/dealer_thread":
            from urllib.parse import parse_qs

            qs = parse_qs(parsed.query)
            dealer_id = (qs.get("dealer_id", ["dlr-raceway-kia-of-freehold"])[0] or "").strip()
            self._send_json(get_dealer_conversation_thread(dealer_id))
            return

        if parsed.path == "/api/monmouth_ocean_dealers":
            self._send_json({"dealers": MONMOUTH_OCEAN_KIA_DEALERS})
            return

        if parsed.path == "/api/dealer_inventory":
            from urllib.parse import parse_qs

            qs = parse_qs(parsed.query)
            filter_dealer = (qs.get("dealer", [""])[0] or "").strip()
            filter_trim = (qs.get("trim", [""])[0] or "").strip()
            filter_interior = (qs.get("interior", [""])[0] or "").strip()
            filter_status = (qs.get("status", [""])[0] or "").strip()
            sort_by = (qs.get("sort", ["otd_asc"])[0] or "otd_asc").strip()
            items = build_enriched_dealer_inventory(
                filter_dealer=filter_dealer,
                filter_trim=filter_trim,
                filter_interior=filter_interior,
                filter_status=filter_status,
                sort_by=sort_by,
            )
            self._send_json(
                {
                    "ok": True,
                    "count": len(items),
                    "filter_dealer": filter_dealer or "ALL",
                    "filter_trim": filter_trim or "ALL",
                    "filter_interior": filter_interior or "ALL",
                    "filter_status": filter_status or "ALL",
                    "sort_by": sort_by,
                    "dealer_inventory": items,
                }
            )
            return

        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = (
            self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        )
        try:
            data = json.loads(raw_body) if raw_body else {}
        except Exception:
            data = {}

        if parsed.path == "/api/calculate_nj_payments":
            res = calculate_nj_payments(
                msrp=float(data.get("msrp", 56035.0)),
                selling_price=float(data.get("selling_price", 53850.0)),
                doc_fee=float(data.get("doc_fee", 689.0)),
                mandatory_addons=float(data.get("mandatory_addons", 0.0)),
                rebates=float(data.get("rebates", 750.0)),
                cash_down=float(data.get("cash_down", 3000.0)),
                target_monthly_budget=float(data.get("target_monthly_budget", 750.0)),
                loan_apr_percent=float(data.get("loan_apr_percent", 4.49)),
                loan_term_months=int(data.get("loan_term_months", 60)),
                lease_term_months=int(data.get("lease_term_months", 36)),
                residual_percent=float(data.get("residual_percent", 62.0)),
                base_mf=float(data.get("base_mf", 0.00245)),
                mv_tire_fees=float(data.get("mv_tire_fees", 400.0)),
                tax_rate_percent=float(data.get("tax_rate_percent", 6.625)),
            )
            self._send_json(res)
            return

        if parsed.path == "/api/audit_worksheet_upload":
            msrp_val = float(data.get("msrp", 56035.0))
            selling_val = float(data.get("selling_price", 54200.0))
            quoted_mo = float(data.get("quoted_monthly_payment", 789.0))
            cash_down_val = float(data.get("cash_down", 3000.0))
            doc_val = float(data.get("doc_fee", 799.0))
            addons_val = float(data.get("mandatory_addons", 0.0))
            rebates_val = float(data.get("rebates", 750.0))
            audit_sheet = audit_uploaded_lease_sheet(
                msrp=msrp_val,
                selling_price=selling_val,
                quoted_monthly_payment=quoted_mo,
                cash_down=cash_down_val,
                residual_percent=float(data.get("residual_percent", 62.0)),
                term_months=int(data.get("term_months", 36)),
                doc_fee=doc_val,
                acquisition_fee=float(data.get("acquisition_fee", 650.0)),
                mandatory_addons=addons_val,
                rebates=rebates_val,
                mv_tire_fees=float(data.get("mv_tire_fees", 400.0)),
                base_mf=float(data.get("base_mf", 0.00245)),
            )
            hud_res = calculate_nj_payments(
                msrp=msrp_val,
                selling_price=selling_val,
                doc_fee=doc_val,
                mandatory_addons=addons_val,
                rebates=rebates_val,
                cash_down=cash_down_val,
                target_monthly_budget=float(data.get("target_monthly_budget", 750.0)),
            )
            self._send_json(
                {
                    "ok": True,
                    "operating_mode": "MODE_4_WORKSHEET_AUDIT",
                    "worksheet_audit": audit_sheet,
                    "hud_metrics": hud_res,
                    "ready_to_send_reply": {
                        "body": audit_sheet.get("ready_to_send_counter", ""),
                        "operating_mode": "MODE_4_WORKSHEET_AUDIT",
                    },
                }
            )
            return

        if parsed.path == "/api/select_inventory_vehicle":
            dealer_id_or_vin = str(
                data.get("dealer_id") or data.get("vin") or ""
            ).strip()
            lead = ensure_dealer_lead_from_inventory(dealer_id_or_vin)
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["monmouth_ocean_dealers"] = MONMOUTH_OCEAN_KIA_DEALERS
            dash["action_result"] = {"ok": bool(lead), "selected_lead": lead}
            self._send_json(dash)
            return

        if parsed.path == "/api/sync_live_inventory":
            scrape_res = scrape_monmouth_ocean_kia_inventory(update_campaign_state=True)
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["monmouth_ocean_dealers"] = MONMOUTH_OCEAN_KIA_DEALERS
            dash["scrape_result"] = scrape_res
            self._send_json(dash)
            return

        if parsed.path == "/api/agent_chat":
            prompt = str(data.get("prompt", "")).strip()
            session_id = str(data.get("session_id", "web-dashboard-session"))
            selected_vin_context = data.get("selected_vin_context") or {}
            agent_mode = str(data.get("agent_mode", "strategist"))
            compared_dealer_ids = data.get("compared_dealer_ids") or None
            if not prompt:
                self._send_json({"error": "Prompt is required"}, status=400)
                return
            agent_res = asyncio.run(
                _run_adk_agent_turn(
                    prompt=prompt,
                    session_id=session_id,
                    selected_vin_context=selected_vin_context,
                    agent_mode=agent_mode,
                    compared_dealer_ids=compared_dealer_ids,
                )
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            agent_res["dashboard"] = dash
            self._send_json(agent_res)
            return

        if parsed.path == "/api/auto_negotiate":
            dealer_ids = data.get("dealer_ids") or []
            autonomy_mode = str(data.get("autonomy_mode", "FULL_AUTOPILOT"))
            max_rounds = int(data.get("max_rounds", 3))
            auto_sim = bool(data.get("auto_simulate_replies", True))
            res = run_autonomous_multi_dealer_negotiation(
                dealer_ids=dealer_ids,
                autonomy_mode=autonomy_mode,
                max_rounds=max_rounds,
                auto_simulate_replies=auto_sim,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/log_dealer_reply":
            dealer_id = str(data.get("dealer_id", "")).strip()
            raw_reply = str(data.get("raw_reply_text", "")).strip()
            auto_draft = bool(data.get("auto_draft_counter", True))
            auto_dispatch = bool(data.get("auto_dispatch", False))
            res = parse_and_log_dealer_reply(
                dealer_id=dealer_id,
                raw_reply_text=raw_reply,
                auto_draft_counter=auto_draft,
                auto_dispatch=auto_dispatch,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/trade_in_impact":
            dealer_id = str(data.get("dealer_id", "dlr-raceway-kia-of-freehold")).strip()
            private_preview = bool(data.get("private_preview_confirmed", False))
            otd_locked = bool(data.get("otd_locked_confirmed", False))
            set_dealer_trade_in_gate(
                dealer_id=dealer_id,
                private_preview_confirmed=private_preview,
                otd_locked_confirmed=otd_locked,
            )
            trade_res = calculate_trade_in_impact(
                locked_vehicle_otd=float(data.get("locked_vehicle_otd", 54877.67)),
                msrp=float(data.get("msrp", 53890.0)),
                doc_fee=float(data.get("doc_fee", 499.0)),
                gov_fees=float(data.get("gov_fees", 385.0)),
                rebates=float(data.get("rebates", 500.0)),
                trade_in_value=float(data.get("trade_in_value", 0.0)),
                trade_in_payoff=float(data.get("trade_in_payoff", 0.0)),
                carmax_offer_floor=float(data.get("carmax_offer_floor", 0.0)),
                tax_rate_percent=float(data.get("tax_rate_percent", 6.625)),
                cash_down_extra=float(data.get("cash_down_extra", 0.0)),
                loan_apr_percent=float(data.get("loan_apr_percent", 4.9)),
                residual_percent=float(data.get("residual_percent", 62.0)),
                money_factor=float(data.get("money_factor", 0.00190)),
                private_preview_confirmed=private_preview,
                otd_locked_confirmed=otd_locked,
                post_trade_dealer_selling_price=float(
                    data.get("post_trade_dealer_selling_price", 0.0)
                ),
                locked_pre_trade_selling_price=float(
                    data.get("locked_pre_trade_selling_price", 0.0)
                ),
                post_trade_doc_or_addons_delta=float(
                    data.get("post_trade_doc_or_addons_delta", 0.0)
                ),
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            self._send_json({"ok": True, "trade_in_impact": trade_res, "dashboard": dash})
            return

        if parsed.path == "/api/update_campaign_settings":
            vehicle = str(
                data.get(
                    "vehicle",
                    "2027 Kia Telluride Hybrid (Priority: #1 Hybrid SX X-Line, #2 Hybrid SX Prestige, #3 Hybrid SX Prestige X-Line — Non-Black Interior Nice-to-Have)",
                )
            ).strip()
            deal_type = str(data.get("deal_type", "PURCHASE")).strip()
            target_msrp = float(data.get("target_msrp", 53890.0))
            zip_code = str(data.get("zip_code", "07724")).strip()
            autonomy_mode = str(data.get("autonomy_mode", "FULL_AUTOPILOT")).strip()
            prev_pct = float(data.get("prevailing_market_discount_pct", 3.8))
            stretch_pct = float(data.get("stretch_target_discount_pct", 5.0))
            inv_pct = float(data.get("invoice_below_msrp_pct", 3.8))
            notes = str(data.get("market_intel_notes", "")).strip()

            target_otd_or_mo = (
                605.0 if deal_type.upper() == "LEASE" else round(target_msrp * (1.0 - stretch_pct / 100.0) * 1.06625 + 499.0 + 385.0 - 500.0, 2)
            )
            camp_res = create_or_update_campaign(
                vehicle=vehicle,
                deal_type=deal_type,
                target_msrp=target_msrp,
                target_otd_or_monthly=target_otd_or_mo,
                zip_code=zip_code,
                autonomy_mode=autonomy_mode,
            )
            bench_res = update_market_benchmark(
                prevailing_market_discount_pct=prev_pct,
                stretch_target_discount_pct=stretch_pct,
                invoice_below_msrp_pct=inv_pct,
                market_intel_notes=notes,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = {"campaign": camp_res, "benchmark": bench_res}
            self._send_json(dash)
            return

        if parsed.path == "/api/upsert_vin":
            dealer_name = str(data.get("dealer_name", "Monmouth County Kia")).strip()
            city = str(data.get("city", "Freehold")).strip()
            county = str(data.get("county", "Monmouth County")).strip()
            state_code = str(data.get("state", "NJ")).strip()
            vin = str(data.get("vin", "5XYP5DGC8VG099101")).strip()
            trim_level = str(
                data.get("trim_level", "2027 Kia Telluride Hybrid SX X-Line")
            ).strip()
            exterior_color = str(data.get("exterior_color", "Wolf Gray")).strip()
            interior_color = str(
                data.get("interior_color", "Terracotta Brown Leather")
            ).strip()
            msrp = float(data.get("msrp", 53890.0))
            selling_price = float(data.get("selling_price", 51842.0))
            quoted_otd = float(data.get("dealer_quoted_otd", 0.0))
            mf = float(data.get("money_factor", 0.00190))
            rv = float(data.get("residual_percent", 62.0))
            addons = float(data.get("dealer_addons_total", 0.0))
            addon_desc = str(data.get("addon_descriptions", "")).strip()
            doc_fee = float(data.get("doc_fee", 499.0))
            gov_fees = float(data.get("gov_fees", 385.0))
            rebates = float(data.get("rebates", 500.0))
            inventory_status = str(data.get("inventory_status", "IN_TRANSIT")).strip()

            lead = upsert_dealer_lead(
                dealer_name=dealer_name,
                city=city,
                county=county,
                state=state_code,
                vin=vin,
                trim_level=trim_level,
                exterior_color=exterior_color,
                interior_color=interior_color,
                msrp=msrp,
                inventory_status=inventory_status,
            )
            quote_res = record_dealer_quote(
                dealer_id=lead["id"],
                selling_price=selling_price,
                dealer_quoted_otd=quoted_otd,
                money_factor=mf,
                residual_percent=rv,
                dealer_addons_total=addons,
                addon_descriptions=addon_desc,
                doc_fee=doc_fee,
                gov_fees=gov_fees,
                rebates=rebates,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = quote_res
            self._send_json(dash)
            return

        if parsed.path == "/api/audit_quote":
            res = audit_dealer_quote(
                deal_type=str(data.get("deal_type", "PURCHASE")),
                msrp=float(data.get("msrp", 53890.0)),
                selling_price=float(data.get("selling_price", 51842.0)),
                dealer_quoted_otd=float(data.get("dealer_quoted_otd", 0.0)),
                dealer_quoted_monthly=float(data.get("dealer_quoted_monthly", 0.0)),
                dealer_quoted_drive_off=float(data.get("dealer_quoted_drive_off", 0.0)),
                residual_percent=float(data.get("residual_percent", 62.0)),
                money_factor=float(data.get("money_factor", 0.00190)),
                base_buy_rate_mf=float(data.get("base_buy_rate_mf", 0.00190)),
                term_months=int(data.get("term_months", 36)),
                annual_miles=int(data.get("annual_miles", 12000)),
                doc_fee=float(data.get("doc_fee", 499.0)),
                gov_fees=float(data.get("gov_fees", 385.0)),
                dealer_addons_total=float(data.get("dealer_addons_total", 0.0)),
                rebates=float(data.get("rebates", 500.0)),
                tax_rate_percent=float(data.get("tax_rate_percent", 6.625)),
            )
            self._send_json(res)
            return

        if parsed.path == "/api/seed":
            deal_type = data.get("deal_type", "PURCHASE")
            vehicle = data.get(
                "vehicle",
                "2027 Kia Telluride Hybrid (Priority: #1 Hybrid SX X-Line, #2 Hybrid SX Prestige, #3 Hybrid SX Prestige X-Line — Non-Black Interior Nice-to-Have)",
            )
            msrp = float(data.get("target_msrp", 53890.0))
            target_val = (
                605.0 if deal_type.upper() == "LEASE" else round(msrp * 1.0183, 2)
            )
            dash = seed_sample_negotiation_campaign(
                vehicle=vehicle,
                deal_type=deal_type,
                target_msrp=msrp,
                target_otd_or_monthly=target_val,
                reset_existing=True,
            )
            dash["best_offer"] = get_best_competing_offer()
            self._send_json(dash)
            return

        if parsed.path == "/api/calculate_from_otd":
            res = calculate_monthly_from_otd(
                otd_price=float(data.get("otd_price", 54877.67)),
                msrp=float(data.get("msrp", 53890.0)),
                doc_fee=float(data.get("doc_fee", 499.0)),
                gov_fees=float(data.get("gov_fees", 385.0)),
                tax_rate_percent=float(data.get("tax_rate_percent", 6.625)),
                rebates=float(data.get("rebates", 500.0)),
                cash_down_or_trade_equity=float(data.get("cash_down_payment", 0.0)),
                loan_apr_percent=float(data.get("apr_percent", 4.9)),
                loan_term_months=int(data.get("loan_term_months", 60)),
                residual_percent=float(data.get("residual_percent", 62.0)),
                money_factor=float(data.get("money_factor", 0.00190)),
                lease_term_months=int(data.get("lease_term_months", 36)),
                lease_annual_miles=int(data.get("annual_miles", 12000)),
                acquisition_fee=float(data.get("acquisition_fee", 650.0)),
            )
            self._send_json(res)
            return

        if parsed.path == "/api/update_market_benchmark":
            prev_pct = float(data.get("prevailing_market_discount_pct", 3.8))
            stretch_pct = float(data.get("stretch_target_discount_pct", 5.0))
            inv_pct = float(data.get("invoice_below_msrp_pct", 3.8))
            notes = data.get("market_intel_notes", "")
            bench_res = update_market_benchmark(
                prevailing_market_discount_pct=prev_pct,
                stretch_target_discount_pct=stretch_pct,
                invoice_below_msrp_pct=inv_pct,
                market_intel_notes=notes,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = bench_res
            self._send_json(dash)
            return

        if parsed.path == "/api/approve":
            msg_id = data.get("message_id", "")
            send_now = bool(data.get("send_immediately_override", False))
            edited_body = data.get("edited_body", "")
            res = approve_queued_message(
                message_id=msg_id,
                send_immediately_override=send_now,
                edited_body=edited_body,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/dispatch_ready":
            force_all = bool(data.get("force_send_all_queued", True))
            res = dispatch_ready_messages(force_send_all_queued=force_all)
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/draft_counter":
            dealer_id = data.get("dealer_id", "")
            body = data.get("body", "")
            channel = data.get("channel", "SMS_PERSONAL")
            res = queue_dealer_message(dealer_id=dealer_id, body=body, channel=channel)
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/simulate_reply":
            dealer_id = data.get("dealer_id", "")
            res = simulate_dealer_reply(dealer_id=dealer_id)
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["comms_status"] = get_live_comms_configuration_status()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/transport_mode":
            mode = str(data.get("mode", "SIMULATION")).strip().upper()
            res = set_transport_mode(mode=mode)
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["comms_status"] = get_live_comms_configuration_status()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/configure_comms":
            status_res = configure_personal_comms_credentials(
                gmail_user_email=str(data.get("gmail_user_email", "")),
                gmail_app_password=str(data.get("gmail_app_password", "")),
                personal_sms_gateway_url=str(data.get("personal_sms_gateway_url", "")),
                personal_sms_gateway_token=str(data.get("personal_sms_gateway_token", "")),
                buyer_personal_phone=str(data.get("buyer_personal_phone", "")),
                transport_mode=str(data.get("transport_mode", "")),
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["comms_status"] = status_res
            dash["action_result"] = status_res
            self._send_json(dash)
            return

        if parsed.path == "/api/update_dealer_contact":
            res = update_dealer_contact_info(
                dealer_id=str(data.get("dealer_id", "")),
                contact_name=str(data.get("contact_name") or data.get("salesperson_name") or ""),
                contact_phone=str(data.get("contact_phone") or data.get("phone") or ""),
                contact_email=str(data.get("contact_email") or data.get("email") or ""),
                preferred_channel=str(data.get("preferred_channel", "")),
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["comms_status"] = get_live_comms_configuration_status()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path in ("/api/webhooks/inbound_sms", "/api/webhooks/inbound_email"):
            default_chan = "EMAIL" if parsed.path.endswith("inbound_email") else "SMS_PERSONAL"
            sender = (
                data.get("sender_contact")
                or data.get("from_phone")
                or data.get("from_email")
                or data.get("from")
                or ""
            )
            body_txt = (
                data.get("raw_body")
                or data.get("body")
                or data.get("message")
                or data.get("text")
                or ""
            )
            res = ingest_live_dealer_message(
                dealer_id=str(data.get("dealer_id", "")),
                sender_contact=str(sender),
                channel=str(data.get("channel", default_chan)),
                raw_body=str(body_txt),
                subject=str(data.get("subject", "")),
                external_message_id=str(data.get("external_message_id", data.get("message_id", ""))),
                thread_id=str(data.get("thread_id", "")),
                in_reply_to=str(data.get("in_reply_to", "")),
                references=str(data.get("references", "")),
                auto_draft_counter=bool(data.get("auto_draft_counter", True)),
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["comms_status"] = get_live_comms_configuration_status()
            dash["action_result"] = res
            self._send_json(dash)
            return

        if parsed.path == "/api/poll_gmail":
            max_msgs = int(data.get("max_messages", 10))
            filter_email = str(data.get("dealer_email_filter", ""))
            res = poll_personal_gmail_inbox(
                max_messages=max_msgs,
                dealer_email_filter=filter_email,
            )
            dash = get_negotiation_dashboard()
            dash["best_offer"] = get_best_competing_offer()
            dash["comms_status"] = get_live_comms_configuration_status()
            dash["action_result"] = res
            self._send_json(dash)
            return

        self._send_json({"error": f"Unknown endpoint {parsed.path}"}, status=404)


import subprocess
import threading
import time


class IPv4ThreadingHTTPServer(ThreadingHTTPServer):
    """Explicit AF_INET (0.0.0.0) HTTP server — populates /proc/net/tcp required by ChromeOS Crostini garcon/chunneld."""

    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        host, port = self.server_address[:2]
        self.server_name = "localhost"
        self.server_port = port


class IPv6OnlyThreadingHTTPServer(ThreadingHTTPServer):
    """Explicit AF_INET6 (::) HTTP server with IPV6_V6ONLY=1 so it coexists with AF_INET on the same port."""

    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        host, port = self.server_address[:2]
        self.server_name = "localhost"
        self.server_port = port


def _get_container_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("100.115.92.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _terminate_stale_server_processes() -> None:
    """Terminates any older dashboard_server.py processes holding port 8090."""
    my_pid = os.getpid()
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return
    killed = []
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == my_pid:
            continue
        try:
            cmdline = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode("utf-8", errors="ignore")
            )
            if "dashboard_server.py" in cmdline:
                os.kill(pid, 9)
                killed.append(pid)
        except Exception:
            pass
    if killed:
        log_structured_event(
            "dashboard_server.terminated_stale_processes",
            level="INFO",
            killed_pids=killed,
        )
        time.sleep(0.4)


def _bind_port_both_stacks(port: int) -> List[ThreadingHTTPServer]:
    servers: List[ThreadingHTTPServer] = []
    try:
        srv4 = IPv4ThreadingHTTPServer(("0.0.0.0", port), NegotiatorDashboardHandler)
        servers.append(srv4)
        log_structured_event("dashboard_server.bound_ipv4", level="INFO", port=port)
    except OSError as e4:
        log_structured_event(
            "dashboard_server.bind_ipv4_failed",
            level="WARNING",
            port=port,
            error=str(e4),
        )
    try:
        srv6 = IPv6OnlyThreadingHTTPServer(("::", port), NegotiatorDashboardHandler)
        servers.append(srv6)
        log_structured_event("dashboard_server.bound_ipv6", level="INFO", port=port)
    except OSError as e6:
        log_structured_event(
            "dashboard_server.bind_ipv6_failed",
            level="WARNING",
            port=port,
            error=str(e6),
        )
    return servers


def _log_network_diagnostics() -> None:
    try:
        out = subprocess.check_output(
            ["ip", "-br", "addr"], stderr=subprocess.STDOUT, text=True, timeout=2
        )
        print("=== NETWORK INTERFACES ===\n" + out.strip(), flush=True)
    except Exception as e:
        print(f"ip addr check failed: {e}", flush=True)
    try:
        out = subprocess.check_output(
            ["systemctl", "--user", "status", "cros-garcon.service", "--no-pager"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=2,
        )
        print("=== CROS-GARCON STATUS ===\n" + out[:800].strip(), flush=True)
    except Exception as e:
        print(f"cros-garcon check: {e}", flush=True)


def run_server(port: int = 8090) -> None:
    _terminate_stale_server_processes()
    os.environ["FIRESTORE_EMULATOR_HOST"] = ""

    if "--daemon" in sys.argv and hasattr(os, "fork"):
        log_file = BASE_DIR / "data" / "dashboard_server.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        pid = os.fork()
        if pid > 0:
            import time as _t

            _t.sleep(0.6)
            print(f"Detached dashboard_server daemon (PID {pid}) on http://localhost:{port}/negotiator.html", flush=True)
            os._exit(0)
        os.setsid()
        with open(os.devnull, "rb") as devnull_in, open(log_file, "ab", buffering=0) as log_out:
            os.dup2(devnull_in.fileno(), sys.stdin.fileno())
            os.dup2(log_out.fileno(), sys.stdout.fileno())
            os.dup2(log_out.fileno(), sys.stderr.fileno())

    _log_network_diagnostics()

    dash = get_negotiation_dashboard()
    dealer_ids = {d.get("id") for d in dash.get("dealers_ranked", [])}
    camp_vehicle = (dash.get("campaign") or {}).get("vehicle", "")
    if (
        "dlr-raceway-kia-of-freehold" not in dealer_ids
        or "hybrid" not in camp_vehicle.lower()
    ):
        seed_sample_negotiation_campaign(deal_type="PURCHASE")

    primary_servers = _bind_port_both_stacks(port)
    for srv in primary_servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    for extra_port in (8080, 8000, 5000, 3000, 8888):
        if extra_port != port:
            aux_servers = _bind_port_both_stacks(extra_port)
            for aux_srv in aux_servers:
                threading.Thread(target=aux_srv.serve_forever, daemon=True).start()

    container_ip = _get_container_ip()
    log_structured_event(
        "dashboard_server.started",
        level="INFO",
        port=port,
        urls=[
            f"http://localhost:{port}/negotiator.html",
            f"http://localhost:8080/negotiator.html",
            f"http://localhost:8000/negotiator.html",
            f"http://{container_ip}:{port}/negotiator.html",
        ],
    )

    if primary_servers:
        primary_servers[0].serve_forever()
    else:
        raise RuntimeError(f"Could not bind port {port} on either IPv4 or IPv6")


if __name__ == "__main__":
    run_server()





