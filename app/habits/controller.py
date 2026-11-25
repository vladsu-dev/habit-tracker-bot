from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.accounts import User
from app.auth import get_current_user
from app.database import get_session

from . import service
from .schemas import HabitCreate, HabitPatch, MarkInput, SettingsPatch

router = APIRouter()
DB = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/habits")
def get_habits(session: DB, user: CurrentUser, include_completed: bool = False):
    return service.list_today(session, user, include_completed=include_completed)


@router.post("/habits", status_code=201)
def create_habit(data: HabitCreate, session: DB, user: CurrentUser):
    return service.create_habit(data, session, user)


@router.patch("/habits/{habit_id}")
def edit_habit(habit_id: int, data: HabitPatch, session: DB, user: CurrentUser):
    return service.edit_habit(habit_id, data, session, user)


@router.delete("/habits/{habit_id}", status_code=204)
def delete_habit(habit_id: int, session: DB, user: CurrentUser):
    service.delete_habit(habit_id, session, user)
    return Response(status_code=204)


@router.put("/habits/{habit_id}/marks/{day}")
def mark_habit(
    habit_id: int, day: date, data: MarkInput, session: DB, user: CurrentUser
):
    return service.mark_habit(habit_id, day, data, session, user)


@router.get("/settings")
def get_settings(user: CurrentUser):
    return service.get_settings(user)


@router.patch("/settings")
def edit_settings(data: SettingsPatch, session: DB, user: CurrentUser):
    return service.edit_settings(data, session, user)
