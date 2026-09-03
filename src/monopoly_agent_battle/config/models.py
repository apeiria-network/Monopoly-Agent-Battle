"""Validated, serializable configuration for a single game run."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORTED_REMOTE_MODELS: frozenset[str] = frozenset(
    {
        "GLM-5-Turbo",
        "DeepSeek-V4-Flash",
        "DeepSeek-V4-Pro",
        "Qwen3.7-Plus",
        "Qwen3.8-Max",
        "Kimi-K2.6",
    }
)


class ModelProfile(BaseModel):
    """Sampling, routing, and credential-reference settings for one AI role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    api_key_env: str | None = Field(
        default=None,
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    seed: int = 42
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    thinking: bool = Field(
        default=False,
        description=(
            "Enable model thinking mode; disabled by default and only "
            "explicitly enabled takes effect"
        ),
    )

    @model_validator(mode="after")
    def validate_provider_settings(self) -> ModelProfile:
        """Require endpoint and environment credential references for real clients."""
        if self.provider not in {"mock", "fake", "openai_compatible"}:
            msg = f"unsupported model provider: {self.provider}"
            raise ValueError(msg)
        if self.provider == "openai_compatible":
            missing = [
                name
                for name, value in (
                    ("base_url", self.base_url),
                    ("api_key_env", self.api_key_env),
                )
                if value is None
            ]
            if missing:
                msg = "openai_compatible model profile requires: " + ", ".join(missing)
                raise ValueError(msg)
            assert self.base_url is not None
            if not self.base_url.startswith(("http://", "https://")):
                msg = "openai_compatible base_url must use http:// or https://"
                raise ValueError(msg)
        return self


class ShangCourtRoleProfiles(BaseModel):
    """Independent model-profile bindings for the two Shang court roles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    great_priest: str = Field(min_length=1)
    emperor: str = Field(min_length=1)


class QinCourtRoleProfiles(BaseModel):
    """Independent model-profile bindings for the four Qin court roles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chancellor: str = Field(min_length=1)
    grand_marshal: str = Field(min_length=1)
    imperial_counsellor: str = Field(min_length=1)
    emperor: str = Field(min_length=1)


class TangCourtRoleProfiles(BaseModel):
    """Independent model-profile bindings for the three Tang court roles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    zhongshu: str = Field(min_length=1)
    menxia: str = Field(min_length=1)
    emperor: str = Field(min_length=1)


class MingCourtRoleProfiles(BaseModel):
    """Independent model-profile bindings for the four Ming court roles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chief_grand_secretary: str = Field(min_length=1)
    grand_secretary_1: str = Field(min_length=1)
    grand_secretary_2: str = Field(min_length=1)
    emperor: str = Field(min_length=1)


class PlayerConfig(BaseModel):
    """A player assigned to one distinct seat."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    player_id: str = Field(min_length=1)
    seat: int = Field(ge=1, le=4)
    model_profile: str | None = Field(
        default=None, description="key into GameConfig.model_profiles; None means no LLM"
    )
    controller_type: (
        Literal[
            "llm_baseline",
            "random_baseline",
            "shang_court",
            "qin_court",
            "tang_court",
            "ming_court",
        ]
        | None
    ) = None
    court_role_profiles: (
        ShangCourtRoleProfiles
        | QinCourtRoleProfiles
        | TangCourtRoleProfiles
        | MingCourtRoleProfiles
        | None
    ) = None


class GameConfig(BaseModel):
    """The frozen configuration required to initialize a Level 0 game."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    game_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    seed: int
    players: tuple[PlayerConfig, ...]
    initial_cash: int = Field(default=1500, ge=0)
    initial_chance_cards: int = Field(default=0, ge=0, le=4)
    max_complete_rounds: int = Field(default=50, ge=1)
    rules_version: str = Field(min_length=1)
    rules_level: int = Field(default=0, ge=0, le=2)
    board_data_version: str = Field(min_length=1)
    card_data_version: str = Field(min_length=1)
    model_profiles: dict[str, ModelProfile] = Field(default_factory=dict)
    validation_retries: int = Field(default=2, ge=0)
    window_turns: int = Field(default=1, ge=1)
    prompt_profile: Literal["full-v1", "cache-first-v1", "full-v2", "cache-first-v2"] = "full-v2"
    sentence_template_version: str | None = None
    context_token_cap: int | None = Field(default=None, ge=1)
    output_directory: Path = Path("runs")

    @field_validator("game_id", "experiment_id")
    @classmethod
    def reject_path_separators(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            msg = "must not contain path separators"
            raise ValueError(msg)
        return value

    @field_validator("output_directory")
    @classmethod
    def reject_escaping_output_directory(cls, value: Path) -> Path:
        if any(part == ".." for part in value.parts):
            msg = "output_directory must not contain '..' path components"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_players_and_level(self) -> GameConfig:
        if not 2 <= len(self.players) <= 4:
            msg = "players must contain between 2 and 4 entries"
            raise ValueError(msg)
        seats = [player.seat for player in self.players]
        if len(seats) != len(set(seats)):
            msg = "player seats must be unique"
            raise ValueError(msg)
        if self.rules_level != 0:
            msg = "Phase 0 only accepts classic Level 0 configurations"
            raise ValueError(msg)

        referenced_profiles = {
            player.model_profile for player in self.players if player.model_profile is not None
        }
        referenced_profiles.update(
            profile_name
            for player in self.players
            if player.court_role_profiles is not None
            for profile_name in player.court_role_profiles.model_dump().values()
        )
        missing = referenced_profiles - set(self.model_profiles)
        if missing:
            msg = f"player model_profile not defined: {sorted(missing)}"
            raise ValueError(msg)

        for player in self.players:
            if player.controller_type == "llm_baseline":
                if player.model_profile is None:
                    msg = f"LLM baseline player {player.player_id} requires model_profile"
                    raise ValueError(msg)
                if player.court_role_profiles is not None:
                    msg = f"LLM baseline player {player.player_id} must not set court_role_profiles"
                    raise ValueError(msg)
            elif player.controller_type == "random_baseline":
                if player.model_profile is not None:
                    msg = f"random baseline player {player.player_id} must not set model_profile"
                    raise ValueError(msg)
                if player.court_role_profiles is not None:
                    msg = (
                        f"random baseline player {player.player_id} "
                        "must not set court_role_profiles"
                    )
                    raise ValueError(msg)
            elif player.controller_type == "shang_court":
                if player.model_profile is not None:
                    msg = f"Shang court player {player.player_id} must not set model_profile"
                    raise ValueError(msg)
                if not isinstance(player.court_role_profiles, ShangCourtRoleProfiles):
                    msg = (
                        f"Shang court player {player.player_id} "
                        "requires court_role_profiles of Shang roles"
                    )
                    raise ValueError(msg)
            elif player.controller_type == "qin_court":
                if player.model_profile is not None:
                    msg = f"Qin court player {player.player_id} must not set model_profile"
                    raise ValueError(msg)
                if not isinstance(player.court_role_profiles, QinCourtRoleProfiles):
                    msg = (
                        f"Qin court player {player.player_id} "
                        "requires court_role_profiles of Qin roles"
                    )
                    raise ValueError(msg)
            elif player.controller_type == "ming_court":
                if player.model_profile is not None:
                    msg = f"Ming court player {player.player_id} must not set model_profile"
                    raise ValueError(msg)
                if not isinstance(player.court_role_profiles, MingCourtRoleProfiles):
                    msg = (
                        f"Ming court player {player.player_id} "
                        "requires court_role_profiles of Ming roles"
                    )
                    raise ValueError(msg)
            elif player.controller_type == "tang_court":
                if player.model_profile is not None:
                    msg = f"Tang court player {player.player_id} must not set model_profile"
                    raise ValueError(msg)
                if not isinstance(player.court_role_profiles, TangCourtRoleProfiles):
                    msg = (
                        f"Tang court player {player.player_id} "
                        "requires court_role_profiles of Tang roles"
                    )
                    raise ValueError(msg)
            elif player.court_role_profiles is not None:
                msg = f"legacy player {player.player_id} must not set court_role_profiles"
                raise ValueError(msg)
        return self
