"""Parser combinators for Mudyla definitions using pyparsing."""

from pyparsing import (
    Word,
    alphanums,
    alphas,
    nums,
    Literal,
    Optional,
    Regex,
    Suppress,
    ZeroOrMore,
    oneOf,
    restOfLine,
)

from ..ast.types import ReturnType


class MudylaGrammar:
    """Parser combinators for Mudyla markdown definitions."""

    def __init__(self):
        # Common patterns
        self.identifier = Word(alphas + "_", alphanums + "_-")
        self.dot_separated = Word(alphas + "_", alphanums + "_-.")
        self.uppercase_identifier = Word(alphas.upper() + "_", alphas.upper() + nums + "_")

        backtick = Suppress("`")
        colon = Suppress(":")
        equals = Suppress("=")

        # Flag definition: - `flags.name`: description
        # Example: - `flags.verbose`: Enable verbose output
        flags_prefix = Suppress("flags.")

        flag_name = self.identifier
        flag_description = restOfLine

        self.flag_def = (
            Suppress("-") +
            backtick + flags_prefix + flag_name("name") + backtick +
            colon +
            flag_description("description")
        )

        # Axis definition: - `axis-name`=`{value1|value2*|value3}`
        # Example: - `build-mode`=`{release|development*}`
        pipe = Suppress("|")
        lbrace = Suppress("{")
        rbrace = Suppress("}")

        axis_name = self.identifier
        axis_value = self.identifier + Optional(Literal("*"))("default_marker")
        axis_values = lbrace + axis_value + ZeroOrMore(pipe + axis_value) + rbrace

        self.axis_def = (
            Suppress("-") +
            backtick + axis_name("name") + backtick +
            Suppress("=") +
            backtick + axis_values("values") + backtick
        )

        # Environment var with value: - `VARIABLE_NAME=value`
        # Example: - `LANG=C.UTF-8`
        env_value = Regex(r"[^`]+")
        self.environment_def = (
            Suppress("-") +
            backtick +
            self.uppercase_identifier("var_name") +
            Suppress("=") +
            env_value("value") +
            backtick
        )

        # Passthrough env var: - `VARIABLE_NAME`
        # Example: - `HOME`
        self.passthrough_def = (
            Suppress("-") +
            backtick + self.uppercase_identifier("var_name") + backtick
        )

        # Vars definition: - `VARIABLE_NAME`: description
        # Example: - `JAVA_HOME`: path to jdk
        self.vars_def = (
            Suppress("-") +
            backtick + self.uppercase_identifier("var_name") + backtick +
            colon +
            restOfLine("description")
        )

        # Return declaration: ret name:type=value
        # Example: ret compiler-binary:file=/path/to/binary
        ret_keyword = Suppress("ret")
        ret_name = self.identifier
        ret_type = oneOf(["int", "string", "bool", "file", "directory"], caseless=True)
        ret_value = restOfLine

        self.return_decl = (
            ret_keyword +
            ret_name("name") +
            colon +
            ret_type("type") +
            equals +
            ret_value("value")
        )

        # Expansion patterns: ${prefix.rest}
        # Examples: ${sys.project-root}, ${action.build.output}, ${env.HOME}
        dollar_brace = Suppress("${")
        close_brace = Suppress("}")

        expansion_prefix = oneOf(["sys", "action", "env", "args", "flags"])
        expansion_rest = self.dot_separated

        self.expansion = (
            dollar_brace +
            expansion_prefix("prefix") +
            Suppress(".") +
            expansion_rest("rest") +
            close_brace
        )


GRAMMAR = MudylaGrammar()


def parse_flag_definition(line: str) -> dict:
    """Parse flag definition line.

    Args:
        line: Line containing flag definition

    Returns:
        Dict with keys: name, description
    """
    grammar = GRAMMAR
    try:
        result = grammar.flag_def.parseString(line, parseAll=True)
        return {
            "name": result.name,
            "description": result.description.strip(),
        }
    except Exception:
        return None


def parse_axis_definition(line: str) -> dict:
    """Parse axis definition line.

    Args:
        line: Line containing axis definition

    Returns:
        Dict with keys: name, values (list of dicts with 'value' and 'is_default')
    """
    grammar = GRAMMAR
    try:
        result = grammar.axis_def.parseString(line, parseAll=True)
        # Access named result using dict notation to avoid conflict with .values() method
        # Convert to list to use list methods
        values_list = list(result["values"])
        values = []
        for i, token in enumerate(values_list):
            if isinstance(token, str) and token != "*":
                is_default = False
                # Check if next token is *
                if i + 1 < len(values_list) and values_list[i + 1] == "*":
                    is_default = True
                values.append({"value": token, "is_default": is_default})

        return {
            "name": result.name,
            "values": values,
        }
    except Exception:
        return None


def parse_environment_definition(line: str) -> dict:
    """Parse environment variable definition with value.

    Args:
        line: Line containing environment definition

    Returns:
        Dict with var_name and value, or None
    """
    grammar = GRAMMAR
    try:
        result = grammar.environment_def.parseString(line, parseAll=True)
        return {
            "var_name": result.var_name,
            "value": result.value,
        }
    except Exception:
        return None


def parse_passthrough_definition(line: str) -> str:
    """Parse passthrough environment variable definition.

    Args:
        line: Line containing passthrough definition

    Returns:
        Variable name or None
    """
    grammar = GRAMMAR
    try:
        result = grammar.passthrough_def.parseString(line, parseAll=True)
        return result.var_name
    except Exception:
        return None


def parse_vars_definition(line: str) -> dict:
    """Parse vars definition line.

    Args:
        line: Line containing vars definition

    Returns:
        Dict with keys: var_name, description
    """
    grammar = GRAMMAR
    try:
        result = grammar.vars_def.parseString(line, parseAll=True)
        return {
            "var_name": result.var_name,
            "description": result.description.strip(),
        }
    except Exception:
        return None


def parse_return_declaration(line: str) -> dict:
    """Parse return declaration line.

    Args:
        line: Line containing return declaration

    Returns:
        Dict with keys: name, type, value
    """
    grammar = GRAMMAR
    try:
        result = grammar.return_decl.parseString(line, parseAll=True)
        return {
            "name": result.name,
            "type": result.type.lower(),
            "value": result.value.strip(),
        }
    except Exception:
        return None


def find_expansions(script: str) -> list[dict]:
    """Find all expansions in a script.

    Args:
        script: Bash script content

    Returns:
        List of dicts with keys: original, prefix, rest
    """
    grammar = GRAMMAR
    expansions = []

    for match, start, end in grammar.expansion.scanString(script):
        expansions.append({
            "original": script[start:end],
            "prefix": match.prefix,
            "rest": match.rest,
        })

    return expansions
