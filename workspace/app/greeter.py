def greet(name: str) -> str:
    """Return a friendly greeting message for the provided name."""
    normalized = name.strip()
    if not normalized:
        return "Hello, stranger!"
    return f"Hello, {normalized}!"
