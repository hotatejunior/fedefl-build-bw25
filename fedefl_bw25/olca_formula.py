"""
olca_formula
------------
Evaluate openLCA parameter formulas, restricted to the subset real datasets use.

Why a hand-written parser
-------------------------
Python's `ast` cannot parse openLCA formulas, for three independent reasons:

    a div b     `div` and `mod` are infix WORD operators. A SyntaxError, not an
                expression. (Unused in the surveyed dataset, refused here.)
    a = b       `=` is EQUALITY in openLCA. In Python it is a statement.
    2 ^ 3       `^` is EXPONENTIATION in openLCA. In Python it is bitwise XOR --
                which parses fine and silently returns 1 instead of 8.

The third is the dangerous one: handing these to `ast` would not raise, it would
produce wrong numbers. So the dialect gets its own lexer and parser.

What is supported, and why exactly this much
--------------------------------------------
The subset was fixed by measuring two real corpora with
`tools/scan_olca_parameters.py`, not by guessing at openLCA's manual.

A 103-process, 6,430-exchange study (napa-lci alpha, 2026-08-12) has 3,890
formulas, of which 2,990 are pure arithmetic and the remaining 900 account for
themselves exactly:

    formulas containing `if`         900
    formulas containing `=`          810   \\  disjoint: the tallies are per-formula
    formulas containing `!=`          90   /   sets, and 810 + 90 = 900 leaves no overlap

USLCI's public database (1,341 processes, 1,731 formulas) adds one construct that
study does not use: a single `>` comparison, in `C_storage_final` of "Municipal
solid waste, MSW, landfilling of mixed MSW". Ordering comparisons are therefore
supported too -- one real formula in a shipped database is enough, and the four of
them cost four lines between them.

    numbers, identifiers, parentheses
    + - * / ^            (`^` right-associative, exponentiation)
    unary + -
    = != < > <= >=       (`==` and `<>` accepted as spellings of `=` and `!=`)
    if(cond; then; else) (semicolon-separated, as openLCA writes it)
    pi, e                (constants; unused in both corpora, free to support)

Everything else -- `div`, `mod`, `&&`, `||`, and all 40-odd openLCA functions --
raises `UnsupportedConstruct`. Neither corpus contains any of them. Refusing
loudly is the whole point: a formula this module cannot evaluate must stop a
build, never fall back to a literal amount, because the fallback is invisible in
the result.

Deliberate semantics
--------------------
Float equality is exact. openLCA's own tolerance behaviour is not documented in a
form worth guessing at, so this implements the literal reading and lets
`tools/verify_olca_formulas.py` adjudicate against ~3,900 values openLCA already
evaluated. If exact equality were wrong, that harness fails loudly rather than
this module quietly compensating for a mismatch nobody measured.
"""
from __future__ import annotations

import math
import re
from collections import Counter


class FormulaError(RuntimeError):
    """A formula could not be evaluated. Carries the text and the position."""

    def __init__(self, message, formula=None, pos=None):
        self.formula = formula
        self.pos = pos
        if formula is not None and pos is not None:
            message = f"{message}\n    {formula}\n    {' ' * pos}^"
        elif formula is not None:
            message = f"{message}\n    {formula}"
        super().__init__(message)


class UnsupportedConstruct(FormulaError):
    """Valid openLCA, outside the subset this module implements.

    Distinct from a syntax error because the remedy is different: a syntax error
    means the formula is malformed, this means the formula is fine and the
    evaluator is not broad enough. It names what to add.
    """


# =============================================================================
# LEXER
# =============================================================================
# Longest match first -- '<=' before '<', '!=' before '!'. The ordering matters
# only for the refused operators, but getting it wrong would report a confusing
# error ('<' unsupported) for a construct that is really '<='.
_OPERATORS = ("<=", ">=", "==", "!=", "<>", "&&", "||",
              "<", ">", "=", "&", "|", "+", "-", "*", "/", "^")

_SUPPORTED_OPERATORS = {"+", "-", "*", "/", "^",
                        "=", "==", "!=", "<>", "<", ">", "<=", ">="}

# Comparison operators, in the order the parser offers them. Non-associative:
# `a < b < c` is a parse error rather than something with a surprising reading.
_COMPARISONS = ("<=", ">=", "==", "!=", "<>", "=", "<", ">")

# Word-spelled infix operators. Lexed so they can be REFUSED by name; leaving them
# out would misreport `a div b` as an undefined variable `div`.
_WORD_OPERATORS = {"div", "mod"}

_CONSTANTS = {"pi": math.pi, "e": math.e}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

# Token kinds
_NUM, _IDENT, _OP, _LPAR, _RPAR, _SEMI, _EOF = (
    "NUM", "IDENT", "OP", "LPAR", "RPAR", "SEMI", "EOF")


class _Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f"<{self.kind} {self.value!r} @{self.pos}>"


def tokenize(formula):
    """Formula text -> [_Token]. Raises FormulaError on bytes the dialect lacks."""
    if not isinstance(formula, str):
        raise FormulaError(
            f"formula is {type(formula).__name__}, not a string")

    s = formula
    n = len(s)
    i = 0
    out = []
    while i < n:
        ch = s[i]

        if ch.isspace():
            i += 1
            continue

        m = _NUMBER_RE.match(s, i)
        if m and (ch.isdigit() or (ch == "." and m.start() == i)):
            out.append(_Token(_NUM, float(m.group(0)), i))
            i = m.end()
            continue

        m = _IDENT_RE.match(s, i)
        if m:
            name = m.group(0)
            out.append(_Token(_IDENT, name, i))
            i = m.end()
            continue

        if ch == "(":
            out.append(_Token(_LPAR, "(", i))
            i += 1
            continue
        if ch == ")":
            out.append(_Token(_RPAR, ")", i))
            i += 1
            continue
        if ch in ";,":
            # openLCA writes if() arguments separated by ';'. ',' is accepted
            # because hand-written and generated exports use it and the intent
            # is never ambiguous -- there is no comma operator in the dialect.
            out.append(_Token(_SEMI, ";", i))
            i += 1
            continue

        for op in _OPERATORS:
            if s.startswith(op, i):
                out.append(_Token(_OP, op, i))
                i += len(op)
                break
        else:
            raise FormulaError(
                f"unexpected character {ch!r}", formula, i)

    out.append(_Token(_EOF, None, n))
    return out


def referenced_names(formula):
    """Every identifier a formula reads, excluding constants and word operators.

    Used to build the dependency graph before anything is evaluated, so a cycle or
    an undefined name is reported as a whole-file problem rather than surfacing as
    a recursion error partway through a build.

    A name immediately followed by '(' is a function call, not a variable.
    """
    names = set()
    toks = tokenize(formula)
    for idx, t in enumerate(toks):
        if t.kind != _IDENT:
            continue
        if toks[idx + 1].kind == _LPAR:
            continue                                  # function call
        low = t.value.lower()
        if low in _CONSTANTS or low in _WORD_OPERATORS:
            continue
        names.add(t.value)
    return names


# =============================================================================
# PARSER
# =============================================================================
# Precedence, loosest first:
#
#   comparison  := additive (('=' | '!=') additive)?      non-associative
#   additive    := multiplicative (('+' | '-') multiplicative)*
#   multiplicative := unary (('*' | '/') unary)*
#   unary       := ('+' | '-') unary | power
#   power       := primary ('^' unary)?                   right-associative
#   primary     := NUMBER | 'if' '(' cmp ';' cmp ';' cmp ')' | IDENT | '(' cmp ')'
#
# The unary/power split is the classic formulation that makes both `-2^2 == -4`
# (negation applies to the whole power) and `2^-3 == 0.125` (a negative exponent
# needs no parentheses) come out right.

class _Parser:
    def __init__(self, formula):
        self.formula = formula
        self.toks = tokenize(formula)
        self.i = 0

    # -- token helpers ------------------------------------------------------
    @property
    def cur(self):
        return self.toks[self.i]

    def advance(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def at_op(self, *ops):
        t = self.cur
        return t.kind == _OP and t.value in ops

    def expect(self, kind, what):
        t = self.cur
        if t.kind != kind:
            got = "end of formula" if t.kind == _EOF else repr(t.value)
            raise FormulaError(
                f"expected {what}, got {got}", self.formula, t.pos)
        return self.advance()

    # -- grammar ------------------------------------------------------------
    def parse(self):
        node = self.comparison()
        if self.cur.kind != _EOF:
            t = self.cur
            # `a div b` lands here rather than in primary(): the left operand
            # parses cleanly and `div` is left over. Naming it beats "unexpected
            # trailing 'div'", because word operators are precisely the reason
            # this dialect cannot be handed to Python's ast.
            if t.kind == _IDENT and t.value.lower() in _WORD_OPERATORS:
                raise UnsupportedConstruct(
                    f"word operator {t.value!r} is outside the supported subset "
                    f"(use '/' for div)", self.formula, t.pos)
            raise FormulaError(
                f"unexpected trailing {t.value!r}", self.formula, t.pos)
        return node

    def comparison(self):
        left = self.additive()
        if self.at_op(*_COMPARISONS):
            op = self.advance()
            right = self.additive()
            return ("cmp", op.value, left, right, op.pos)
        # Logical connectives parse to here and are refused by name so the message
        # says which operator to implement. Neither appears in USLCI or the
        # surveyed study, and both need a boolean type the evaluator does not have.
        if self.at_op("&&", "||", "&", "|"):
            t = self.cur
            raise UnsupportedConstruct(
                f"operator {t.value!r} is outside the supported subset "
                f"(arithmetic, comparisons, and if())",
                self.formula, t.pos)
        return left

    def additive(self):
        node = self.multiplicative()
        while self.at_op("+", "-"):
            op = self.advance()
            node = ("bin", op.value, node, self.multiplicative(), op.pos)
        return node

    def multiplicative(self):
        node = self.unary()
        while self.at_op("*", "/"):
            op = self.advance()
            node = ("bin", op.value, node, self.unary(), op.pos)
        return node

    def unary(self):
        if self.at_op("+", "-"):
            op = self.advance()
            operand = self.unary()
            return ("neg", operand, op.pos) if op.value == "-" else operand
        return self.power()

    def power(self):
        base = self.primary()
        if self.at_op("^"):
            op = self.advance()
            return ("bin", "^", base, self.unary(), op.pos)   # right-assoc
        return base

    def primary(self):
        t = self.cur

        if t.kind == _NUM:
            self.advance()
            return ("num", t.value)

        if t.kind == _LPAR:
            self.advance()
            node = self.comparison()
            self.expect(_RPAR, "')'")
            return node

        if t.kind == _IDENT:
            low = t.value.lower()

            if low in _WORD_OPERATORS:
                raise UnsupportedConstruct(
                    f"word operator {t.value!r} is outside the supported subset "
                    f"(use '/' for div)", self.formula, t.pos)

            # Function call?
            if self.toks[self.i + 1].kind == _LPAR:
                if low != "if":
                    raise UnsupportedConstruct(
                        f"function {t.value}() is outside the supported subset "
                        f"(only if() is implemented)", self.formula, t.pos)
                return self.if_call()

            self.advance()
            if low in _CONSTANTS:
                return ("num", _CONSTANTS[low])
            return ("var", t.value, t.pos)

        got = "end of formula" if t.kind == _EOF else repr(t.value)
        raise FormulaError(f"expected a value, got {got}", self.formula, t.pos)

    def if_call(self):
        pos = self.cur.pos
        self.advance()                      # 'if'
        self.expect(_LPAR, "'(' after if")
        cond = self.comparison()
        self.expect(_SEMI, "';' after the if() condition")
        then = self.comparison()
        self.expect(_SEMI, "';' after the if() true-branch")
        other = self.comparison()
        # A 4th argument would mean a dialect feature we have not seen; say so
        # rather than reporting a confusing "expected ')'".
        if self.cur.kind == _SEMI:
            raise UnsupportedConstruct(
                "if() takes exactly 3 arguments in the supported subset",
                self.formula, self.cur.pos)
        self.expect(_RPAR, "')' closing if()")
        return ("if", cond, then, other, pos)


def parse(formula):
    """Formula text -> AST tuple. Raises FormulaError / UnsupportedConstruct."""
    return _Parser(formula).parse()


# =============================================================================
# EVALUATOR
# =============================================================================
def _truthy(value):
    """openLCA conditions are comparisons, which yield a boolean.

    A bare numeric condition is not something the surveyed corpus produces, but
    treating non-zero as true costs nothing and avoids a spurious hard stop on a
    file that does it.
    """
    return bool(value)


def evaluate(formula, scope, _cache=None):
    """Evaluate `formula` against a name -> float mapping.

    `scope` is consulted case-sensitively first. openLCA's own interpreter is
    case-insensitive, so a miss retries case-insensitively -- but an ambiguous
    case-insensitive match is a hard stop rather than a coin flip. In the surveyed
    corpus every reference matched exactly, so the fallback never fires there.
    """
    return eval_ast(parse(formula), scope, formula)


def eval_ast(node, scope, formula=None):
    """Evaluate a parsed AST. Separated from `evaluate` so a formula parsed once
    can be evaluated many times -- which is the whole point of a parameter sweep.
    """
    kind = node[0]

    if kind == "num":
        return node[1]

    if kind == "var":
        _, name, pos = node
        if name in scope:
            return scope[name]
        hits = [k for k in scope if k.lower() == name.lower()]
        if len(hits) == 1:
            return scope[hits[0]]
        if len(hits) > 1:
            raise FormulaError(
                f"{name!r} matches {len(hits)} parameters differing only in case "
                f"-- resolution would be arbitrary", formula, pos)
        raise FormulaError(f"undefined parameter {name!r}", formula, pos)

    if kind == "neg":
        return -eval_ast(node[1], scope, formula)

    if kind == "bin":
        _, op, left, right, pos = node
        a = eval_ast(left, scope, formula)
        b = eval_ast(right, scope, formula)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise FormulaError("division by zero", formula, pos)
            return a / b
        if op == "^":
            try:
                result = a ** b
            except (OverflowError, ValueError) as exc:
                raise FormulaError(f"{a} ^ {b} is undefined ({exc})",
                                   formula, pos) from None
            if isinstance(result, complex):
                raise FormulaError(
                    f"{a} ^ {b} is complex; openLCA formulas are real-valued",
                    formula, pos)
            return result
        raise FormulaError(f"unhandled operator {op!r}", formula, pos)

    if kind == "cmp":
        _, op, left, right, pos = node
        a = eval_ast(left, scope, formula)
        b = eval_ast(right, scope, formula)
        if op in ("=", "=="):
            return a == b
        if op in ("!=", "<>"):
            return a != b
        if op == "<":
            return a < b
        if op == ">":
            return a > b
        if op == "<=":
            return a <= b
        if op == ">=":
            return a >= b
        raise FormulaError(f"unhandled comparison {op!r}", formula, pos)

    if kind == "if":
        _, cond, then, other, _pos = node
        # Lazy: only the taken branch is evaluated, so `if(x != 0; a/x; 0)` --
        # the divide-guard idiom, 90 formulas in the surveyed corpus -- does not
        # raise on the guarded division.
        if _truthy(eval_ast(cond, scope, formula)):
            return eval_ast(then, scope, formula)
        return eval_ast(other, scope, formula)

    raise FormulaError(f"unhandled node {kind!r}", formula)


# =============================================================================
# INTROSPECTION
# =============================================================================
def describe_support(formulas):
    """Which of `formulas` this module can handle, as (ok, refused).

    `refused` maps formula text -> reason. Lets a caller size the gap on a new
    dataset without running a build, and gives the verification harness its
    "constructs refused" section.
    """
    ok = []
    refused = {}
    for f in formulas:
        try:
            parse(f)
        except FormulaError as exc:
            refused[f] = str(exc).splitlines()[0]
        else:
            ok.append(f)
    return ok, refused


def dialect_histogram(formulas):
    """Counter of operator/function tokens across `formulas`, for reporting."""
    hist = Counter()
    for f in formulas:
        try:
            toks = tokenize(f)
        except FormulaError:
            hist["<unlexable>"] += 1
            continue
        for idx, t in enumerate(toks):
            if t.kind == _OP:
                hist[t.value] += 1
            elif t.kind == _IDENT and toks[idx + 1].kind == _LPAR:
                hist[t.value.lower() + "()"] += 1
    return hist
