"""Validated, serializable configuration for a single game run."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelProfile(BaseModel):
    """Sampling and routing settings bound to one AI role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)


class ShangCourtRoleProfiles(BaseModel):
    """Independent model-profile bindings for the two Shang court roles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    great_priest: str = Field(min_length=1)
    emperor: str = Field(min_length=1)


class PlayerConfig(BaseModel):
    """A player assigned to one distinct seat."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    player_id: str = Field(min_length=1)
    seat: int = Field(ge=1, le=4)
    model_profile: str | None = Field(
        default=None, description="key into GameConfig.model_profiles; None means no LLM"
    )
    controller_type: Literal["llm_baseline", "random_baseline", "shang_court"] | None = None
    court_role_profiles: ShangCourtRoleProfiles | None = None


class GameConfig(BaseModel):
    """The frozen configuration required to initialize a Level 0 game."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    game_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    seed: int
    players: tuple[PlayerConfig, ...]
    initial_cash: int = Field(default=1500, ge=0)
    max_complete_rounds: int = Field(default=50, ge=1)
    rules_version: str = Field(min_length=1)
    rules_level: int = Field(default=0, ge=0, le=2)
    board_data_version: str = Field(min_length=1)
    card_data_version: str = Field(min_length=1)
    model_profiles: dict[str, ModelProfile] = Field(default_factory=dict)
    validation_retries: int = Field(default=2, ge=0)
    window_turns: int = Field(default=1, ge=1)
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
            for profile_name in (
                player.court_role_profiles.great_priest,
                player.court_role_profiles.emperor,
            )
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
                if player.court_role_profiles is None:
                    msg = f"Shang court player {player.player_id} requires court_role_profiles"
                    raise ValueError(msg)
            elif player.court_role_profiles is not None:
                msg = f"legacy player {player.player_id} must not set court_role_profiles"
                raise ValueError(msg)
        return self
