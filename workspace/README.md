# Greeter Package

A tiny package that offers a friendly greeting function.

## Requirements

Install the runtime dependency for the tests with:

```bash
pip install -r requirements.txt
```

## Usage

Import the `greet` helper and call it with a name:

```python
from app.greeter import greet

print(greet("Alice"))  # Hello, Alice!
```

If an empty string or whitespace is provided, the function falls back to a default greeting.

## Testing

Run the pytest suite with:

```bash
python -m pytest -q
```
