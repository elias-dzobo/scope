"""Asset class detection and per-class pillar routing for the research harness.

Different asset classes require fundamentally different research frameworks.
A mutual fund is not analysed like a company stock; a REIT is not the same
as a SaaS business; a pre-revenue startup has different financial signals
than a mature compounder.

This module:
  1. Defines the supported asset classes.
  2. Detects the asset class from ticker / name / description signals.
  3. Returns the correct pillar set, evidence requirements, search focus,
     pillar signals (for scoring), and pillar weights for each class.

All downstream callers (planner, scorer, synthesis) read from this module
so that adding a new asset class only requires changes in one place.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from research_core.harness.models import EvidenceRequirement


# ---------------------------------------------------------------------------
# Asset class taxonomy
# ---------------------------------------------------------------------------

class AssetClass(StrEnum):
    EQUITY_STOCK       = "equity_stock"        # Profitable / mature listed company
    PRE_REVENUE_EQUITY = "pre_revenue_equity"  # Early-stage, pre-profitability
    FUND               = "fund"                # Mutual fund / closed-end fund
    ETF                = "etf"                 # Exchange-traded fund
    REIT               = "reit"                # Real estate investment trust
    FIXED_INCOME       = "fixed_income"        # Bond / note / debenture
    UNKNOWN            = "unknown"             # Falls back to EQUITY_STOCK treatment


# ---------------------------------------------------------------------------
# Pillar sets per asset class
# ---------------------------------------------------------------------------

EQUITY_STOCK_PILLARS: tuple[str, ...] = (
    "Macro & Industry",
    "Economic Moat",
    "Financial Engine",
    "Management & Capital Allocation",
    "Valuation",
    "Technical Analysis",
)

PRE_REVENUE_EQUITY_PILLARS: tuple[str, ...] = (
    "Macro & Industry",
    "Technology & Product",
    "Path to Profitability",
    "Management & Team",
    "Valuation & Dilution Risk",
    "Competitive Positioning",
)

FUND_PILLARS: tuple[str, ...] = (
    "Fund Performance",
    "Manager Quality",
    "Portfolio Construction",
    "Fee Structure",
    "Risk-Adjusted Returns",
    "Investor Fit",
)

ETF_PILLARS: tuple[str, ...] = (
    "Index & Strategy Quality",
    "Performance & Tracking",
    "Holdings & Exposure",
    "Fee Structure",
    "Liquidity & Trading",
    "Provider Quality",
)

REIT_PILLARS: tuple[str, ...] = (
    "Portfolio Quality",
    "Distribution Sustainability",
    "Balance Sheet & Leverage",
    "Management & Operations",
    "Valuation",
    "Macro & Real Estate Market",
)

FIXED_INCOME_PILLARS: tuple[str, ...] = (
    "Credit Quality",
    "Interest Rate Sensitivity",
    "Issuer Financial Health",
    "Covenant Analysis",
    "Yield & Spread",
    "Liquidity",
)

PILLARS_BY_ASSET_CLASS: dict[AssetClass, tuple[str, ...]] = {
    AssetClass.EQUITY_STOCK:       EQUITY_STOCK_PILLARS,
    AssetClass.PRE_REVENUE_EQUITY: PRE_REVENUE_EQUITY_PILLARS,
    AssetClass.FUND:               FUND_PILLARS,
    AssetClass.ETF:                ETF_PILLARS,
    AssetClass.REIT:               REIT_PILLARS,
    AssetClass.FIXED_INCOME:       FIXED_INCOME_PILLARS,
    AssetClass.UNKNOWN:            EQUITY_STOCK_PILLARS,
}


# ---------------------------------------------------------------------------
# Core pillars per asset class (get extra retry budget in the planner)
# ---------------------------------------------------------------------------

CORE_PILLARS_BY_ASSET_CLASS: dict[AssetClass, frozenset[str]] = {
    AssetClass.EQUITY_STOCK:       frozenset({"Financial Engine", "Management & Capital Allocation", "Valuation"}),
    AssetClass.PRE_REVENUE_EQUITY: frozenset({"Path to Profitability", "Valuation & Dilution Risk", "Technology & Product"}),
    AssetClass.FUND:               frozenset({"Fund Performance", "Manager Quality", "Risk-Adjusted Returns"}),
    AssetClass.ETF:                frozenset({"Performance & Tracking", "Holdings & Exposure", "Fee Structure"}),
    AssetClass.REIT:               frozenset({"Distribution Sustainability", "Balance Sheet & Leverage", "Valuation"}),
    AssetClass.FIXED_INCOME:       frozenset({"Credit Quality", "Issuer Financial Health", "Yield & Spread"}),
    AssetClass.UNKNOWN:            frozenset({"Financial Engine", "Management & Capital Allocation", "Valuation"}),
}


# ---------------------------------------------------------------------------
# Evidence requirements per asset class + pillar
# ---------------------------------------------------------------------------

EVIDENCE_REQUIREMENTS: dict[AssetClass, dict[str, list[EvidenceRequirement]]] = {

    AssetClass.EQUITY_STOCK: {
        "Macro & Industry": [
            EvidenceRequirement(name="industry growth and demand", description="Market growth, demand drivers, regulation, or secular trend evidence.", preferred_source_types=["industry_report", "financial_data", "investor_relations"]),
            EvidenceRequirement(name="competitive landscape", description="Market structure or competitor evidence."),
        ],
        "Economic Moat": [
            EvidenceRequirement(name="competitive advantage", description="Durable moat signal such as brand, patents, cost advantage, or network effect."),
            EvidenceRequirement(name="customer or contract durability", description="Evidence of retention, contracts, switching costs, or market share."),
        ],
        "Financial Engine": [
            EvidenceRequirement(name="recent revenue and profitability", description="Revenue, EPS, margin, EBITDA, or operating income evidence.", preferred_source_types=["regulatory", "investor_relations"]),
            EvidenceRequirement(name="cash flow and balance sheet", description="Free cash flow, leverage, debt, or liquidity evidence.", preferred_source_types=["regulatory", "investor_relations"]),
        ],
        "Management & Capital Allocation": [
            EvidenceRequirement(name="capital allocation", description="Buybacks, dividends, acquisitions, reinvestment, or allocation priorities.", preferred_source_types=["investor_relations", "regulatory"]),
            EvidenceRequirement(name="governance and ownership", description="Board, insider ownership, governance, or management incentives."),
        ],
        "Valuation": [
            EvidenceRequirement(name="valuation multiples", description="P/E, EV/EBITDA, price target, peer multiple, or historical multiple evidence."),
            EvidenceRequirement(name="guidance or intrinsic value inputs", description="Forward guidance, DCF inputs, margin assumptions, or upside/downside evidence.", preferred_source_types=["investor_relations", "financial_data"]),
        ],
        "Technical Analysis": [
            EvidenceRequirement(name="trend and momentum", description="Moving averages, RSI, breakout, price trend, or relative performance."),
            EvidenceRequirement(name="volume and levels", description="Volume, support/resistance, accumulation, or institutional activity."),
        ],
    },

    AssetClass.PRE_REVENUE_EQUITY: {
        "Macro & Industry": [
            EvidenceRequirement(name="TAM and market opportunity", description="Total addressable market, growth projections, and demand drivers."),
            EvidenceRequirement(name="industry tailwinds", description="Regulatory, technology, or secular trends favouring the company."),
        ],
        "Technology & Product": [
            EvidenceRequirement(name="technology differentiation", description="What makes the product technically superior and how defensible is it.", preferred_source_types=["investor_relations", "regulatory"]),
            EvidenceRequirement(name="IP and patents", description="Patent portfolio, proprietary methods, or trade secrets.", required=False),
            EvidenceRequirement(name="product validation", description="Pilot customers, design wins, certifications, or proof-of-concept evidence."),
        ],
        "Path to Profitability": [
            EvidenceRequirement(name="burn rate and cash runway", description="Current cash burn, cash on hand, and months of runway.", preferred_source_types=["regulatory", "investor_relations"]),
            EvidenceRequirement(name="revenue trajectory", description="Revenue growth, backlog, order book, or customer pipeline."),
            EvidenceRequirement(name="gross margin trend", description="Gross margin improvement path and target margin at scale."),
        ],
        "Management & Team": [
            EvidenceRequirement(name="founder and executive quality", description="Relevant domain experience, prior exits, and execution track record."),
            EvidenceRequirement(name="team depth and retention", description="Key hires, team completeness, and advisor quality.", required=False),
        ],
        "Valuation & Dilution Risk": [
            EvidenceRequirement(name="current valuation vs peers", description="EV/Revenue multiple vs comparable early-stage or public peers."),
            EvidenceRequirement(name="dilution history and future capital needs", description="Prior rounds, ownership dilution, and likely future raise size.", preferred_source_types=["regulatory"]),
            EvidenceRequirement(name="comparable transactions", description="M&A or funding comps that support or challenge the current price.", required=False),
        ],
        "Competitive Positioning": [
            EvidenceRequirement(name="competitive landscape", description="Who else is solving this problem and how well-funded are they."),
            EvidenceRequirement(name="barriers to entry", description="What stops a well-resourced competitor from replicating this."),
        ],
    },

    AssetClass.FUND: {
        "Fund Performance": [
            EvidenceRequirement(name="total returns vs benchmark", description="1yr, 3yr, 5yr annualised returns compared to the stated benchmark.", preferred_source_types=["fund_factsheet", "regulatory"]),
            EvidenceRequirement(name="NAV history", description="Net asset value trend and consistency over time."),
            EvidenceRequirement(name="alpha generation", description="Whether the fund consistently beats its benchmark net of fees."),
        ],
        "Manager Quality": [
            EvidenceRequirement(name="manager track record", description="Portfolio manager tenure and historical performance across market cycles.", preferred_source_types=["investor_relations"]),
            EvidenceRequirement(name="investment process", description="Stated methodology, how decisions are made, and consistency of process."),
        ],
        "Portfolio Construction": [
            EvidenceRequirement(name="top holdings and concentration", description="Largest positions and how concentrated the portfolio is.", preferred_source_types=["fund_factsheet", "regulatory"]),
            EvidenceRequirement(name="sector and geographic allocation", description="Breakdown of exposure by sector, country, or asset type."),
            EvidenceRequirement(name="portfolio turnover", description="How frequently the portfolio trades — high turnover signals costs and style drift.", required=False),
        ],
        "Fee Structure": [
            EvidenceRequirement(name="total expense ratio", description="TER or ongoing charges figure vs peer average.", preferred_source_types=["fund_factsheet", "regulatory"]),
            EvidenceRequirement(name="performance or entry/exit fees", description="Performance fees, front-end loads, or redemption charges.", required=False),
        ],
        "Risk-Adjusted Returns": [
            EvidenceRequirement(name="Sharpe and Sortino ratio", description="Risk-adjusted return metrics compared to benchmark or peer group."),
            EvidenceRequirement(name="maximum drawdown", description="Worst peak-to-trough decline and recovery period."),
            EvidenceRequirement(name="volatility vs benchmark", description="Standard deviation of returns relative to the benchmark."),
        ],
        "Investor Fit": [
            EvidenceRequirement(name="fund mandate and objective", description="What the fund is designed to achieve and for whom."),
            EvidenceRequirement(name="liquidity and minimums", description="Redemption terms, minimum investment, and liquidity constraints.", required=False),
        ],
    },

    AssetClass.ETF: {
        "Index & Strategy Quality": [
            EvidenceRequirement(name="index methodology", description="How the underlying index is constructed, rebalanced, and maintained."),
            EvidenceRequirement(name="factor or strategy soundness", description="Evidence that the strategy or factor has a durable basis.", required=False),
        ],
        "Performance & Tracking": [
            EvidenceRequirement(name="returns vs benchmark", description="1yr, 3yr, 5yr returns vs the stated index or benchmark."),
            EvidenceRequirement(name="tracking error and tracking difference", description="How closely the ETF follows its index in practice."),
        ],
        "Holdings & Exposure": [
            EvidenceRequirement(name="top holdings and concentration", description="Largest positions, sector weights, and geographic exposure."),
            EvidenceRequirement(name="underlying liquidity", description="Whether the underlying securities are sufficiently liquid."),
        ],
        "Fee Structure": [
            EvidenceRequirement(name="expense ratio", description="Annual fee charged vs direct peers tracking the same index."),
        ],
        "Liquidity & Trading": [
            EvidenceRequirement(name="AUM and daily volume", description="Total assets under management and average daily trading volume."),
            EvidenceRequirement(name="bid-ask spread and premium/discount", description="Intraday trading costs and how often ETF trades away from NAV."),
        ],
        "Provider Quality": [
            EvidenceRequirement(name="issuer reputation and scale", description="Provider track record, operational risk, and counterparty risk for synthetic ETFs.", required=False),
        ],
    },

    AssetClass.REIT: {
        "Portfolio Quality": [
            EvidenceRequirement(name="occupancy and tenant quality", description="Occupancy rates, tenant creditworthiness, and lease expiry profile.", preferred_source_types=["regulatory", "investor_relations"]),
            EvidenceRequirement(name="property type and diversification", description="Sector mix (office, industrial, retail, residential) and geographic spread."),
        ],
        "Distribution Sustainability": [
            EvidenceRequirement(name="AFFO payout ratio", description="Adjusted FFO payout ratio — whether distributions are covered by cash flows.", preferred_source_types=["regulatory", "investor_relations"]),
            EvidenceRequirement(name="dividend history", description="Dividend growth record, consistency, and any past cuts."),
        ],
        "Balance Sheet & Leverage": [
            EvidenceRequirement(name="LTV and debt maturity", description="Loan-to-value ratio, debt maturity schedule, and refinancing risk.", preferred_source_types=["regulatory"]),
            EvidenceRequirement(name="credit rating", description="Investment grade status and rating trend.", required=False),
        ],
        "Management & Operations": [
            EvidenceRequirement(name="internal vs external management", description="Whether management is internalised or external — key for fee alignment."),
            EvidenceRequirement(name="capital allocation track record", description="Acquisition discipline, development pipeline quality, and disposition history."),
        ],
        "Valuation": [
            EvidenceRequirement(name="premium or discount to NAV", description="Whether the stock trades at a premium or discount to net asset value."),
            EvidenceRequirement(name="P/AFFO vs peers", description="Price-to-AFFO multiple relative to the REIT subsector."),
        ],
        "Macro & Real Estate Market": [
            EvidenceRequirement(name="interest rate sensitivity", description="How the REIT's cost of debt and cap rates respond to rate changes."),
            EvidenceRequirement(name="property market supply and demand", description="Vacancy trends, new supply pipeline, and rental growth in target markets."),
        ],
    },

    AssetClass.FIXED_INCOME: {
        "Credit Quality": [
            EvidenceRequirement(name="credit rating and outlook", description="Current rating from major agencies and recent rating actions.", preferred_source_types=["regulatory", "financial_data"]),
            EvidenceRequirement(name="default probability", description="Market-implied or agency-assessed probability of default."),
        ],
        "Interest Rate Sensitivity": [
            EvidenceRequirement(name="duration and convexity", description="Modified duration and convexity as measures of price sensitivity to rate moves."),
            EvidenceRequirement(name="call or put features", description="Embedded options that alter effective duration.", required=False),
        ],
        "Issuer Financial Health": [
            EvidenceRequirement(name="debt service coverage", description="EBITDA or cash flow coverage of interest and principal payments.", preferred_source_types=["regulatory"]),
            EvidenceRequirement(name="leverage and liquidity", description="Gross/net leverage ratios and liquidity position of the issuer."),
        ],
        "Covenant Analysis": [
            EvidenceRequirement(name="key covenants", description="Maintenance or incurrence covenants that restrict issuer behaviour.", required=False),
            EvidenceRequirement(name="covenant headroom", description="How much cushion the issuer has before triggering covenants.", required=False),
        ],
        "Yield & Spread": [
            EvidenceRequirement(name="current yield and YTM", description="Current yield, yield-to-maturity, and yield-to-worst."),
            EvidenceRequirement(name="spread vs benchmark", description="Option-adjusted spread or Z-spread vs a risk-free benchmark."),
        ],
        "Liquidity": [
            EvidenceRequirement(name="issue size and trading volume", description="Bond issue size and secondary market trading activity."),
        ],
    },
}

# Fill UNKNOWN with EQUITY_STOCK requirements
EVIDENCE_REQUIREMENTS[AssetClass.UNKNOWN] = EVIDENCE_REQUIREMENTS[AssetClass.EQUITY_STOCK]


# ---------------------------------------------------------------------------
# Search focus per asset class + pillar
# ---------------------------------------------------------------------------

SEARCH_FOCUS: dict[AssetClass, dict[str, list[str]]] = {

    AssetClass.EQUITY_STOCK: {
        "Macro & Industry":              ["industry reports", "market growth", "regulation", "competitor dynamics"],
        "Economic Moat":                 ["annual reports", "customer retention", "market share", "competitive advantage"],
        "Financial Engine":              ["annual report", "quarterly results", "earnings presentation", "cash flow"],
        "Management & Capital Allocation": ["proxy statement", "governance", "dividends", "buybacks", "acquisitions"],
        "Valuation":                     ["guidance", "valuation multiples", "peer comparison", "price targets"],
        "Technical Analysis":            ["price trend", "volume", "moving averages", "relative strength"],
    },

    AssetClass.PRE_REVENUE_EQUITY: {
        "Macro & Industry":              ["TAM sizing", "market growth", "industry tailwinds", "regulatory environment"],
        "Technology & Product":          ["product specs", "patent filings", "technical differentiation", "product roadmap", "design wins"],
        "Path to Profitability":         ["cash burn", "runway", "revenue growth", "gross margin", "S-1 filing", "10-K"],
        "Management & Team":             ["founder background", "executive team", "prior exits", "domain expertise"],
        "Valuation & Dilution Risk":     ["EV/revenue multiple", "fundraising history", "cap table", "comparable funding rounds", "10-K"],
        "Competitive Positioning":       ["competitive landscape", "market map", "barriers to entry", "competitor funding"],
    },

    AssetClass.FUND: {
        "Fund Performance":              ["fund factsheet", "annual report", "benchmark returns", "NAV history"],
        "Manager Quality":               ["portfolio manager profile", "investment process", "manager tenure", "fund prospectus"],
        "Portfolio Construction":        ["top holdings", "sector allocation", "portfolio turnover", "fund factsheet"],
        "Fee Structure":                 ["total expense ratio", "ongoing charges", "fund prospectus", "fee comparison"],
        "Risk-Adjusted Returns":         ["Sharpe ratio", "max drawdown", "fund volatility", "risk metrics"],
        "Investor Fit":                  ["fund mandate", "investor type", "liquidity terms", "minimum investment"],
    },

    AssetClass.ETF: {
        "Index & Strategy Quality":      ["index methodology", "index factsheet", "rebalancing rules"],
        "Performance & Tracking":        ["ETF returns", "tracking error", "tracking difference", "benchmark comparison"],
        "Holdings & Exposure":           ["ETF holdings", "sector weights", "geographic exposure", "top positions"],
        "Fee Structure":                 ["expense ratio", "ETF cost comparison", "total cost of ownership"],
        "Liquidity & Trading":           ["ETF AUM", "daily volume", "bid-ask spread", "premium discount to NAV"],
        "Provider Quality":              ["ETF provider", "issuer reputation", "fund domicile", "counterparty risk"],
    },

    AssetClass.REIT: {
        "Portfolio Quality":             ["occupancy rate", "tenant quality", "property type", "lease expiry", "investor presentation"],
        "Distribution Sustainability":   ["FFO", "AFFO", "dividend history", "payout ratio", "distribution coverage"],
        "Balance Sheet & Leverage":      ["LTV", "debt maturity", "credit rating", "refinancing", "10-K"],
        "Management & Operations":       ["REIT management structure", "internal external manager", "acquisition history"],
        "Valuation":                     ["NAV discount premium", "P/AFFO", "cap rate", "peer comparison"],
        "Macro & Real Estate Market":    ["interest rates", "property market", "vacancy trends", "rental growth", "cap rate environment"],
    },

    AssetClass.FIXED_INCOME: {
        "Credit Quality":                ["credit rating", "rating action", "default risk", "rating agency report"],
        "Interest Rate Sensitivity":     ["bond duration", "modified duration", "convexity", "interest rate risk"],
        "Issuer Financial Health":       ["issuer financials", "EBITDA coverage", "leverage ratio", "annual report"],
        "Covenant Analysis":             ["bond covenants", "indenture", "maintenance covenants", "covenant headroom"],
        "Yield & Spread":                ["bond yield", "YTM", "OAS", "credit spread", "spread vs treasuries"],
        "Liquidity":                     ["bond liquidity", "issue size", "secondary market volume", "trading activity"],
    },
}

SEARCH_FOCUS[AssetClass.UNKNOWN] = SEARCH_FOCUS[AssetClass.EQUITY_STOCK]


# ---------------------------------------------------------------------------
# Pillar signals for scoring (keyword → signal name mapping)
# ---------------------------------------------------------------------------

PILLAR_SIGNALS: dict[AssetClass, dict[str, dict[str, list[str]]]] = {

    AssetClass.EQUITY_STOCK: {
        "Macro & Industry": {
            "Secular Trends":  ["trend", "growth", "demand", "outlook", "cagr", "tam"],
            "Market Structure": ["market share", "industry", "competition", "regulation"],
        },
        "Economic Moat": {
            "Switching Costs":       ["switching", "retention", "contract", "renewal"],
            "Competitive Advantage": ["brand", "patent", "network effect", "cost advantage"],
        },
        "Financial Engine": {
            "Profitability":    ["roic", "margin", "ebitda", "eps", "profit"],
            "Cash and Leverage": ["free cash flow", "fcf", "debt", "debt/ebitda", "balance sheet"],
        },
        "Management & Capital Allocation": {
            "Capital Allocation": ["buyback", "dividend", "acquisition", "allocation"],
            "Governance":         ["insider", "ownership", "board", "governance", "management"],
        },
        "Valuation": {
            "Multiples":       ["p/e", "ev/ebitda", "multiple", "valuation", "price target"],
            "Intrinsic Value": ["dcf", "fair value", "discount", "upside", "margin of safety"],
        },
        "Technical Analysis": {
            "Trend and Momentum":         ["moving average", "breakout", "rsi", "momentum", "uptrend"],
            "Volume and Relative Strength": ["volume", "accumulation", "relative strength", "support", "resistance"],
        },
    },

    AssetClass.PRE_REVENUE_EQUITY: {
        "Macro & Industry": {
            "TAM and Market Opportunity": ["tam", "market size", "addressable market", "growth rate", "cagr"],
            "Industry Tailwinds":         ["tailwind", "regulation", "policy", "adoption", "secular trend"],
        },
        "Technology & Product": {
            "Technical Differentiation": ["patent", "proprietary", "silicon", "energy density", "unique", "breakthrough"],
            "Product Validation":         ["design win", "qualification", "certification", "pilot", "customer trial", "contract"],
        },
        "Path to Profitability": {
            "Burn Rate and Runway":  ["burn rate", "cash runway", "cash on hand", "months of cash", "operating cash"],
            "Revenue Trajectory":    ["revenue growth", "backlog", "order book", "pipeline", "arr", "mrr"],
            "Gross Margin Progress": ["gross margin", "cogs", "scale", "manufacturing cost", "cost reduction"],
        },
        "Management & Team": {
            "Founder Quality":  ["founder", "ceo", "executive", "track record", "prior exit", "domain expert"],
            "Team Depth":       ["team", "hire", "vp", "chief", "advisor", "board"],
        },
        "Valuation & Dilution Risk": {
            "Valuation vs Peers":   ["ev/revenue", "price/sales", "valuation", "multiple", "comparable", "comp"],
            "Dilution and Capital": ["dilution", "shares outstanding", "capital raise", "secondary offering", "warrant"],
        },
        "Competitive Positioning": {
            "Competitive Landscape": ["competitor", "rival", "competition", "alternative", "market map"],
            "Barriers to Entry":     ["barrier", "moat", "proprietary", "switching cost", "qualification", "certification"],
        },
    },

    AssetClass.FUND: {
        "Fund Performance": {
            "Returns vs Benchmark": ["return", "performance", "benchmark", "outperform", "alpha", "nav"],
            "Alpha Generation":     ["alpha", "excess return", "active return", "information ratio"],
        },
        "Manager Quality": {
            "Track Record":      ["track record", "tenure", "years", "managed", "outperformed", "history"],
            "Investment Process": ["process", "methodology", "screen", "selection", "philosophy", "framework"],
        },
        "Portfolio Construction": {
            "Holdings and Concentration": ["top holdings", "position", "concentration", "portfolio", "weight"],
            "Diversification":            ["diversified", "sector", "geographic", "allocation", "exposure"],
        },
        "Fee Structure": {
            "TER and Charges": ["ter", "expense ratio", "ongoing charge", "management fee", "ocf"],
            "Performance Fees": ["performance fee", "high watermark", "hurdle rate", "carried interest"],
        },
        "Risk-Adjusted Returns": {
            "Sharpe and Sortino": ["sharpe", "sortino", "risk-adjusted", "information ratio", "calmar"],
            "Drawdown":           ["drawdown", "peak", "trough", "worst", "recovery", "underwater"],
        },
        "Investor Fit": {
            "Fund Mandate":    ["mandate", "objective", "strategy", "focus", "income", "growth"],
            "Liquidity Terms": ["liquidity", "redemption", "lock-up", "minimum", "notice period"],
        },
    },

    AssetClass.ETF: {
        "Index & Strategy Quality": {
            "Index Methodology":  ["index", "methodology", "rebalance", "rules-based", "construction"],
            "Strategy Rationale": ["factor", "smart beta", "momentum", "value", "quality", "strategy"],
        },
        "Performance & Tracking": {
            "Returns":            ["return", "performance", "ytd", "1yr", "3yr", "annualised"],
            "Tracking Quality":   ["tracking error", "tracking difference", "replication", "sampling"],
        },
        "Holdings & Exposure": {
            "Top Holdings":      ["holding", "position", "weight", "top", "constituent"],
            "Sector Exposure":   ["sector", "industry", "geographic", "country", "allocation"],
        },
        "Fee Structure": {
            "Expense Ratio": ["expense ratio", "ter", "ongoing charge", "fee", "cost"],
        },
        "Liquidity & Trading": {
            "AUM and Volume":     ["aum", "assets", "volume", "daily", "traded"],
            "Spread and Premium": ["spread", "premium", "discount", "nav", "bid", "ask"],
        },
        "Provider Quality": {
            "Issuer Reputation": ["blackrock", "vanguard", "invesco", "ishares", "provider", "domicile"],
        },
    },

    AssetClass.REIT: {
        "Portfolio Quality": {
            "Occupancy and Tenants": ["occupancy", "tenant", "lease", "vacancy", "renewal", "retention"],
            "Property Type":         ["office", "industrial", "retail", "residential", "logistics", "data center"],
        },
        "Distribution Sustainability": {
            "FFO and AFFO":       ["ffo", "affo", "funds from operations", "adjusted ffo"],
            "Dividend Coverage":  ["payout ratio", "dividend", "distribution", "coverage", "yield"],
        },
        "Balance Sheet & Leverage": {
            "LTV and Debt":     ["ltv", "loan to value", "leverage", "debt", "maturity", "refinance"],
            "Credit Rating":    ["rating", "investment grade", "bbb", "moody", "s&p"],
        },
        "Management & Operations": {
            "Management Structure": ["internal", "external", "manager", "internalized", "fee"],
            "Capital Allocation":   ["acquisition", "development", "disposition", "cap rate", "noi"],
        },
        "Valuation": {
            "NAV":          ["nav", "net asset value", "premium", "discount", "book value"],
            "P/AFFO":       ["p/affo", "price to affo", "multiple", "peer", "valuation"],
        },
        "Macro & Real Estate Market": {
            "Interest Rates":    ["interest rate", "fed", "rate", "cap rate", "cost of debt"],
            "Property Markets":  ["vacancy", "rental growth", "supply", "demand", "absorption"],
        },
    },

    AssetClass.FIXED_INCOME: {
        "Credit Quality": {
            "Rating":         ["rating", "aaa", "bbb", "bb", "default", "credit quality", "downgrade"],
            "Default Risk":   ["probability of default", "pd", "recovery rate", "credit risk"],
        },
        "Interest Rate Sensitivity": {
            "Duration":    ["duration", "modified duration", "dv01", "basis point"],
            "Convexity":   ["convexity", "negative convexity", "callable", "embedded option"],
        },
        "Issuer Financial Health": {
            "Coverage":  ["interest coverage", "ebitda", "dscr", "debt service"],
            "Leverage":  ["leverage", "net debt", "gross debt", "debt/ebitda"],
        },
        "Covenant Analysis": {
            "Covenants": ["covenant", "indenture", "maintenance", "incurrence", "restricted payment"],
        },
        "Yield & Spread": {
            "Yield":   ["yield", "ytm", "yield to maturity", "ytw", "coupon"],
            "Spread":  ["spread", "oas", "z-spread", "credit spread", "basis"],
        },
        "Liquidity": {
            "Market Liquidity": ["liquidity", "volume", "bid-ask", "issue size", "outstanding"],
        },
    },
}

PILLAR_SIGNALS[AssetClass.UNKNOWN] = PILLAR_SIGNALS[AssetClass.EQUITY_STOCK]


# ---------------------------------------------------------------------------
# Pillar weights for scoring
# ---------------------------------------------------------------------------

PILLAR_WEIGHTS: dict[AssetClass, dict[str, float]] = {
    AssetClass.EQUITY_STOCK: {
        "Macro & Industry":              0.15,
        "Economic Moat":                 0.20,
        "Financial Engine":              0.25,
        "Management & Capital Allocation": 0.15,
        "Valuation":                     0.15,
        "Technical Analysis":            0.10,
    },
    AssetClass.PRE_REVENUE_EQUITY: {
        "Macro & Industry":       0.10,
        "Technology & Product":   0.25,
        "Path to Profitability":  0.20,
        "Management & Team":      0.20,
        "Valuation & Dilution Risk": 0.15,
        "Competitive Positioning": 0.10,
    },
    AssetClass.FUND: {
        "Fund Performance":     0.25,
        "Manager Quality":      0.20,
        "Portfolio Construction": 0.15,
        "Fee Structure":        0.15,
        "Risk-Adjusted Returns": 0.20,
        "Investor Fit":         0.05,
    },
    AssetClass.ETF: {
        "Index & Strategy Quality": 0.20,
        "Performance & Tracking":   0.25,
        "Holdings & Exposure":      0.15,
        "Fee Structure":            0.20,
        "Liquidity & Trading":      0.15,
        "Provider Quality":         0.05,
    },
    AssetClass.REIT: {
        "Portfolio Quality":          0.20,
        "Distribution Sustainability": 0.25,
        "Balance Sheet & Leverage":   0.20,
        "Management & Operations":    0.10,
        "Valuation":                  0.15,
        "Macro & Real Estate Market": 0.10,
    },
    AssetClass.FIXED_INCOME: {
        "Credit Quality":          0.25,
        "Interest Rate Sensitivity": 0.15,
        "Issuer Financial Health": 0.25,
        "Covenant Analysis":       0.10,
        "Yield & Spread":          0.15,
        "Liquidity":               0.10,
    },
}

PILLAR_WEIGHTS[AssetClass.UNKNOWN] = PILLAR_WEIGHTS[AssetClass.EQUITY_STOCK]


# ---------------------------------------------------------------------------
# Asset class detection
# ---------------------------------------------------------------------------

# Keyword patterns for deterministic detection.  Order matters — more specific
# patterns are checked before broader ones.
_FUND_PATTERNS = re.compile(
    r"\b(mutual fund|open.?end fund|closed.?end fund|unit trust|"
    r"sicav|fcp|investment fund|equity fund|income fund|"
    r"balanced fund|growth fund|money market fund)\b",
    re.IGNORECASE,
)
_FUND_SUFFIX = re.compile(
    r"\b(fund|fcp|sicav)\s*(plc|ltd|limited|inc|lp|llc)?\s*$",
    re.IGNORECASE,
)
_ETF_PATTERNS = re.compile(
    r"\b(etf|exchange.?traded fund|exchange traded fund)\b",
    re.IGNORECASE,
)
_ETF_SUFFIX = re.compile(
    r"\b(etf|exchange.?traded)\s*(fund)?\s*$",
    re.IGNORECASE,
)
_REIT_PATTERNS = re.compile(
    r"\b(reit|real estate investment trust|realty|"
    r"property trust|property fund)\b",
    re.IGNORECASE,
)
_FIXED_INCOME_PATTERNS = re.compile(
    r"\b(bond|note|debenture|fixed income|credit fund|"
    r"high yield|investment grade bond|senior note)\b",
    re.IGNORECASE,
)


def detect_asset_class(
    name: str,
    ticker: str = "",
    description: str = "",
    explicit: AssetClass | str | None = None,
) -> AssetClass:
    """Detect asset class from name, ticker, and optional description.

    Args:
        name:        Human-readable entity name (e.g. "InvestCorp Active Equity Fund").
        ticker:      Ticker symbol (e.g. "IAEF", "SPY").
        description: Optional free-text description for richer signals.
        explicit:    If the caller already knows the asset class, pass it here
                     to skip detection entirely.

    Returns:
        The detected AssetClass.  Falls back to EQUITY_STOCK for ambiguous
        cases so the existing pipeline always gets a usable result.
    """
    if explicit is not None:
        try:
            return AssetClass(str(explicit))
        except ValueError:
            pass

    combined = f"{name} {ticker} {description}".strip()

    # ETF — check before fund because some ETFs have "fund" in the name.
    if _ETF_PATTERNS.search(combined) or _ETF_SUFFIX.search(name):
        return AssetClass.ETF

    # Mutual / closed-end fund
    if _FUND_PATTERNS.search(combined) or _FUND_SUFFIX.search(name):
        return AssetClass.FUND

    # REIT
    if _REIT_PATTERNS.search(combined):
        return AssetClass.REIT

    # Fixed income
    if _FIXED_INCOME_PATTERNS.search(combined):
        return AssetClass.FIXED_INCOME

    # Pre-revenue equity: common markers in name or description
    pre_revenue_signals = [
        "pre-revenue", "pre revenue", "early stage", "startup",
        "series a", "series b", "spac", "de-spac",
    ]
    combined_lower = combined.lower()
    if any(sig in combined_lower for sig in pre_revenue_signals):
        return AssetClass.PRE_REVENUE_EQUITY

    return AssetClass.EQUITY_STOCK


def get_pillars(asset_class: AssetClass) -> tuple[str, ...]:
    """Return the ordered pillar tuple for a given asset class."""
    return PILLARS_BY_ASSET_CLASS.get(asset_class, EQUITY_STOCK_PILLARS)


def get_core_pillars(asset_class: AssetClass) -> frozenset[str]:
    """Return the set of core pillars that receive extra retry budget."""
    return CORE_PILLARS_BY_ASSET_CLASS.get(asset_class, frozenset())


def get_evidence_requirements(
    asset_class: AssetClass,
    pillar: str,
) -> list[EvidenceRequirement]:
    """Return evidence requirements for one pillar of a given asset class."""
    return EVIDENCE_REQUIREMENTS.get(asset_class, {}).get(pillar, [])


def get_search_focus(asset_class: AssetClass, pillar: str) -> list[str]:
    """Return search focus hints for one pillar of a given asset class."""
    return SEARCH_FOCUS.get(asset_class, {}).get(pillar, [])


def get_pillar_signals(asset_class: AssetClass) -> dict[str, dict[str, list[str]]]:
    """Return the keyword signal map used by the scoring layer."""
    return PILLAR_SIGNALS.get(asset_class, PILLAR_SIGNALS[AssetClass.EQUITY_STOCK])


def get_pillar_weights(asset_class: AssetClass) -> dict[str, float]:
    """Return the pillar weights used by the scoring layer."""
    return PILLAR_WEIGHTS.get(asset_class, PILLAR_WEIGHTS[AssetClass.EQUITY_STOCK])


def asset_class_label(asset_class: AssetClass) -> str:
    """Return a human-readable label for display in memos and UI."""
    labels = {
        AssetClass.EQUITY_STOCK:       "Listed Equity",
        AssetClass.PRE_REVENUE_EQUITY: "Early-Stage Equity",
        AssetClass.FUND:               "Mutual Fund",
        AssetClass.ETF:                "ETF",
        AssetClass.REIT:               "REIT",
        AssetClass.FIXED_INCOME:       "Fixed Income",
        AssetClass.UNKNOWN:            "Listed Equity",
    }
    return labels.get(asset_class, "Listed Equity")
