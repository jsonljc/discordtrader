from __future__ import annotations

from config.settings import Settings


class MessageFilter:
    """
    Whitelist-based gate for incoming Discord messages.

    Each dimension (guild, channel, role) is checked independently.
    An empty whitelist for a dimension means "no restriction on that dimension".
    A non-empty whitelist means "only these IDs pass".

    All three dimensions must pass for a message to be allowed.
    """

    def __init__(self, settings: Settings) -> None:
        self._guild_ids: frozenset[str] = frozenset(settings.discord_allowed_guild_ids)
        self._channel_ids: frozenset[str] = frozenset(settings.discord_allowed_channel_ids)
        self._role_ids: frozenset[str] = frozenset(settings.discord_allowed_role_ids)

    def is_allowed(
        self,
        guild_id: str,
        channel_id: str,
        author_role_ids: list[str],
    ) -> bool:
        """
        Return True if the message should be processed.

        Args:
            guild_id:        Discord guild snowflake (empty string for DMs).
            channel_id:      Discord channel snowflake.
            author_role_ids: Role snowflakes the message author holds.
        """
        if self._guild_ids and guild_id not in self._guild_ids:
            return False
        if self._channel_ids and channel_id not in self._channel_ids:
            return False
        return not (self._role_ids and not self._role_ids.intersection(author_role_ids))

    @property
    def guild_ids(self) -> frozenset[str]:
        return self._guild_ids

    @property
    def channel_ids(self) -> frozenset[str]:
        return self._channel_ids

    @property
    def role_ids(self) -> frozenset[str]:
        return self._role_ids
