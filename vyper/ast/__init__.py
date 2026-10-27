"""
isort:skip_file
"""

import sys

from . import nodes, validation
from .natspec import parse_natspec
from .nodes import as_tuple
from .utils import ast_to_dict
from .parse import parse_to_ast
from typing import TypeAlias

# adds vyper.ast.nodes classes into the local namespace
for name, obj in (
    (k, v) for k, v in nodes.__dict__.items() if type(v) is type and nodes.VyperNode in v.__mro__
):
    setattr(sys.modules[__name__], name, obj)

Decl: TypeAlias = (
    nodes.FunctionDef
    | nodes.VariableDecl
    | nodes.StructDef
    | nodes.FlagDef
    | nodes.InterfaceDef
    | nodes.EventDef
    | nodes.ErrorDef
)
"""
Members of a module which create new analysis targets
"""
