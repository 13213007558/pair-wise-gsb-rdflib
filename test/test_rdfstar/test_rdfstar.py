"""
Tests for RDF-star quoted triples: the QuotedTriple term, Turtle-star and
N-Triples-star parsing/serialization, and SPARQL-star querying and updates.\n"""

import pickle

import pytest

from rdflib import BNode, Dataset, Graph, Literal, QuotedTriple, URIRef, Variable
from rdflib.compare import isomorphic
from rdflib.exceptions import ParserError
from rdflib.namespace import XSD

EX = "http://example.org/"


def ex(name: str) -> URIRef:
    return URIRef(EX + name)


@pytest.fixture
def price() -> QuotedTriple:
    return QuotedTriple(
        (ex("widget"), ex("price"), Literal("42", datatype=XSD.integer))
    )


@pytest.fixture
def quoted_graph(price: QuotedTriple) -> Graph:
    """An in-memory graph using different blank node names and nested
    quoted triples."""
    graph = Graph()
    graph.add((price, ex("source"), ex("systemA")))
    graph.add((price, ex("recordedAt"), Literal("2026-09-24", datatype=XSD.date)))
    nested = QuotedTriple((price, ex("assertedBy"), ex("alice")))
    graph.add((nested, ex("source"), ex("systemB")))
    graph.add((ex("claim1"), ex("restates"), price))
    graph.add((BNode("b1"), ex("label"), Literal("first blank node")))
    graph.add((BNode("b2"), ex("references"), BNode("b1")))
    graph.add((ex("widget"), ex("name"), Literal('Widget "quoted"', lang="en")))
    return graph


class TestQuotedTripleTerm:
    def test_equality_and_hash(self):
        t1 = QuotedTriple((ex("s"), ex("p"), ex("o")))
        t2 = QuotedTriple((ex("s"), ex("p"), ex("o")))
        t3 = QuotedTriple((ex("s"), ex("p"), ex("other")))
        assert t1 == t2
        assert hash(t1) == hash(t2)
        assert t1 != t3
        assert t1 != (ex("s"), ex("p"), ex("o"))
        assert t1 != ex("s")
        assert not t1 == None  # noqa: E711
        # usable as a dict key, like any other term
        assert {t1: 1}[t2] == 1

    def test_constructor_forms(self):
        assert QuotedTriple(ex("s"), ex("p"), ex("o")) == QuotedTriple(
            (ex("s"), ex("p"), ex("o"))
        )

    def test_nesting(self):
        inner = QuotedTriple((ex("s"), ex("p"), ex("o")))
        middle = QuotedTriple((inner, ex("q"), Literal("1", datatype=XSD.integer)))
        outer = QuotedTriple((ex("a"), ex("b"), middle))
        assert outer.object.subject.subject == ex("s")
        assert outer.triple == (ex("a"), ex("b"), middle)

    def test_rdf_rules(self):
        # literal subject is not allowed
        with pytest.raises(ValueError):
            QuotedTriple((Literal("x"), ex("p"), ex("o")))
        # literal predicate is not allowed
        with pytest.raises(ValueError):
            QuotedTriple((ex("s"), Literal("p"), ex("o")))
        # blank node predicate is not allowed
        with pytest.raises(ValueError):
            QuotedTriple((ex("s"), BNode("b"), ex("o")))
        # non-term object is not allowed
        with pytest.raises(ValueError):
            QuotedTriple((ex("s"), ex("p"), "plain string"))
        with pytest.raises(TypeError):
            QuotedTriple(ex("s"), ex("p"))

    def test_variables_allowed_for_patterns(self):
        pattern = QuotedTriple((Variable("s"), Variable("p"), Variable("o")))
        assert pattern.subject == Variable("s")

    def test_n3(self):
        qt = QuotedTriple((ex("s"), ex("p"), Literal("lit", lang="en")))
        assert qt.n3() == '<< <http://example.org/s> <http://example.org/p> "lit"@en >>'
        nested = QuotedTriple((qt, ex("q"), ex("r")))
        assert nested.n3() == (
            '<< << <http://example.org/s> <http://example.org/p> "lit"@en >> '
            "<http://example.org/q> <http://example.org/r> >>"
        )

    def test_pickle_roundtrip(self):
        qt = QuotedTriple(
            (QuotedTriple((ex("s"), ex("p"), ex("o"))), ex("q"), Literal("l"))
        )
        assert pickle.loads(pickle.dumps(qt)) == qt

    def test_ordering(self):
        # ordering consistent with the other term classes
        qt = QuotedTriple((ex("s"), ex("p"), ex("o")))
        assert BNode("b") < qt
        assert qt < Literal("l")
        assert ex("a") < qt
        assert QuotedTriple((ex("a"), ex("p"), ex("o"))) < qt
        assert qt > BNode("b")
        terms = [qt, BNode("b"), ex("a"), Literal("l")]
        assert sorted(terms) == [BNode("b"), ex("a"), qt, Literal("l")]


class TestGraphContract:
    def test_add_and_lookup(self, quoted_graph: Graph, price: QuotedTriple):
        assert (price, ex("source"), ex("systemA")) in quoted_graph
        assert list(quoted_graph.objects(price, ex("source"))) == [ex("systemA")]
        assert list(quoted_graph.subjects(ex("restates"), price)) == [ex("claim1")]

    def test_quoted_not_asserted(self, quoted_graph: Graph):
        # the quoted triple itself is not asserted in the graph
        assert (
            ex("widget"),
            ex("price"),
            Literal("42", datatype=XSD.integer),
        ) not in quoted_graph

    def test_dataset(self, price: QuotedTriple):
        ds = Dataset()
        ds.add((price, ex("source"), ex("sys"), ex("graph1")))
        assert (price, ex("source"), ex("sys"), ex("graph1")) in ds
        quads = list(ds.quads((price, None, None, None)))
        assert len(quads) == 1
        assert quads[0][0] == price


class TestTurtleStar:
    def test_roundtrip(self, quoted_graph: Graph):
        data = quoted_graph.serialize(format="turtle")
        assert "<<" in data and ">>" in data
        graph = Graph()
        graph.parse(data=data, format="turtle")
        assert isomorphic(quoted_graph, graph)

    def test_parse_does_not_assert_quoted_triple(self):
        graph = Graph()
        graph.parse(
            data="@prefix ex: <http://example.org/> .\n"
            "<< ex:s ex:p ex:o >> ex:source ex:sys .",
            format="turtle",
        )
        assert len(graph) == 1
        assert (ex("s"), ex("p"), ex("o")) not in graph
        (triple,) = list(graph)
        assert triple[0] == QuotedTriple((ex("s"), ex("p"), ex("o")))

    def test_parse_nesting_escapes_datatype_lang(self):
        graph = Graph()
        graph.parse(
            data="@prefix ex: <http://example.org/> .\n"
            '<< << ex:a ex:b "lit\\nline"@en >> ex:q '
            '"3"^^<http://www.w3.org/2001/XMLSchema#integer> >>\n'
            "    ex:r << ex:d ex:e [] >> .",
            format="turtle",
        )
        (triple,) = list(graph)
        subject = triple[0]
        assert isinstance(subject, QuotedTriple)
        assert isinstance(subject.subject, QuotedTriple)
        assert subject.subject.object == Literal("lit\nline", lang="en")
        assert subject.object == Literal("3", datatype=XSD.integer)
        obj = triple[2]
        assert isinstance(obj, QuotedTriple)
        assert isinstance(obj.object, BNode)

    def test_parse_bnode_names_are_scoped_to_document(self):
        # the same blank node label inside and outside a quoted triple
        # denotes the same blank node within one document
        graph = Graph()
        graph.parse(
            data="@prefix ex: <http://example.org/> .\n"
            "<< ex:s ex:p _:b1 >> ex:source ex:sys .\n"
            "_:b1 ex:label \"seen\" .",
            format="turtle",
        )
        quoted = next(iter(graph.subjects(ex("source"), ex("sys"))))
        assert isinstance(quoted, QuotedTriple)
        assert isinstance(quoted.object, BNode)
        assert (quoted.object, ex("label"), Literal("seen")) in graph

    def test_rdfstar_disabled_raises(self):
        with pytest.raises(Exception):
            Graph().parse(
                data="@prefix ex: <http://example.org/> .\n"
                "<< ex:s ex:p ex:o >> ex:source ex:sys .",
                format="turtle",
                rdfstar=False,
            )


class TestNTriplesStar:
    def test_roundtrip(self, quoted_graph: Graph):
        data = quoted_graph.serialize(format="nt")
        assert "<<" in data and ">>" in data
        graph = Graph()
        graph.parse(data=data, format="nt")
        assert isomorphic(quoted_graph, graph)

    def test_parse(self):
        graph = Graph()
        graph.parse(
            data="<< <http://example.org/s> <http://example.org/p> "
            '<< <http://example.org/a> <http://example.org/b> "lit"@en >> >> '
            "<http://example.org/source> <http://example.org/sys> .\n",
            format="nt",
        )
        (triple,) = list(graph)
        assert triple[0] == QuotedTriple(
            (
                ex("s"),
                ex("p"),
                QuotedTriple((ex("a"), ex("b"), Literal("lit", lang="en"))),
            )
        )
        assert triple[1:] == (ex("source"), ex("sys"))

    def test_literal_escaping_roundtrip(self):
        graph = Graph()
        qt = QuotedTriple(
            (ex("s"), ex("p"), Literal("new\nline\ttab \"quote\"", lang="en"))
        )
        graph.add((qt, ex("source"), ex("sys")))
        data = graph.serialize(format="nt")
        reparsed = Graph()
        reparsed.parse(data=data, format="nt")
        assert set(reparsed) == set(graph)

    def test_rdfstar_disabled_raises(self):
        with pytest.raises(ParserError):
            Graph().parse(
                data="<< <http://example.org/s> <http://example.org/p> "
                "<http://example.org/o> >> <http://example.org/source> "
                "<http://example.org/sys> .\n",
                format="nt",
                rdfstar=False,
            )


class TestSparqlStar:
    def test_bind_quoted_triple(self, quoted_graph: Graph, price: QuotedTriple):
        rows = list(
            quoted_graph.query(
                "SELECT ?sys WHERE { << ?s ?p ?o >> <http://example.org/source> ?sys }"
            )
        )
        systems = {row[0] for row in rows}
        assert systems == {ex("systemA"), ex("systemB")}

    def test_quoted_pattern_does_not_match_asserted_triple(
        self, quoted_graph: Graph
    ):
        # the same-shaped asserted triple must not match a quoted pattern
        quoted_graph.add((ex("widget"), ex("price"), Literal("42", datatype=XSD.integer)))
        rows = list(
            quoted_graph.query(
                "SELECT ?sys WHERE {"
                " << <http://example.org/widget> <http://example.org/price>"
                ' "42"^^<http://www.w3.org/2001/XMLSchema#integer> >>'
                " <http://example.org/source> ?sys }"
            )
        )
        assert [row[0] for row in rows] == [ex("systemA")]
        # and a plain pattern does not match the quoted triple term
        rows = list(
            quoted_graph.query(
                "SELECT ?o WHERE {"
                " <http://example.org/widget> <http://example.org/price> ?o }"
            )
        )
        assert rows == [(Literal("42", datatype=XSD.integer),)]

    def test_nested_quoted_pattern(self, quoted_graph: Graph, price: QuotedTriple):
        rows = list(
            quoted_graph.query(
                "SELECT ?who ?sys WHERE {"
                " << << ?s ?p ?o >> <http://example.org/assertedBy> ?who >>"
                " <http://example.org/source> ?sys }"
            )
        )
        assert rows == [(ex("alice"), ex("systemB"))]

    def test_quoted_triple_as_object(self, quoted_graph: Graph, price: QuotedTriple):
        rows = list(
            quoted_graph.query(
                "SELECT ?claim WHERE {"
                " ?claim <http://example.org/restates>"
                " << <http://example.org/widget> <http://example.org/price>"
                ' "42"^^<http://www.w3.org/2001/XMLSchema#integer> >> }'
            )
        )
        assert rows == [(ex("claim1"),)]

    def test_insert_data(self, price: QuotedTriple):
        graph = Graph()
        graph.update(
            "INSERT DATA {"
            " << <http://example.org/widget> <http://example.org/price>"
            ' "42"^^<http://www.w3.org/2001/XMLSchema#integer> >>'
            " <http://example.org/source> <http://example.org/systemC> }"
        )
        assert (price, ex("source"), ex("systemC")) in graph
        assert len(graph) == 1

    def test_delete_data(self, quoted_graph: Graph, price: QuotedTriple):
        quoted_graph.update(
            "DELETE DATA {"
            " << <http://example.org/widget> <http://example.org/price>"
            ' "42"^^<http://www.w3.org/2001/XMLSchema#integer> >>'
            " <http://example.org/source> <http://example.org/systemA> }"
        )
        assert (price, ex("source"), ex("systemA")) not in quoted_graph

    def test_insert_where(self, quoted_graph: Graph):
        quoted_graph.update(
            "INSERT { << ?s ?p ?o >> <http://example.org/audited> true }"
            "WHERE { << ?s ?p ?o >> <http://example.org/source>"
            " <http://example.org/systemA> }"
        )
        assert (
            QuotedTriple((ex("widget"), ex("price"), Literal("42", datatype=XSD.integer))),
            ex("audited"),
            Literal(True),
        ) in quoted_graph

    def test_construct(self, quoted_graph: Graph, price: QuotedTriple):
        result = quoted_graph.query(
            "CONSTRUCT { << ?s ?p ?o >> <http://example.org/copied> ?sys }"
            "WHERE { << ?s ?p ?o >> <http://example.org/source> ?sys }"
        )
        assert (price, ex("copied"), ex("systemA")) in result


class TestSparqlStarResults:
    def test_json_results_roundtrip(self, quoted_graph, price):
        import io

        from rdflib.query import Result

        rows = quoted_graph.query(
            "SELECT ?t WHERE { ?t <http://example.org/source>"
            " <http://example.org/systemA> }"
        )
        data = rows.serialize(format="json")
        parsed = Result.parse(io.BytesIO(data), format="json")
        assert list(parsed)[0][0] == price

    def test_xml_results_roundtrip(self, quoted_graph, price):
        import io

        from rdflib.query import Result

        rows = quoted_graph.query(
            "SELECT ?t WHERE { ?t <http://example.org/source>"
            " <http://example.org/systemA> }"
        )
        data = rows.serialize(format="xml")
        parsed = Result.parse(io.BytesIO(data), format="xml")
        assert list(parsed)[0][0] == price
