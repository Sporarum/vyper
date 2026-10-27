import ast as python_ast
from typing import Any, Optional, TypeAlias, Union

from . import nodes, validation
from .natspec import parse_natspec as parse_natspec
from .nodes import *
from .parse import parse_to_ast as parse_to_ast
from .utils import ast_to_dict as ast_to_dict

Decl: TypeAlias = (
    nodes.FunctionDef
    | nodes.VariableDecl
    | nodes.StructDef
    | nodes.FlagDef
    | nodes.InterfaceDef
    | nodes.EventDef
    | nodes.ErrorDef
)
