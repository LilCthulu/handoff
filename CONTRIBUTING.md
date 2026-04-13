# Contributing to Handoff

Thanks for your interest in contributing to Handoff. This guide covers the process for contributing code, reporting bugs, and suggesting features.

## Getting Started

1. Fork the repository
2. Clone your fork and create a feature branch from `master`
3. Install development dependencies:

```bash
cd packages/server
pip install -e ".[dev]"
```

## Development Workflow

### Running Tests

```bash
# Server tests
cd packages/server
pytest tests/ -v

# SDK tests
cd packages/sdk
pip install -e ".[dev]"
pytest tests/ -v
```

### Code Style

We use [Ruff](https://github.com/astral-sh/ruff) for formatting and linting:

```bash
ruff format .
ruff check .
```

CI will reject PRs that fail format or lint checks.

### Commit Messages

- Use present tense ("Add feature" not "Added feature")
- Keep the first line under 72 characters
- Reference issues where applicable (`Fixes #123`)

## Submitting Changes

1. **Open an issue first** for anything beyond a trivial fix. This lets us discuss the approach before you invest time coding.
2. Keep PRs focused — one logical change per PR.
3. Add or update tests for any new functionality.
4. Ensure all tests pass locally before pushing.
5. Update documentation if your change affects the public API.

## Bug Reports

File an issue with:

- Steps to reproduce
- Expected vs. actual behavior
- Python version, OS, and database backend (PostgreSQL/SQLite)
- Server logs if applicable

## Feature Requests

Open an issue describing:

- The problem you're trying to solve
- Your proposed solution
- Alternatives you've considered

## Project Structure

```
packages/server/    # FastAPI backend (protocol core)
packages/sdk/       # Python SDK
packages/demo/      # Demo agents
protocol/           # Protocol spec and JSON schemas
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
