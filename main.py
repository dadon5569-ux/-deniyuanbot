
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

user_data = defaultdict(lambda: {"rate": None, "amounts": []})


def money(x, symbol=""):
    d = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{symbol}{d:,.2f}"


def yuan(x):
    d = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"¥{d:,.2f}"


def parse_rate(text):
    m = re.search(r"(kurs|курс|rate)?\s*[:=]?\s*(\d+(?:[.,]\d+)?)", text.lower())
    if not m:
        return None
    try:
        rate = Decimal(m.group(2).replace(",", "."))
        if rate > 0:
            return rate
    except:
        return None
    return None


async def extract_amount_from_image(image_bytes: bytes):
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
Alipay yoki WeChat Pay screenshotidagi ASOSIY TO'LOV SUMMASINI top.
Faqat Xitoy yuanidagi to'lov summasini ol.
Telefon, vaqt, karta, order ID, balans, komissiya, raqamlarni olmang.

Javob faqat JSON bo'lsin:
{"amount": 1234.56}

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

    text = res.choices[0].message.content.strip()

    try:
        data = json.loads(text)
        amount = data.get("amount")
        if amount is None:
            return None
        return Decimal(str(amount))
    except Exception:
        m = re.search(r"\d+(?:[.,]\d+)?", text)
        if m:
            return Decimal(m.group(0).replace(",", "."))
        return None


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Assalomu alaykum!\n\n"
        "Bot Alipay va WeChat screenshotlaridagi yuan summalarni hisoblaydi.\n\n"
        "1) Kurs yozing: Kurs: 6.78\n"
        "2) Rasmlarni yuboring\n"
        "3) Yakunlash: /total\n\n"
        "Tozalash: /clear"
    )


@dp.message(F.text)
async def text_handler(message: Message):
    uid = message.from_user.id
    text = message.text or ""

    if text.startswith("/clear"):
        user_data[uid] = {"rate": None, "amounts": []}
        await message.answer("Tozalandi. Yangi hisob boshlashingiz mumkin.")
        return

    if text.startswith("/total"):
        rate = user_data[uid]["rate"]
        amounts = user_data[uid]["amounts"]

        if not amounts:
            await message.answer("Hali rasm yuborilmadi.")
            return

        if not rate:
            await message.answer("Avval kurs yozing. Masalan: Kurs: 6.78")
            return

        total_yuan = sum(amounts, Decimal("0"))
        usd = (total_yuan / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        service = (usd * Decimal("0.006")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        final_total = (usd + service).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        lines = []
        for i, a in enumerate(amounts, 1):
            lines.append(f"{i}-rasm: {yuan(a)}")

        msg = (
            "\n".join(lines)
            + f"\n\nJami yuan: {yuan(total_yuan)}"
            + f"\nKurs: {rate}"
            + f"\n\nDollar: {money(usd, '$')}"
            + f"\nXizmat 0.6%: {money(service, '$')}"
            + f"\n\n💰 To'lanadigan jami: {money(final_total, '$')}"
        )

        await message.answer(msg)
        return

    rate = parse_rate(text)
    if rate:
        user_data[uid]["rate"] = rate
        await message.answer(f"Kurs saqlandi: {rate}\nEndi rasmlarni yuboring.")
        return

    await message.answer("Kurs yozing: Kurs: 6.78\nYoki /total bosing.")


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

        await message.answer(
            f"{len(user_data[uid]['amounts'])}-rasm: {yuan(amount)} qo'shildi.\n"
            f"Hozircha jami: {yuan(total_now)}\n\n"
            f"Yana rasm yuboring yoki /total bosing."
        )

    except Exception as e:
        await message.answer(f"Xato chiqdi:\n{str(e)[:700]}")


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
