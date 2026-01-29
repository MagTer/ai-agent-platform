import pytest
from utils.template import substitute_variables


def test_basic_substitution():
    template = "Hello $1, welcome to $2."
    args = ["Alice", "Wonderland"]
    result = substitute_variables(template, args)
    assert result == "Hello Alice, welcome to Wonderland."


def test_arguments_substitution():
    template = "Command: $ARGUMENTS"
    args = ["run", "fast", "--verbose"]
    result = substitute_variables(template, args)
    assert result == "Command: run fast --verbose"


def test_mixed_substitution():
    template = "First: $1, Rest: $ARGUMENTS"
    args = ["one", "two", "three"]
    result = substitute_variables(template, args)
    assert result == "First: one, Rest: one two three"


def test_escaped_dollar():
    template = r"Cost is \$100. Pay to $1."
    args = ["Bob"]
    result = substitute_variables(template, args)
    assert result == "Cost is $100. Pay to Bob."


def test_missing_argument_error():
    template = "Hello $1, meet $2."
    args = ["Alice"]
    with pytest.raises(ValueError):
        substitute_variables(template, args)


def test_unused_arguments_ignored():
    template = "Hello $1."
    args = ["Alice", "Bob"]
    result = substitute_variables(template, args)
    assert result == "Hello Alice."


def test_no_variables():
    template = "Hello World."
    args = ["Alice"]
    result = substitute_variables(template, args)
    assert result == "Hello World."
