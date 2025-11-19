"""
Database Schemas for MLBB Fantasy League (MPL ID)

Each Pydantic model corresponds to a MongoDB collection. The collection name is the
lowercased class name. Example: class User -> collection "user".

These models are used for validation before inserting/updating data.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
from datetime import datetime

Role = Literal["tank", "mage", "assassin", "support", "marksman", "fighter", "roamer", "goldlane", "midlane", "exp", "jungler"]

class User(BaseModel):
    username: str = Field(..., min_length=3, max_length=24)
    email: EmailStr
    password_hash: str = Field(..., description="Hashed password")
    avatar_url: Optional[str] = None
    favorite_team: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class Player(BaseModel):
    name: str
    ign: str = Field(..., description="In-game name")
    team: str = Field(..., description="MPL ID team")
    role: Role
    cost: int = Field(..., ge=1)
    kda: float = Field(..., ge=0)
    damage: int = Field(..., ge=0)
    objectives: int = Field(..., ge=0)
    win_rate: float = Field(..., ge=0, le=100)
    mvp_count: int = Field(0, ge=0)
    photo_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class DraftTeam(BaseModel):
    user_id: str
    week: int
    budget: int = 100
    player_ids: List[str]
    total_cost: int
    points: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class League(BaseModel):
    name: str
    code: str = Field(..., description="Invite code")
    owner_user_id: str
    member_user_ids: List[str] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class Transfer(BaseModel):
    user_id: str
    week: int
    out_player_id: str
    in_player_id: str
    created_at: Optional[datetime] = None

class Notification(BaseModel):
    title: str
    message: str
    type: Literal["match", "points", "league", "system"] = "system"
    created_at: Optional[datetime] = None

class Matchweek(BaseModel):
    week: int
    name: str
    is_current: bool = False
    lock_time: Optional[datetime] = None

# Note: The database helper will use these models for validation in the app.
