"""
Implements a well defined versioning schema.

There are three class types - VersionToken, Version and VersionRange. A Version
is a set of zero or more VersionTokens, separate by '.'s or '-'s (eg "1.2-3").
A VersionToken is a string containing alphanumerics, and default implemenations
'NumericToken' and 'AlphanumericVersionToken' are supplied. You can implement
your own if you want stricter tokens or different sorting behaviour.

A VersionRange is a set of one or more contiguous version ranges - for example,
"3+<5" contains any version >=3 but less than 5. Version ranges can be used to
define dependency requirements between objects. They can be OR'd together, AND'd
and inverted.

The empty version '', and empty version range '', are also handled. The empty
version is used to denote unversioned objects. The empty version range, also
known as the 'any' range, is used to refer to any version of an object.
"""
from rez.contrib.version.util import VersionError, _Common, total_ordering
import rez.contrib.pyparsing.pyparsing as pp
from bisect import bisect_left
import threading
import re


re_token = re.compile(r"[a-zA-Z0-9_]+")



@total_ordering
class _Comparable(_Common):
    def __lt__(self, other):
        raise NotImplementedError



class VersionToken(_Comparable):
    """Token within a version number.

    A version token is that part of a version number that appears between a
    delimiter, typically '.' or '-'. For example, the version number '2.3.07b'
    contains the tokens '2', '3' and '07b' respectively.

    Version tokens are only allowed to contain alphanumerics (any case) and
    underscores.
    """
    def __init__(self, token):
        """Create a VersionToken.

        Args:
            token: Token string, eg "rc02"
        """
        raise NotImplementedError

    @classmethod
    def create_random_token_string(cls):
        """Create a random token string. For testing purposes only."""
        raise NotImplementedError

    def less_than(self, other):
        """Compare to another VersionToken.

        Args:
            other: The VersionToken object to compare against.

        Returns:
            True if this token is less than other, False otherwise.
        """
        raise NotImplementedError

    def next(self):
        """Returns the next largest token."""
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __lt__(self, other):
        return self.less_than(other)

    def __eq__(self, other):
        return (not self < other) and (not other < self)



class NumericToken(VersionToken):
    """Numeric version token.

    Version token supporting numbers only. Padding is ignored.
    """
    def __init__(self, token):
        if not token.isdigit():
            raise VersionError("Invalid version token: '%s'" % token)
        else:
            self.n = int(n)

    @classmethod
    def create_random_token_string(cls):
        import random
        chars = str(pp.srange("[0-9]"))
        return ''.join([chars[random.randint(0, len(chars)-1)] for i in range(8)])

    def __str__(self):
        return str(self.n)

    def less_than(self, other):
        return (self.n < other.n)

    def next(self):
        other = copy.copy(self)
        other.n = self.n = 1
        return other



class _SubToken(_Comparable):
    """Used internally by AlphanumericVersionToken."""
    def __init__(self, s):
        self.s = s
        self.n = int(s) if s.isdigit() else None

    def __lt__(self, other):
        if self.n is None:
            return (self.s < other.s) if other.n is None else True
        else:
            return False if other.n is None \
                else ((self.n,self.s) < (other.n,other.s))

    def __eq__(self, other):
        return (self.s == other.s) and (self.n == other.n)

    def __str__(self):
        return self.s



class AlphanumericVersionToken(VersionToken):
    """Alphanumeric version token.

    These tokens compare as follows:
    - each token is split into alpha and numeric groups (subtokens);
    - the resulting subtoken list is compared.
    - alpha comparison is case-sensitive, numeric comparison is padding-sensitive.

    Subtokens compare as follows:
    - alphas come before numbers;
    - alphas are compared alphabetically (_, then A-Z, then a-z);
    - numbers are compared numerically. If numbers are equivalent but zero-
      padded differently, they are then compared alphabetically. Thus "01" < "1".

    Some example comparisons that equate to true:
    - "3" < "4"
    - "01" < "1"
    - "beta" < "1"
    - "alpha3" < "alpha4"
    - "alpha" < "alpha3"
    - "gamma33" < "33gamma"
    """
    numeric_regex = re.compile("[0-9]+")
    regex = re.compile(r"[a-zA-Z0-9_]+\Z")

    def __init__(self, token):
        if token is None:
            self.subtokens = None
        elif not self.regex.match(token):
            raise VersionError("Invalid version token: '%s'" % token)
        else:
            self.subtokens = self._parse(token)

    @classmethod
    def create_random_token_string(cls):
        import random
        chars = str(pp.srange("[0-9a-zA-Z_]"))
        return ''.join([chars[random.randint(0, len(chars)-1)] for i in range(8)])

    def __str__(self):
        return ''.join(str(x) for x in self.subtokens)

    def less_than(self, other):
        return (self.subtokens < other.subtokens)

    def next(self):
        other = AlphanumericVersionToken(None)
        other.subtokens = self.subtokens[:]
        subtok = other.subtokens[-1]
        if subtok.n is None:
            other.subtokens[-1] = _SubToken(subtok.s + '_')
        else:
            other.subtokens.append(_SubToken('_'))
        return other

    @classmethod
    def _parse(cls, s):
        subtokens = []
        alphas = cls.numeric_regex.split(s)
        numerics = cls.numeric_regex.findall(s)
        b = True

        while alphas or numerics:
            if b:
                alpha = alphas[0]
                alphas = alphas[1:]
                if alpha:
                    subtokens.append(_SubToken(alpha))
            else:
                numeric = numerics[0]
                numerics = numerics[1:]
                subtokens.append(_SubToken(numeric))
            b = not b

        return subtokens



class Version(_Comparable):
    """Version object.

    A Version is a sequence of zero or more version tokens, separated by either
    a dot '.' or hyphen '-' delimiters. Note that separators only affect Version
    objects cosmetically - in other words, the version '1.0.0' is equivalent to
    '1-0-0'.

    The empty version '' is the smallest possible version, and can be used to
    represent an unversioned resource.
    """
    inf = None

    def __init__(self, ver_str='', make_token=AlphanumericVersionToken):
        """Create a Version object.

        Args:
            ver_str: Version string.
            make_token: Callable that creates a VersionToken subclass from a
                string.
        """
        self.tokens = []
        self.seps = []

        if ver_str:
            toks = re_token.findall(ver_str)
            if not toks:
                raise VersionError(ver_str)

            seps = re_token.split(ver_str)
            if seps[0] or seps[-1] or max(len(x) for x in seps) > 1:
                raise VersionError("Invalid version syntax: '%s'" % ver_str)

            for tok in toks:
                try:
                    self.tokens.append(make_token(tok))
                except VersionError as e:
                    raise VersionError("Invalid version '%s': %s"
                                       % (ver_str, str(e)))

            self.seps = seps[1:-1]

    def next(self):
        """Return 'next' version. Eg, next(1.2) is 1.2_"""
        if self.tokens:
            other = Version(None)
            other.tokens = self.tokens[:]
            other.seps = self.seps

            tok = other.tokens.pop()
            other.tokens.append(tok.next())
            return other
        else:
            return Version.inf

    def __len__(self):
        return len(self.tokens or [])

    def __getitem__(self, index):
        try:
            return (self.tokens or [])[index]
        except IndexError:
            raise IndexError("version token index out of range")

    def __nonzero__(self):
        """The empty version equates to False."""
        return bool(self.tokens)

    def __eq__(self, other):
        return (self.tokens == other.tokens)

    def __lt__(self, other):
        if self.tokens is None:
            return False
        elif other.tokens is None:
            return True
        else:
            return (self.tokens < other.tokens)

    def __hash__(self):
        return hash(None) if self.tokens is None \
            else hash(tuple(str(x) for x in self.tokens))

    def __str__(self):
        return "[INF]" if self.tokens is None \
            else ''.join(str(x)+y for x,y in zip(self.tokens, self.seps+['']))

# internal use only
Version.inf = Version()
Version.inf.tokens = None



class _LowerBound(_Comparable):
    min = None

    def __init__(self, version, inclusive):
        self.version = version
        self.inclusive = inclusive

    def __str__(self):
        if self.version:
            s = "%s+" if self.inclusive else ">%s"
            return s % self.version
        else:
            return '' if self.inclusive else ">"

    def __eq__(self, other):
        return (self.version == other.version) \
            and (self.inclusive == other.inclusive)

    def __lt__(self, other):
        return (self.version < other.version) \
            or ((self.version == other.version) \
            and (self.inclusive and not other.inclusive))

    def __hash__(self):
        return hash((self.version, self.inclusive))

    def contains_version(self, version):
        return (version > self.version) \
            or (self.inclusive and (version == self.version))

_LowerBound.min = _LowerBound(Version(), True)



class _UpperBound(_Comparable):
    inf = None

    def __init__(self, version, inclusive):
        self.version = version
        self.inclusive = inclusive
        if not version and not inclusive:
            raise VersionError("Invalid upper bound: '%s'" % str(self))

    def __str__(self):
        s = "<=%s" if self.inclusive else "<%s"
        return s % self.version

    def __eq__(self, other):
        return (self.version == other.version) \
            and (self.inclusive == other.inclusive)

    def __lt__(self, other):
        return (self.version < other.version) \
            or ((self.version == other.version) \
            and (not self.inclusive and other.inclusive))

    def __hash__(self):
        return hash((self.version, self.inclusive))

    def contains_version(self, version):
        return (version < self.version) \
            or (self.inclusive and (version == self.version))

_UpperBound.inf = _UpperBound(Version.inf, True)



class _Bound(_Comparable):
    any = None

    def __init__(self, lower=None, upper=None):
        self.lower = lower or _LowerBound.min
        self.upper = upper or _UpperBound.inf
        if (self.lower.version > self.upper.version) \
            or ((self.lower.version == self.upper.version) \
            and not (self.lower.inclusive and self.upper.inclusive)):
            raise VersionError("Invalid bound")

    def __str__(self):
        if self.upper.version == Version.inf:
            return str(self.lower)
        elif self.lower.version == self.upper.version:
            return "==%s" % str(self.lower.version)
        elif self.lower.inclusive and self.upper.inclusive:
            if self.lower.version:
                return "%s..%s" % (self.lower.version, self.upper.version)
            else:
                return "<=%s" % self.upper.version
        elif (self.lower.inclusive and not self.upper.inclusive) \
            and (self.lower.version.next() == self.upper.version):
            return str(self.lower.version)
        else:
            return "%s%s" % (self.lower, self.upper)

    def __eq__(self, other):
        return (self.lower == other.lower) and (self.upper == other.upper)

    def __lt__(self, other):
        return (self.lower, self.upper) < (other.lower, other.upper)

    def __hash__(self):
        return hash((self.lower, self.upper))

    def lower_bounded(self):
        return (self.lower != _LowerBound.min)

    def upper_bounded(self):
        return (self.upper != _UpperBound.inf)

    def contains_version(self, version):
        return (self.lower.contains_version(version)
                and self.upper.contains_version(version))

    def contains_bound(self, bound):
        return (self.lower <= bound.lower) and (self.upper >= bound.upper)

    def intersection(self, other):
        lower = max(self.lower, other.lower)
        upper = min(self.upper, other.upper)

        if (lower.version < upper.version) or \
            ((lower.version == upper.version) and \
            (lower.inclusive and upper.inclusive)):
            return _Bound(lower, upper)
        else:
            return None

_Bound.any = _Bound()



class _VersionRangeParser(object):
    parsers = {}

    @classmethod
    def parse(cls, s, make_token, debug=False):
        id_ = id(threading.currentThread())
        parser = cls.parsers.get(id_)
        if parser is None:
            parser = _VersionRangeParser()
            cls.parsers[id_] = parser
        return parser._parse(s, make_token=make_token, debug=debug)

    def __init__(self):
        self.stack = []
        self.bounds = []
        self.make_token = None
        self.debug = False

        # grammar
        token = pp.Word(pp.srange("[0-9a-zA-Z_]"))
        version_sep = pp.oneOf(['.','-'])
        version = pp.Optional(token + pp.ZeroOrMore(version_sep + token)).setParseAction(self._act_version)
        exact_version = ("==" + version).setParseAction(self._act_exact_version)
        inclusive_bound = (version + ".." + version).setParseAction(self._act_inclusive_bound)
        lower_bound = ((pp.oneOf([">", ">="]) + version) | (version + "+")).setParseAction(self._act_lower_bound)
        upper_bound = (pp.oneOf(["<", "<="]) + version).setParseAction(self._act_upper_bound)
        bound = (lower_bound + pp.Optional(",") + upper_bound)
        range = (version ^ exact_version ^ lower_bound ^ upper_bound
                 ^ bound ^ inclusive_bound).setParseAction(self._act_range)
        self.ranges = pp.Optional(range + pp.ZeroOrMore("|" + range))

    def action(fn):
        def fn_(self, s, i, tokens):
            fn(self, s, i, tokens)
            if self.debug:
                label = fn.__name__.replace("_act_","")
                print "%-16s%s" % (label+':', s)
                print "%s%s" % ((16+i)*' ', '^'*len(''.join(tokens)))
                print "%s%s" % (16*' ', self.stack)
        return fn_

    def _bound(self, lower, upper, tokens):
        try:
            return _Bound(lower, upper)
        except VersionError as e:
            raise VersionError("Invalid bound: '%s'" % ''.join(tokens))

    @action
    def _act_version(self, s, i, tokens):
        self.stack.append(Version(''.join(tokens), make_token=self.make_token))

    @action
    def _act_exact_version(self, s, i, tokens):
        ver = self.stack.pop()
        lower = _LowerBound(ver, True)
        upper = _UpperBound(ver, True)
        self.stack.append(self._bound(lower, upper, tokens))

    @action
    def _act_inclusive_bound(self, s, i, tokens):
        upper_ver = self.stack.pop()
        lower_ver = self.stack.pop()
        self.stack.append(_LowerBound(lower_ver, True))
        self.stack.append(_UpperBound(upper_ver, True))

    @action
    def _act_lower_bound(self, s, i, tokens):
        ver = self.stack.pop()
        exclusive = (">" in list(tokens))
        self.stack.append(_LowerBound(ver, not exclusive))

    @action
    def _act_upper_bound(self, s, i, tokens):
        ver = self.stack.pop()
        exclusive = ("<" in list(tokens))
        self.stack.append(_UpperBound(ver, not exclusive))

    @action
    def _act_range(self, s, i, tokens):
        if len(self.stack) == 1:
            obj = self.stack.pop()
            if isinstance(obj, Version):
                lower = _LowerBound(obj, True)
                upper = _UpperBound(obj.next(), False) if obj else None
                self.bounds.append(self._bound(lower, upper, tokens))
            elif isinstance(obj, _Bound):
                self.bounds.append(obj)
            elif isinstance(obj, _LowerBound):
                self.bounds.append(self._bound(obj, None, tokens))
            else:  # _UpperBound
                self.bounds.append(self._bound(None, obj, tokens))
        else:
            upper = self.stack.pop()
            lower = self.stack.pop()
            self.bounds.append(self._bound(lower, upper, tokens))

    def _parse(self, s, make_token, debug):
        self.stack = []
        self.bounds = []
        self.make_token = make_token
        self.debug = debug
        self.ranges.parseString(s, parseAll=True)
        return self.bounds



class VersionRange(_Comparable):
    """Version range.

    A version range is a set of one or more contiguous ranges of versions. For
    example, "3.0 or greater, but less than 4" is a contiguous range that contains
    versions such as "3.0", "3.1.0", "3.99" etc. Version ranges behave something
    like sets - they can be intersected, added and subtracted, but can also be
    inverted. You can test to see if a Version is contained within a VersionRange.

    A VersionRange "3" (for example) is the superset of any version "3[.X.X...]".
    The version "3" itself is also within this range, and is smaller than "3.0"
    - any version with common leading tokens, but with a larger token count, is
    the larger version of the two.

    VersionRange objects have a flexible syntax that let you describe any
    combination of contiguous ranges, including inclusive and exclusive upper
    and lower bounds. This is best explained by example (those listed on the
    same line are equivalent):

    "3": 'superset' syntax, contains "3", "3.0", "3.1.4" etc;
    "2+", ">=2": inclusive lower bound syntax, contains "2", "2.1", "5.0.0" etc;
    ">2": exclusive lower bound;
    "<5": exclusive upper bound;
    "<=5": inclusive upper bound;

    "1+<5", ">=1<5": inclusive lower, exclusive upper. The most common form of
        a 'bounded' version range (ie, one with a lower and upper bound);
    ">1<5": exclusive lower, exclusive upper;
    ">1<=5": exclusive lower, inclusive upper;
    "1+<=5", "1..5": inclusive lower, inclusive upper;
    "==2": a range that contains only the single version "2".

    To describe more than one contiguous range, seperate ranges with the or '|'
    symbol. For example, the version range "4|6+" contains versions such as "4",
    "4.0", "4.3.1", "6", "6.1", "10.0.0", but does not contain any version
    "5[.X.X...X]". If you provide multiple ranges that overlap, they will be
    automatically optimised - for example, the version range "3+<6|4+<8"
    becomes "3+<8".

    Note that the empty string version range represents the superset of all
    possible versions - this is called the "any" range. The empty version can
    also be used as an upper or lower bound, leading to some odd but perfectly
    valid version range syntax. For example, ">" is a valid range - read like
    ">''", it means "any version greater than the empty version".

    To help with readability, bounded ranges can also have their bounds separated
    with a comma, eg ">=2,<=6". The comma is purely cosmetic and is dropped in
    the string representation.
    """
    def __init__(self, range_str='', make_token=AlphanumericVersionToken,
                 debug_parsing=False):
        """Create a VersionRange object.

        Args:
            range_str: Range string, such as "3", "3+<4.5", "2|6+". The range
                will be optimised, so the string representation of this instance
                may not match range_str. For example, "3+<6|4+<8" == "3+<8".
            make_token: Version token class to use.
        """
        self.bounds = []
        if range_str is None:
            return

        try:
            bounds = _VersionRangeParser.parse(range_str,
                                               make_token=make_token,
                                               debug=debug_parsing)
        except pp.ParseException as e:
            raise VersionError("Syntax error in version range '%s': %s"
                               % (range_str, str(e)))
        except VersionError as e:
            raise VersionError("Invalid version range '%s': %s"
                               % (range_str, str(e)))

        if bounds:
            self.bounds = self._union(bounds)
        else:
            self.bounds.append(_Bound.any)

    def is_any(self):
        """Returns True if this is the "any" range, ie the empty string range
        that contains all versions."""
        return (len(self.bounds) == 1) and (self.bounds[0] == _Bound.any)

    def lower_bounded(self):
        """Returns True if the range has a lower bound (that is not the empty
        version)."""
        return self.bounds[0].lower_bounded()

    def upper_bounded(self):
        """Returns True if the range has an upper bound."""
        return self.bounds[-1].upper_bounded()

    def bounded(self):
        """Returns True if the range has a lower and upper bound."""
        return (self.lower_bounded() and self.upper_bounded())

    def issuperset(self, range):
        """Returns True if the VersionRange is contained within this range.
        """
        for bound in range.bounds:
            i = bisect_left(self.bounds, bound)
            if i:
                if self.bounds[i-1].contains_bound(bound):
                    continue
            if (i < len(self.bounds)) and self.bounds[i].contains_bound(bound):
                continue
            return False

        return True

    def issubset(self, range):
        """Returns True if we are contained within the version range.
        """
        return range.issuperset(self)

    def union(self, other):
        """OR together version ranges.

        Calculates the union of this range with one or more other ranges.

        Args:
            other: VersionRange object (or list of) to OR with.

        Returns:
            New VersionRange object representing the union.
        """
        if not hasattr(other, "__iter__"):
            other = [other]
        bounds = self.bounds[:]
        for range in other:
            bounds += range.bounds

        bounds = self._union(bounds)
        range = VersionRange(None)
        range.bounds = bounds
        return range

    def intersection(self, other):
        """AND together version ranges.

        Calculates the intersection of this range with one or more other ranges.

        Args:
            other: VersionRange object (or list of) to AND with.

        Returns:
            New VersionRange object representing the intersection, or None if
            no ranges intersect.
        """
        if not hasattr(other, "__iter__"):
            other = [other]

        bounds = self.bounds
        for range in other:
            bounds = self._intersection(bounds, range.bounds)
            if not bounds:
                return None

        range = VersionRange(None)
        range.bounds = bounds
        return range

    def inverse(self):
        """Calculate the inverse of the range.

        Returns:
            New VersionRange object representing the inverse of this range, or
            None if there is no inverse (ie, this range is the any range).
        """
        if self.is_any():
            return None
        else:
            bounds = self._inverse(self.bounds)
            range = VersionRange(None)
            range.bounds = bounds
            return range

    def intersects(self, other):
        """Determine if we intersect with another range.

        Args:
            other: VersionRange object.

        Returns:
            True if the ranges intersect, False otherwise.
        """
        return self._intersects(self.bounds, other.bounds)

    def split(self):
        """Split into separate contiguous ranges.

        Returns:
            A list of VersionRange objects. For example, the range "3|5+" will
            be split into ["3", "5+"].
        """
        ranges = []
        for bound in self.bounds:
            range = VersionRange(None)
            range.bounds = [bound]
            ranges.append(range)
        return ranges

    @classmethod
    def as_span(cls, lower_version=None, upper_version=None):
        """Create a range from lower_version..upper_version.

        Args:
            lower_version Version object representing lower bound of the range.
        """
        lower = None if lower_version is None else _LowerBound(lower_version, True)
        upper = None if upper_version is None else _UpperBound(upper_version, True)
        bound = _Bound(lower, upper)

        range = cls(None)
        range.bounds = [bound]
        return range

    @classmethod
    def from_version(cls, version, op=None):
        """Create a range from a version.

        Args:
            version: Version object. This is used as the upper/lower bound of
                the range.
            op: Operation as a string. One of 'gt'/'>', 'gte'/'>=', lt'/'<',
                'lte'/'<=', 'eq'/'=='. If None, a bounded range will be created
                that contains exactly this version only.
        """
        lower = None
        upper = None

        if op in (None, "eq", "=="):
            lower = _LowerBound(version, True)
            upper = _UpperBound(version, True)
        elif op in ("gt",">"):
            lower = _LowerBound(version, False)
        elif op in ("gte", ">="):
            lower = _LowerBound(version, True)
        elif op in ("lt","<"):
            upper = _UpperBound(version, False)
        elif op in ("lte","<="):
            upper = _UpperBound(version, True)
        else:
            raise VersionError("Unknown bound operation '%s'" % op)

        bound = _Bound(lower, upper)
        range = cls(None)
        range.bounds = [bound]
        return range

    @classmethod
    def from_versions(cls, versions):
        """Create a range from a list of versions.

        This method creates a range that contains only the given versions and
        no other. Typically the range looks like (for eg) "==3|==4|==5.1".

        Args:
            versions: List of Version objects.
        """
        range = cls(None)
        range.bounds = []
        for version in sorted(set(versions)):
            lower = _LowerBound(version, True)
            upper = _UpperBound(version, True)
            bound = _Bound(lower, upper)
            range.bounds.append(bound)
        return range

    def to_versions(self):
        """Returns exact version ranges as Version objects, or None if there
        are no exact version ranges present.
        """
        versions = []
        for bound in self.bounds:
            if bound.lower.inclusive and bound.upper.inclusive \
                and (bound.lower.version == bound.upper.version):
                versions.append(bound.lower.version)

        return versions or None

    def contains_version(self, version):
        """Returns True if version is contained in this range."""
        nbounds = len(self.bounds)
        if nbounds < 5:
            # not worth overhead of binary search
            for bound in self.bounds:
                if bound.contains_version(version):
                    return True
        else:
            vbound = _Bound(_LowerBound(version, True))
            i = bisect_left(self.bounds, vbound)
            if i:
                if self.bounds[i-1].contains_version(version):
                    return True
            if (i < nbounds) and self.bounds[i].contains_version(version):
                return True

        return False

    def span(self):
        """Return a contiguous range that is a superset of this range.

        Returns:
            A VersionRange object representing the span of this range. For
            example, the span of "2+<4|6+<8" would be "2+<8".
        """
        other = VersionRange(None)
        bound = _Bound(self.bounds[0].lower, self.bounds[-1].upper)
        other.bounds = [bound]
        return other

    def __contains__(self, version_or_range):
        if isinstance(version_or_range, Version):
            return self.contains_version(version_or_range)
        else:
            return self.issuperset(version_or_range)

    def __len__(self):
        return len(self.bounds)

    def __invert__(self):
        return self.inverse()

    def __and__(self, other):
        return self.intersection(other)

    def __or__(self, other):
        return self.union(other)

    def __add__(self, other):
        return self.union(other)

    def __sub__(self, other):
        inv = other.inverse()
        return None if inv is None else self.intersection(inv)

    def __str__(self):
        return '|'.join(str(x) for x in self.bounds)

    def __eq__(self, other):
        return (self.bounds == other.bounds)

    def __lt__(self, other):
        return (self.bounds < other.bounds)

    def __hash__(self):
        return hash(tuple(self.bounds))

    @classmethod
    def _union(cls, bounds):
        if len(bounds) < 2:
            return bounds

        bounds_ = list(sorted(bounds))
        new_bounds = []
        prev_bound = None
        upper = None
        start = 0

        for i,bound in enumerate(bounds_):
            if i and ((bound.lower.version > upper.version) \
                or ((bound.lower.version == upper.version) \
                and (not bound.lower.inclusive) \
                and (not prev_bound.upper.inclusive))):
                new_bound = _Bound(bounds_[start].lower, upper)
                new_bounds.append(new_bound)
                start = i

            prev_bound = bound
            upper = bound.upper if upper is None else max(upper, bound.upper)

        new_bound = _Bound(bounds_[start].lower, upper)
        new_bounds.append(new_bound)
        return new_bounds

    @classmethod
    def _intersection(cls, bounds1, bounds2):
        new_bounds = []
        for bound1 in bounds1:
            for bound2 in bounds2:
                b = bound1.intersection(bound2)
                if b:
                    new_bounds.append(b)
        return new_bounds

    @classmethod
    def _inverse(cls, bounds):
        lbounds = [None]
        ubounds = []

        for bound in bounds:
            if not bound.lower.version and bound.lower.inclusive:
                ubounds.append(None)
            else:
                b = _UpperBound(bound.lower.version, not bound.lower.inclusive)
                ubounds.append(b)

            if bound.upper.version == Version.inf:
                lbounds.append(None)
            else:
                b = _LowerBound(bound.upper.version, not bound.upper.inclusive)
                lbounds.append(b)

        ubounds.append(None)
        new_bounds = []

        for lower,upper in zip(lbounds, ubounds):
            if not (lower is None and upper is None):
                new_bounds.append(_Bound(lower, upper))

        return new_bounds

    @classmethod
    def _intersects(cls, bounds1, bounds2):
        for bound1 in bounds1:
            for bound2 in bounds2:
                b = bound1.intersection(bound2)
                if b:
                    return True
        return False