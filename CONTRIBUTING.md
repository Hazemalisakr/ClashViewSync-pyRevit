# Contributing to ClashView Sync

Thank you for your interest in contributing to **ClashView Sync**! We welcome bug reports, feature requests, documentation improvements, and code contributions from the BIM and computational design community.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free environment for all contributors. Please be respectful and constructive in all issues, pull requests, and discussions.

---

## How Can I Contribute?

### 1. Reporting Bugs

Before creating a bug report, please check existing [Issues](https://github.com/Hazemalisakr/ClashViewSync-pyRevit/issues) to see if it has already been reported.

When filing an issue, please include:
- **Autodesk Revit Version** (e.g. 2024.3)
- **pyRevit Version** (e.g. 4.8.16)
- **Navisworks Version & Report Format** (must be Clash Detective XML)
- **Steps to Reproduce** the issue
- **Observed vs Expected Behavior**
- **Error Messages or Screenshots**

### 2. Suggesting Enhancements

Feature requests are very welcome! Please describe:
- The problem you are trying to solve.
- Your proposed solution or workflow improvement.
- Any relevant context (e.g., specific MEP discipline coordination, IFC links, workshared cloud models).

### 3. Submitting Pull Requests

1. **Fork the Repository** and create your branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. **Make Your Changes**:
   - Keep code clean, readable, and well-commented.
   - Follow Python 2.7 & Python 3 dual-compatibility guidelines (using `from __future__ import absolute_import, division, print_function`).
   - Maintain non-destructive safety guarantees: never write to the Revit document database during pure inspection tasks.
3. **Run the Automated Test Suite**:
   All tests must pass before submitting a PR:
   ```bash
   python -B tests/run_all_tests.py
   ```
4. **Commit with Clear Messages**:
   Use descriptive commit messages following the Conventional Commits style (e.g., `fix: ...`, `feat: ...`, `docs: ...`).
5. **Open a Pull Request**:
   Describe what was added or fixed and reference any open issues.

---

## Development Setup

The repository is designed to run its entire test suite **offline** without an active Autodesk Revit license or Revit API runtime:
- Mock objects simulate `Autodesk.Revit.DB` and `pyRevit` structures.
- Unit tests run on standard Python 3.9 through 3.12 via GitHub Actions.

To run tests with verbose output:
```bash
python -m unittest discover -v -s tests
```

---

## Questions & Contact

For questions or feedback, feel free to open a GitHub Discussion or reach out to [Hazem Sakr on LinkedIn](https://www.linkedin.com/in/hazem-sakr/).
