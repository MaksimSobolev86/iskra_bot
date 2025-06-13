import asyncio
import os
import calendar
from datetime import datetime, date as dt_date
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import gspread
import re
import random

ADMIN_ID = 408892234   # <-- твой Telegram user id
ADMIN_PHONE = "+79820001122"  # <-- номер для кнопки "Позвонить админу"

WORK_START = 8
WORK_END = 21

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GS_KEY = os.getenv("GOOGLE_SHEETS_KEY")

class Booking(StatesGroup):
    choosing_hut = State()
    choosing_date = State()
    entering_time_from = State()
    entering_time_to = State()
    entering_name = State()
    entering_phone = State()
    waiting_receipt = State()

gc = gspread.service_account(filename=GS_KEY)
spreadsheet = gc.open("besedka_booking")
huts_sheet = spreadsheet.worksheet("huts")
bookings_sheet = spreadsheet.worksheet("bookings")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

def validate_time(s):
    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
        hours, minutes = map(int, s.split(":"))
        if WORK_START <= hours < WORK_END:
            return True
    return False

def validate_time_to(s):
    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
        hours, minutes = map(int, s.split(":"))
        if WORK_START < hours < WORK_END or (hours == WORK_END and minutes == 0):
            return True
    return False

def is_date_past(date_str):
    try:
        day, month, year = map(int, date_str.split("."))
        chosen = dt_date(year, month, day)
        return chosen < dt_date.today()
    except Exception:
        return True

def is_slot_busy(hut_name, date, time_from, time_to):
    bookings = bookings_sheet.get_all_records()
    try:
        user_from = datetime.strptime(time_from, "%H:%M").time()
        user_to = datetime.strptime(time_to, "%H:%M").time()
    except Exception:
        return True

    for booking in bookings:
        if str(booking.get("Беседка")).strip().lower() == hut_name.strip().lower() and booking.get("дата") == date:
            booked_from = str(booking.get("время от", "")).strip()
            booked_to = str(booking.get("время до", "")).strip()
            try:
                booked_from_t = datetime.strptime(booked_from, "%H:%M").time()
                booked_to_t = datetime.strptime(booked_to, "%H:%M").time()
            except Exception:
                return True
            if not (user_to <= booked_from_t or user_from >= booked_to_t):
                return True
    return False

def build_calendar(year=None, month=None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    markup = {"inline_keyboard": []}
    month_name = calendar.month_name[month]
    markup["inline_keyboard"].append([
        {"text": f'{month_name} {year}', "callback_data": 'ignore'}
    ])
    week_days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    markup["inline_keyboard"].append([
        {"text": day, "callback_data": 'ignore'} for day in week_days
    ])
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append({"text": " ", "callback_data": "ignore"})
            else:
                d = dt_date(year, month, day)
                today = dt_date.today()
                disabled = d < today
                row.append({
                    "text": str(day) if not disabled else "❌",
                    "callback_data": f"select_date:{year}:{month}:{day}" if not disabled else "ignore"
                })
        markup["inline_keyboard"].append(row)
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    markup["inline_keyboard"].append([
        {"text": "⬅️", "callback_data": f"nav:{prev_year}:{prev_month}"},
        {"text": "➡️", "callback_data": f"nav:{next_year}:{next_month}"}
    ])
    from aiogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(**markup)

@dp.message(F.text.lower() == "/start")
async def start(message: Message, state: FSMContext):
    await state.clear()
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="📅 Забронировать беседку")],
        [KeyboardButton(text="📋 Посмотреть беседки")],
        [KeyboardButton(text="📞 Позвонить администратору")]
    ])
    await message.answer("Привет! Я бот для аренды беседок 🌿", reply_markup=kb)

@dp.message(F.text == "📞 Позвонить администратору")
async def call_admin(message: Message):
    await message.answer(
        f"Связаться с администратором:\n"
        f"📞 <a href='tel:{ADMIN_PHONE}'><code>{ADMIN_PHONE}</code></a>",
        parse_mode="HTML"
    )

@dp.message(F.text == "📋 Посмотреть беседки")
async def show_huts(message: Message):
    huts = huts_sheet.get_all_records()
    if not huts:
        await message.answer("Нет доступных беседок.")
        return
    for hut in huts:
        text = f"<b>{hut['name']}</b>\n💰 {hut['price']}₽/час\n📝 {hut['description']}"
        if hut.get('photo'):
            await message.answer_photo(hut['photo'], caption=text)
        else:
            await message.answer(text)

@dp.message(F.text == "📅 Забронировать беседку")
async def choose_hut(message: Message, state: FSMContext):
    huts = huts_sheet.get_all_records()
    if not huts:
        await message.answer("Нет свободных беседок.")
        return
    builder = InlineKeyboardBuilder()
    for i, hut in enumerate(huts):
        builder.button(text=hut['name'], callback_data=f"choose_hut:{i}")
    await message.answer("Выберите беседку:", reply_markup=builder.as_markup())
    await state.set_state(Booking.choosing_hut)

@dp.callback_query(F.data.startswith("choose_hut:"))
async def hut_selected(callback: CallbackQuery, state: FSMContext):
    huts = huts_sheet.get_all_records()
    index = int(callback.data.split(":")[1])
    hut = huts[index]
    await state.update_data(hut_name=hut['name'])
    await callback.message.edit_text(
        f"Вы выбрали: {hut['name']}\nТеперь выберите дату:",
        reply_markup=build_calendar()
    )
    await state.set_state(Booking.choosing_date)
    await callback.answer()

@dp.callback_query(F.data.startswith("nav:"))
async def nav_calendar(callback: CallbackQuery, state: FSMContext):
    _, year, month = callback.data.split(":")
    markup = build_calendar(int(year), int(month))
    await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer()

@dp.callback_query(F.data.startswith("select_date:"))
async def date_selected(callback: CallbackQuery, state: FSMContext):
    _, year, month, day = callback.data.split(":")
    date_str = f"{int(day):02}.{int(month):02}.{year}"
    if is_date_past(date_str):
        await callback.answer("❌ Нельзя бронировать на прошедшую дату.", show_alert=True)
        return
    data = await state.get_data()
    hut_name = data["hut_name"]

    bookings = bookings_sheet.get_all_records()
    busy_times = []
    for booking in bookings:
        if (
            str(booking.get("Беседка")).strip().lower() == hut_name.strip().lower()
            and booking.get("дата") == date_str
        ):
            booked_from = str(booking.get("время от", "")).strip()
            booked_to = str(booking.get("время до", "")).strip()
            if booked_from and booked_to:
                busy_times.append(f"{booked_from} – {booked_to}")

    if busy_times:
        busy_text = "<b>⏳ Уже занято:</b>\n" + "\n".join(f"• {x}" for x in busy_times) + "\n\n"
    else:
        busy_text = "<b>Все окна свободны на этот день!</b>\n\n"

    await state.update_data(date=date_str)
    await callback.message.edit_text(
        f"✅ Дата выбрана: {date_str}\n\n"
        + busy_text +
        "Введите время начала (например: 08:00):"
    )
    await state.set_state(Booking.entering_time_from)
    await callback.answer()

@dp.message(Booking.entering_time_from)
async def get_time_from(message: Message, state: FSMContext):
    time_from = message.text.strip()
    if not validate_time(time_from):
        await message.answer(
            "⏰ Комплекс работает с 08:00 до 21:00.\n"
            "Введите время начала в формате ЧЧ:ММ (например: 08:00):"
        )
        return
    await state.update_data(time_from=time_from)
    await message.answer("Введите время окончания (например: 21:00):")
    await state.set_state(Booking.entering_time_to)

@dp.message(Booking.entering_time_to)
async def get_time_to(message: Message, state: FSMContext):
    time_to = message.text.strip()
    if not validate_time_to(time_to):
        await message.answer(
            "⏰ Комплекс работает с 08:00 до 21:00.\n"
            "Введите время окончания в формате ЧЧ:ММ (например: 21:00):"
        )
        return
    data = await state.get_data()
    if time_to <= data["time_from"]:
        await message.answer("❗ Время окончания должно быть позже времени начала. Попробуйте снова.")
        return

    from_time = datetime.strptime(data["time_from"], "%H:%M")
    to_time = datetime.strptime(time_to, "%H:%M")
    delta = to_time - from_time
    total_minutes = delta.seconds // 60

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if minutes == 0:
        time_units = hours
    elif minutes <= 30:
        time_units = hours + 0.5
    else:
        time_units = hours + 1

    if time_units < 2:
        await message.answer("⏰ Минимальное время бронирования — 2 часа. Попробуйте указать другой диапазон.")
        await state.set_state(Booking.entering_time_from)
        await message.answer("Введите время начала (например: 08:00):")
        return

    await state.update_data(time_to=time_to)
    await message.answer("Введите ваше имя:")
    await state.set_state(Booking.entering_name)

@dp.message(Booking.entering_name)
async def get_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("📞 Теперь введите номер телефона:")
    await state.set_state(Booking.entering_phone)

@dp.message(Booking.entering_phone)
async def get_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    data = await state.get_data()

    if is_slot_busy(data["hut_name"], data["date"], data["time_from"], data["time_to"]):
        bookings = bookings_sheet.get_all_records()
        busy_times = []
        for booking in bookings:
            if (
                str(booking.get("Беседка")).strip().lower() == data["hut_name"].strip().lower()
                and booking.get("дата") == data["date"]
            ):
                booked_from = str(booking.get("время от", "")).strip()
                booked_to = str(booking.get("время до", "")).strip()
                if booked_from and booked_to:
                    busy_times.append(f"{booked_from} – {booked_to}")
        text = (
            "❌ Эта беседка уже занята на выбранную дату и время.\n\n"
            "⏳ Уже занято:\n"
            + "\n".join(f"• {x}" for x in busy_times)
            + "\n\nПопробуйте выбрать другое время или беседку."
        )
        await message.answer(text)
        await state.clear()
        return

    huts = huts_sheet.get_all_records()
    hut = next((h for h in huts if h['name'].strip().lower() == data['hut_name'].strip().lower()), None)
    price = int(hut['price']) if hut and hut.get('price') else 0

    from_time = datetime.strptime(data["time_from"], "%H:%M")
    to_time = datetime.strptime(data["time_to"], "%H:%M")
    delta = to_time - from_time
    total_minutes = delta.seconds // 60

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if minutes == 0:
        time_units = hours
    elif minutes <= 30:
        time_units = hours + 0.5
    else:
        time_units = hours + 1

    total = int(price * time_units)

    last_row = len(bookings_sheet.get_all_values()) + 1
    row = [
        last_row - 1,
        data["hut_name"],
        data["date"],
        data["time_from"],
        data["time_to"],
        data["name"],
        data["phone"],
        "ожидает"
    ]
    bookings_sheet.insert_row(row, index=last_row)

    payment_details = (
        "🔗 Реквизиты для оплаты:\n"
        "<code>2202202202202202</code> (Сбербанк)\n"
        "В комментарии укажите: ФИО и дату брони."
    )

    await message.answer(
        f"✅ Бронирование отправлено для <b>{data['hut_name']}</b> на {data['date']} "
        f"с {data['time_from']} до {data['time_to']}.\n"
        f"<b>Стоимость аренды: {price}₽/час × {time_units} ч = <u>{total}₽</u></b>\n\n"
        f"{payment_details}\n\n"
        "Пожалуйста, оплатите в течение 30 минут и отправьте чек в ответ на это сообщение."
    )
    await state.set_state(Booking.waiting_receipt)

@dp.message(Booking.waiting_receipt, F.photo)
@dp.message(Booking.waiting_receipt, F.document)
async def receive_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    caption = (
        f"Чек по бронированию:\n"
        f"Беседка: {data.get('hut_name')}\n"
        f"Дата: {data.get('date')}\n"
        f"Время: {data.get('time_from')} - {data.get('time_to')}\n"
        f"Имя: {data.get('name')}\n"
        f"Телефон: <code>{data.get('phone')}</code>"
    )
    if message.photo:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=caption, parse_mode="HTML")
    elif message.document:
        await bot.send_document(ADMIN_ID, document=message.document.file_id, caption=caption, parse_mode="HTML")
    
    # === Меняем статус бронирования в таблице ===
    all_bookings = bookings_sheet.get_all_values()
    header = all_bookings[0]
    for idx, row in enumerate(all_bookings[1:], start=2):
        if (
            row[header.index("Беседка")].strip() == data["hut_name"].strip()
            and row[header.index("дата")].strip() == data["date"].strip()
            and row[header.index("время от")].strip() == data["time_from"].strip()
            and row[header.index("время до")].strip() == data["time_to"].strip()
            and row[header.index("статус брони")].strip() == "ожидает"
        ):
            bookings_sheet.update_cell(idx, header.index("статус брони")+1, "забронировано")
            break

    name = data.get('name', 'гость')
    replies = [
        f"✅ Чек получен! Спасибо, {name}, что выбрали нас. Хорошего отдыха! Приходите ещё 🌿",
        f"✅ Спасибо, {name}, платёж получен! Отличного отдыха и ждём вас снова 🤗",
        f"✅ Ваш чек успешно получен, {name}! Наслаждайтесь отдыхом — будем рады видеть ещё!"
    ]
    reply_text = random.choice(replies)
    await message.answer(reply_text)
    await state.clear()

async def main():
    print("✅ Бот запущен и слушает...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
