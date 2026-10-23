"""
Automated Verification Suite for the Autonomous Car Purchase & Lease Negotiation Agent.
Tests:
1. OTD Purchase calculations & junk-fee redline auditing (6.625% NJ Sales Tax)
2. Reverse OTD-to-Monthly Payment Calculator (`calculate_monthly_from_otd`) working backward from OTD price to Implied Selling Price, Monthly Financed Purchase, and 36-Month Lease payments
3. Lease calculations (Depreciation + Rent Charge, MF->APR, MF markup detection)
4. Copilot Approval Queue, Anti-Bot Variable Stealth Wait Timer & Trade-In Secrecy Guardrail
5. Monmouth & Ocean County, NJ dealership priority, 2027 Kia Telluride Hybrid requirement, Trim Priority (#1 Hybrid SX X-Line, #2 Hybrid SX Prestige, #3 Hybrid SX Prestige X-Line), and Non-Black Interior preference
6. Dynamic Prevailing Market Comps Benchmark (`get_prevailing_market_benchmark` & `update_market_benchmark`)
7. Multi-round 3-dealer 2027 Kia Telluride Hybrid IN-TRANSIT competitive negotiation simulation
8. AgentOps Production Architecture:
   - Tool docstrings (`Args:` / `Returns:`), Pydantic input/output schemas, and guided error recovery instructions
   - Persistent session service (`PersistentFirestoreSessionService`) and semantic RAG memory (`SemanticDealMemoryStore`)
   - Multi-agent orchestration (`sub_agents` + `SequentialAgent`), safety guardrails, self-evaluation, and HITL confirmation hooks
   - Structured JSON logging and `@instrument_tool` pre/post tool execution logging
   - Secret Manager integration (no hardcoded API keys in `.env`) and Firebase configuration (`firebase.json`, `requirements.txt`)
"""

from pathlib import Path
import sys
import unittest

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if __package__:
    from .agent import (
        app,
        deal_audit_and_counter_pipeline,
        financial_auditor_agent,
        inventory_research_agent,
        negotiation_comms_agent,
        root_agent,
    )
    from .tools import (
        DEFAULT_SAFETY_SETTINGS,
        OTDCalculationInputSchema,
        OTDCalculationOutputSchema,
        PersistentFirestoreSessionService,
        approve_queued_message,
        audit_dealer_quote,
        calculate_color_parity_benchmark,
        calculate_lease_payment,
        calculate_monthly_from_otd,
        calculate_otd_price,
        calculate_trade_in_impact,
        dealer_initiated_accounting_jargon,
        dispatch_ready_messages,
        evaluate_agent_response_policy,
        generate_natural_buyer_counter,
        get_best_competing_offer,
        get_dealer_conversation_thread,
        get_live_comms_configuration_status,
        get_negotiation_dashboard,
        get_prevailing_market_benchmark,
        get_secret,
        get_transport_mode,
        ingest_live_dealer_message,
        is_placeholder_contact,
        log_structured_event,
        log_tool_intent,
        log_tool_outcome,
        parse_and_log_dealer_reply,
        queue_dealer_message,
        record_dealer_quote,
        require_human_confirmation_hook,
        run_autonomous_multi_dealer_negotiation,
        search_negotiation_memory,
        seed_sample_negotiation_campaign,
        send_via_personal_gmail,
        set_dealer_trade_in_gate,
        set_transport_mode,
        simulate_dealer_reply,
        update_dealer_contact_info,
        update_market_benchmark,
        upsert_dealer_lead,
        upsert_document,
    )
    from .tools.inventory_scraper import (
        MONMOUTH_OCEAN_KIA_DEALERS,
        _extract_vehicles_from_html,
        _extract_vehicles_from_intercepted_payloads,
        _should_block_request,
    )
else:
    from agent import (
        app,
        deal_audit_and_counter_pipeline,
        financial_auditor_agent,
        inventory_research_agent,
        negotiation_comms_agent,
        root_agent,
    )
    from tools import (
        DEFAULT_SAFETY_SETTINGS,
        OTDCalculationInputSchema,
        OTDCalculationOutputSchema,
        PersistentFirestoreSessionService,
        approve_queued_message,
        audit_dealer_quote,
        calculate_color_parity_benchmark,
        calculate_lease_payment,
        calculate_monthly_from_otd,
        calculate_otd_price,
        calculate_trade_in_impact,
        dealer_initiated_accounting_jargon,
        dispatch_ready_messages,
        evaluate_agent_response_policy,
        generate_natural_buyer_counter,
        get_best_competing_offer,
        get_dealer_conversation_thread,
        get_live_comms_configuration_status,
        get_negotiation_dashboard,
        get_prevailing_market_benchmark,
        get_secret,
        get_transport_mode,
        ingest_live_dealer_message,
        is_placeholder_contact,
        log_structured_event,
        log_tool_intent,
        log_tool_outcome,
        parse_and_log_dealer_reply,
        queue_dealer_message,
        record_dealer_quote,
        require_human_confirmation_hook,
        run_autonomous_multi_dealer_negotiation,
        search_negotiation_memory,
        seed_sample_negotiation_campaign,
        send_via_personal_gmail,
        set_dealer_trade_in_gate,
        set_transport_mode,
        simulate_dealer_reply,
        update_dealer_contact_info,
        update_market_benchmark,
        upsert_dealer_lead,
        upsert_document,
    )
    from tools.inventory_scraper import (
        MONMOUTH_OCEAN_KIA_DEALERS,
        _extract_vehicles_from_html,
        _extract_vehicles_from_intercepted_payloads,
        _should_block_request,
    )


class TestCarNegotiator(unittest.TestCase):
    def test_otd_purchase_calculation_and_red_flags(self):
        res = calculate_otd_price(
            msrp=53890.0,
            selling_price=53000.0,
            doc_fee=599.0,
            gov_fees=385.0,
            tax_rate_percent=6.625,
            dealer_addons_total=1495.0,
            rebates=500.0,
            addon_descriptions="Nitrogen Tires, LoJack",
            prevailing_market_discount_pct=3.8,
            stretch_counter_discount_pct=5.0,
        )
        self.assertEqual(res["deal_type"], "PURCHASE")
        self.assertGreater(res["otd_total"], 56000.0)
        self.assertGreater(res["potential_addon_savings"], 1495.0)
        self.assertTrue(any("add-ons" in flag.lower() for flag in res["red_flags"]))
        self.assertTrue(any("prevailing nj market comp" in flag.lower() for flag in res["red_flags"]))

    def test_reverse_otd_to_monthly_calculator(self):
        # First compute forward OTD on Priority #1 Hybrid SX X-Line ($53,890 MSRP, $51,842 invoice selling price)
        fwd = calculate_otd_price(
            msrp=53890.0,
            selling_price=51842.0,
            doc_fee=499.0,
            gov_fees=385.0,
            tax_rate_percent=6.625,
            dealer_addons_total=0.0,
            rebates=500.0,
        )
        otd_price = fwd["otd_total"]

        # Now work backward from OTD price
        rev = calculate_monthly_from_otd(
            otd_price=otd_price,
            msrp=53890.0,
            doc_fee=499.0,
            gov_fees=385.0,
            tax_rate_percent=6.625,
            rebates=500.0,
            cash_down_or_trade_equity=0.0,
            loan_apr_percent=4.9,
            loan_term_months=60,
            residual_percent=62.0,
            money_factor=0.00190,
            lease_term_months=36,
            lease_annual_miles=12000,
        )
        breakdown = rev["reverse_otd_breakdown"]
        self.assertAlmostEqual(breakdown["implied_selling_price_cap_cost"], 51842.0, places=0)
        self.assertAlmostEqual(breakdown["implied_discount_pct"], 3.8, places=1)
        self.assertGreater(rev["finance_purchase_from_otd"]["monthly_loan_payment"], 900.0)
        self.assertGreater(rev["lease_from_otd_cap_cost"]["total_monthly_payment"], 550.0)
        self.assertGreater(
            rev["lease_from_otd_cap_cost"]["sign_and_drive_zero_due_monthly"],
            rev["lease_from_otd_cap_cost"]["total_monthly_payment"],
        )

    def test_lease_calculation_and_mf_markup_detection(self):
        res = calculate_lease_payment(
            msrp=55590.0,
            selling_price=52000.0,
            residual_percent=62.0,
            money_factor=0.00250,
            term_months=36,
            annual_miles=12000,
            tax_rate_percent=6.625,
            dealer_addons_total=695.0,
            rebates_and_lease_cash=1000.0,
            cap_cost_reduction_down=0.0,
            base_buy_rate_mf=0.00190,
        )
        self.assertEqual(res["deal_type"], "LEASE")
        self.assertAlmostEqual(res["equivalent_apr"], 6.0, places=2)
        self.assertAlmostEqual(res["buy_rate_apr"], 4.56, places=2)
        self.assertGreater(res["mf_markup_extra_cost"], 1000.0)
        self.assertTrue(any("money factor marked up" in f.lower() for f in res["red_flags"]))

    def test_prevailing_market_benchmark_updates_dynamically(self):
        seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        bench = get_prevailing_market_benchmark("2027 Kia Telluride Hybrid SX X-Line", msrp=53890.0)
        self.assertEqual(bench["prevailing_market_discount_pct"], 3.8)
        self.assertEqual(bench["stretch_target_discount_pct"], 5.0)
        self.assertEqual(bench["zip_code"], "07724")

        # Update prevailing market benchmark based on new market comps (e.g. 4.5% prevailing, 6.0% stretch)
        updated = update_market_benchmark(
            prevailing_market_discount_pct=4.5,
            stretch_target_discount_pct=6.0,
            market_intel_notes="Year-end December NJ Kia volume bonus unlocked 6% off MSRP on in-transit Hybrid units.",
        )
        self.assertEqual(updated["prevailing_market_discount_pct"], 4.5)
        self.assertEqual(updated["stretch_target_discount_pct"], 6.0)

        dash = get_negotiation_dashboard()
        first_dealer = dash["dealers_ranked"][0]
        # 6% off $53,890 = $50,657
        self.assertEqual(first_dealer["recommended_counter_selling_price"], round(53890.0 * 0.94, 0))

    def test_trade_in_secrecy_guardrail_and_copilot_stealth_timer(self):
        seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        draft = queue_dealer_message(
            dealer_id="dlr-raceway-kia-of-freehold",
            body="Hi Marcus, can you beat $54,200 OTD on the in-transit 2027 Kia Telluride Hybrid SX X-Line? Also I have a trade-in and my current lease is up at the end of December.",
            channel="SMS_PERSONAL",
        )
        self.assertEqual(draft["status"], "PENDING_APPROVAL")
        self.assertNotIn("trade-in", draft["body"].lower())
        self.assertNotIn("current lease", draft["body"].lower())
        self.assertIn("TRADE-IN SECRECY GUARDRAIL TRIGGERED", draft["guardrail_warning"])
        self.assertIn("sms:+17325550142", draft["sms_deep_link"])
        self.assertGreaterEqual(draft["delay_seconds"], 900)

        approval = approve_queued_message(draft["id"], send_immediately_override=False)
        self.assertEqual(approval["mode"], "STEALTH_TIMER_QUEUED")
        approved_msg = approval["approved_messages"][0]
        self.assertEqual(approved_msg["status"], "APPROVED_QUEUED")

        check_res = dispatch_ready_messages(force_send_all_queued=False)
        self.assertGreaterEqual(len(check_res["still_waiting_in_stealth_queue"]), 1)

    def test_nj_monmouth_ocean_priority_hybrid_trims_and_non_black_interior(self):
        dash = seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        self.assertEqual(dash["campaign"]["zip_code"], "07724")
        dealers = dash["dealers_ranked"]
        self.assertEqual(len(dealers), 3)

        counties = {d["county"] for d in dealers}
        self.assertEqual(counties, {"Monmouth County", "Ocean County"})
        for d in dealers:
            self.assertEqual(d["state"], "NJ")
            self.assertEqual(d["county_priority_tier"], 1)
            self.assertTrue(d["is_hybrid"])
            self.assertTrue(d["is_non_black_interior"])
            self.assertGreater(d["implied_finance_60mo"], 800.0)
            self.assertGreater(d["implied_lease_36mo"], 500.0)
            self.assertIn("color_parity_otd", d)

        self.assertEqual(dealers[0]["trim_level"], "2027 Kia Telluride Hybrid SX X-Line")
        self.assertEqual(dealers[0]["trim_priority_rank"], 1)
        self.assertEqual(dealers[1]["trim_level"], "2027 Kia Telluride Hybrid SX Prestige")
        self.assertEqual(dealers[1]["trim_priority_rank"], 2)
        self.assertEqual(dealers[2]["trim_level"], "2027 Kia Telluride Hybrid SX Prestige X-Line")
        self.assertEqual(dealers[2]["trim_priority_rank"], 3)

        bad_lead = upsert_dealer_lead(
            dealer_name="Out Of State Kia",
            city="Stamford",
            county="Fairfield County",
            state="CT",
            trim_level="2027 Kia Telluride V6 SX X-Line",
            interior_color="Black Leather",
        )
        audit_res = record_dealer_quote(dealer_id=bad_lead["id"], selling_price=51000.0)
        flags_joined = " ".join(audit_res["dealer_lead"]["red_flags"])
        self.assertIn("POWERTRAIN VIOLATION", flags_joined)
        self.assertIn("LOCATION VIOLATION", flags_joined)
        self.assertIn("INTERIOR NOTE", flags_joined)

    def test_multi_round_2027_kia_telluride_hybrid_in_transit_competition(self):
        dash_r1 = seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        self.assertIn("2027 Kia Telluride Hybrid", dash_r1["campaign"]["vehicle"])
        self.assertEqual(dash_r1["campaign"]["target_delivery_window"], "End of December 2026")
        best_r1 = get_best_competing_offer()
        self.assertTrue(best_r1["has_competing_offer"])
        r1_best_otd = best_r1["best_otd_total"]

        approve_queued_message("ALL", send_immediately_override=False)
        dispatch_ready_messages(force_send_all_queued=True)

        best_r2 = get_best_competing_offer()
        r2_best_otd = best_r2["best_otd_total"]
        self.assertLess(r2_best_otd, r1_best_otd)

        dash_r2 = get_negotiation_dashboard()
        for d in dash_r2["dealers_ranked"]:
            self.assertEqual(d["dealer_addons_total"], 0.0)

    def test_financial_edge_cases_for_negotiation_accuracy(self):
        # Edge Case 7: Lease Money Factor markup (0.00245 vs 0.00190) + $49.85/mo payment padding
        lease_audit = audit_dealer_quote(
            deal_type="LEASE",
            msrp=56985.0,
            selling_price=54700.0,
            dealer_quoted_monthly=865.0,
            residual_percent=62.0,
            money_factor=0.00245,
            base_buy_rate_mf=0.00190,
            term_months=36,
            annual_miles=12000,
            doc_fee=699.0,
            gov_fees=385.0,
            dealer_addons_total=695.0,
            rebates=500.0,
            tax_rate_percent=6.625,
        )
        self.assertAlmostEqual(lease_audit["mf_markup_extra_cost"], 1904.82, places=2)
        self.assertAlmostEqual(lease_audit["unexplained_monthly_padding"], 49.85, places=2)
        self.assertAlmostEqual(lease_audit["recommended_counter_monthly"], 722.40, places=2)

        # Edge Case 8: Unexplained OTD padding ($57,200 quoted vs $56,788.56 math) + $899 NJ Doc Fee
        otd_audit = audit_dealer_quote(
            deal_type="PURCHASE",
            msrp=55590.0,
            selling_price=52500.0,
            dealer_quoted_otd=57200.0,
            doc_fee=899.0,
            gov_fees=385.0,
            dealer_addons_total=0.0,
            rebates=500.0,
            tax_rate_percent=6.625,
        )
        self.assertAlmostEqual(otd_audit["otd_total"], 56788.56, places=2)
        self.assertAlmostEqual(otd_audit["unexplained_otd_padding"], 411.44, places=2)
        self.assertTrue(any("high nj doc fee" in f.lower() for f in otd_audit["red_flags"]))

        # Edge Case 9: Non-Black interior (+$295 MSRP) vs Black interior OTD & residual comparison
        non_black = calculate_monthly_from_otd(
            otd_price=54950.0,
            msrp=54185.0,
            doc_fee=499.0,
            gov_fees=385.0,
            tax_rate_percent=6.625,
            rebates=500.0,
            residual_percent=62.0,
            money_factor=0.00190,
        )
        black = calculate_monthly_from_otd(
            otd_price=54800.0,
            msrp=53890.0,
            doc_fee=499.0,
            gov_fees=385.0,
            tax_rate_percent=6.625,
            rebates=500.0,
            residual_percent=62.0,
            money_factor=0.00190,
        )
        self.assertGreater(
            non_black["reverse_otd_breakdown"]["implied_discount_pct"],
            black["reverse_otd_breakdown"]["implied_discount_pct"],
        )
        self.assertLess(
            non_black["lease_from_otd_cap_cost"]["total_monthly_payment"],
            black["lease_from_otd_cap_cost"]["total_monthly_payment"],
        )

    def test_zero_buzzword_default_and_reactive_mirror_exception(self):
        # 1. Default outbound counter (when dealer does NOT use accounting jargon) must contain ZERO buzzwords
        default_counter = generate_natural_buyer_counter(
            dealer_name="Matt Blatt Kia of Toms River",
            contact_name="Devon",
            vin="5XYP5DGC2VG048319",
            trim_level="2027 Kia Telluride Hybrid SX Prestige",
            interior_color="Sage Green Leather",
            stock_status="IN_TRANSIT",
            target_otd=55900.0,
            target_selling_price=52811.0,
            competing_dealer_name="Raceway Kia of Freehold",
            competing_otd=55303.0,
            round_num=2,
            dealer_inbound_text="Hi, we can offer $54,480 selling price and $58,450 OTD on the incoming Telluride Hybrid.",
            has_addons=True,
            addon_descriptions="Appearance Package",
        )
        self.assertEqual(default_counter["tone_mode"], "ZERO_BUZZWORD_NATURAL")
        body_low = default_counter["body"].lower()
        for forbidden_buzzword in ["invoice", "holdback", "floorplan", "pdi hold", "volume bonus"]:
            self.assertNotIn(forbidden_buzzword, body_low)
        self.assertLessEqual(default_counter["sentence_count"], 3)

        # 2. Reactive Mirror Exception: when dealer brings up invoice/losing money/floorplan first
        self.assertTrue(
            dealer_initiated_accounting_jargon(
                "We are already at invoice and losing money on this 2027 Telluride Hybrid."
            )
        )
        reactive_counter = generate_natural_buyer_counter(
            dealer_name="Matt Blatt Kia of Toms River",
            contact_name="Devon",
            vin="5XYP5DGC2VG048319",
            trim_level="2027 Kia Telluride Hybrid SX Prestige",
            interior_color="Sage Green Leather",
            stock_status="IN_TRANSIT",
            target_otd=55900.0,
            target_selling_price=52811.0,
            competing_dealer_name="Raceway Kia of Freehold",
            competing_otd=55303.0,
            round_num=2,
            dealer_inbound_text="We are already at invoice and losing money on this 2027 Telluride Hybrid.",
        )
        self.assertEqual(reactive_counter["tone_mode"], "REACTIVE_ACCOUNTING_MIRROR")
        self.assertIn("holdback", reactive_counter["body"].lower())
        self.assertIn("floorplan", reactive_counter["body"].lower())

    def test_color_parity_benchmark_and_two_stage_trade_in_workflow(self):
        # Color-Parity math on Non-Black interior (+$295 factory option)
        cp = calculate_color_parity_benchmark(
            msrp=55590.0,
            selling_price=54480.0,
            interior_color="Sage Green Leather",
            doc_fee=799.0,
            gov_fees=385.0,
            dealer_addons_total=0.0,
            rebates=500.0,
            tax_rate_percent=6.625,
            competing_black_discount_pct=3.8,
        )
        self.assertTrue(cp["is_non_black_interior"])
        self.assertEqual(cp["factory_color_option_msrp"], 295.0)
        self.assertAlmostEqual(cp["otd_color_delta"], 314.54, places=2)
        self.assertAlmostEqual(cp["lease_monthly_36m_color_delta"], 3.11, places=1)
        self.assertGreater(cp["color_scarcity_overcharge_otd"], 1000.0)

        # Two-Stage Trade-In Gate
        seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        # Gate #1 only: Private preview ON, OTD lock OFF -> outbound gag remains active
        set_dealer_trade_in_gate(
            dealer_id="dlr-raceway-kia-of-freehold",
            private_preview_confirmed=True,
            otd_locked_confirmed=False,
        )
        t_stage1 = calculate_trade_in_impact(
            locked_vehicle_otd=55303.11,
            msrp=53890.0,
            trade_in_value=18000.0,
            trade_in_payoff=0.0,
            carmax_offer_floor=18500.0,
            private_preview_confirmed=True,
            otd_locked_confirmed=False,
        )
        self.assertTrue(t_stage1["outbound_gag_active"])
        self.assertAlmostEqual(t_stage1["nj_sales_tax_credit_savings"], 1192.50, places=2)
        self.assertEqual(t_stage1["phase_2_outbound_trade_script"], "")

        # Gate #2 ON: OTD locked in writing -> unlocks Phase 2 trade script & Shell-Game Detector
        set_dealer_trade_in_gate(
            dealer_id="dlr-raceway-kia-of-freehold",
            private_preview_confirmed=True,
            otd_locked_confirmed=True,
        )
        t_stage2 = calculate_trade_in_impact(
            locked_vehicle_otd=55303.11,
            msrp=53890.0,
            trade_in_value=18000.0,
            trade_in_payoff=0.0,
            carmax_offer_floor=18500.0,
            private_preview_confirmed=True,
            otd_locked_confirmed=True,
            locked_pre_trade_selling_price=51842.0,
            post_trade_dealer_selling_price=52842.0,
        )
        self.assertFalse(t_stage2["outbound_gag_active"])
        self.assertTrue(t_stage2["shell_game_detected"])
        self.assertIn("POST-TRADE SHELL GAME DETECTED", t_stage2["shell_game_warnings"][0])
        self.assertIn("CarMax", t_stage2["phase_2_outbound_trade_script"])

    def test_autonomous_multi_dealer_3_vehicle_negotiation_and_reply_parser(self):
        seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        auto_res = run_autonomous_multi_dealer_negotiation(
            dealer_ids=[
                "dlr-raceway-kia-of-freehold",
                "dlr-matt-blatt-kia-of-toms-river",
                "dlr-sansone-jr-s-66-automall-kia",
            ],
            autonomy_mode="FULL_AUTOPILOT",
            max_rounds=2,
            auto_simulate_replies=True,
        )
        self.assertTrue(auto_res["ok"])
        self.assertEqual(auto_res["vehicles_selected_count"], 3)
        for summary in auto_res["dealer_run_summaries"]:
            self.assertLessEqual(summary["ending_otd"], summary["starting_otd"])

        # Test unstructured dealer reply parser triggering Reactive Mirror
        parsed_res = parse_and_log_dealer_reply(
            dealer_id="dlr-matt-blatt-kia-of-toms-river",
            raw_reply_text="We are already at invoice at $53,500 selling price ($57,100 OTD) and losing money on this incoming unit.",
            auto_draft_counter=True,
            auto_dispatch=False,
        )
        self.assertTrue(parsed_res["ok"])
        self.assertTrue(parsed_res["parsed_extraction"]["jargon_detected"])
        self.assertEqual(
            parsed_res["generated_counter"]["tone_mode"],
            "REACTIVE_ACCOUNTING_MIRROR",
        )

    def test_adk_root_agent_and_multi_agent_hierarchy(self):
        self.assertEqual(root_agent.name, "car_negotiator")
        self.assertGreaterEqual(len(root_agent.tools), 25)
        sub_agent_names = [sa.name for sa in getattr(root_agent, "sub_agents", [])]
        self.assertIn("inventory_research_agent", sub_agent_names)
        self.assertIn("financial_auditor_agent", sub_agent_names)
        self.assertIn("negotiation_comms_agent", sub_agent_names)
        self.assertIn("deal_audit_and_counter_pipeline", sub_agent_names)
        self.assertEqual(len(deal_audit_and_counter_pipeline.sub_agents), 2)
        self.assertIsNotNone(app)

        # Verify all Python function tools on root_agent have comprehensive Google-style docstrings
        for tool_fn in root_agent.tools:
            if callable(tool_fn):
                doc = getattr(tool_fn, "__doc__", "") or ""
                self.assertIn("Returns:", doc, f"Tool {tool_fn.__name__} missing Returns: docstring section")

    def test_pydantic_schemas_and_guided_error_recovery(self):
        # 1. Invalid negative MSRP triggers structured guided error with recovery_instructions
        bad_otd = calculate_otd_price(msrp=-500.0, selling_price=51000.0)
        self.assertFalse(bad_otd["ok"])
        self.assertEqual(bad_otd["error_type"], "SCHEMA_VALIDATION_ERROR")
        self.assertIn("recovery_instructions", bad_otd)

        # 2. Invalid dealer_id in set_dealer_trade_in_gate returns recovery instructions and available IDs
        bad_gate = set_dealer_trade_in_gate(dealer_id="dlr-nonexistent-dealer")
        self.assertFalse(bad_gate["ok"])
        self.assertIn("recovery_instructions", bad_gate)
        self.assertIn("available_dealer_ids", bad_gate["context"])

        # 3. Invalid message_id in approve_queued_message returns recovery instructions
        bad_approve = approve_queued_message(message_id="msg-does-not-exist")
        self.assertFalse(bad_approve["ok"])
        self.assertIn("recovery_instructions", bad_approve)

        # 4. Valid OTD schema validation
        valid_in = OTDCalculationInputSchema(msrp=53890.0, selling_price=51842.0)
        self.assertEqual(valid_in.msrp, 53890.0)

    def test_persistent_sessions_and_semantic_memory(self):
        # 1. PersistentFirestoreSessionService
        session_svc = PersistentFirestoreSessionService()
        session_svc.append_turn(
            session_id="test-session-agentops",
            user_message="I am looking to lock $54,600 OTD.",
            agent_reply="Got it, targeting $54,600 OTD.",
        )
        loaded_sess = session_svc.get_or_create_session("test-session-agentops")
        self.assertGreaterEqual(len(loaded_sess["history"]), 1)

        # 2. Semantic Deal Memory RAG search
        mem_res = search_negotiation_memory(
            query="Monmouth County NJ Telluride Hybrid SX X-Line invoice holdback discount",
            top_k=3,
        )
        self.assertTrue(mem_res["ok"])
        self.assertGreaterEqual(mem_res["results_count"], 1)

    def test_guardrails_and_human_confirmation_gate(self):
        self.assertGreaterEqual(len(DEFAULT_SAFETY_SETTINGS), 4)

        # 1. Self-evaluation policy checker catches premature trade-in and unprompted buzzwords
        eval_res = evaluate_agent_response_policy(
            response_text="Ask the dealer about their 2% holdback and floorplan and mention my trade-in.",
            dealer_initiated_jargon=False,
            otd_locked_in_writing=False,
        )
        self.assertFalse(eval_res["passed"])
        self.assertGreaterEqual(len(eval_res["violations"]), 2)

        # 2. Human-in-the-loop confirmation gate defaults COPILOT to PENDING_APPROVAL
        gate_copilot = require_human_confirmation_hook(
            action_name="queue_dealer_message",
            action_payload={"dealer_id": "dlr-raceway-kia-of-freehold"},
            autonomy_mode="COPILOT",
            explicit_auto_dispatch=False,
        )
        self.assertTrue(gate_copilot["requires_human_confirmation"])
        self.assertFalse(gate_copilot["approved"])
        self.assertEqual(gate_copilot["recommended_status"], "PENDING_APPROVAL")

    def test_observability_structured_logging_and_tool_instrumentation(self):
        ev = log_structured_event(
            event_type="TEST_EVENT",
            message="Structured logging check",
            component="test_suite",
        )
        self.assertEqual(ev["event_type"], "TEST_EVENT")
        intent_ev = log_tool_intent("calculate_otd_price", {"msrp": 53890.0})
        self.assertEqual(intent_ev["phase"], "INTENT_BEFORE_EXECUTION")
        outcome_ev = log_tool_outcome("calculate_otd_price", {"ok": True}, latency_ms=1.5)
        self.assertEqual(outcome_ev["phase"], "OUTCOME_AFTER_EXECUTION")

    def test_secret_manager_and_config_files_present(self):
        env_path = BASE_DIR / ".env"
        env_text = env_path.read_text(encoding="utf-8") if env_path.is_file() else (BASE_DIR / ".env.example").read_text(encoding="utf-8")
        js_text = (BASE_DIR / "public" / "negotiator.js").read_text(encoding="utf-8")
        self.assertNotIn("AIza", env_text, ".env must not contain hardcoded plaintext Google API keys")
        self.assertNotIn("AQ.Ab8", env_text, ".env must not contain hardcoded tokens")
        self.assertNotIn("AIza", js_text, "negotiator.js must not contain hardcoded or split Google API keys")
        self.assertNotIn("generativelanguage.googleapis.com", js_text, "negotiator.js must not call Gemini directly with a client-side API key")
        self.assertTrue((BASE_DIR / ".env.example").is_file())
        self.assertTrue((BASE_DIR / ".gitignore").is_file())
        self.assertTrue((BASE_DIR / "requirements.txt").is_file())
        self.assertTrue((BASE_DIR / "firebase.json").is_file())

        doc = upsert_document(
            "negotiation_campaigns",
            "camp-2027-kia-telluride-hybrid-nj",
            get_negotiation_dashboard()["campaign"],
        )
        self.assertIn("updated_at", doc)
        self.assertIsInstance(get_secret("NON_EXISTENT_SECRET_KEY", default="fallback_val"), str)

    def test_monmouth_ocean_live_inventory_scraper_parser(self):
        self.assertEqual(len(MONMOUTH_OCEAN_KIA_DEALERS), 5)
        dealer_names = {d["dealer_name"] for d in MONMOUTH_OCEAN_KIA_DEALERS}
        self.assertIn("DiFeo Kia of Lakewood", dealer_names)
        self.assertIn("Manahawkin Kia", dealer_names)
        counties = {d["county"] for d in MONMOUTH_OCEAN_KIA_DEALERS}
        self.assertEqual(counties, {"Monmouth County", "Ocean County"})

        sample_html = """
        <html><head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Vehicle",
          "name": "New 2027 Kia Telluride Hybrid SX X-Line AWD",
          "vehicleIdentificationNumber": "5XYP5DGC9SG650111",
          "sku": "RK27111",
          "color": "Wolf Gray",
          "vehicleInteriorColor": "Terracotta Brown",
          "offers": {
            "@type": "Offer",
            "price": "51950",
            "HighPrice": "53890",
            "url": "https://www.racewaykia.com/inventory/new-2027-kia-telluride-5XYP5DGC9SG650111/"
          }
        }
        </script>
        </head><body></body></html>
        """
        vehicles = _extract_vehicles_from_html(
            sample_html,
            MONMOUTH_OCEAN_KIA_DEALERS[0],
            MONMOUTH_OCEAN_KIA_DEALERS[0]["inventory_urls"][0],
        )
        self.assertEqual(len(vehicles), 1)
        v = vehicles[0]
        self.assertEqual(v["vin"], "5XYP5DGC9SG650111")
        self.assertIn("SX X-Line", v["trim_level"])
        self.assertEqual(v["msrp"], 53890.0)
        self.assertEqual(v["selling_price"], 51950.0)
        self.assertEqual(v["interior_color"], "Terracotta Brown")
        self.assertEqual(v["data_source"], "LIVE_DEALER_SITE")

        # Verify Playwright WAF-safe route filter blocks dealership bloatware while allowing WAF/Algolia/Cosmos
        self.assertTrue(_should_block_request("https://cdn.gubagoo.com/widget.js", "script"))
        self.assertTrue(_should_block_request("https://app.carnow.com/chat.js", "script"))
        self.assertTrue(_should_block_request("https://www.racewaykia.com/hero.mp4", "media"))
        self.assertFalse(_should_block_request("https://challenges.cloudflare.com/turnstile/v0/api.js", "script"))
        self.assertFalse(_should_block_request("https://v53-dsn.algolia.net/1/indexes/*/queries", "xhr"))
        self.assertFalse(
            _should_block_request(
                "https://www.sansoneauto.com/api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/19645/1499280",
                "fetch",
            )
        )

        # Verify Playwright-intercepted Algolia & DealerOn payloads extract Telluride Hybrid units & filter out V6 X-Pro
        intercepted_payloads = [
            {
                "url": "https://v53-dsn.algolia.net/1/indexes/*/queries",
                "data": {
                    "results": [
                        {
                            "hits": [
                                {
                                    "vin": "5XYPDESA8VG041820",
                                    "stock": "RK27084",
                                    "title_vrp": "New 2027 Kia Telluride Hybrid SX X-Line AWD",
                                    "msrp": "$56,890",
                                    "our_price": "$54,615",
                                    "ext_color": "Wolf Gray",
                                    "int_color": "Terracotta Brown Leather",
                                    "in_transit_filter": "true",
                                    "link": "https://www.racewaykia.com/inventory/new-2027-kia-telluride-hybrid-5XYPDESA8VG041820/",
                                },
                                {
                                    "vin": "5XYP5DGC9VG099999",
                                    "stock": "RK27999",
                                    "title_vrp": "New 2027 Kia Telluride SX Prestige X-Pro V6",
                                    "msrp": "$57,500",
                                    "our_price": "$56,000",
                                },
                            ]
                        }
                    ]
                },
            }
        ]
        intercepted_vehicles = _extract_vehicles_from_intercepted_payloads(
            intercepted_payloads,
            MONMOUTH_OCEAN_KIA_DEALERS[0],
            MONMOUTH_OCEAN_KIA_DEALERS[0]["inventory_urls"][0],
        )
        self.assertEqual(len(intercepted_vehicles), 1)
        iv = intercepted_vehicles[0]
        self.assertEqual(iv["vin"], "5XYPDESA8VG041820")
        self.assertEqual(iv["stock_number"], "RK27084")
        self.assertEqual(iv["trim_level"], "2027 Kia Telluride Hybrid SX X-Line")
        self.assertEqual(iv["msrp"], 56890.0)
        self.assertEqual(iv["selling_price"], 54615.0)
        self.assertEqual(iv["inventory_status"], "IN_TRANSIT")

    def test_phase2_live_gmail_and_mobile_negotiation_pipeline(self):
        seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        try:
            # 1. Verify default SIMULATION mode and placeholder contact detection
            self.assertEqual(get_transport_mode(), "SIMULATION")
            self.assertTrue(is_placeholder_contact("sales@racewaykia.example.com", "EMAIL"))
            self.assertTrue(is_placeholder_contact("+17325550114", "SMS_PERSONAL"))
            self.assertFalse(is_placeholder_contact("internet.sales@racewaykia.com", "EMAIL"))
            self.assertFalse(is_placeholder_contact("+17324624422", "SMS_PERSONAL"))

            # 2. Switch to LIVE mode and verify placeholder contacts are blocked from live dispatch
            mode_res = set_transport_mode("LIVE")
            self.assertEqual(mode_res["transport_mode"], "LIVE")

            q_placeholder = queue_dealer_message(
                dealer_id="dlr-raceway-kia-of-freehold",
                body="Hi Raceway Kia team, can we lock $54,878 Out-The-Door with $0 add-ons?",
                channel="EMAIL",
                subject="2027 Kia Telluride Hybrid SX X-Line (VIN 5XYP5DGC8VG041822) — Out-The-Door Quote",
                auto_dispatch=False,
            )
            self.assertEqual(q_placeholder["status"], "PENDING_APPROVAL")
            self.assertIn("mailto:", q_placeholder["email_deep_link"])

            app_blocked = approve_queued_message(
                message_id=q_placeholder["id"],
                send_immediately_override=True,
            )
            self.assertEqual(
                app_blocked["dispatched_results"][0]["error_code"],
                "PLACEHOLDER_CONTACT_IN_LIVE_MODE",
            )

            # 3. Update dealer with real contact info and verify Single-Thread RFC 5322 email continuity
            upd = update_dealer_contact_info(
                dealer_id="dlr-raceway-kia-of-freehold",
                contact_name="Mike R. (Internet Director)",
                contact_email="mrodriguez@racewaykia.com",
                contact_phone="+17324624422",
            )
            self.assertTrue(upd["ok"])
            self.assertTrue(upd["dealer_lead"]["has_real_contact"])

            # Send/prepare Round 1 email and verify Message-ID and thread continuity
            gmail_r1 = send_via_personal_gmail(
                to_email="mrodriguez@racewaykia.com",
                subject="2027 Kia Telluride Hybrid SX X-Line (VIN 5XYP5DGC8VG041822) — Out-The-Door Quote",
                body="Hi Mike, I am ready to place a deposit today at $54,878 Out-The-Door.",
            )
            self.assertTrue(gmail_r1["message_id"].startswith("<"))
            self.assertIn("mailto:mrodriguez@racewaykia.com", gmail_r1["email_deep_link"])

            # 4. Ingest live inbound dealer email reply in the same thread & verify auto-drafted Re: counter
            inbound_email = ingest_live_dealer_message(
                dealer_id="dlr-raceway-kia-of-freehold",
                sender_contact="mrodriguez@racewaykia.com",
                channel="EMAIL",
                raw_body="Hi! We can offer $51,950 selling price ($55,418 OTD including $499 doc fee and NJ tax/tags) with $0 add-ons on VIN 5XYP5DGC8VG041822.",
                subject="Re: 2027 Kia Telluride Hybrid SX X-Line (VIN 5XYP5DGC8VG041822) — Out-The-Door Quote",
                external_message_id="<dealer-reply-001@racewaykia.com>",
                in_reply_to=gmail_r1["message_id"],
                references=gmail_r1["message_id"],
                thread_id=gmail_r1["thread_id"],
                auto_draft_counter=True,
            )
            self.assertTrue(inbound_email["ok"])
            self.assertEqual(inbound_email["parsed_quote"]["dealer_lead"]["selling_price"], 51950.0)
            self.assertAlmostEqual(inbound_email["parsed_quote"]["dealer_lead"]["otd_total"], 55418.0, delta=5.0)

            draft_msg = inbound_email["auto_drafted_counter"]
            self.assertTrue(draft_msg["subject"].lower().startswith("re:"))
            self.assertEqual(draft_msg["in_reply_to"], "<dealer-reply-001@racewaykia.com>")
            self.assertIn(gmail_r1["message_id"], draft_msg["references"])
            self.assertIn("<dealer-reply-001@racewaykia.com>", draft_msg["references"])

            # 5. Ingest live inbound dealer SMS via Mobile Gateway webhook & verify thread history
            inbound_sms = ingest_live_dealer_message(
                dealer_id="dlr-raceway-kia-of-freehold",
                sender_contact="+17324624422",
                channel="SMS_PERSONAL",
                raw_body="Text update from Raceway Kia: we can drop to $51,700 selling price ($55,152 OTD) with $0 add-ons if you lock in today.",
                auto_draft_counter=True,
            )
            self.assertTrue(inbound_sms["ok"])
            thread_res = get_dealer_conversation_thread("dlr-raceway-kia-of-freehold")
            self.assertTrue(thread_res["ok"])
            self.assertGreaterEqual(thread_res["message_count"], 3)
            comms_status = get_live_comms_configuration_status()
            self.assertEqual(comms_status["transport_mode"], "LIVE")
        finally:
            set_transport_mode("SIMULATION")
            seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)

    def test_unified_chat_ui_and_non_canned_dynamic_responses(self):
        from dashboard_server import _dynamic_conversational_deal_advisor

        html_text = (BASE_DIR / "public" / "negotiator.html").read_text(encoding="utf-8")
        self.assertIn('id="unified-chat-window"', html_text)
        # Ensure chat history and chat input are inside #unified-chat-window
        unified_idx = html_text.index('id="unified-chat-window"')
        history_idx = html_text.index('id="agent-chat-history"')
        input_idx = html_text.index('id="agent-chat-input"')
        details_idx = html_text.index('id="agent-tool-trajectory"')
        self.assertLess(unified_idx, history_idx)
        self.assertLess(history_idx, input_idx)
        self.assertLess(input_idx, details_idx)

        js_text = (BASE_DIR / "public" / "negotiator.js").read_text(encoding="utf-8")
        self.assertIn("callDirectGeminiFromBrowser", js_text)
        self.assertNotIn("Advisor Response to", js_text)
        self.assertNotIn("ADK_CONVERSATIONAL_ADVISOR", js_text)

        dash = seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        dealers = dash["dealers_ranked"]
        best = get_best_competing_offer()
        target = dealers[0]

        prompts = [
            "hello",
            "How do I set up my 16-letter Gmail App Password for single-thread emails?",
            "What if Raceway offers $54,750 OTD with $3,000 down at 3.9% APR?",
            "What doc fee and add-ons are on this worksheet?",
            "Draft a counter email to Chris at $54,600 OTD",
        ]
        replies = []
        for p in prompts:
            res = _dynamic_conversational_deal_advisor(
                prompt=p,
                target_dealer=target,
                dealers_ranked=dealers,
                best_offer=best,
            )
            self.assertTrue(res["ok"])
            self.assertTrue(res["reply"].strip())
            replies.append(res["reply"])

        # Every single prompt must produce a distinct response
        self.assertEqual(len(set(replies)), len(prompts))
        self.assertIn("54,750.00", replies[2])
        self.assertIn("App Password", replies[1])

    def test_dealer_inventory_dropdown_and_trim_dealer_sort_filter(self):
        from tools import build_enriched_dealer_inventory, ensure_dealer_lead_from_inventory

        dash = seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)
        inv = dash.get("dealer_inventory", [])
        self.assertGreaterEqual(len(inv), 15)

        dealers_set = {v["dealer_name"] for v in inv}
        expected_dealers = {
            "Raceway Kia of Freehold",
            "Sansone Jr's 66 Automall Kia",
            "Matt Blatt Kia of Toms River",
            "DiFeo Kia of Lakewood",
            "Manahawkin Kia",
        }
        self.assertEqual(dealers_set, expected_dealers)

        trims_set = {v["trim_level"] for v in inv}
        expected_trims = {
            "2027 Kia Telluride Hybrid SX X-Line",
            "2027 Kia Telluride Hybrid SX Prestige",
            "2027 Kia Telluride Hybrid SX Prestige X-Line",
        }
        self.assertEqual(trims_set, expected_trims)

        # Filter by dealer
        manahawkin_only = build_enriched_dealer_inventory(filter_dealer="Manahawkin Kia")
        self.assertEqual(len(manahawkin_only), 3)
        self.assertTrue(all(v["dealer_name"] == "Manahawkin Kia" for v in manahawkin_only))

        # Filter by trim (ensuring SX Prestige does NOT bleed into SX Prestige X-Line)
        prestige_only = build_enriched_dealer_inventory(
            filter_trim="2027 Kia Telluride Hybrid SX Prestige"
        )
        self.assertGreaterEqual(len(prestige_only), 5)
        self.assertTrue(
            all(v["trim_level"] == "2027 Kia Telluride Hybrid SX Prestige" for v in prestige_only)
        )

        # Sort by trim ascending (Priority #1 -> #2 -> #3)
        sorted_trim = build_enriched_dealer_inventory(sort_by="trim_asc")
        trim_ranks = [v["trim_priority_rank"] for v in sorted_trim]
        self.assertEqual(trim_ranks, sorted(trim_ranks))

        # Sort by dealer ascending (A -> Z)
        sorted_dealer = build_enriched_dealer_inventory(sort_by="dealer_asc")
        dealer_names = [v["dealer_name"] for v in sorted_dealer]
        self.assertEqual(dealer_names, sorted(dealer_names))

        # Materialize an inventory vehicle into dealer_leads
        lead = ensure_dealer_lead_from_inventory(manahawkin_only[0]["id"])
        self.assertIsNotNone(lead)
        self.assertEqual(lead["dealer_name"], "Manahawkin Kia")

        # Verify UI elements exist in negotiator.html and negotiator.js
        html_text = (BASE_DIR / "public" / "negotiator.html").read_text(encoding="utf-8")
        js_text = (BASE_DIR / "public" / "negotiator.js").read_text(encoding="utf-8")
        self.assertIn('id="dealer-inventory-dropdown"', html_text)
        self.assertIn('id="inventory-dealer-filter"', html_text)
        self.assertIn('id="inventory-trim-filter"', html_text)
        self.assertIn('id="inventory-sort-select"', html_text)
        self.assertIn("handleInventoryFilterChange", js_text)
        self.assertIn("getFilteredAndSortedInventory", js_text)

    def test_v3_herbie_nj_payments_and_worksheet_audit(self):
        from tools import (
            audit_uploaded_lease_sheet,
            build_enriched_dealer_inventory,
            calculate_nj_payments,
            get_negotiation_dashboard,
        )

        # 1. Verify calculate_nj_payments returns both $3,000 down and $0 down side-by-side + True Deal Score
        nj = calculate_nj_payments(
            msrp=56035.0,
            selling_price=53500.0,
            doc_fee=689.0,
            gov_fees=400.0,
            addons_total=0.0,
            rebates=750.0,
            apr_percent=5.49,
            loan_term_months=60,
            residual_percent=62.0,
            money_factor=0.00210,
            lease_term_months=36,
            acquisition_fee=650.0,
            target_monthly_lease_budget=750.0,
        )
        # True Deal Score = (53500 - 56035) + 689 + 0 = -1846
        self.assertAlmostEqual(nj["true_deal_score"], -1846.0, places=2)
        self.assertAlmostEqual(nj["pure_dealer_discount"], 2535.0, places=2)
        self.assertGreater(nj["loan_0_down_monthly"], nj["loan_3000_down_monthly"])
        self.assertGreater(nj["lease_0_down_monthly"], nj["lease_3000_down_monthly"])
        self.assertIn("extra_discount_to_hit_lease_budget", nj)

        # 2. Verify Mode 4 audit_uploaded_lease_sheet reverse-engineers hidden Money Factor markup
        sheet = audit_uploaded_lease_sheet(
            msrp=58135.0,
            selling_price=55800.0,
            dealer_quoted_monthly=869.0,
            cash_due_at_signing=3000.0,
            residual_percent=61.0,
            base_buy_rate_mf=0.00210,
            term_months=36,
            doc_fee=799.0,
            gov_fees=400.0,
            acquisition_fee=650.0,
            dealer_addons_total=695.0,
            rebates=750.0,
        )
        self.assertEqual(sheet["mode"], "MODE_4_WORKSHEET_AUDIT")
        self.assertGreater(sheet["implied_dealer_mf"], 0.00210)
        self.assertGreater(sheet["total_36mo_overcharge"], 1000.0)
        self.assertTrue(len(sheet["forensic_flags"]) >= 1)

        # 3. Verify 5-Dealer Tracker has all 5 unified NJ dealers (Sansone Jr Neptune & Keyport combined)
        dash = get_negotiation_dashboard()
        five_tracker = dash.get("five_dealer_tracker", [])
        self.assertEqual(len(five_tracker), 5)
        sansone_entries = [d for d in five_tracker if "Sansone" in d["dealer_name"]]
        self.assertEqual(len(sansone_entries), 1)
        self.assertEqual(sansone_entries[0]["doc_fee"], 895.0)

        # 4. Verify Screen 2 Inventory Browser includes both Priority Non-Black and All-Black interiors
        non_black_inv = build_enriched_dealer_inventory(filter_interior="NON_BLACK")
        black_inv = build_enriched_dealer_inventory(filter_interior="BLACK")
        self.assertGreaterEqual(len(non_black_inv), 10)
        self.assertGreaterEqual(len(black_inv), 3)
        self.assertTrue(all(v["is_non_black_interior"] for v in non_black_inv))
        self.assertTrue(all(not v["is_non_black_interior"] for v in black_inv))

        # 5. Verify Herbie is positioned at the very top of Screen 1's main column before Reply Card and HUD
        html_text = (BASE_DIR / "public" / "negotiator.html").read_text(encoding="utf-8")
        idx_herbie = html_text.find('id="unified-chat-window"')
        idx_reply = html_text.find('id="parsed-counter-result-box"')
        idx_hud = html_text.find('id="private-affordability-hud"')
        self.assertNotEqual(idx_herbie, -1)
        self.assertNotEqual(idx_reply, -1)
        self.assertNotEqual(idx_hud, -1)
        self.assertLess(idx_herbie, idx_reply)
        self.assertLess(idx_reply, idx_hud)

    @classmethod
    def tearDownClass(cls):
        set_transport_mode("SIMULATION")
        seed_sample_negotiation_campaign(deal_type="PURCHASE", reset_existing=True)


if __name__ == "__main__":
    unittest.main()



