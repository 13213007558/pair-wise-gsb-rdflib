# test for https://github.com/RDFLib/rdflib/issues/554

import rdflib


def test_sparql_empty_no_row():
    g = rdflib.Graph()
    q = "select ?whatever { }"
    r = list(g.query(q))
    # The empty group graph pattern matches once with the empty solution
    # (SPARQL 1.1), so the result is a single row in which ?whatever
    # is unbound.
    assert r == [(None,)], (
        "sparql query %s should return a single unbound row but returns %s"
        % (q, r)
    )


if __name__ == "__main__":
    test_sparql_empty_no_row()
