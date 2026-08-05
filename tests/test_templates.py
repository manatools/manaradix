"""Unit tests for the setup profile template loader (core/templates.py)."""

from unittest.mock import patch

from manaradix.core.templates import default_template_dirs, load_profiles

DESKTOP_TOML = """\
id = "desktop"
name = "Desktop workstation"
description = "For workstations"
role = "desktop"

[global]
boot_from_snapshot = true
snapshot_before_upgrade = true

[[policies]]
subvolume = "/"
schedules = ["daily", "weekly", "boot"]
keep_daily = 3
keep_pre_upgrade = 2
"""


def write_template(directory, filename, content: str):
    path = directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadProfiles:
    def test_parses_valid_template(self, tmp_path) -> None:
        write_template(tmp_path, "desktop.toml", DESKTOP_TOML)
        profiles = load_profiles([tmp_path])

        assert len(profiles) == 1
        profile = profiles[0]
        assert profile.id == "desktop"
        assert profile.name == "Desktop workstation"
        assert profile.description == "For workstations"
        assert profile.role == "desktop"
        assert profile.boot_from_snapshot is True
        assert profile.snapshot_before_upgrade is True
        assert profile.snapshot_dir == ".snapshots"  # default
        assert len(profile.policies) == 1

        policy = profile.policies[0]
        assert policy.subvolume == "/"
        assert policy.schedules == ["daily", "weekly", "boot"]
        assert policy.required is True  # default
        # Explicit keep_daily/keep_pre_upgrade are honoured; the rest use
        # model defaults.
        assert policy.retention.keep_daily == 3
        assert policy.retention.keep_pre_upgrade == 2
        assert policy.retention.keep_weekly == 4

    def test_snapshot_before_upgrade_defaults_false(self, tmp_path) -> None:
        content = DESKTOP_TOML.replace("snapshot_before_upgrade = true\n", "")
        write_template(tmp_path, "desktop.toml", content)
        assert load_profiles([tmp_path])[0].snapshot_before_upgrade is False

    def test_retention_defaults_when_unspecified(self, tmp_path) -> None:
        content = DESKTOP_TOML.replace("keep_daily = 3\n", "")
        write_template(tmp_path, "desktop.toml", content)
        retention = load_profiles([tmp_path])[0].policies[0].retention
        assert retention.keep_daily == 7

    def test_override_precedence_later_dir_wins(self, tmp_path) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        write_template(first, "d.toml", DESKTOP_TOML)
        write_template(
            second, "d.toml",
            DESKTOP_TOML.replace('role = "desktop"', 'role = "server"'),
        )
        profiles = load_profiles([first, second])
        assert len(profiles) == 1
        assert profiles[0].role == "server"

    def test_unknown_schedule_dropped(self, tmp_path) -> None:
        content = DESKTOP_TOML.replace(
            '["daily", "weekly", "boot"]', '["daily", "yearly"]'
        )
        write_template(tmp_path, "d.toml", content)
        assert load_profiles([tmp_path])[0].policies[0].schedules == ["daily"]

    def test_policy_without_valid_schedules_skipped(self, tmp_path) -> None:
        content = DESKTOP_TOML.replace('["daily", "weekly", "boot"]', '["yearly"]')
        write_template(tmp_path, "d.toml", content)
        assert load_profiles([tmp_path])[0].policies == []

    def test_invalid_role_treated_as_any(self, tmp_path) -> None:
        content = DESKTOP_TOML.replace('role = "desktop"', 'role = "fridge"')
        write_template(tmp_path, "d.toml", content)
        assert load_profiles([tmp_path])[0].role == "any"

    def test_missing_id_or_name_skipped(self, tmp_path, caplog) -> None:
        write_template(tmp_path, "a.toml", 'name = "No id"\n')
        write_template(tmp_path, "b.toml", 'id = "no-name"\n')
        with caplog.at_level("WARNING"):
            profiles = load_profiles([tmp_path])
        assert profiles == []
        assert any("missing the required" in r.message for r in caplog.records)

    def test_malformed_file_skipped(self, tmp_path, caplog) -> None:
        write_template(tmp_path, "broken.toml", "this is = not [valid toml\n  ]")
        with caplog.at_level("WARNING"):
            profiles = load_profiles([tmp_path])
        assert profiles == []
        assert any("Could not parse" in r.message for r in caplog.records)

    def test_non_integer_keep_value_uses_default(self, tmp_path, caplog) -> None:
        content = DESKTOP_TOML.replace("keep_daily = 3", 'keep_daily = "many"')
        write_template(tmp_path, "d.toml", content)
        with caplog.at_level("WARNING"):
            retention = load_profiles([tmp_path])[0].policies[0].retention
        assert retention.keep_daily == 7


class TestDefaultTemplateDirs:
    def test_env_dirs_prepended(self, tmp_path) -> None:
        with patch.dict(
            "os.environ", {"MANARADIX_TEMPLATE_DIRS": str(tmp_path)}
        ):
            dirs = default_template_dirs()
        assert dirs[0] == tmp_path

    def test_env_colon_separated(self, tmp_path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        with patch.dict(
            "os.environ",
            {"MANARADIX_TEMPLATE_DIRS": f"{a}:{b}"},
        ):
            dirs = default_template_dirs()
        assert dirs[0] == a
        assert dirs[1] == b
