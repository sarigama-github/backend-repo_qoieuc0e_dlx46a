import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import User as UserSchema, Player as PlayerSchema, DraftTeam as DraftTeamSchema, League as LeagueSchema, Transfer as TransferSchema, Notification as NotificationSchema, Matchweek as MatchweekSchema

app = FastAPI(title="MLBB Fantasy League API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Utilities

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def with_timestamps(data: dict):
    now = datetime.now(timezone.utc)
    data.setdefault("created_at", now)
    data["updated_at"] = now
    return data


# Auth (mock, no real hashing for prototype)
class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class AuthResponse(BaseModel):
    user_id: str
    username: str
    email: str
    avatar_url: Optional[str] = None


@app.post("/auth/register", response_model=AuthResponse)
def register(req: RegisterRequest):
    if db["user"].find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = UserSchema(username=req.username, email=req.email, password_hash=req.password)
    user_id = create_document("user", with_timestamps(user.model_dump()))
    return AuthResponse(user_id=user_id, username=req.username, email=req.email)


@app.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest):
    u = db["user"].find_one({"email": req.email, "password_hash": req.password})
    if not u:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return AuthResponse(user_id=str(u["_id"]), username=u["username"], email=u["email"], avatar_url=u.get("avatar_url"))


# Players
@app.get("/players", response_model=List[PlayerSchema])
def list_players(role: Optional[str] = None, team: Optional[str] = None):
    q = {}
    if role:
        q["role"] = role
    if team:
        q["team"] = team
    docs = get_documents("player", q)
    # Convert _id
    for d in docs:
        d.pop("_id", None)
    return docs

@app.post("/players", response_model=str)
def seed_player(player: PlayerSchema):
    return create_document("player", with_timestamps(player.model_dump()))


# Draft teams
class DraftRequest(BaseModel):
    user_id: str
    week: int
    player_ids: List[str]
    budget: int = 100

@app.post("/draft", response_model=str)
def create_draft(req: DraftRequest):
    # compute cost
    ids = [oid(pid) for pid in req.player_ids]
    players = list(db["player"].find({"_id": {"$in": ids}}))
    total_cost = sum(p.get("cost", 0) for p in players)
    if total_cost > req.budget:
        raise HTTPException(status_code=400, detail="Budget exceeded")
    draft = DraftTeamSchema(user_id=req.user_id, week=req.week, budget=req.budget, player_ids=req.player_ids, total_cost=total_cost, points=0)
    return create_document("draftteam", with_timestamps(draft.model_dump()))

@app.get("/draft/{user_id}/{week}")
def get_draft(user_id: str, week: int):
    d = db["draftteam"].find_one({"user_id": user_id, "week": week})
    if not d:
        raise HTTPException(status_code=404, detail="No draft found")
    d["id"] = str(d.pop("_id"))
    return d


# Leaderboard (simple: sum points per user)
@app.get("/leaderboard")
def leaderboard(week: Optional[int] = None, limit: int = 50):
    pipeline = []
    if week is not None:
        pipeline.append({"$match": {"week": week}})
    pipeline.extend([
        {"$group": {"_id": "$user_id", "points": {"$sum": "$points"}}},
        {"$sort": {"points": -1}},
        {"$limit": limit},
    ])
    rows = list(db["draftteam"].aggregate(pipeline))
    # hydrate usernames
    for r in rows:
        u = db["user"].find_one({"_id": oid(r["_id"])}) if ObjectId.is_valid(r["_id"]) else db["user"].find_one({"_id": r["_id"]})
        r["user_id"] = r.pop("_id")
        r["username"] = u.get("username") if u else "Unknown"
    return rows


# Leagues
class CreateLeagueRequest(BaseModel):
    name: str
    owner_user_id: str

@app.post("/leagues", response_model=str)
def create_league(req: CreateLeagueRequest):
    code = os.urandom(4).hex().upper()
    league = LeagueSchema(name=req.name, code=code, owner_user_id=req.owner_user_id, member_user_ids=[req.owner_user_id])
    return create_document("league", with_timestamps(league.model_dump()))

@app.post("/leagues/join")
def join_league(code: str, user_id: str):
    lg = db["league"].find_one({"code": code})
    if not lg:
        raise HTTPException(status_code=404, detail="League not found")
    if user_id in lg.get("member_user_ids", []):
        return {"status": "ok"}
    db["league"].update_one({"_id": lg["_id"]}, {"$addToSet": {"member_user_ids": user_id}})
    return {"status": "ok"}

@app.get("/leagues/{league_id}")
def get_league(league_id: str):
    lg = db["league"].find_one({"_id": oid(league_id)})
    if not lg:
        raise HTTPException(status_code=404, detail="Not found")
    lg["id"] = str(lg.pop("_id"))
    return lg


# Transfers
class TransferRequest(BaseModel):
    user_id: str
    week: int
    out_player_id: str
    in_player_id: str

@app.post("/transfer")
def make_transfer(req: TransferRequest):
    # simple budget enforcement: recalc cost after swap
    draft = db["draftteam"].find_one({"user_id": req.user_id, "week": req.week})
    if not draft:
        raise HTTPException(status_code=404, detail="No draft found")
    players = list(db["player"].find({"_id": {"$in": [oid(pid) for pid in draft["player_ids"]]}}))
    cost_map = {str(p["_id"]): p["cost"] for p in players}
    # replace
    ids = [pid for pid in draft["player_ids"] if pid != req.out_player_id]
    ids.append(req.in_player_id)
    new_players = list(db["player"].find({"_id": {"$in": [oid(pid) for pid in ids]}}))
    new_total = sum(p.get("cost", 0) for p in new_players)
    if new_total > draft.get("budget", 100):
        raise HTTPException(status_code=400, detail="Budget exceeded")
    db["draftteam"].update_one({"_id": draft["_id"]}, {"$set": {"player_ids": ids, "total_cost": new_total, "updated_at": datetime.now(timezone.utc)}})
    tr = TransferSchema(user_id=req.user_id, week=req.week, out_player_id=req.out_player_id, in_player_id=req.in_player_id, created_at=datetime.now(timezone.utc))
    create_document("transfer", tr)
    return {"status": "ok"}


# Notifications
@app.get("/notifications", response_model=List[NotificationSchema])
def list_notifications(limit: int = 20):
    docs = get_documents("notification", {}, limit)
    for d in docs:
        d.pop("_id", None)
    return docs

@app.post("/notifications", response_model=str)
def create_notification(n: NotificationSchema):
    return create_document("notification", with_timestamps(n.model_dump()))


# Matchweeks
@app.get("/weeks")
def list_weeks():
    docs = get_documents("matchweek", {})
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs

@app.post("/weeks", response_model=str)
def create_week(w: MatchweekSchema):
    return create_document("matchweek", with_timestamps(w.model_dump()))


# Test
@app.get("/")
def root():
    return {"message": "MLBB Fantasy League API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = getattr(db, 'name', '✅ Connected')
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
