from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    height = State()
    weight = State()
    shoulders = State()
    chest = State()
    waist = State()
    belt = State()
