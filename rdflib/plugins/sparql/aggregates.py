"""
Aggregation functions
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from decimal import Decimal, getcontext, localcontext
from typing import (
    Any,
    Optional,
    TypeVar,
    Union,
    overload,
)

from rdflib.namespace import XSD
from rdflib.plugins.sparql.datatypes import type_promotion
from rdflib.plugins.sparql.evalutils import _eval, _val
from rdflib.plugins.sparql.operators import numeric
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import (
    FrozenBindings,
    NotBoundError,
    SPARQLError,
    SPARQLTypeError,
)
from rdflib.term import BNode, Identifier, Literal, URIRef, Variable


class Accumulator:
    """abstract base class for different aggregation functions"""

    def __init__(self, aggregation: CompValue):
        self.get_value: Callable[[], Optional[Literal]]
        self.update: Callable[[FrozenBindings, Aggregator], None]
        self.var = aggregation.res
        self.expr = aggregation.vars
        if not aggregation.distinct:
            # type error: Cannot assign to a method
            self.use_row = self.dont_care  # type: ignore[method-assign]
            self.distinct = False
        else:
            self.distinct = aggregation.distinct
            self.seen: set[Any] = set()

    def dont_care(self, row: FrozenBindings) -> bool:
        """skips distinct test"""
        return True

    def use_row(self, row: FrozenBindings) -> bool:
        """tests distinct with set"""
        return _eval(self.expr, row) not in self.seen

    def set_value(self, bindings: MutableMapping[Variable, Identifier]) -> None:
        """sets final value in bindings"""
        # type error: Incompatible types in assignment (expression has type "Optional[Literal]", target has type "Identifier")
        bindings[self.var] = self.get_value()  # type: ignore[assignment]


class Counter(Accumulator):
    def __init__(self, aggregation: CompValue):
        super(Counter, self).__init__(aggregation)
        self.value = 0
        if self.expr == "*":
            # cannot eval "*" => always use the full row
            # type error: Cannot assign to a method
            self.eval_row = self.eval_full_row  # type: ignore[assignment]

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            val = self.eval_row(row)
        except NotBoundError:
            # skip UNDEF
            return
        self.value += 1
        if self.distinct:
            self.seen.add(val)

    def get_value(self) -> Literal:
        return Literal(self.value)

    def eval_row(self, row: FrozenBindings) -> Identifier:
        return _eval(self.expr, row)

    def eval_full_row(self, row: FrozenBindings) -> FrozenBindings:
        return row

    def use_row(self, row: FrozenBindings) -> bool:
        try:
            return self.eval_row(row) not in self.seen
        except NotBoundError:
            # happens when counting zero optional nodes. See issue #2229
            return False


@overload
def type_safe_numbers(*args: int) -> tuple[int]: ...


@overload
def type_safe_numbers(
    *args: Union[Decimal, float, int]
) -> tuple[Union[float, int]]: ...


def type_safe_numbers(*args: Union[Decimal, float, int]) -> Iterable[Union[float, int]]:
    if any(isinstance(arg, float) for arg in args) and any(
        isinstance(arg, Decimal) for arg in args
    ):
        return map(float, args)
    # type error: Incompatible return value type (got "tuple[Union[Decimal, float, int], ...]", expected "Iterable[Union[float, int]]")
    # NOTE on type error: if args contains a Decimal it will nopt get here.
    return args  # type: ignore[return-value]


class Sum(Accumulator):
    def __init__(self, aggregation: CompValue):
        super(Sum, self).__init__(aggregation)
        self.value = 0
        self.datatype: Optional[str] = None

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            value = _eval(self.expr, row)
            dt = self.datatype
            if dt is None:
                dt = value.datatype
            else:
                # type error: Argument 1 to "type_promotion" has incompatible type "str"; expected "URIRef"
                dt = type_promotion(dt, value.datatype)  # type: ignore[arg-type]
            self.datatype = dt
            self.value = sum(type_safe_numbers(self.value, numeric(value)))
            if self.distinct:
                self.seen.add(value)
        except NotBoundError:
            # skip UNDEF
            pass

    def get_value(self) -> Literal:
        return Literal(self.value, datatype=self.datatype)


class Average(Accumulator):
    def __init__(self, aggregation: CompValue):
        super(Average, self).__init__(aggregation)
        self.counter = 0
        self.sum = 0
        self.datatype: Optional[str] = None

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            value = _eval(self.expr, row)
            dt = self.datatype
            self.sum = sum(type_safe_numbers(self.sum, numeric(value)))
            if dt is None:
                dt = value.datatype
            else:
                # type error: Argument 1 to "type_promotion" has incompatible type "str"; expected "URIRef"
                dt = type_promotion(dt, value.datatype)  # type: ignore[arg-type]
            self.datatype = dt
            if self.distinct:
                self.seen.add(value)
            self.counter += 1
        # skip UNDEF or BNode => SPARQLTypeError
        except NotBoundError:
            pass
        except SPARQLTypeError:
            pass

    def get_value(self) -> Literal:
        if self.counter == 0:
            return Literal(0)
        if self.datatype in (XSD.float, XSD.double):
            return Literal(self.sum / self.counter)
        else:
            return Literal(Decimal(self.sum) / Decimal(self.counter))


class Extremum(Accumulator):
    """abstract base class for Minimum and Maximum"""

    def __init__(self, aggregation: CompValue):
        self.compare: Callable[[Any, Any], Any]
        super(Extremum, self).__init__(aggregation)
        self.value: Any = None
        # DISTINCT would not change the value for MIN or MAX
        # type error: Cannot assign to a method
        self.use_row = self.dont_care  # type: ignore[method-assign]

    def set_value(self, bindings: MutableMapping[Variable, Identifier]) -> None:
        if self.value is not None:
            # simply do not set if self.value is still None
            bindings[self.var] = Literal(self.value)

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            if self.value is None:
                self.value = _eval(self.expr, row)
            else:
                # self.compare is implemented by Minimum/Maximum
                self.value = self.compare(self.value, _eval(self.expr, row))
        # skip UNDEF or BNode => SPARQLTypeError
        except NotBoundError:
            pass
        except SPARQLTypeError:
            pass


_ValueT = TypeVar("_ValueT", Variable, BNode, URIRef, Literal)


class Minimum(Extremum):
    def compare(self, val1: _ValueT, val2: _ValueT) -> _ValueT:
        return min(val1, val2, key=_val)


class Maximum(Extremum):
    def compare(self, val1: _ValueT, val2: _ValueT) -> _ValueT:
        return max(val1, val2, key=_val)


class Sample(Accumulator):
    """takes the first eligible value"""

    def __init__(self, aggregation):
        super(Sample, self).__init__(aggregation)
        # DISTINCT would not change the value
        # type error: Cannot assign to a method
        self.use_row = self.dont_care  # type: ignore[method-assign]

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            # set the value now
            aggregator.bindings[self.var] = _eval(self.expr, row)
            # and skip this accumulator for future rows
            del aggregator.accumulators[self.var]
        except NotBoundError:
            pass

    def get_value(self) -> None:
        # set None if no value was set
        return None


class GroupConcat(Accumulator):
    value: list[Literal]

    def __init__(self, aggregation: CompValue):
        super(GroupConcat, self).__init__(aggregation)
        # only GROUPCONCAT needs to have a list as accumulator
        self.value = []
        if aggregation.separator is None:
            self.separator = " "
        else:
            self.separator = aggregation.separator

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            value = _eval(self.expr, row)
            # skip UNDEF
            if isinstance(value, NotBoundError):
                return
            self.value.append(value)
            if self.distinct:
                self.seen.add(value)
        # skip UNDEF
        # NOTE: It seems like this is not the way undefined values occur, they
        # come through not as exceptions but as values. This is left here
        # however as it may occur in some cases.
        # TODO: Consider removing this.
        except NotBoundError:
            pass

    def get_value(self) -> Literal:
        return Literal(self.separator.join(str(v) for v in self.value))


def _percentile_context(
    nums: Iterable[Union[int, Decimal, float]], p: Union[int, Decimal, float]
):
    """
    A decimal context with enough precision to interpolate between the
    given numbers without rounding high-precision decimals.
    """
    prec = 28
    for n in list(nums) + [p]:
        if isinstance(n, Decimal):
            t = n.as_tuple()
            if isinstance(t.exponent, int):
                prec = max(prec, len(t.digits) + abs(t.exponent) + 10)
    ctx = getcontext().copy()
    ctx.prec = prec
    return localcontext(ctx)


def _strip_decimal(d: Decimal) -> Decimal:
    """remove insignificant trailing zeros from a Decimal"""
    if d == d.to_integral_value():
        return d.quantize(Decimal(1))
    return d.normalize()


class Percentile(Accumulator):
    """
    Abstract base class for the PERCENTILE_CONT and PERCENTILE_DISC
    aggregate extensions.

    Syntax: PERCENTILE_CONT(p, expr) / PERCENTILE_DISC(p, expr)

    p must evaluate to a number in [0, 1] that is invariant within each
    group, otherwise a SPARQLError is raised. Rows where expr is unbound
    or non-numeric are ignored, as are NaN values. DISTINCT (allowed
    before p or before expr) is applied to the collected values before
    the percentile is computed. An empty group leaves the result
    variable unbound.
    """

    def __init__(self, aggregation: CompValue):
        # DISTINCT may be written before p or before the value expression
        if aggregation.distinct_value:
            aggregation["distinct"] = aggregation.distinct_value
        super(Percentile, self).__init__(aggregation)
        self.p_expr = aggregation.p
        self.p: Optional[Union[int, Decimal, float]] = None
        self.values: list[tuple[Union[int, Decimal, float], Literal]] = []
        self.datatype: Optional[URIRef] = None

    def use_row(self, row: FrozenBindings) -> bool:
        try:
            return _eval(self.expr, row) not in self.seen
        except NotBoundError:
            # skip UNDEF
            return False

    def _eval_p(self, row: FrozenBindings) -> None:
        try:
            p_value = _eval(self.p_expr, row)
        except NotBoundError:
            raise SPARQLError("PERCENTILE: p is not bound")
        try:
            p = numeric(p_value)
        except SPARQLTypeError:
            raise SPARQLError("PERCENTILE: p must be numeric, got %r" % (p_value,))
        if p != p:
            raise SPARQLError("PERCENTILE: p must not be NaN")
        if not 0 <= p <= 1:
            raise SPARQLError("PERCENTILE: p must be in [0, 1], got %r" % (p,))
        if self.p is None:
            self.p = p
        elif p != self.p:
            raise SPARQLError("PERCENTILE: p must be invariant within a group")

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        self._eval_p(row)
        try:
            value = _eval(self.expr, row)
        except NotBoundError:
            # skip UNDEF
            return
        try:
            number = numeric(value)
        except SPARQLTypeError:
            # skip non-numeric values, as the other aggregates do
            return
        if number != number:
            # NaN values are excluded from the sorted values
            return
        self.values.append((number, value))
        dt = value.datatype
        if dt is not None:
            if self.datatype is None:
                self.datatype = dt
            else:
                self.datatype = type_promotion(self.datatype, dt)
        if self.distinct:
            self.seen.add(value)

    def sorted_values(self) -> list[tuple[Union[int, Decimal, float], Literal]]:
        # sort on the native python numbers; Decimals are never converted
        # to float, so high-precision decimals keep their exact order
        return sorted(self.values, key=lambda item: item[0])

    def set_value(self, bindings: MutableMapping[Variable, Identifier]) -> None:
        value = self.get_value()
        if value is not None:
            # empty groups leave the variable unbound
            bindings[self.var] = value


class PercentileCont(Percentile):
    """
    PERCENTILE_CONT(p, expr): continuous percentile with interpolation.

    For n sorted values the result is the value at 0-based position
    p * (n - 1), linearly interpolated between neighbouring values.
    p = 0 yields the minimum, p = 1 the maximum. The result type follows
    SPARQL numeric type promotion of the input values: xsd:double if any
    value is xsd:double, xsd:float if any value is xsd:float, otherwise
    xsd:decimal. Interpolation is computed with Decimal arithmetic, so
    high-precision decimals are never rounded through floats.
    """

    def get_value(self) -> Optional[Literal]:
        if not self.values:
            # empty group => unbound
            return None
        values = [number for number, _literal in self.sorted_values()]
        n = len(values)
        p = self.p
        if p is None:
            return None
        if self.datatype in (XSD.float, XSD.double):
            vals = [float(v) for v in values]
            fpos = float(p) * (n - 1)
            k = int(math.floor(fpos))
            fraction = fpos - k
            if k + 1 < n:
                result = vals[k] + fraction * (vals[k + 1] - vals[k])
            else:
                result = vals[k]
            return Literal(result, datatype=self.datatype)
        with _percentile_context(values, p):
            if isinstance(p, Decimal):
                p_dec = p
            elif isinstance(p, float):
                p_dec = Decimal(str(p))
            else:
                p_dec = Decimal(p)
            dpos = p_dec * (n - 1)
            # dpos >= 0, so truncation is the same as floor
            dk = int(dpos)
            dfraction = dpos - dk
            if dk + 1 < n:
                dresult = Decimal(values[dk]) + dfraction * (
                    Decimal(values[dk + 1]) - Decimal(values[dk])
                )
            else:
                dresult = Decimal(values[dk])
            dresult = _strip_decimal(dresult)
        # format with fixed-point notation: str() of a Decimal may use
        # scientific notation, which is not a valid xsd:decimal lexical form
        return Literal(format(dresult, "f"), datatype=XSD.decimal)


class PercentileDisc(Percentile):
    """
    PERCENTILE_DISC(p, expr): discrete percentile.

    Returns the first value (in ascending order) whose cumulative
    proportion reaches p, i.e. the value at 1-based position
    max(1, ceil(p * n)). p = 0 yields the minimum, p = 1 the maximum.
    The result is always one of the actual input values and keeps its
    original datatype.
    """

    def get_value(self) -> Optional[Literal]:
        if not self.values:
            # empty group => unbound
            return None
        values = self.sorted_values()
        n = len(values)
        p = self.p
        if p is None:
            return None
        index = max(math.ceil(p * n), 1) - 1
        # return the original literal, preserving its datatype
        return values[index][1]


class Aggregator:
    """combines different Accumulator objects"""

    accumulator_classes = {
        "Aggregate_Count": Counter,
        "Aggregate_Sample": Sample,
        "Aggregate_Sum": Sum,
        "Aggregate_Avg": Average,
        "Aggregate_Min": Minimum,
        "Aggregate_Max": Maximum,
        "Aggregate_GroupConcat": GroupConcat,
        "Aggregate_PercentileCont": PercentileCont,
        "Aggregate_PercentileDisc": PercentileDisc,
    }

    def __init__(self, aggregations: list[CompValue]):
        self.bindings: dict[Variable, Identifier] = {}
        self.accumulators: dict[str, Accumulator] = {}
        for a in aggregations:
            accumulator_class = self.accumulator_classes.get(a.name)
            if accumulator_class is None:
                raise Exception("Unknown aggregate function " + a.name)
            self.accumulators[a.res] = accumulator_class(a)

    def update(self, row: FrozenBindings) -> None:
        """update all own accumulators"""
        # SAMPLE accumulators may delete themselves
        # => iterate over list not generator

        for acc in list(self.accumulators.values()):
            if acc.use_row(row):
                acc.update(row, self)

    def get_bindings(self) -> Mapping[Variable, Identifier]:
        """calculate and set last values"""
        for acc in self.accumulators.values():
            acc.set_value(self.bindings)
        return self.bindings
