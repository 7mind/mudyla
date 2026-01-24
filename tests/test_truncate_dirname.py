"""Tests for truncate_dirname utility function."""

import hashlib

from mudyla.formatters.action import (
    MAX_DIRNAME_LENGTH,
    TRUNCATED_HASH_LENGTH,
    truncate_dirname,
)


class TestTruncateDirname:
    """Tests for truncate_dirname function."""

    def test_short_name_unchanged(self) -> None:
        """Names within limit should not be modified."""
        short_name = "simple_action"
        assert truncate_dirname(short_name) == short_name

    def test_exactly_max_length_unchanged(self) -> None:
        """Names exactly at the limit should not be modified."""
        name = "a" * MAX_DIRNAME_LENGTH
        assert truncate_dirname(name) == name
        assert len(truncate_dirname(name)) == MAX_DIRNAME_LENGTH

    def test_long_name_truncated_with_hash(self) -> None:
        """Names exceeding limit should be truncated with hash suffix."""
        long_name = "a" * 100
        result = truncate_dirname(long_name)

        assert len(result) == MAX_DIRNAME_LENGTH
        assert result.endswith("...")  is False  # ends with hash, not just dots
        assert "..." in result

    def test_truncated_name_format(self) -> None:
        """Verify the format of truncated names without action suffix: prefix...hash."""
        long_name = "args.docker-image-name_tg-unified-launcher+args.docker-image-platform_auto+args.docker-image-tag_auto"
        result = truncate_dirname(long_name)

        # Should have format: truncated_prefix...hash (no # in name)
        assert "..." in result
        parts = result.split("...")
        assert len(parts) == 2

        prefix, hash_suffix = parts
        assert len(hash_suffix) == TRUNCATED_HASH_LENGTH

    def test_hash_is_deterministic(self) -> None:
        """Same input should always produce the same truncated output."""
        long_name = "x" * 100
        result1 = truncate_dirname(long_name)
        result2 = truncate_dirname(long_name)
        assert result1 == result2

    def test_hash_matches_sha256(self) -> None:
        """Verify the hash suffix matches SHA256 of the original name."""
        long_name = "y" * 100
        result = truncate_dirname(long_name)

        expected_hash = hashlib.sha256(long_name.encode("utf-8")).hexdigest()[:TRUNCATED_HASH_LENGTH]
        assert result.endswith(expected_hash)

    def test_different_long_names_produce_different_hashes(self) -> None:
        """Different long names should produce different truncated names."""
        name1 = "a" * 100
        name2 = "b" * 100

        result1 = truncate_dirname(name1)
        result2 = truncate_dirname(name2)

        assert result1 != result2

    def test_custom_max_length(self) -> None:
        """Custom max_length parameter should be respected."""
        name = "a" * 50
        result = truncate_dirname(name, max_length=30)

        assert len(result) == 30
        assert "..." in result

    def test_real_world_example(self) -> None:
        """Test with a realistic long directory name from the issue."""
        long_name = (
            "args.docker-image-name_tg-unified-launcher+"
            "args.docker-image-platform_auto+"
            "args.docker-image-tag_auto+"
            "args.docker-main-class_net.playq.tg.launcher.TGLauncher+"
            "args.docker-registry_377166687780.dkr.ecr.us-east-1.amazonaws.com+"
            "args.pack-agg_tg-launcher-app+"
            "args.pack-agg-target_tg-unified-launcher#docker-nix"
        )

        result = truncate_dirname(long_name)

        assert len(result) <= MAX_DIRNAME_LENGTH
        assert len(result) == MAX_DIRNAME_LENGTH  # Should use full allowed length
        assert "..." in result
        # Action suffix should be preserved
        assert result.endswith("#docker-nix")

    def test_empty_string(self) -> None:
        """Empty string should be returned as-is."""
        assert truncate_dirname("") == ""

    def test_result_never_exceeds_max_length(self) -> None:
        """Result should never exceed max_length regardless of input."""
        for length in [65, 100, 500, 1000]:
            name = "x" * length
            result = truncate_dirname(name)
            assert len(result) <= MAX_DIRNAME_LENGTH

    def test_action_suffix_preserved(self) -> None:
        """Action suffix after # should be preserved when truncating."""
        long_name = "args.message_Hello+args.output-dir_test-output#write-message"
        result = truncate_dirname(long_name, max_length=50)

        assert result.endswith("#write-message")
        assert "..." in result
        assert len(result) == 50

    def test_action_suffix_preserved_with_hash(self) -> None:
        """Action suffix preservation should still include hash for uniqueness."""
        long_name = "a" * 80 + "#my-action"
        result = truncate_dirname(long_name)

        assert result.endswith("#my-action")
        assert "..." in result
        # Hash should be present between ... and #
        parts = result.split("...")
        assert len(parts) == 2
        middle_and_suffix = parts[1]
        assert "#my-action" in middle_and_suffix
        hash_part = middle_and_suffix.replace("#my-action", "")
        assert len(hash_part) == TRUNCATED_HASH_LENGTH

    def test_action_suffix_deterministic(self) -> None:
        """Truncation with action suffix should be deterministic."""
        long_name = "x" * 70 + "#build-action"
        result1 = truncate_dirname(long_name)
        result2 = truncate_dirname(long_name)
        assert result1 == result2
        assert result1.endswith("#build-action")

    def test_short_name_with_hash_unchanged(self) -> None:
        """Short names with # should not be modified."""
        short_name = "context#action"
        assert truncate_dirname(short_name) == short_name
