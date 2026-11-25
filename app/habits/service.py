from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.accounts import User

from .models import Habit, HabitMark
from .schemas import HabitCreate, HabitPatch, MarkInput, SettingsPatch


def get_local_day(user: User) -> date:
    return datetime.now(ZoneInfo(user.timezone)).date()


def list_habit_rows(
    session: Session,
    user: User,
    day: date,
    habit_id: int | None = None,
    *,
    include_completed: bool = False,
) -> list[dict]:
    successes = (
        select(func.count())
        .select_from(HabitMark)
        .where(HabitMark.habit_id == Habit.id, HabitMark.completed.is_(True))
        .correlate(Habit)
        .scalar_subquery()
    )
    query = (
        select(
            Habit.id,
            Habit.name,
            Habit.target,
            successes.label("count"),
            HabitMark.completed,
        )
        .outerjoin(HabitMark, (HabitMark.habit_id == Habit.id) & (HabitMark.day == day))
        .where(Habit.user_id == user.id)
        .order_by(Habit.id)
    )
    if habit_id is not None:
        query = query.where(Habit.id == habit_id)
    elif not include_completed:
        query = query.where(
            (successes < Habit.target) | HabitMark.completed.is_not(None)
        )
    return [dict(row) for row in session.execute(query).mappings()]


def list_today(
    session: Session, user: User, *, include_completed: bool = False
) -> dict:
    day = get_local_day(user)
    return {
        "date": day.isoformat(),
        "habits": list_habit_rows(
            session, user, day, include_completed=include_completed
        ),
    }


def lock_habit(session: Session, user: User, habit_id: int) -> Habit:
    owner_filter = (Habit.id == habit_id, Habit.user_id == user.id)
    if session.get_bind().dialect.name == "sqlite":
        # SQLite acquires its write lock at the first write and ignores FOR UPDATE.
        session.execute(update(Habit).where(*owner_filter).values(target=Habit.target))
    habit = session.scalar(select(Habit).where(*owner_filter).with_for_update())
    if habit is None:
        raise HTTPException(404, "Привычка не найдена")
    return habit


def create_habit(data: HabitCreate, session: Session, user: User):
    habit = Habit(user_id=user.id, **data.model_dump())
    session.add(habit)
    session.flush()
    result = list_habit_rows(session, user, get_local_day(user), habit.id)[0]
    session.commit()
    return result


def edit_habit(habit_id: int, data: HabitPatch, session: Session, user: User):
    habit = lock_habit(session, user, habit_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(habit, key, value)
    session.flush()
    result = list_habit_rows(session, user, get_local_day(user), habit.id)[0]
    session.commit()
    return result


def delete_habit(habit_id: int, session: Session, user: User):
    habit = lock_habit(session, user, habit_id)
    session.execute(delete(HabitMark).where(HabitMark.habit_id == habit_id))
    session.delete(habit)
    session.commit()


def mark_habit(habit_id: int, day: date, data: MarkInput, session: Session, user: User):
    habit = lock_habit(session, user, habit_id)
    if day != get_local_day(user):
        raise HTTPException(409, "Отметить привычку можно только за текущий день")
    current = list_habit_rows(session, user, day, habit.id)[0]
    if current["count"] >= habit.target and current["completed"] is None:
        raise HTTPException(409, "Цель уже достигнута")
    mark = session.get(HabitMark, (habit_id, day))
    if mark is None:
        session.add(HabitMark(habit_id=habit_id, day=day, completed=data.completed))
    else:
        mark.completed = data.completed
    session.flush()
    result = list_habit_rows(session, user, day, habit.id)[0]
    session.commit()
    return result


def get_settings(user: User):
    return {
        "timezone": user.timezone,
        "reminder_time": user.reminder_time.strftime("%H:%M"),
    }


def edit_settings(data: SettingsPatch, session: Session, user: User):
    if data.timezone is not None:
        user.timezone = data.timezone
    if data.reminder_time is not None:
        user.reminder_time = time.fromisoformat(data.reminder_time)
    session.commit()
    return get_settings(user)
