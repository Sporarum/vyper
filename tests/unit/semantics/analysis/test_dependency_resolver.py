import pytest

from vyper import ast as vy_ast
from vyper.ast import parse_to_ast
from vyper.compiler.input_bundle import FilesystemInputBundle
from vyper.semantics.analysis.dependency_resolver import Decl, compute_dependencies, extract_members
from vyper.semantics.analysis.imports import resolve_imports
from vyper.utils import OrderedSet


def _decl_name(decl: Decl) -> str:
    if isinstance(decl, vy_ast.VariableDecl):
        return decl.target.id
    return decl.name


def _deps_by_name(deps: dict) -> dict[str, list[str]]:
    return {_decl_name(k): sorted({_decl_name(d) for d in v}) for k, v in deps.items()}


def _run(source: str, input_bundle=None) -> dict[str, list[str]]:
    if input_bundle is None:
        input_bundle = FilesystemInputBundle([])
    module_ast = parse_to_ast(source)
    resolve_imports(module_ast, input_bundle)
    members = extract_members(module_ast)
    return _deps_by_name(compute_dependencies(module_ast, members[module_ast]))


PER_DECL_TYPE_CASES = [
    (
        """
struct Point:
    x: uint256
    y: uint256

@external
def foo(p: Point) -> Point:
    return p
""",
        {"Point": [], "foo": ["Point"]},
    ),
    (
        """
FOO: constant(uint256) = 42

@external
def foo(x: uint256 = FOO) -> uint256:
    return x
""",
        {"FOO": [], "foo": ["FOO"]},
    ),
    (
        """
struct S:
    a: uint256

FOO: uint256

@external
def foo() -> uint256:
    x: S = empty(S)
    return self.FOO + x.a
""",
        {"S": [], "FOO": [], "foo": []},
    ),
    (
        """
struct S:
    a: uint256

X: S
""",
        {"S": [], "X": ["S"]},
    ),
    (
        """
FOO: constant(uint256) = 42
BAR: constant(uint256) = FOO + 1
""",
        {"FOO": [], "BAR": ["FOO"]},
    ),
    (
        """
struct Inner:
    a: uint256

struct Outer:
    inner: Inner
    n: uint256
""",
        {"Inner": [], "Outer": ["Inner"]},
    ),
    (
        """
struct S:
    a: uint256

flag F:
    A
    B
""",
        {"S": [], "F": []},
    ),
    (
        """
flag F:
    A
    B

@external
def foo(x: uint256 = convert(F.A, uint256)) -> uint256:
    return x
""",
        {"F": [], "foo": ["F"]},
    ),
    (
        """
struct S:
    a: uint256

interface I:
    def foo(x: S) -> S: view
    def bar() -> uint256: nonpayable
""",
        {"S": [], "I": ["S"]},
    ),
    (
        """
struct S:
    a: uint256

event E:
    val: S
    n: uint256
""",
        {"S": [], "E": ["S"]},
    ),
    (
        """
struct S:
    a: uint256

error MyErr:
    val: S
""",
        {"S": [], "MyErr": ["S"]},
    ),
]


@pytest.mark.parametrize("source,expected", PER_DECL_TYPE_CASES)
def test_dependencies_per_decl_type(source, expected):
    assert _run(source) == expected


REFERENCE_SHAPE_CASES = [
    (
        """
struct S:
    a: uint256

X: DynArray[S, 10]
Y: HashMap[address, S]
""",
        {"S": [], "X": ["S"], "Y": ["S"]},
    ),
    (
        """
X: uint256
Y: address
Z: bytes32
""",
        {"X": [], "Y": [], "Z": []},
    ),
    (
        """
struct S:
    a: uint256

@external
def foo(x: S) -> S:
    return x
""",
        {"S": [], "foo": ["S"]},
    ),
]


@pytest.mark.parametrize("source,expected", REFERENCE_SHAPE_CASES)
def test_reference_shapes(source, expected):
    assert _run(source) == expected


def test_import_module_attribute(make_input_bundle):
    lib_src = """
struct LibStruct:
    val: uint256

FOO: constant(uint256) = 42
"""
    main_src = """
import lib

X: lib.LibStruct

@external
def foo() -> uint256:
    return lib.FOO

@external
def bar(x: uint256 = lib.FOO) -> uint256:
    return x
"""
    input_bundle = make_input_bundle({"lib.vy": lib_src})
    assert _run(main_src, input_bundle=input_bundle) == {
        "X": ["LibStruct"],
        "foo": [],  # body-only ref to lib.FOO is not captured
        "bar": ["FOO"],  # default value IS captured
    }


def test_attribute_on_non_import_is_no_dep(make_input_bundle):
    # self.X is an Attribute whose value is not an imported alias -> no dep.
    src = """
X: uint256

@external
def foo() -> uint256:
    return self.X
"""
    assert _run(src) == {"X": [], "foo": []}


def test_import_missing_member_produces_no_dep(make_input_bundle):
    lib_src = """
X: constant(uint256) = 1
"""
    main_src = """
import lib

@external
def foo() -> uint256:
    return 1

Y: uint256
"""
    input_bundle = make_input_bundle({"lib.vy": lib_src})
    assert _run(main_src, input_bundle=input_bundle) == {"foo": [], "Y": []}


def test_transitive_import_chained_attribute_is_a_dep(make_input_bundle):
    inner_src = """
FOO: constant(uint256) = 7
"""
    mid_src = """
import inner
"""
    main_src = """
import mid

X: constant(uint256) = mid.inner.FOO
"""
    input_bundle = make_input_bundle({"inner.vy": inner_src, "mid.vy": mid_src})
    assert _run(main_src, input_bundle=input_bundle) == {"X": ["FOO"]}


def test_returned_deps_are_the_actual_ast_nodes():
    # Guard against a future refactor returning copies or entries from the
    # wrong module: the values must be identity-equal to the source AST nodes.
    src = """
struct Point:
    x: uint256

X: Point

@external
def foo(p: Point) -> Point:
    return p
"""
    module_ast = parse_to_ast(src)
    resolve_imports(module_ast, FilesystemInputBundle([]))
    members = extract_members(module_ast)
    deps = compute_dependencies(module_ast, members[module_ast])

    (struct_def,) = module_ast.get_children(vy_ast.StructDef)
    (var_decl,) = module_ast.get_children(vy_ast.VariableDecl)
    (func_def,) = module_ast.get_children(vy_ast.FunctionDef)

    assert deps[struct_def] == OrderedSet()
    assert deps[var_decl] == OrderedSet([struct_def])
    assert deps[func_def] == OrderedSet([struct_def])
