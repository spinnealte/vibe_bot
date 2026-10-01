from aiogram.fsm.state import State, StatesGroup

from stubbot.services.registration import OptionalField
from stubbot.tg.callbacks import SelectGroup


class Consent(StatesGroup):
    personal_data = State()
    marketing = State()


class Registration(StatesGroup):
    # Обязательные шаги.
    phone = State()
    full_name = State()
    confirm_name = State()
    specialties = State()
    positions = State()
    # Необязательные — после завершения регистрации, каждый можно пропустить.
    city = State()
    workplace = State()
    experience = State()
    email = State()
    birth_date = State()


class EditProfile(StatesGroup):
    full_name = State()
    confirm_name = State()
    phone = State()
    specialties = State()
    positions = State()
    optional_value = State()  # город/клиника/опыт/email/дата рождения; какое поле — в data["field"]


class Application(StatesGroup):
    """Заявка на поток. В data: apply_session_id, price_id, seats, comment."""

    price = State()
    seats = State()
    comment = State()
    confirm = State()


# Ключ в FSM data: заявка, к которой вернуться после согласия и регистрации.
PENDING_APPLY_KEY = "apply_session_id"
FULL_NAME_KEY = "full_name"  # введённое ФИО до подтверждения


# Шаги с мультивыбором и справочник, который на них выбирают (общий обработчик переключения галочек).
SELECTION_GROUP_BY_STATE: dict[str, SelectGroup] = {
    Registration.specialties.state: SelectGroup.SPECIALTIES,
    Registration.positions.state: SelectGroup.POSITIONS,
    EditProfile.specialties.state: SelectGroup.SPECIALTIES,
    EditProfile.positions.state: SelectGroup.POSITIONS,
}


OPTIONAL_STATES: dict[OptionalField, State] = {
    OptionalField.CITY: Registration.city,
    OptionalField.WORKPLACE: Registration.workplace,
    OptionalField.EXPERIENCE: Registration.experience,
    OptionalField.EMAIL: Registration.email,
    OptionalField.BIRTH_DATE: Registration.birth_date,
}
FIELD_BY_STATE: dict[str, OptionalField] = {state.state: field for field, state in OPTIONAL_STATES.items()}

# Шаги, где ждём текст с клавиатуры.
REGISTRATION_TEXT_STATES = (Registration.full_name, *OPTIONAL_STATES.values())
# Шаги, где ответ — кнопки в сообщении.
REGISTRATION_BUTTON_STATES = (Registration.confirm_name, Registration.specialties, Registration.positions)
