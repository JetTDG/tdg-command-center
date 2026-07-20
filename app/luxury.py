"""
Luxury segment qualification logic.

Luxury is a subset of Residential with a $750,000 inclusive threshold.
- Closed transactions MUST use sale_price only (blank/low excludes even if list_price is high)
- Open transactions use positive sale_price if entered, otherwise fall back to list_price
- Commercial transactions NEVER qualify
"""

from sqlalchemy import and_, or_


LUXURY_THRESHOLD = 750000


def qualifies_as_luxury(status, sale_price, list_price, division):
    """
    Pure Python predicate: determine if a transaction qualifies as Luxury.
    
    Args:
        status: Transaction status (e.g., 'Closed', 'Pending', 'Active')
        sale_price: Closing price (None, 0, or numeric)
        list_price: List price (None or numeric)
        division: 'Residential', 'Commercial', etc.
    
    Returns:
        True if transaction qualifies as Luxury; False otherwise.
    """
    # Must be Residential division
    if division != 'Residential':
        return False
    
    # Closed transactions: use sale_price only
    if status == 'Closed':
        # Must have a positive sale_price >= threshold
        return sale_price is not None and sale_price >= LUXURY_THRESHOLD
    
    # Open transactions: use sale_price only when it is positive; otherwise
    # fall back to list_price. This deliberately mirrors sql_luxury_predicate().
    if sale_price is not None and sale_price > 0:
        return sale_price >= LUXURY_THRESHOLD
    
    # Fall back to list_price
    if list_price:
        return list_price >= LUXURY_THRESHOLD
    
    # Neither available
    return False


def normalize_segment(segment):
    """
    Normalize segment name to canonical form.
    
    Args:
        segment: User-provided segment name (case-insensitive)
    
    Returns:
        Canonical segment name: 'Luxury', 'Residential', 'Commercial', 'Combined'
    """
    if not segment:
        return None
    
    normalized = segment.lower().strip()
    
    if normalized == 'luxury':
        return 'Luxury'
    elif normalized == 'residential':
        return 'Residential'
    elif normalized == 'commercial':
        return 'Commercial'
    elif normalized == 'combined':
        return 'Combined'
    
    # Unknown segment — return as-is (caller will handle)
    return segment


def apply_segment_filter(query, segment):
    """Apply a canonical Command Center segment to a Transaction query.

    ``all``/``combined`` leave the query unchanged. Luxury always uses the
    shared status-aware price rule so every reporting surface agrees.
    """
    from app.models import Transaction

    key = (segment or 'combined').strip().lower()
    if key in ('res', 'residential'):
        return query.filter(Transaction.division == 'Residential')
    if key in ('comm', 'commercial'):
        return query.filter(Transaction.division == 'Commercial')
    if key == 'luxury':
        return query.filter(sql_luxury_predicate())
    return query


def sql_luxury_predicate():
    """
    SQLAlchemy predicate for transactions that qualify as Luxury (open or closed).
    
    Returns:
        A SQLAlchemy and_() expression for use in .filter()
    """
    from app.models import Transaction
    
    # Luxury = Residential + price >= $750k
    # For closed: sale_price >= 750000
    # For open: (sale_price > 0 and sale_price >= 750000) OR (sale_price <= 0 and list_price >= 750000)
    closed_predicate = and_(
        Transaction.status == 'Closed',
        Transaction.sale_price >= LUXURY_THRESHOLD,
    )
    
    open_predicate = and_(
        Transaction.status != 'Closed',
        or_(
            # Has positive sale_price >= threshold
            and_(
                Transaction.sale_price > 0,
                Transaction.sale_price >= LUXURY_THRESHOLD,
            ),
            # No/low sale_price, but list_price >= threshold
            and_(
                or_(
                    Transaction.sale_price.is_(None),
                    Transaction.sale_price <= 0,
                ),
                Transaction.list_price >= LUXURY_THRESHOLD,
            ),
        ),
    )
    
    # Luxury = Residential + (closed OR open) qualifications
    return and_(
        Transaction.division == 'Residential',
        or_(closed_predicate, open_predicate),
    )


def sql_luxury_closed_predicate():
    """
    SQLAlchemy predicate for closed Luxury transactions only.
    
    Returns:
        A SQLAlchemy and_() expression for use in .filter()
    """
    from app.models import Transaction
    
    return and_(
        Transaction.division == 'Residential',
        Transaction.status == 'Closed',
        Transaction.sale_price >= LUXURY_THRESHOLD,
    )
