from app.greeter import greet


def test_greet_with_name():
    assert greet("Alice") == "Hello, Alice!"


def test_greet_with_whitespace_name():
    assert greet("  Bob  ") == "Hello, Bob!"


def test_greet_with_empty_name():
    assert greet("") == "Hello, stranger!"


def test_greet_with_only_spaces():
    assert greet("   ") == "Hello, stranger!"
