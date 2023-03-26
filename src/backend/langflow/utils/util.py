import ast
import importlib
import inspect
import re
from typing import Dict, Optional
import types

from langchain.agents.load_tools import (
    _BASE_TOOLS,
    _EXTRA_LLM_TOOLS,
    _EXTRA_OPTIONAL_TOOLS,
    _LLM_TOOLS,
)


from langchain.agents.tools import Tool
from langchain.tools import BaseTool

from langflow.utils import constants


def build_template_from_function(
    name: str, type_to_loader_dict: Dict, add_function: bool = False
):
    classes = [
        item.__annotations__["return"].__name__ for item in type_to_loader_dict.values()
    ]

    # Raise error if name is not in chains
    if name not in classes:
        raise ValueError(f"{name} not found")

    for _type, v in type_to_loader_dict.items():
        if v.__annotations__["return"].__name__ == name:
            _class = v.__annotations__["return"]

            docs = get_class_doc(_class)

            variables = {"_type": _type}
            for class_field_items, value in _class.__fields__.items():
                if class_field_items in ["callback_manager", "requests_wrapper"]:
                    continue
                variables[class_field_items] = {}
                for name_, value_ in value.__repr_args__():
                    if name_ == "default_factory":
                        try:
                            variables[class_field_items][
                                "default"
                            ] = get_default_factory(
                                module=_class.__base__.__module__, function=value_
                            )
                        except Exception:
                            variables[class_field_items]["default"] = None
                    elif name_ not in ["name"]:
                        variables[class_field_items][name_] = value_

                variables[class_field_items]["placeholder"] = (
                    docs["Attributes"][class_field_items]
                    if class_field_items in docs["Attributes"]
                    else ""
                )
            # Adding function to base classes to allow
            # the output to be a function
            base_classes = get_base_classes(_class)
            if add_function:
                base_classes.append("function")

            return {
                "template": format_dict(variables, name),
                "description": docs["Description"],
                "base_classes": get_base_classes(_class),
            }


def build_template_from_class(
    name: str, type_to_cls_dict: Dict, add_function: bool = False
):
    classes = [item.__name__ for item in type_to_cls_dict.values()]

    # Raise error if name is not in chains
    if name not in classes:
        raise ValueError(f"{name} not found.")

    for _type, v in type_to_cls_dict.items():
        if v.__name__ == name:
            _class = v

            # Get the docstring
            docs = get_class_doc(_class)

            variables = {"_type": _type}
            for class_field_items, value in _class.__fields__.items():
                if class_field_items in ["callback_manager"]:
                    continue
                variables[class_field_items] = {}
                for name_, value_ in value.__repr_args__():
                    if name_ == "default_factory":
                        try:
                            variables[class_field_items][
                                "default"
                            ] = get_default_factory(
                                module=_class.__base__.__module__, function=value_
                            )
                        except Exception:
                            variables[class_field_items]["default"] = None
                    elif name_ not in ["name"]:
                        variables[class_field_items][name_] = value_

                variables[class_field_items]["placeholder"] = (
                    docs["Attributes"][class_field_items]
                    if class_field_items in docs["Attributes"]
                    else ""
                )
            base_classes = get_base_classes(_class)
            # Adding function to base classes to allow
            # the output to be a function
            if add_function:
                base_classes.append("function")
            return {
                "template": format_dict(variables, name),
                "description": docs["Description"],
                "base_classes": base_classes,
            }


def eval_function(function_string: str):
    # Create an empty dictionary to serve as a separate namespace
    namespace: Dict = {}

    # Execute the code string in the new namespace
    exec(function_string, namespace)
    function_object = next(
        (obj for name, obj in namespace.items() if isinstance(obj, types.FunctionType)),
        None,
    )
    if function_object is None:
        raise ValueError("Function string does not contain a function")
    return function_object


def get_base_classes(cls):
    """Get the base classes of a class.
    These are used to determine the output of the nodes.
    """
    bases = cls.__bases__
    if not bases:
        return []
    else:
        result = []
        for base in bases:
            if any(type in base.__module__ for type in ["pydantic", "abc"]):
                continue
            result.append(base.__name__)
            result.extend(get_base_classes(base))
        return result


def get_default_factory(module: str, function: str):
    pattern = r"<function (\w+)>"

    if match := re.search(pattern, function):
        imported_module = importlib.import_module(module)
        return getattr(imported_module, match[1])()
    return None


class GenericTool(Tool):
    """Base class for all tools."""

    def default_func(self, **kwargs):
        """Default function for the tool."""
        return "Default function"

    def __init__(
        self,
        name: str = "Tool name",
        description: str = "Tool description",
        func: callable = None,
    ):
        """Initialize the tool."""
        super().__init__(name=name, description=description, func=func)


def get_base_tool(name, description, func: callable) -> BaseTool:
    return GenericTool(func=func, name="Generic Tool", description="Bacon")


def get_tools_dict(name: Optional[str] = None):
    """Get the tools dictionary."""
    tools = {
        **_BASE_TOOLS,
        **_LLM_TOOLS,  # type: ignore
        **{k: v[0] for k, v in _EXTRA_LLM_TOOLS.items()},  # type: ignore
        **{k: v[0] for k, v in _EXTRA_OPTIONAL_TOOLS.items()},
    }
    tools.update({"BaseTool": get_base_tool})
    return tools[name] if name else tools


def get_tool_params(func, **kwargs):
    # Parse the function code into an abstract syntax tree

    tree = ast.parse(inspect.getsource(func))

    # Iterate over the statements in the abstract syntax tree
    for node in ast.walk(tree):
        # Find the first return statement
        if isinstance(node, ast.Return):
            tool = node.value
            if isinstance(tool, ast.Call):
                if tool.func.id == "Tool":
                    if tool.keywords:
                        tool_params = {}
                        for keyword in tool.keywords:
                            if keyword.arg == "name":
                                tool_params["name"] = ast.literal_eval(keyword.value)
                            elif keyword.arg == "description":
                                tool_params["description"] = ast.literal_eval(
                                    keyword.value
                                )

                        return tool_params
                    return {
                        "name": ast.literal_eval(tool.args[0]),
                        "description": ast.literal_eval(tool.args[2]),
                    }
                else:
                    # get the class object from the return statement
                    try:
                        class_obj = eval(
                            compile(ast.Expression(tool), "<string>", "eval")
                        )
                    except Exception:
                        return None

                    return {
                        "name": getattr(class_obj, "name"),
                        "description": getattr(class_obj, "description"),
                    }

    # Return None if no return statement was found
    return None


def get_class_doc(class_name):
    """
    Extracts information from the docstring of a given class.

    Args:
        class_name: the class to extract information from

    Returns:
        A dictionary containing the extracted information, with keys
        for 'Description', 'Parameters', 'Attributes', and 'Returns'.
    """
    # Template
    data = {
        "Description": "",
        "Parameters": {},
        "Attributes": {},
        "Example": [],
        "Returns": {},
    }

    # Get the class docstring
    docstring = class_name.__doc__

    if not docstring:
        return data

    # Parse the docstring to extract information
    lines = docstring.split("\n")

    current_section = "Description"

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if (
            line.startswith(tuple(data.keys()))
            and len(line.split()) == 1
            and line.endswith(":")
        ):
            current_section = line[:-1]
            continue

        if current_section in ["Description", "Example"]:
            data[current_section] += line
        else:
            param, desc = line.split(":")
            data[current_section][param.strip()] = desc.strip()

    return data


def format_dict(d, name: Optional[str] = None):
    """
    Formats a dictionary by removing certain keys and modifying the
    values of other keys.

    Args:
        d: the dictionary to format
        name: the name of the class to format

    Returns:
        A new dictionary with the desired modifications applied.
    """

    # Process remaining keys
    for key, value in d.items():
        if key == "_type":
            continue

        _type = value["type"]

        # Remove 'Optional' wrapper
        if "Optional" in _type:
            _type = _type.replace("Optional[", "")[:-1]

        # Check for list type
        if "List" in _type:
            _type = _type.replace("List[", "")[:-1]
            value["list"] = True
        else:
            value["list"] = False

        # Replace 'Mapping' with 'dict'
        if "Mapping" in _type:
            _type = _type.replace("Mapping", "dict")

        # Change type from str to Tool
        value["type"] = "Tool" if key in ["allowed_tools", "func"] else _type

        # Show or not field
        value["show"] = bool(
            (value["required"] and key not in ["input_variables"])
            or key
            in [
                "allowed_tools",
                "memory",
                "prefix",
                "examples",
                "temperature",
                "model_name",
            ]
            or "api_key" in key
        )

        # Add password field
        value["password"] = any(
            text in key for text in ["password", "token", "api", "key"]
        )

        # Add multline
        value["multiline"] = key in ["suffix", "prefix", "template", "examples"]

        # Replace default value with actual value
        if "default" in value:
            value["value"] = value["default"]
            value.pop("default")

        # Add options to openai
        if name == "OpenAI" and key == "model_name":
            value["options"] = constants.OPENAI_MODELS
        elif name == "OpenAIChat" and key == "model_name":
            value["options"] = constants.CHAT_OPENAI_MODELS

    return d


def update_verbose(d: dict, new_value: bool) -> dict:
    """
    Recursively updates the value of the 'verbose' key in a dictionary.

    Args:
        d: the dictionary to update
        new_value: the new value to set

    Returns:
        The updated dictionary.
    """

    for k, v in d.items():
        if isinstance(v, dict):
            update_verbose(v, new_value)
        elif k == "verbose":
            d[k] = new_value
    return d
