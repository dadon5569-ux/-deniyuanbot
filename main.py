import os
import re
import json
import base64
import asyncio
from collections import defaultdict
from io import BytesIO
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiohttp import web
from openai import AsyncOpenAI


BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN kerak")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY kerak")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Har bir foydalanuvchi uchun alohida hisob
user_data = defaultdict(lambda: {"rate": None, "amounts": []})


def d2(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def yuan_fmt(x):
    return f"¥{d2(x):,.2f}"


def usd_fmt(x):
    return f"${d2(x):,.2f}"


def parse_rate(text):
    """
    Kurs: 6.79
    6.79
    kurs 6,78
    shu formatlarni qabul qiladi.
    """
    text = (text or "").lower().replace(",", ".")
    m = re.search(r"(kurs|курс|rate)?\s*[:=]?\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return None

    try:
        rate = Decimal(m.group(2))
        # Yuan / Dollar kursi odatda 5-9 oralig'ida
        if Decimal("5") <= rate <= Decimal("9"):
            return rate
    except Exception:
        return None

    return None


def clean_json(text):
    text = (text or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return text


async def extract_amount_from_image(image_bytes: bytes):
    """
    Alipay / WeChat screenshotidan asosiy to'lov summasini GPT Vision bilan o'qiydi.
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
Alipay yoki WeChat Pay screenshotidan ASOSIY TO'LOV SUMMASINI top.
Faqat to'langan Xitoy yuani summasini ol.

Qoidalar:
- Karta raqami, vaqt, telefon, order ID, cashback, kupon, reklama narxi, balansni olmang.
- Ekranda katta ko'rsatilgan to'lov summasini oling.
- Agar summa minus bilan yozilgan bo'lsa, masalan -17,400.00, natijada musbat 17400.00 qaytar.
- Agar bir nechta summa bo'lsa, asosiy payment amountni oling.
- Faqat JSON qaytar.

Format:
{"amount": 4357.00}

Agar topa olmasang:
{"amount": null}
"""

    res = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        },
                    },
                ],
            }
        ],
        temperature=0,
    )

    text = clean_json(res.choices[0].message.content or "")

    try:
        data = json.loads(text)
        amount = data.get("amount")
        if amount is None:
            return None
        return abs(Decimal(str(amount)))
    except Exception:
        m = re.search(r"-?\d+(?:[.,]\d+)?", text)
        if m:
            return abs(Decimal(m.group(0).replace(",", ".")))
        return None


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Assalomu alaykum!\n\n"
        "Men Alipay va WeChat rasmlaridan yuan summani o'qib hisoblayman.\n\n"
        "Ishlash tartibi:\n"
        "1) Kurs yozing: 6.79\n"
        "2) Shu zakaz rasmlarini yuboring\n"
        "3) /total bosing\n\n"
        "Formula:\n"
        "Jami yuan ÷ kurs = dollar\n"
        "Dollar + 0.6% xizmat = yakuniy summa\n\n"
        "/clear — rasmlarni tozalash\n"
        "/start — yordam"
    )


@dp.message(F.photo)
async def photo_handler(message: Message):
    uid = message.from_user.id
    await message.answer("Rasm o'qilyapti...")

    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)

        bio = BytesIO()
        await bot.download_file(file.file_path, bio)
        image_bytes = bio.getvalue()

        amount = await extract_amount_from_image(image_bytes)

        if amount is None:
            await message.answer("Summani topa olmadim. Rasmni tiniqroq yuboring.")
            return

        user_data[uid]["amounts"].append(amount)
        total_now = sum(user_data[uid]["amounts"], Decimal("0"))
        count = len(user_data[uid]["amounts"])

        await message.answer(
            f"{count}-rasm: {yuan_fmt(amount)} qo'shildi.\n"
            f"Hozircha jami: {yuan_fmt(total_now)}\n\n"
            f"Yana rasm yuboring yoki /total bosing."
        )

    except Exception as e:
        await message.answer(f"Xato chiqdi:\n{str(e)[:700]}")


@dp.message(F.text)
async def text_handler(message: Message):
    uid = message.from_user.id
    text = message.text or ""

    if text.startswith("/clear"):
        rate = user_data[uid]["rate"]
        user_data[uid] = {"rate": rate, "amounts": []}
        await message.answer("Rasmlar tozalandi. Kurs saqlanib qoldi.")
        return

    if text.startswith("/total"):
        rate = user_data[uid]["rate"]
        amounts = user_data[uid]["amounts"]

        if not rate:
            await message.answer("Avval kurs yozing. Masalan: 6.79")
            return

        if not amounts:
            await message.answer("Hali rasm yuborilmadi.")
            return

        total_yuan = sum(amounts, Decimal("0"))

        # Asosiy formula:
        # Jami yuan / kurs = dollar
        # Dollar * 0.006 = xizmat 0.6%
        # Dollar + xizmat = yakuniy summa
        usd = total_yuan / rate
        service = usd * Decimal("0.006")
        final_total = usd + service

        lines = [f"{i}-rasm: {yuan_fmt(a)}" for i, a in enumerate(amounts, 1)]

        msg = (
            "\n".join(lines)
            + f"\n\nJami Yuan:\n{yuan_fmt(total_yuan)}"
            + f"\n\nKurs:\n{rate}"
            + f"\n\n{total_yuan:,.2f} ÷ {rate}"
            + f"\n= {usd:,.8f} USD"
            + f"\n\n0.6% xizmat:"
            + f"\n+ {service:,.8f} USD"
            + f"\n\n💰 Yakuniy summa:"
            + f"\n{usd_fmt(final_total)}"
        )

        await message.answer(msg)

        # Har bir zakaz /total dan keyin yopiladi.
        # Kurs saqlanadi, rasmlar tozalanadi.
        user_data[uid] = {"rate": rate, "amounts": []}
        return

    rate = parse_rate(text)
    if rate:
        user_data[uid]["rate"] = rate
        await message.answer(f"Kurs saqlandi: {rate}\nEndi rasmlarni yuboring.")
        return

    await message.answer("Kurs yozing: 6.79\nYoki /total bosing.")


async def health(request):
    return web.Response(text="OK")


async def main():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
