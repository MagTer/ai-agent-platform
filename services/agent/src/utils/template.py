import re


def substitute_variables(template: str, args: list[str]) -> str:
    """
    Substitute variables in a template string with provided arguments.

    Supported variables:
    - $N: Positional arguments (1-based index), e.g., $1, $2.
    - $ARGUMENTS: The entire argument string (rest of line if conceptually joined).
    - \$: Escaped dollar sign.

    Args:
        template: The markdown template string.
        args: A list of string arguments.

    Returns:
        The template string with variables substituted.

    Raises:
        ValueError: If a required positional argument ($N) is missing.
    """

    def replacer(match: re.Match) -> str:
        token = match.group(0)

        # Handle escaped dollar sign
        if token == "\\$":  # noqa: S105
            return "$"

        # Handle $ARGUMENTS
        if token == "$ARGUMENTS":  # noqa: S105
            return " ".join(args)

        # Handle positional arguments $N
        if token.startswith("$") and token[1:].isdigit():
            idx = int(token[1:]) - 1
            if 0 <= idx < len(args):
                return args[idx]
            else:
                raise ValueError(f"Missing argument for {token}")

        return token

    # Regex to match \$ (escaped), $ARGUMENTS, or $N (positional)
    # We use negative lookbehind/lookahead if needed, but simple matching covers most cases
    # The pattern matches:
    # 1. \\$ (Literal escaped dollar)
    # 2. \$ARGUMENTS (The specific keyword)
    # 3. \$\d+ (Positional args)
    pattern = r"\\\$|\$ARGUMENTS|\$\d+"

    try:
        return re.sub(pattern, replacer, template)
    except ValueError as e:
        # Re-raise with more context if needed, or let it bubble up
        raise e
