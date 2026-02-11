# Bidirectional Type Checker Implementation Plan

## Overview

Implement a bidirectional type checker for Vyper using the `NodeAccumulator` pattern. The system operates as a **parallel type checker** alongside the existing analysis, providing a principled foundation based on the Pfenning recipe (Dunfield & Krishnaswami).

## Design Principles

1. **Bidirectional Typing**: Two modes - checking (type is input) and synthesis (type is output)
2. **Pfenning Recipe**: Introduction forms check, elimination forms synthesize, variables synthesize, subsumption bridges modes
3. **Fully Stateless**: Uses immutable accumulator pattern from `NodeAccumulator[Res]`
4. **Two-Pass Architecture**: Pass 1 generates constraints, Pass 2 solves via unification
5. **Function-by-function**: Each function analyzed independently with cross-function call constraints

## File Organization

```
vyper/semantics/analysis/bidir/
    __init__.py           # Public API
    types.py              # Mode, TypeVar, Constraint, BidirAccumulator, TypingContext
    unify.py              # Substitution, UnificationResult
    checker.py            # BidirExprChecker, BidirStmtChecker
    pass1.py              # analyze_function_bidir, analyze_module_bidir
    pass2.py              # solve_constraints, verify_module_bidir
```

## Data Structures

### types.py

```python
class Mode(Enum):
    CHECK = auto()   # Type is input
    SYNTH = auto()   # Type is output

@dataclass(frozen=True)
class TypeVar:
    """Type variable for inference/unification."""
    id: int
    name: str = ""

MonoType = Union[VyperType, TypeVar]

@dataclass(frozen=True)
class SubtypeConstraint(Constraint):
    """lhs <: rhs"""
    lhs: MonoType
    rhs: MonoType

@dataclass(frozen=True)
class EqualityConstraint(Constraint):
    """lhs = rhs"""
    lhs: MonoType
    rhs: MonoType

@dataclass(frozen=True)
class CallConstraint(Constraint):
    """Cross-function call constraint for Pass 2 verification."""
    func_name: str
    arg_types: tuple[MonoType, ...]
    return_type: MonoType

@dataclass(frozen=True)
class TypingContext:
    """Immutable context (Gamma) mapping names to types."""
    bindings: dict[str, MonoType]

    def extend(self, name: str, typ: MonoType) -> "TypingContext": ...
    def lookup(self, name: str) -> Optional[MonoType]: ...

@dataclass(frozen=True)
class BidirAccumulator:
    """Immutable accumulator state threaded through NodeAccumulator."""
    context: TypingContext
    return_type: Optional[VyperType]  # tau_r for return checking
    mutability: StateMutability       # m for state access
    constraints: frozenset[Constraint]
    next_typevar_id: int
    type_assignments: dict[int, MonoType]  # node_id -> type

    # Immutable update methods
    def with_context(self, ctx) -> "BidirAccumulator": ...
    def with_constraint(self, c) -> "BidirAccumulator": ...
    def with_type_assignment(self, node_id, typ) -> "BidirAccumulator": ...
    def fresh_typevar(self, name="") -> tuple["BidirAccumulator", TypeVar]: ...
```

### unify.py

```python
@dataclass
class Substitution:
    """Mutable substitution for unification (Pass 2)."""
    mapping: dict[TypeVar, MonoType]

    def apply(self, typ: MonoType) -> MonoType: ...
    def unify(self, t1: MonoType, t2: MonoType) -> bool: ...
    def check_subtype(self, sub: MonoType, sup: MonoType) -> bool: ...

@dataclass
class UnificationResult:
    success: bool
    substitution: Optional[Substitution]
    errors: list[tuple[Constraint, str]]
```

## NodeAccumulator Classes

### BidirExprChecker

```python
class BidirExprChecker(NodeAccumulator[tuple[BidirAccumulator, MonoType]]):
    """
    Bidirectional type checker for expressions.
    Returns (accumulator, type) tuple.
    """
    def __init__(self, expected_type: Optional[MonoType] = None):
        self.expected_type = expected_type  # None = synth, Some = check
```

**Expression Visitor Methods:**

| AST Node | Rule | Mode | Implementation |
|----------|------|------|----------------|
| `Name` | Var-Synth | Synth | Lookup in context, subsumption if checking |
| `Constant` | IntLit-Check, etc. | Check | Validate literal against expected type |
| `BinOp` | BinArith-Synth | Synth | Synth left, check right against left's type |
| `Compare` | NumCmp-Synth | Synth | Synth both, return `BoolT()` |
| `BoolOp` | And/Or-Synth | Synth | Check operands as bool, return `BoolT()` |
| `UnaryOp` | Neg/Not-Synth | Synth | Synth operand (or check bool for `not`) |
| `Subscript` | SArrayIdx-Synth | Synth | Synth base, determine result type |
| `Attribute` | StructAttr-Synth | Synth | Synth base, lookup member |
| `Call` | InternalCall-Synth | Synth | Synth func, check args, add CallConstraint |
| `IfExp` | IfExp-Check/Synth | Both | Check: check branches; Synth: synth first, check second |
| `List` | ArrayLit-Check | Check | Check elements against element type |
| `Tuple` | Tuple-Check | Check | Check elements against member types |

**Subsumption Method:**

```python
def _apply_subsumption(self, acc, synth_type, node_id):
    """Bridge synth to check mode via subtyping constraint."""
    if self.expected_type is not None:
        acc = acc.with_constraint(SubtypeConstraint(node_id, synth_type, self.expected_type))
        return acc, self.expected_type
    return acc, synth_type
```

### BidirStmtChecker

```python
class BidirStmtChecker(NodeAccumulator[BidirAccumulator]):
    """All statements check against None."""
```

| AST Node | Rule | Implementation |
|----------|------|----------------|
| `AnnAssign` | VarDecl-Check | Check init, extend context |
| `Assign` | Assign-Check | Synth target, check value |
| `AugAssign` | AugAssign-Check | Synth target, check value |
| `If` | If-Check | Check test as bool, check branches |
| `For` | ForRange-Check | Extend context with loop var, check body |
| `Return` | Return-Check | Check value against return_type |
| `Assert` | Assert-Check | Check test as bool |
| `Log` | Log-Check | Check args against event fields |
| `Expr` | - | Synth expression |

## Two-Pass Architecture

### Pass 1: Constraint Generation (pass1.py)

```python
@dataclass
class FunctionAnalysisResult:
    func_name: str
    constraints: frozenset[Constraint]
    type_assignments: dict[int, MonoType]

@dataclass
class ModuleAnalysisResult:
    function_results: dict[str, FunctionAnalysisResult]
    all_constraints: frozenset[Constraint]

def analyze_function_bidir(fn_node: FunctionDef) -> FunctionAnalysisResult:
    """Analyze one function, generating constraints."""
    # Build context from parameters
    # Create BidirAccumulator with return_type, mutability
    # Visit each statement with BidirStmtChecker
    # Return constraints and type assignments

def analyze_module_bidir(module: Module) -> ModuleAnalysisResult:
    """Analyze all functions in module."""
```

### Pass 2: Constraint Solving (pass2.py)

```python
def solve_constraints(analysis: ModuleAnalysisResult) -> UnificationResult:
    """Solve constraints via unification."""
    state = SolverState()

    # Sort: equality constraints first, then subtyping
    for constraint in sorted_constraints:
        state.solve_constraint(constraint)

    return UnificationResult(success, substitution, errors)

def verify_module_bidir(module: Module) -> list[tuple[Constraint, str]]:
    """Full bidirectional type checking."""
    analysis = analyze_module_bidir(module)
    result = solve_constraints(analysis)
    return result.errors
```

## Implementation Order

1. **types.py**: Core data structures (TypeVar, Constraint types, TypingContext, BidirAccumulator)
2. **unify.py**: Substitution and unification algorithm
3. **checker.py**: BidirExprChecker and BidirStmtChecker with visitor methods
4. **pass1.py**: Function and module analysis
5. **pass2.py**: Constraint solving
6. **__init__.py**: Public API

## Typing Rules Reference

| Category | Direction | Examples |
|----------|-----------|----------|
| Variables | Synthesize | `x`, state variables |
| Literals | Check | `True`, `42`, `"hello"` |
| Composite Literals | Check | `[e1, ...]`, `(e1, e2)`, `S(...)` |
| Operations | Synthesize | `e1 + e2`, `not e` |
| Subscript/Attribute | Synthesize | `e[i]`, `e.f` |
| Function Calls | Synthesize | `f(e1, ...)` |
| Type Annotations | Synthesize | `convert`, `empty` |
| Conditional Expr | Both | `e1 if c else e2` |
| Statements | Check None | assignments, control flow |

## Testing

Create unit tests in `tests/unit/semantics/analysis/test_bidir.py`:

```python
# Test categories:
# 1. TypeVar and Substitution
# 2. Constraint generation
# 3. Unification algorithm
# 4. Expression synthesis/checking
# 5. Statement checking
# 6. Full function analysis
# 7. Cross-function constraint verification
```

**Test Cases:**
- Literal checking: `42` checks against `uint256`, fails against `bool`
- Variable synthesis: `x` synthesizes type from context
- Binary ops: `x + y` synthesizes type of `x`, checks `y` against it
- Subsumption: synth type is subtype of expected type
- Function calls: generates CallConstraint, args checked against params
- Unification: TypeVar resolves to concrete type
- Error cases: type mismatches produce constraint errors

## Critical Files

- `vyper/semantics/analysis/common.py` - NodeAccumulator pattern
- `vyper/semantics/types/base.py` - VyperType, is_subtype_of()
- `vyper/semantics/analysis/local.py` - Pattern reference
- `docs/vyper_bidirectional_typing.tex` - Formal specification
