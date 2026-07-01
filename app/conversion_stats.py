"""
conversion_stats.py — Blended-default calculator for agent Business Plans.

Combines an individual agent's trailing-window conversion rates with the
company-wide baseline using sample-size-weighted shrinkage, so agents with
few closed deals get defaults that lean on company data, while high-volume
agents' own track record dominates naturally (no hard cliff at a threshold).

Formula (per rate):
    blended = (agent_n * agent_rate + K * company_rate) / (agent_n + K)

K controls how many "company-equivalent" deals of skepticism to apply before
trusting the individual number. K=10 means an agent with 10 closed deals gets
a 50/50 blend of their own rate and company rate; an agent with 40 deals gets
80% their own rate; an agent with 0-2 deals gets mostly company rate.

Renee-approved defaults (2026-07-01):
  - K = 10 (shrinkage weight)
  - No fee stack (KW cap / franchise / office / desk) — out of scope
  - No referral/co-list split fields — out of scope
  - No showing-agent split — only primary-agent GCI drives the math
"""

SHRINKAGE_K = 10


def blend_rate(agent_rate, agent_n, company_rate):
    """Sample-size-weighted blend of an individual rate with the company
    baseline. Falls back cleanly when either side is missing data.

    Returns None only if BOTH agent_rate and company_rate are None (i.e.
    there is truly no data anywhere to build a default from)."""
    if agent_rate is None and company_rate is None:
        return None
    if agent_rate is None:
        return company_rate
    if company_rate is None:
        return agent_rate
    n = agent_n or 0
    blended = (n * agent_rate + SHRINKAGE_K * company_rate) / (n + SHRINKAGE_K)
    return round(blended, 4)


def blend_price(agent_price, agent_n, company_price):
    """Same shrinkage logic applied to dollar averages (avg sale price),
    not just rates — a new agent's 'typical deal size' default should also
    lean on company data until they have a track record."""
    if agent_price is None and company_price is None:
        return None
    if agent_price is None:
        return company_price
    if company_price is None:
        return agent_price
    n = agent_n or 0
    blended = (n * agent_price + SHRINKAGE_K * company_price) / (n + SHRINKAGE_K)
    return round(blended, 2)


def get_blended_defaults(agent_stats, company_stats):
    """Build the full set of blended funnel defaults for a Business Plan form.

    agent_stats / company_stats: AgentConversionStats ORM rows (or None).
    Returns a dict matching the BusinessPlan funnel field names, ready to
    pre-fill the form. All values are None-safe: if there's no data at all
    (brand-new company, no closed deals anywhere yet), falls back to sane
    hardcoded defaults so the form never renders blank/broken.
    """
    a = agent_stats
    c = company_stats

    def a_get(field):
        return getattr(a, field) if a else None

    def c_get(field):
        return getattr(c, field) if c else None

    n_listing = a_get('n_listing_deals') or 0
    n_buyer = a_get('n_buyer_deals') or 0

    listing_held_rate = blend_rate(a_get('listing_held_rate'), n_listing, c_get('listing_held_rate'))
    listing_signed_rate = blend_rate(a_get('listing_signed_rate'), n_listing, c_get('listing_signed_rate'))
    listing_close_rate = blend_rate(a_get('listing_close_rate'), n_listing, c_get('listing_close_rate'))

    buyer_held_rate = blend_rate(a_get('buyer_held_rate'), n_buyer, c_get('buyer_held_rate'))
    buyer_signed_rate = blend_rate(a_get('buyer_signed_rate'), n_buyer, c_get('buyer_signed_rate'))
    buyer_close_rate = blend_rate(a_get('buyer_close_rate'), n_buyer, c_get('buyer_close_rate'))

    avg_list_price = blend_price(a_get('avg_list_price'), n_listing, c_get('avg_list_price')) or 350000
    avg_buy_price = blend_price(a_get('avg_buy_price'), n_buyer, c_get('avg_buy_price')) or 300000
    avg_listing_comm_pct = blend_price(a_get('avg_listing_comm_pct'), n_listing, c_get('avg_listing_comm_pct')) or 0.03
    avg_buyer_comm_pct = blend_price(a_get('avg_buyer_comm_pct'), n_buyer, c_get('avg_buyer_comm_pct')) or 0.03

    # Fallback rates if truly no data exists anywhere yet (new company, no
    # history at all) — conservative reasonable-real-estate-funnel guesses,
    # never left as None so the form/JS calculator always has a number.
    listing_held_rate = listing_held_rate if listing_held_rate is not None else 0.70
    listing_signed_rate = listing_signed_rate if listing_signed_rate is not None else 0.40
    listing_close_rate = listing_close_rate if listing_close_rate is not None else 0.80
    buyer_held_rate = buyer_held_rate if buyer_held_rate is not None else 0.70
    buyer_signed_rate = buyer_signed_rate if buyer_signed_rate is not None else 0.50
    buyer_close_rate = buyer_close_rate if buyer_close_rate is not None else 0.80

    return {
        'listing_held_rate': listing_held_rate,
        'listing_signed_rate': listing_signed_rate,
        'listing_close_rate': listing_close_rate,
        'buyer_held_rate': buyer_held_rate,
        'buyer_signed_rate': buyer_signed_rate,
        'buyer_close_rate': buyer_close_rate,
        'avg_list_price': avg_list_price,
        'avg_buy_price': avg_buy_price,
        'avg_listing_comm_pct': avg_listing_comm_pct,
        'avg_buyer_comm_pct': avg_buyer_comm_pct,
        'n_listing_deals': n_listing,
        'n_buyer_deals': n_buyer,
    }
