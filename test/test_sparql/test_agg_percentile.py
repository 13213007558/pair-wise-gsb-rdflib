from decimal import Decimal

import pytest

from rdflib import Graph, Literal, URIRef, Variable
from rdflib.namespace import XSD
from rdflib.plugins.sparql.aggregates import enable_percentile_aggregates
from rdflib.plugins.sparql.sparql import SPARQLError


def query(query_string):
    return list(Graph().query(query_string))


def test_percentile_cont_even_length_interpolates():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1 2 3 4 }
        }
        """
    )
    assert results[0][0] == Literal(Decimal("2.5"), datatype=XSD.decimal)


def test_percentile_cont_odd_length():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1 2 3 }
        }
        """
    )
    assert results[0][0] == Literal(Decimal("2"), datatype=XSD.decimal)


def test_percentile_cont_high_precision_decimal_not_corrupted():
    # the midpoint is not representable as a float; it must stay exact
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1.000000000000000000001 1.000000000000000000002 }
        }
        """
    )
    assert results[0][0] == Literal(
        Decimal("1.0000000000000000000015"), datatype=XSD.decimal
    )


def test_percentile_cont_close_decimals_sorted_exactly():
    # these collapse to the same float; decimal sorting must keep them apart
    results = query(
        """
        SELECT (PERCENTILE_CONT(1, ?v) AS ?mx) (PERCENTILE_CONT(0, ?v) AS ?mn) {
          VALUES ?v { 1.000000000000000000002 1.000000000000000000001 }
        }
        """
    )
    assert results[0][0] == Literal(
        Decimal("1.000000000000000000002"), datatype=XSD.decimal
    )
    assert results[0][1] == Literal(
        Decimal("1.000000000000000000001"), datatype=XSD.decimal
    )


def test_percentile_cont_p0_and_p1():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0, ?v) AS ?lo) (PERCENTILE_CONT(1, ?v) AS ?hi) {
          VALUES ?v { 3 1 2 }
        }
        """
    )
    assert results[0][0] == Literal(1, datatype=XSD.integer)
    assert results[0][1] == Literal(3, datatype=XSD.integer)


def test_percentile_disc_p0_and_p1():
    results = query(
        """
        SELECT (PERCENTILE_DISC(0, ?v) AS ?lo) (PERCENTILE_DISC(1, ?v) AS ?hi) {
          VALUES ?v { 3 1 2 }
        }
        """
    )
    assert results[0][0] == Literal(1)
    assert results[0][1] == Literal(3)


def test_percentile_disc_returns_actual_reading():
    # 2.2 is not an input value; the discrete result must be one of the inputs
    results = query(
        """
        SELECT (PERCENTILE_DISC(0.5, ?v) AS ?d) {
          VALUES ?v { 1.1 2.2 3.3 4.4 }
        }
        """
    )
    assert results[0][0] == Literal(Decimal("2.2"))
    assert results[0][0].datatype == XSD.decimal


def test_percentile_disc_first_cumulative_reach():
    # cumulative proportions of [1, 2, 3, 4] are .25 .5 .75 1
    results = query(
        """
        SELECT (PERCENTILE_DISC(0.5, ?v) AS ?a) (PERCENTILE_DISC(0.51, ?v) AS ?b)
               (PERCENTILE_DISC(0.25, ?v) AS ?c) {
          VALUES ?v { 4 2 1 3 }
        }
        """
    )
    assert results[0][0] == Literal(2)
    assert results[0][1] == Literal(3)
    assert results[0][2] == Literal(1)


def test_percentile_single_element_group():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.3, ?v) AS ?c) (PERCENTILE_DISC(0.3, ?v) AS ?d) {
          VALUES ?v { 7 }
        }
        """
    )
    assert results[0][0] == Literal(Decimal("7"), datatype=XSD.decimal)
    assert results[0][1] == Literal(7)


def test_percentile_group_by():
    results = query(
        """
        SELECT ?g (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES (?g ?v) {
            ("a" 1) ("a" 3)
            ("b" 10) ("b" 20) ("b" 30)
          }
        } GROUP BY ?g ORDER BY ?g
        """
    )
    assert results[0][1] == Literal(Decimal("2"), datatype=XSD.decimal)
    assert results[1][1] == Literal(Decimal("20"), datatype=XSD.decimal)


def test_percentile_cont_duplicate_values():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1 2 2 2 3 }
        }
        """
    )
    assert results[0][0] == Literal(Decimal("2"), datatype=XSD.decimal)


def test_percentile_disc_duplicate_values():
    results = query(
        """
        SELECT (PERCENTILE_DISC(0.75, ?v) AS ?d) {
          VALUES ?v { 1 1 1 2 }
        }
        """
    )
    assert results[0][0] == Literal(1)


def test_percentile_distinct_precedes_computation():
    results = query(
        """
        SELECT (PERCENTILE_DISC(DISTINCT 0.75, ?v) AS ?d)
               (PERCENTILE_CONT(DISTINCT 0.5, ?v) AS ?c) {
          VALUES ?v { 1 1 1 2 }
        }
        """
    )
    # distinct values are [1, 2]
    assert results[0][0] == Literal(2)
    assert results[0][1] == Literal(Decimal("1.5"), datatype=XSD.decimal)


def test_percentile_cont_float_promotion():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1 2.5 "3.5"^^xsd:double }
        }
        """
    )
    assert results[0][0] == Literal(2.5, datatype=XSD.double)


def test_percentile_cont_integer_and_decimal_stay_exact():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1 1.00000000000000000000000001 }
        }
        """
    )
    assert results[0][0] == Literal(
        Decimal("1.000000000000000000000000005"), datatype=XSD.decimal
    )


def test_percentile_skips_non_numeric_and_unbound():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES ?v { 1 "abc" 3 UNDEF }
        }
        """
    )
    assert results[0][0] == Literal(Decimal("2"), datatype=XSD.decimal)


def test_percentile_ignores_nan():
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?c) (PERCENTILE_DISC(0.5, ?v) AS ?d) {
          VALUES ?v { 1 "NaN"^^xsd:double 3 }
        }
        """
    )
    # the NaN reading is ignored completely, also for type promotion
    assert results[0][0] == Literal(Decimal("2"), datatype=XSD.decimal)
    assert results[0][1] == Literal(1)


def test_percentile_empty_group_is_unbound():
    # an empty group leaves the aggregate unbound, which - like MIN/MAX -
    # drops the solution from the result
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?c) (PERCENTILE_DISC(0.5, ?v) AS ?d) {
          VALUES ?v { UNDEF }
        }
        """
    )
    assert results == []


def test_percentile_p_out_of_range_is_unbound():
    for p in ("-0.1", "1.5", "\"NaN\"^^xsd:double"):
        results = query(
            """
            SELECT (PERCENTILE_CONT(%s, ?v) AS ?c) (PERCENTILE_DISC(%s, ?v) AS ?d) {
              VALUES ?v { 1 2 3 }
            }
            """
            % (p, p)
        )
        assert results == []


def test_percentile_p_must_be_group_invariant():
    # a group-varying p is an error and leaves the aggregate unbound
    results = query(
        """
        SELECT ?g (PERCENTILE_CONT(?p, ?v) AS ?c) {
          VALUES (?g ?p ?v) { ("a" 0.5 1) ("a" 0.9 2) }
        } GROUP BY ?g
        """
    )
    assert results[0][1] is None


def test_percentile_p_as_grouped_variable():
    results = query(
        """
        SELECT ?g (PERCENTILE_CONT(?p, ?v) AS ?c) {
          VALUES (?g ?p ?v) {
            ("a" 0.5 1) ("a" 0.5 3)
            ("b" 1 10) ("b" 1 20)
          }
        } GROUP BY ?g ?p ORDER BY ?g
        """
    )
    assert results[0][1] == Literal(Decimal("2"), datatype=XSD.decimal)
    assert results[1][1] == Literal(20, datatype=XSD.integer)


def test_percentile_in_having():
    results = query(
        """
        SELECT ?g (PERCENTILE_CONT(0.5, ?v) AS ?med) {
          VALUES (?g ?v) {
            ("a" 1) ("a" 3)
            ("b" 10) ("b" 30)
          }
        } GROUP BY ?g
        HAVING (PERCENTILE_CONT(0.5, ?v) > 5)
        """
    )
    assert len(results) == 1
    assert results[0][0] == Literal("b")


def test_percentile_uses_readings_from_rdf_graph():
    g = Graph()
    g.parse(
        format="turtle",
        data="""
        @prefix : <http://example.org/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :s1 :inGroup :g1 ; :reading 1.000000000000000000001 .
        :s2 :inGroup :g1 ; :reading 1.000000000000000000003 .
        :s3 :inGroup :g2 ; :reading 5 .
        """,
    )
    results = list(
        g.query(
            """
            SELECT ?g (PERCENTILE_CONT(0.5, ?r) AS ?med) (PERCENTILE_DISC(0.5, ?r) AS ?d)
            WHERE { ?s :inGroup ?g ; :reading ?r . }
            GROUP BY ?g ORDER BY ?g
            """
        )
    )
    assert results[0][1] == Literal(
        Decimal("1.000000000000000000002"), datatype=XSD.decimal
    )
    # the discrete result is an actual reading from the graph
    assert results[0][2] == Literal(Decimal("1.000000000000000000001"))
    assert results[1][1] == Literal(Decimal("5"), datatype=XSD.decimal)
    assert results[1][2] == Literal(5)


def test_percentile_aggregates_can_be_disabled():
    enable_percentile_aggregates(False)
    try:
        with pytest.raises(SPARQLError):
            query(
                """
                SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) { VALUES ?v { 1 2 } }
                """
            )
    finally:
        enable_percentile_aggregates(True)
    results = query(
        """
        SELECT (PERCENTILE_CONT(0.5, ?v) AS ?med) { VALUES ?v { 1 2 } }
        """
    )
    assert results[0][0] == Literal(Decimal("1.5"), datatype=XSD.decimal)
