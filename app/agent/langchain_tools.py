"""Adapters that expose the project's business tools through LangChain.

The business implementations stay in :mod:`app.agent.tools`; this module only
translates their parameter metadata and preserves the existing retry contract.
"""
from __future__ import annotations

from typing import Any, Dict, Type

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from app.agent.tools import BaseTool, ToolExecutionResult, get_all_tools


_PYTHON_TYPES: Dict[str, Type[Any]] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _args_schema(tool: BaseTool) -> Type[BaseModel]:
    """Build a Pydantic schema from the existing tool metadata."""
    fields: Dict[str, Any] = {}
    for parameter in tool.parameters:
        python_type = _PYTHON_TYPES.get(parameter.get("type", "string"), str)
        required = parameter.get("required", True)
        default = ... if required and parameter.get("default") is None else parameter.get("default")
        fields[parameter["name"]] = (
            python_type,
            Field(default=default, description=parameter.get("description", "")),
        )
    return create_model(f"{tool.name.title().replace('_', '')}Args", **fields)


def _adapt_tool(tool: BaseTool) -> StructuredTool:
    async def execute(**kwargs: Any) -> Dict[str, Any]:
        result: ToolExecutionResult = await tool.execute_with_retry(**kwargs)
        return result.model_dump()

    return StructuredTool.from_function(
        name=tool.name,
        description=tool.description,
        coroutine=execute,
        args_schema=_args_schema(tool),
    )


def build_langchain_tools() -> Dict[str, StructuredTool]:
    """Return all registered business tools keyed by their stable name."""
    return {tool.name: _adapt_tool(tool) for tool in get_all_tools()}


def get_langchain_tool(name: str) -> StructuredTool | None:
    """Look up one adapted tool without changing the existing registry API."""
    return build_langchain_tools().get(name)

