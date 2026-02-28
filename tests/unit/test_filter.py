"""Unit tests for MessageFilter — guild/channel/role whitelist logic."""
from __future__ import annotations

from agents.discord_listener.filter import MessageFilter
from config.settings import Settings


def _settings(**kwargs: object) -> Settings:
    """Build a minimal Settings with the given whitelist overrides."""
    defaults: dict[str, object] = {
        "discord_bot_token": "fake",
        "discord_allowed_guild_ids": [],
        "discord_allowed_channel_ids": [],
        "discord_allowed_role_ids": [],
    }
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


GUILD = "111"
CHANNEL = "222"
ROLES = ["333", "444"]


class TestEmptyWhitelists:
    def test_all_empty_allows_everything(self) -> None:
        f = MessageFilter(_settings())
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is True

    def test_all_empty_allows_no_roles(self) -> None:
        f = MessageFilter(_settings())
        assert f.is_allowed(GUILD, CHANNEL, []) is True

    def test_all_empty_allows_empty_guild_id(self) -> None:
        """DM messages have no guild (empty string)."""
        f = MessageFilter(_settings())
        assert f.is_allowed("", CHANNEL, ROLES) is True


class TestGuildFilter:
    def test_known_guild_allowed(self) -> None:
        f = MessageFilter(_settings(discord_allowed_guild_ids=[GUILD]))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is True

    def test_unknown_guild_rejected(self) -> None:
        f = MessageFilter(_settings(discord_allowed_guild_ids=[GUILD]))
        assert f.is_allowed("999", CHANNEL, ROLES) is False

    def test_empty_guild_id_rejected_when_whitelist_non_empty(self) -> None:
        """DMs are blocked when a guild whitelist is configured."""
        f = MessageFilter(_settings(discord_allowed_guild_ids=[GUILD]))
        assert f.is_allowed("", CHANNEL, ROLES) is False

    def test_multiple_guilds_all_allowed(self) -> None:
        f = MessageFilter(_settings(discord_allowed_guild_ids=["111", "555", "666"]))
        assert f.is_allowed("555", CHANNEL, ROLES) is True
        assert f.is_allowed("666", CHANNEL, ROLES) is True
        assert f.is_allowed("777", CHANNEL, ROLES) is False


class TestChannelFilter:
    def test_known_channel_allowed(self) -> None:
        f = MessageFilter(_settings(discord_allowed_channel_ids=[CHANNEL]))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is True

    def test_unknown_channel_rejected(self) -> None:
        f = MessageFilter(_settings(discord_allowed_channel_ids=[CHANNEL]))
        assert f.is_allowed(GUILD, "999", ROLES) is False


class TestRoleFilter:
    def test_matching_role_allowed(self) -> None:
        f = MessageFilter(_settings(discord_allowed_role_ids=["333"]))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is True

    def test_no_matching_role_rejected(self) -> None:
        f = MessageFilter(_settings(discord_allowed_role_ids=["333"]))
        assert f.is_allowed(GUILD, CHANNEL, ["999"]) is False

    def test_empty_author_roles_rejected_when_whitelist_set(self) -> None:
        f = MessageFilter(_settings(discord_allowed_role_ids=["333"]))
        assert f.is_allowed(GUILD, CHANNEL, []) is False

    def test_any_matching_role_sufficient(self) -> None:
        f = MessageFilter(_settings(discord_allowed_role_ids=["333", "777"]))
        assert f.is_allowed(GUILD, CHANNEL, ["777", "888"]) is True


class TestCombinedFilters:
    def test_all_filters_pass(self) -> None:
        f = MessageFilter(_settings(
            discord_allowed_guild_ids=[GUILD],
            discord_allowed_channel_ids=[CHANNEL],
            discord_allowed_role_ids=["333"],
        ))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is True

    def test_guild_fails_others_pass(self) -> None:
        f = MessageFilter(_settings(
            discord_allowed_guild_ids=["999"],
            discord_allowed_channel_ids=[CHANNEL],
            discord_allowed_role_ids=["333"],
        ))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is False

    def test_channel_fails_others_pass(self) -> None:
        f = MessageFilter(_settings(
            discord_allowed_guild_ids=[GUILD],
            discord_allowed_channel_ids=["999"],
            discord_allowed_role_ids=["333"],
        ))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is False

    def test_role_fails_others_pass(self) -> None:
        f = MessageFilter(_settings(
            discord_allowed_guild_ids=[GUILD],
            discord_allowed_channel_ids=[CHANNEL],
            discord_allowed_role_ids=["999"],
        ))
        assert f.is_allowed(GUILD, CHANNEL, ROLES) is False


class TestProperties:
    def test_guild_ids_property(self) -> None:
        f = MessageFilter(_settings(discord_allowed_guild_ids=["111", "222"]))
        assert f.guild_ids == frozenset({"111", "222"})

    def test_channel_ids_property(self) -> None:
        f = MessageFilter(_settings(discord_allowed_channel_ids=["333"]))
        assert f.channel_ids == frozenset({"333"})

    def test_role_ids_property(self) -> None:
        f = MessageFilter(_settings(discord_allowed_role_ids=["444", "555"]))
        assert f.role_ids == frozenset({"444", "555"})
