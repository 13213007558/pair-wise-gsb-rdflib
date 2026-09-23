"""
Aggregation functions

This module also provides the non-standard extension aggregates
'PERCENTILE_CONT(p, expr)' and 'PERCENTILE_DISC(p, expr)'.

They are explicitly enabled extensions (not part of SPARQL 1.1) and are
available by default; they can be turned off (and back on) with
:func:'enable_percentile_aggregates'.

Semantics:

* 'p' must be a numeric literal/expression in the closed interval [0, 1]
  and must be invariant within each group.  A non-numeric, out-of-range,
  NaN or group-varying 'p' is an error and makes the aggregate result
  unbound for that group.
* Rows where the value expression is unbound or evaluates to a non-numeric
  literal are skipped, following the usual SPARQL aggregate error rules.
* Values that are NaN (float/double/decimal) are ignored.
* 'DISTINCT' is applied to the value expression before the percentile
  is computed.
* 'PERCENTILE_CONT' interpolates between the sorted values
  ('p = 0' yields the minimum, 'p = 1' the maximum).  Numeric
  promotion follows the SPARQL numeric type promotion rules; exact
  decimal arithmetic is used unless a float/double is involved, so
  high-precision decimals are never rounded through floats.
* 'PERCENTILE_DISC' returns the first sorted value whose cumulative
  distribution reaches 'p'; the result is always one of the actual
  input readings, returned with its original datatype.
* A group with no usable values (empty group) leaves the result unbound.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from decimal import ROUND_FLOOR, Decimal, localcontext
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

PERCENTILE_AGGREGATES_ENABLED = True


def enable_percentile_aggregates(enabled: bool = True) -> None:
    """
    Explicitly enable (or disable, with 'enabled=False') the
    non-standard 'PERCENTILE_CONT' and 'PERCENTILE_DISC' aggregates.
    """
    global PERCENTILE_AGGREGATES_ENABLED
    PERCENTILE_AGGREGATES_ENABLED = enabled


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


class Percentile(Accumulator):
    """
    Abstract base class for PERCENTILE_CONT and PERCENTILE_DISC.

    Collects the numeric values of a group together with the percentile
    parameter 'p', which must be numeric, within [0, 1] and invariant
    within the group.
    """

    def __init__(self, aggregation: CompValue):
        if not PERCENTILE_AGGREGATES_ENABLED:
            raise SPARQLError(
                "Percentile aggregates are not enabled, "
                "call enable_percentile_aggregates() first"
            )
        super(Percentile, self).__init__(aggregation)
        self.p_expr = aggregation.p
        self.p: Optional[Union[Decimal, float, int]] = None
        self.p_datatype: Optional[URIRef] = None
        self.error = False
        self.values: list[tuple[Union[Decimal, float, int], Literal]] = []

    def use_row(self, row: FrozenBindings) -> bool:
        try:
            return _eval(self.expr, row) not in self.seen
        except NotBoundError:
            return False

    def update(self, row: FrozenBindings, aggregator: Aggregator) -> None:
        try:
            p_literal = _eval(self.p_expr, row)
        except NotBoundError:
            p_literal = None
        if p_literal is not None:
            try:
                p_num = numeric(p_literal)
            except SPARQLTypeError:
                self.error = True
            else:
                # NaN != NaN, so this also rejects NaN percentiles
                if p_num != p_num or not 0 <= p_num <= 1:
                    self.error = True
                elif self.p is None:
                    self.p = p_num
                    self.p_datatype = p_literal.datatype
                elif p_num != self.p:
                    # p must be invariant within the group
                    self.error = True
        try:
            value = _eval(self.expr, row)
            num = numeric(value)
        except NotBoundError:
            # skip UNDEF
            return
        except SPARQLTypeError:
            # skip non-numeric values, as the other aggregates do
            return
        if num != num:
            # NaN values are ignored
            return
        self.values.append((num, value))
        if self.distinct:
            self.seen.add(value)

    def sorted_values(self) -> Optional[list[tuple[Union[Decimal, float, int], Literal]]]:
        if self.error or self.p is None or not self.values:
            return None
        # sort on the python numbers; int/Decimal/float compare exactly,
        # so high-precision decimals are never rounded through floats
        return sorted(self.values, key=lambda item: item[0])

    def set_value(self, bindings: MutableMapping[Variable, Identifier]) -> None:
        value = self.get_value()
        if value is not None:
            # an error, an empty group or no valid p leaves the var unbound
            bindings[self.var] = value


class PercentileCont(Percentile):
    """
    PERCENTILE_CONT(p, expr): interpolates between the sorted values.

    p = 0 yields the minimum, p = 1 the maximum of the group.  The result
    datatype follows the SPARQL numeric type promotion rules over the
    group values and p; interpolation is done with exact decimal
    arithmetic unless a float/double is involved.
    """

    def get_value(self) -> Optional[Literal]:
        values = self.sorted_values()
        if values is None:
            return None
        assert self.p is not None
        nums = [num for num, _literal in values]
        count = len(nums)
        datatype: Optional[URIRef] = self.p_datatype
        for _num, literal in values:
            if datatype is None:
                datatype = literal.datatype
            else:
                datatype = type_promotion(datatype, literal.datatype)
        if datatype in (XSD.float, XSD.double):
            fnums = [float(num) for num in nums]
            rank = float(self.p) * (count - 1)
            lower = math.floor(rank)
            frac = rank - lower
            if frac == 0 or lower >= count - 1:
                result = fnums[min(lower, count - 1)]
            else:
                result = fnums[lower] + frac * (fnums[lower + 1] - fnums[lower])
            return Literal(result, datatype=datatype)
        # exact decimal arithmetic, never rounded through floats
        prec = max(
            [28]
            + [
                len(num.as_tuple().digits)
                for num in nums + [self.p]
                if isinstance(num, Decimal)
            ]
        )
        with localcontext() as ctx:
            ctx.prec = prec + 10
            dnums = [
                num if isinstance(num, Decimal) else Decimal(num) for num in nums
            ]
            p = self.p if isinstance(self.p, Decimal) else Decimal(self.p)
            rank = p * (count - 1)
            lower = int(rank.to_integral_value(rounding=ROUND_FLOOR))
            frac = rank - lower
            if frac == 0 or lower >= count - 1:
                result_d = dnums[min(lower, count - 1)]
            else:
                result_d = dnums[lower] + frac * (dnums[lower + 1] - dnums[lower])
            # strip insignificant trailing zeroes from the exact result
            if result_d == result_d.to_integral_value():
                result_d = result_d.quantize(Decimal(1))
            else:
                result_d = result_d.normalize()
        if datatype == XSD.integer:
            # only possible for integral p, i.e. the exact min/max
            return Literal(int(result_d), datatype=XSD.integer)
        return Literal(result_d, datatype=XSD.decimal)


class PercentileDisc(Percentile):
    """
    PERCENTILE_DISC(p, expr): returns the first sorted value whose
    cumulative distribution reaches p.

    p = 0 yields the minimum, p = 1 the maximum of the group.  The result
    is always one of the actual input readings, returned as the original
    literal with its original datatype.
    """

    def get_value(self) -> Optional[Literal]:
        values = self.sorted_values()
        if values is None:
            return None
        assert self.p is not None
        count = len(values)
        # the cumulative distribution of element i (0-based) is (i+1)/count;
        # take the first element with (i+1)/count >= p
        index = max(math.ceil(self.p * count), 1) - 1
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
