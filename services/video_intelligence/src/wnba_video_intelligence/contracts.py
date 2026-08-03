"""Strict interchange contracts between future video pipeline stages."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VideoModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Detection(VideoModel):
    frame_index: Annotated[int, Field(ge=0)]
    object_type: Literal["player", "ball", "referee"]
    x1: Annotated[float, Field(ge=0)]
    y1: Annotated[float, Field(ge=0)]
    x2: Annotated[float, Field(ge=0)]
    y2: Annotated[float, Field(ge=0)]
    confidence: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def ordered_box(self) -> Detection:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("detection bounding box must have positive area")
        return self


class TrackPoint(VideoModel):
    track_id: UUID
    frame_index: Annotated[int, Field(ge=0)]
    court_x_feet: Annotated[float, Field(ge=0, le=94)]
    court_y_feet: Annotated[float, Field(ge=0, le=50)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    player_id: UUID | None = None


class CourtCalibration(VideoModel):
    frame_index: Annotated[int, Field(ge=0)]
    image_to_court_homography: tuple[float, float, float, float, float, float, float, float, float]
    visible_keypoints: Annotated[int, Field(ge=4)]
    reprojection_error_pixels: Annotated[float, Field(ge=0)]


class VideoEvent(VideoModel):
    event_id: UUID
    event_type: Literal["touch", "pass", "shot", "rebound", "substitution", "possession_change"]
    start_frame: Annotated[int, Field(ge=0)]
    end_frame: Annotated[int, Field(ge=0)]
    primary_track_id: UUID | None = None
    confidence: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def ordered_frames(self) -> VideoEvent:
        if self.end_frame < self.start_frame:
            raise ValueError("event cannot end before it starts")
        return self


class ReviewItem(VideoModel):
    review_id: UUID
    game_id: UUID
    start_frame: Annotated[int, Field(ge=0)]
    end_frame: Annotated[int, Field(ge=0)]
    reason: Literal["identity", "occlusion", "event_conflict", "calibration", "low_confidence"]
    confidence: Annotated[float, Field(ge=0, le=1)]
    status: Literal["open", "confirmed", "corrected", "rejected"] = "open"
