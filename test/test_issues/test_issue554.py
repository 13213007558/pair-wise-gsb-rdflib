# test for https://github.com/RDFLib/rdflib/issues/554

import rdflib


def test_sparql_empty_no_row():
    g = rdflib.Graph()
    q = "select ?whatever { }"
    r = list(g.query(q))
    # The empty group graph pattern matches with a single (empty) solution,
    # cf. the SPARQL 1.1 semantics of the empty group graph pattern.
    assert r == [(None,)], (
        "sparql query %s should return one empty row but returns %s" % (q, r)
    )


if __name__ == "__main__":
    test_sparql_empty_no_row()
