"""
Test-Driven Development for Luxury segment qualification.
Tests boundary, closed sale-price-only, open sale/list fallback, commercial exclusion, and segment aliases.
"""

import pytest
from datetime import date


class TestLuxuryQualification:
    """Test the Luxury segment qualification logic."""
    
    def test_closed_transaction_uses_sale_price_only(self):
        """
        Closed transaction MUST qualify using sale_price only.
        High list_price alone does not qualify if sale_price is low/absent.
        """
        from app.luxury import qualifies_as_luxury
        
        # Closed, $800k sale, $1M list -> qualifies
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=800000,
            list_price=1000000,
            division='Residential'
        ) is True
        
        # Closed, $700k sale (below threshold), $1M list -> does NOT qualify
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=700000,
            list_price=1000000,
            division='Residential'
        ) is False
        
        # Closed, blank/None sale_price, $1M list -> does NOT qualify
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=None,
            list_price=1000000,
            division='Residential'
        ) is False
    
    def test_closed_transaction_at_threshold_boundary(self):
        """$750,000 threshold is INCLUSIVE for closed transactions."""
        from app.luxury import qualifies_as_luxury
        
        # Exactly $750k -> qualifies
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=750000,
            list_price=None,
            division='Residential'
        ) is True
        
        # Just under $750k -> does not qualify
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=749999.99,
            list_price=None,
            division='Residential'
        ) is False
    
    def test_open_transaction_uses_sale_price_if_entered(self):
        """Open transaction uses positive sale_price if entered, otherwise list_price."""
        from app.luxury import qualifies_as_luxury
        
        # Open (not Closed), $800k sale_price entered -> qualifies
        assert qualifies_as_luxury(
            status='Pending',
            sale_price=800000,
            list_price=None,
            division='Residential'
        ) is True
        
        # Open (not Closed), $700k sale_price (below threshold) -> does not qualify
        assert qualifies_as_luxury(
            status='Pending',
            sale_price=700000,
            list_price=1000000,
            division='Residential'
        ) is False
    
    def test_open_transaction_fallback_to_list_price(self):
        """Open transaction falls back to list_price when sale_price is blank/zero."""
        from app.luxury import qualifies_as_luxury
        
        # Open, no sale_price, $800k list_price -> qualifies
        assert qualifies_as_luxury(
            status='Pending',
            sale_price=None,
            list_price=800000,
            division='Residential'
        ) is True
        
        # Open, $0 sale_price, $800k list_price -> qualifies (zero is falsy fallback)
        assert qualifies_as_luxury(
            status='Pending',
            sale_price=0,
            list_price=800000,
            division='Residential'
        ) is True

        # Negative/non-positive sale_price is not a usable entered price;
        # fall back to the list price just as the SQL predicate does.
        assert qualifies_as_luxury(
            status='Active',
            sale_price=-1,
            list_price=800000,
            division='Residential'
        ) is True
        
        # Open, no sale_price, $700k list_price -> does not qualify
        assert qualifies_as_luxury(
            status='Pending',
            sale_price=None,
            list_price=700000,
            division='Residential'
        ) is False
    
    def test_commercial_excluded(self):
        """Commercial transactions NEVER qualify as Luxury, regardless of price."""
        from app.luxury import qualifies_as_luxury
        
        # Commercial, closed, $10M sale_price -> does NOT qualify
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=10000000,
            list_price=None,
            division='Commercial'
        ) is False
        
        # Commercial, open, $10M list_price -> does NOT qualify
        assert qualifies_as_luxury(
            status='Pending',
            sale_price=None,
            list_price=10000000,
            division='Commercial'
        ) is False
    
    def test_non_residential_excluded(self):
        """Non-Residential divisions NEVER qualify."""
        from app.luxury import qualifies_as_luxury
        
        # Unknown division, closed, $10M -> does NOT qualify
        assert qualifies_as_luxury(
            status='Closed',
            sale_price=10000000,
            list_price=None,
            division='Other'
        ) is False
    
    def test_segment_aliases(self):
        """Luxury segment can be referred to by alias."""
        from app.luxury import normalize_segment
        
        assert normalize_segment('luxury') == 'Luxury'
        assert normalize_segment('Luxury') == 'Luxury'
        assert normalize_segment('LUXURY') == 'Luxury'
        assert normalize_segment('Residential') == 'Residential'
        assert normalize_segment('Commercial') == 'Commercial'
        assert normalize_segment('Combined') == 'Combined'


class TestLuxurySQLPredicate:
    """Test SQL-safe qualification predicates."""
    
    def test_sql_closed_predicate(self):
        """SQL predicate for closed Luxury transactions."""
        from app.luxury import sql_luxury_closed_predicate
        
        # Should return a SQLAlchemy predicate (callable or expression)
        predicate = sql_luxury_closed_predicate()
        assert predicate is not None
    
    def test_sql_all_predicate(self):
        """SQL predicate for all Luxury transactions (closed or open)."""
        from app.luxury import sql_luxury_predicate
        
        # Should return a SQLAlchemy predicate
        predicate = sql_luxury_predicate()
        assert predicate is not None


class TestLuxuryEffectivePrice:
    """Metrics must use the same effective price rule as qualification."""

    def test_effective_price_matches_closed_and_open_rules(self):
        from app.luxury import effective_luxury_price

        assert effective_luxury_price('Closed', None, 900_000) == 0
        assert effective_luxury_price('Pending', 700_000, 900_000) == 700_000
        assert effective_luxury_price('Pending', None, 900_000) == 900_000
        assert effective_luxury_price('Active', 0, 800_000) == 800_000

    def test_sql_effective_price_expression_is_constructible(self):
        from app.luxury import sql_luxury_effective_price

        assert sql_luxury_effective_price() is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
