"""
Tests for the PERCENTILE_CONT / PERCENTILE_DISC aggregate extensions.
"""

import pytest

from rdflib import Graph, Literal
from rdflib.namespace import XSD
from rdflib.plugins.sparql.sparql import SPARQLError


def _query(q):
    return list(Graph().query(q))


def test_percentile_cont_even_length_interpolation():
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v { 10 20 30 40 }
        }
        """
    )
    assert results[0][0] == Literal("25", datatype=XSD.decimal)


def test_percentile_cont_repeated_values():
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v { 10 20 20 40 }
        }
        """
    )
    assert results[0][0] == Literal("20", datatype=XSD.decimal)


def test_percentile_cont_fractional_result():
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0.25, ?v) AS ?q1) {
          VALUES ?v { 10 20 30 40 }
        }
        """
    )
    # 0-based position 0.25 * 3 = 0.75 -> 10 + 0.75 * (20 - 10)
    assert results[0][0] == Literal("17.5", datatype=XSD.decimal)


def test_percentile_cont_high_precision_decimals():
    # the two values are indistinguishable as floats (both 1.0)
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v {
            "1.00000000000000000001"^^xsd:decimal
            "1.00000000000000000002"^^xsd:decimal
          }
        }
        """
    )
    assert results[0][0] == Literal("1.000000000000000000015", datatype=XSD.decimal)


def test_percentile_cont_close_decimals_not_collapsed_to_float():
    # interpolation between very close decimals must stay exact
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v {
            "0.2999999999999999999999"^^xsd:decimal
            "0.3000000000000000000001"^^xsd:decimal
          }
        }
        """
    )
    assert results[0][0] == Literal("0.3", datatype=XSD.decimal)
    assert str(results[0][0]) == "0.3"


def test_percentile_cont_precision_beyond_decimal_context():
    # 38 significant digits: interpolation must not be rounded to the
    # default decimal context precision of 28
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v {
            "1.0000000000000000000000000000000000001"^^xsd:decimal
            "1.0000000000000000000000000000000000003"^^xsd:decimal
          }
        }
        """
    )
    assert results[0][0] == Literal(
        "1.0000000000000000000000000000000000002", datatype=XSD.decimal
    )


def test_percentile_cont_single_element_group():
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0.9, ?v) AS ?p) {
          VALUES ?v { 42 }
        }
        """
    )
    assert results[0][0] == Literal("42", datatype=XSD.decimal)


def test_percentile_cont_p0_and_p1():
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0, ?v) AS ?lo) (PERCENTILE_CONT(1, ?v) AS ?hi) {
          VALUES ?v { 30 10 20 }
        }
        """
    )
    assert results[0][0] == Literal("10", datatype=XSD.decimal)
    assert results[0][1] == Literal("30", datatype=XSD.decimal)


def test_percentile_disc_p0_and_p1():
    results = _query(
        """
        SELECT (PERCENTILE_DISC(0, ?v) AS ?lo) (PERCENTILE_DISC(1, ?v) AS ?hi) {
          VALUES ?v { 30 10 20 }
        }
        """
    )
    assert results[0][0] == Literal(10)
    assert results[0][1] == Literal(30)


def test_percentile_disc_returns_actual_value():
    # 4 values, p = 0.5 -> cumulative proportion first reaches 0.5 at the
    # second value; the result must be an actual reading, not interpolated
    results = _query(
        """
        SELECT (PERCENTILE_DISC(0.5, ?v) AS ?median) {
          VALUES ?v { 10 21 22 40 }
        }
        """
    )
    assert results[0][0] == Literal(21)


def test_percentile_disc_keeps_original_datatype():
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_DISC(0.6, ?v) AS ?p) {
          VALUES ?v {
            "1.00000000000000000001"^^xsd:decimal
            "1.00000000000000000002"^^xsd:decimal
          }
        }
        """
    )
    # ceil(0.6 * 2) = 2 -> second value, with its exact original lexical form
    assert results[0][0] == Literal("1.00000000000000000002", datatype=XSD.decimal)


def test_percentile_disc_high_precision_ordering():
    # as floats both values are 1.0; decimal ordering must pick the max
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_DISC(1, ?v) AS ?p) {
          VALUES ?v {
            "1.00000000000000000002"^^xsd:decimal
            "1.00000000000000000001"^^xsd:decimal
          }
        }
        """
    )
    assert results[0][0] == Literal("1.00000000000000000002", datatype=XSD.decimal)


def test_percentile_group_by():
    results = _query(
        """
        SELECT ?g (PERCENTILE_CONT(0.5, ?v) AS ?median)
                 (PERCENTILE_DISC(0.5, ?v) AS ?disc) {
          VALUES (?g ?v) {
            ("a" 10) ("a" 20) ("a" 20) ("a" 40)
            ("b" 5)
          }
        } GROUP BY ?g ORDER BY ?g
        """
    )
    assert results[0][1] == Literal("20", datatype=XSD.decimal)
    assert results[0][2] == Literal(20)
    assert results[1][1] == Literal("5", datatype=XSD.decimal)
    assert results[1][2] == Literal(5)


def test_percentile_empty_group_is_unbound():
    results = _query(
        """
        SELECT ?g (PERCENTILE_CONT(0.5, ?v) AS ?c) (PERCENTILE_DISC(0.5, ?v) AS ?d) {
          VALUES (?g ?v) { ("a" 1) ("b" UNDEF) ("c" "not a number") }
        } GROUP BY ?g ORDER BY ?g
        """
    )
    assert results[0][1] == Literal("1", datatype=XSD.decimal)
    assert results[0][2] == Literal(1)
    # unbound and non-numeric only groups => unbound results
    assert results[1][1] is None
    assert results[1][2] is None
    assert results[2][1] is None
    assert results[2][2] is None


def test_percentile_skips_unbound_and_non_numeric():
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v { 10 20 "hello" UNDEF }
        }
        """
    )
    assert results[0][0] == Literal("15", datatype=XSD.decimal)


def test_percentile_skips_nan():
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?c) (PERCENTILE_DISC(1, ?v) AS ?d) {
          VALUES ?v { 10 30 "NaN"^^xsd:float }
        }
        """
    )
    # the NaN value is excluded entirely, so the result stays decimal
    assert results[0][0] == Literal("20", datatype=XSD.decimal)
    assert results[0][1] == Literal(30)


def test_percentile_cont_float_promotion():
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v { "1.5"^^xsd:float 3 }
        }
        """
    )
    assert results[0][0] == Literal("2.25", datatype=XSD.float)


def test_percentile_cont_double_promotion():
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v { "1"^^xsd:double "3"^^xsd:double }
        }
        """
    )
    assert results[0][0] == Literal("2", datatype=XSD.double)


def test_percentile_cont_mixed_int_decimal():
    results = _query(
        """
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES ?v { 1 "2.5"^^xsd:decimal }
        }
        """
    )
    assert results[0][0] == Literal("1.75", datatype=XSD.decimal)


def test_percentile_distinct_before_value_expr():
    # distinct values are {1, 100}: position 0.9 * 1 = 0.9 -> 1 + 0.9 * 99
    results = _query(
        """
        SELECT (PERCENTILE_CONT(0.9, DISTINCT ?v) AS ?p) {
          VALUES ?v { 1 1 1 100 }
        }
        """
    )
    assert results[0][0] == Literal("90.1", datatype=XSD.decimal)


def test_percentile_distinct_before_p():
    results = _query(
        """
        SELECT (PERCENTILE_DISC(DISTINCT 0.5, ?v) AS ?p) {
          VALUES ?v { 1 1 1 100 }
        }
        """
    )
    # distinct values {1, 100}: ceil(0.5 * 2) = 1 -> first value
    assert results[0][0] == Literal(1)


def test_percentile_distinct_disc():
    results = _query(
        """
        SELECT (PERCENTILE_DISC(0.9, DISTINCT ?v) AS ?p) {
          VALUES ?v { 1 1 1 100 }
        }
        """
    )
    # distinct values {1, 100}: ceil(0.9 * 2) = 2 -> second value
    assert results[0][0] == Literal(100)


def test_percentile_in_having():
    results = _query(
        """
        SELECT ?g (PERCENTILE_CONT(0.5, ?v) AS ?median) {
          VALUES (?g ?v) { ("a" 10) ("a" 20) ("b" 1) ("b" 2) }
        } GROUP BY ?g
        HAVING (PERCENTILE_CONT(0.5, ?v) > 10)
        ORDER BY ?g
        """
    )
    assert len(results) == 1
    assert results[0][0] == Literal("a")
    assert results[0][1] == Literal("15", datatype=XSD.decimal)


def test_percentile_p_as_variable():
    results = _query(
        """
        SELECT ?g (PERCENTILE_CONT(?p, ?v) AS ?median) {
          VALUES (?g ?p ?v) {
            ("a" 0.5 10) ("a" 0.5 20)
            ("b" 0.25 10) ("b" 0.25 20) ("b" 0.25 30) ("b" 0.25 40)
          }
        } GROUP BY ?g ORDER BY ?g
        """
    )
    assert results[0][1] == Literal("15", datatype=XSD.decimal)
    assert results[1][1] == Literal("17.5", datatype=XSD.decimal)


def test_percentile_p_out_of_range():
    with pytest.raises(SPARQLError):
        _query(
            """
            SELECT (PERCENTILE_CONT(1.5, ?v) AS ?p) { VALUES ?v { 1 2 } }
            """
        )
    with pytest.raises(SPARQLError):
        _query(
            """
            SELECT (PERCENTILE_DISC(-0.1, ?v) AS ?p) { VALUES ?v { 1 2 } }
            """
        )


def test_percentile_p_not_numeric():
    with pytest.raises(SPARQLError):
        _query(
            """
            SELECT (PERCENTILE_CONT("high", ?v) AS ?p) { VALUES ?v { 1 2 } }
            """
        )


def test_percentile_p_must_be_group_invariant():
    with pytest.raises(SPARQLError):
        _query(
            """
            SELECT ?g (PERCENTILE_CONT(?p, ?v) AS ?median) {
              VALUES (?g ?p ?v) { ("a" 0.5 10) ("a" 0.6 20) }
            } GROUP BY ?g
            """
        )
