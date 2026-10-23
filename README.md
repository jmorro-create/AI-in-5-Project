# 2027 Kia Telluride Hybrid — V3 Autonomous Car Negotiator (`🤖 Herbie`)

An autonomous car purchase & lease negotiation agent and 2-screen Tailwind CSS Deal Command Center built with the **Google Agent Development Kit (ADK)** for Monmouth & Ocean County, NJ (`ZIP 07724`).

---

## Key Features (V3 Edition)

- **🤖 Herbie — Live Deal Coach & Situational Strategist** (`Screen 1 — Top`):
  - Positioned at the top of the primary workspace for zero-scroll real-time coaching, raw dealer email/SMS parsing, 1-click situational prompt chips, and **Mode 4 Worksheet / PDF Forensic Auditing**.
- **✉️ Ready-to-Send Dealer Reply Card** (`Screen 1 — Middle`):
  - Generates clinical, peer-to-peer buyer scripts (`<120` words) with zero AI buzzwords and strict Stage 1 Trade-In & Lease Secrecy guardrails, plus 1-click **Copy**, **Send in Gmail**, and **Text via SMS** actions.
- **🛡️ Private Buyer Affordability HUD** (`Screen 1 — Bottom`):
  - Computes **True Deal Score** (`(Selling Price - MSRP) + Doc Fee + Mandatory Add-Ons`), separates **Pure Dealer Discount** from the stackable **`$750` Kia Loyalty/Conquest Rebate**, and displays **both `$3,000` Out-of-Pocket and `$0` Out-of-Pocket (True Sign & Drive)** monthly payments side-by-side for:
    - **36-Mo / 12k NJ Lease** (`6.625%` NJ tax applied only to depreciation + fees)
    - **60-Mo Purchase Loan** (`6.625%` NJ tax applied to full Selling Price + Doc Fee + Add-Ons before rebates)
- **🏆 5-Dealer Monmouth & Ocean Tracker** (`Screen 1 — Right Column`):
  - Tracks and ranks all 5 local dealerships by True Deal Score, treating **Sansone Jr's (Neptune & Keyport)** as **1 unified dealership** (`$895` doc fee) alongside **Matt Blatt Kia** (`$689`), **Raceway Kia** (`$799`), **DiFeo Kia** (`$749`), and **Manahawkin Kia** (`$749`).
- **🚙 Dealer Inventory Browser** (`Screen 2 — Toggleable Full-Width View`):
  - Displays all **16 In-Transit and On-Lot 2027 Telluride Hybrid VINs** across all 5 dealerships, including **both `★ Priority Non-Black` interiors and `⬛ All-Black` interiors**, with full sorting and filtering by Dealership, Trim Tier, Interior Color, and Lot Status.

---

## Security & Environment Configuration

- **Zero Hardcoded API Keys**: No Google/Gemini API keys or tokens are stored in source code or frontend assets.
- Copy [`.env.example`](.env.example) to `.env` for local development (`.env` is git-ignored via [`.gitignore`](.gitignore)):
  ```bash
  cp .env.example .env
  ```
- In production (Cloud Run), secrets (`GOOGLE_API_KEY`, `TWILIO_*`, `GMAIL_*`) are retrieved dynamically from **Google Cloud Secret Manager** via [`tools/secret_manager.py`](tools/secret_manager.py).

---

## Quickstart

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Automated Test Suite (22 Unit Tests)
```bash
python3 -m unittest discover -s "." -p "test_*.py" -v
```

### 3. Launch the V3 Web Dashboard & ADK Bridge
```bash
python3 dashboard_server.py
```
Then open `http://localhost:8090` in your browser.

---

## Architecture & Handover Documentation

For a deep dive into the multi-agent architecture, tool schemas, evaluation set (`nj_telluride_hybrid_eval_set.evalset.json`), and Terraform Cloud Run deployment, see [`handover.md`](handover.md).
