from aiogram import types
from aiogram.filters import CommandStart, Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton,
    Message
)
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
import random


async def send_divider(callback: types.CallbackQuery):
    await callback.message.answer("────────────────────────", parse_mode=ParseMode.HTML)

# test
async def global_command_filter(message: Message, state: FSMContext) -> bool:
    ALLOWED_COMMANDS = ["/cancel_trade", "/sent_wallet"]
    current_state = await state.get_state()
    is_command = any(entity.type == 'bot_command' for entity in (message.entities or []))

    if current_state is not None and is_command:
        if message.text.split()[0] in ALLOWED_COMMANDS:  
            return True
        else:
            await message.reply("You are currently in a deal creation process. Use /cancel_trade to exit.")
            return False
    return True


class GroupChatFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.type in ['group', 'supergroup']


class RoleFilter(Filter):
    def __init__(self, role_needed, collection=None) -> None:
        self.role_needed = role_needed
        self.collection = collection

    async def __call__(self, callback_or_message, state: FSMContext) -> bool:
        username = callback_or_message.from_user.username
        if not username:
            return False
        
        from config import async_session
        from sqlalchemy.future import select
        from database.models import User, Deal

        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.username == username.lstrip('@'))
            )
            user = result.scalars().first()
            if not user:
                return False
            
            if self.role_needed == "Buyer":
                stmt = select(Deal).where((Deal.buyer_id == user.id) & (Deal.status == "Active"))
            elif self.role_needed == "Seller":
                stmt = select(Deal).where((Deal.seller_id == user.id) & (Deal.status == "Active"))
            else:
                return False
                
            deal_result = await session.execute(stmt)
            deal = deal_result.scalars().first()
            return deal is not None


async def generate_unique_id(collection=None, max_value=9999):
    import uuid6
    return uuid6.uuid7().bytes


async def button_builder(text_list: list, callback: list, ) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for text in range(len(text_list)):
        builder.add(types.InlineKeyboardButton(
            text=text_list[text],
            callback_data=callback[text])
        )
    return builder


async def get_data_from_db(deal_data: dict, collection_lobby=None):
    unique_id = deal_data.get("unique_id")
    from config import async_session
    from sqlalchemy.future import select
    from database.models import Deal
    async with async_session() as session:
        result = await session.execute(
            select(Deal).where((Deal.unique_id == unique_id) & (Deal.status == "Active"))
        )
        return result.scalars().first()
