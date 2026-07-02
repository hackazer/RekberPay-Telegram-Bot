from aiogram.fsm.state import State, StatesGroup


class Form(StatesGroup):
    seller = State()
    buyer = State()
    deal_details = State()
    unique_id = State()


class WalletStates(StatesGroup):
    awaiting_topup_amount_gateway = State()
    awaiting_topup_amount_manual = State()
    awaiting_withdrawal_address = State()
    awaiting_withdrawal_amount = State()

