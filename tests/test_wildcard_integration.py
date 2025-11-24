"""Integration tests for wildcard axis expansion."""

from pathlib import Path
import tempfile
import pytest

from mudyla.cli import CLI


def test_wildcard_integration_build_all_platforms(tmp_path: Path):
    """Test building for all platforms using wildcard."""
    # Create a simple test markdown file
    test_md = tmp_path / ".mdl" / "defs" / "test.md"
    test_md.parent.mkdir(parents=True)
    test_md.write_text("""# Axis

- `platform`=`{jvm*|js}`

# action: build

```bash
echo "Building for platform: ${platform}"
ret message:string=built
```
""")

    # Create CLI instance and run with wildcard
    cli = CLI()
    argv = [
        "--defs", str(test_md),
        "--dry-run",
        "-u", "platform:*",
        ":build",
    ]

    exit_code = cli.run(argv)
    assert exit_code == 0


def test_wildcard_integration_prefix_match(tmp_path: Path):
    """Test building with prefix wildcard."""
    # Create a simple test markdown file
    test_md = tmp_path / ".mdl" / "defs" / "test.md"
    test_md.parent.mkdir(parents=True)
    test_md.write_text("""# Axis

- `scala`=`{2.12.0|2.13.0|2.13.5*|3.3.0}`

# action: test

```bash
echo "Testing with scala: ${scala}"
ret message:string=tested
```
""")

    # Create CLI instance and run with prefix wildcard
    cli = CLI()
    argv = [
        "--defs", str(test_md),
        "--dry-run",
        "-u", "scala:2.13*",
        ":test",
    ]

    exit_code = cli.run(argv)
    assert exit_code == 0


def test_wildcard_integration_combined(tmp_path: Path):
    """Test the example from requirements: platform:* :build scala:* :test scala:2.13*"""
    # Create a test markdown file
    test_md = tmp_path / ".mdl" / "defs" / "test.md"
    test_md.parent.mkdir(parents=True)
    test_md.write_text("""# Axis

- `platform`=`{jvm*|js}`
- `scala`=`{2.12.0|2.13.0|2.13.5*|3.3.0}`

# action: build

```bash
echo "Building for platform: ${platform}, scala: ${scala}"
ret message:string=built
```

# action: test

```bash
echo "Testing for platform: ${platform}, scala: ${scala}"
ret message:string=tested
```
""")

    # Create CLI instance and run with combined wildcards
    cli = CLI()
    argv = [
        "--defs", str(test_md),
        "--dry-run",
        "-u", "platform:*",
        ":build", "scala:*",
        ":test", "scala:2.13*",
    ]

    exit_code = cli.run(argv)
    assert exit_code == 0


def test_wildcard_integration_no_match_error(tmp_path: Path):
    """Test that invalid wildcard pattern produces error."""
    # Create a simple test markdown file
    test_md = tmp_path / ".mdl" / "defs" / "test.md"
    test_md.parent.mkdir(parents=True)
    test_md.write_text("""# Axis

- `scala`=`{2.13.0*|3.3.0}`

# action: build

```bash
echo "Building"
ret message:string=built
```
""")

    # Create CLI instance and run with non-matching wildcard
    cli = CLI()
    argv = [
        "--defs", str(test_md),
        "--dry-run",
        "-u", "scala:2.12*",  # This should match nothing
        ":build",
    ]

    exit_code = cli.run(argv)
    assert exit_code == 1  # Should fail
