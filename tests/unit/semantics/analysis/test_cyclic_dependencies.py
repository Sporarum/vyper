import pytest

from tests.utils import analyze_module_single
from vyper.ast import parse_to_ast
from vyper.exceptions import NamespaceCollision, StructureException

fail_list = [
    (
        """
struct S:
    x: S
    """,
        "S -> S",
    ),
    (
        """
struct A:
    b: B

struct B:
    a: A
    """,
        "A -> B -> A",
    ),
    (
        """
struct A:
    b: B

struct B:
    c: C

struct C:
    a: A
    """,
        "A -> B -> C -> A",
    ),
    (
        """
X: constant(uint256) = X
    """,
        "X -> X",
    ),
    (
        """
X: constant(uint256) = Y
Y: constant(uint256) = X
    """,
        "X -> Y -> X",
    ),
    (
        """
X: constant(uint256) = Y
Y: constant(uint256) = Z
Z: constant(uint256) = X
    """,
        "X -> Y -> Z -> X",
    ),
    (
        """
interface Foo:
    def f() -> Foo: view
    """,
        "Foo -> Foo",
    ),
    (
        """
interface Foo:
    def f(x: Foo): nonpayable
    """,
        "Foo -> Foo",
    ),
    (
        """
interface A:
    def f() -> B: view

interface B:
    def g() -> A: view
    """,
        "A -> B -> A",
    ),
    (
        """
struct S:
    f: Foo

interface Foo:
    def f() -> S: view
    """,
        "S -> Foo -> S",
    ),
    (
        """
struct S:
    x: E

event E:
    s: S
    """,
        "S -> E -> S",
    ),
    (
        """
struct S:
    arr: DynArray[uint256, N]

N: constant(uint256) = S(arr=[]).arr[0]
    """,
        "S -> N -> S",
    ),
    (
        """
struct S:
    f: Foo

interface Foo:
    def f() -> E: view

event E:
    s: S
    """,
        "S -> Foo -> E -> S",
    ),
]


@pytest.mark.parametrize("bad_code,expected_cycle", fail_list)
def test_cyclic_dependency(bad_code, expected_cycle):
    vyper_module = parse_to_ast(bad_code)
    with pytest.raises(StructureException) as e:
        analyze_module_single(vyper_module)

    path = vyper_module.path
    assert e.value.message == f"Cyclic dependency in module {path}: {expected_cycle}"
    assert e.value.hint is None


# TODO: Fix this
@pytest.mark.xfail()
def test_function_name_shadows_struct():
    code = """
struct Foo:
    x: uint256

@external
def Foo(a: Foo) -> uint256:
    return a.x
    """
    vyper_module = parse_to_ast(code)
    with pytest.raises(NamespaceCollision) as e:
        analyze_module_single(vyper_module)

    assert e.value.message == "multiple functions, events, or errors named 'Foo'!"
