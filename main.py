import os, re, base64, asyncio
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
    raise RuntimeError("BOT_TOKEN environment variable kerak")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY environment variable kerak")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

user_data = defaultdict(lambda: {"rate": None, "amounts": []})

def fmt_num(x):
    d = Decimal(str(x))
    if d == d.to_integral():
        return f"{int(d):,}".replace(",", " ")
    return f"{d:,.2f}".replace(",", " ")

def parse_rate(text):
    m = re.search(r"(kurs|курс|rate)\s*[:=]?\s*([\d\s.,]+)", text.lower())
    if not m:
        return None
    raw = m.group(2).replace(" ", "").replace(",", ".")
    try:
        return Decimal(raw)
    except:
        return None

async def extract_amount_from_image(image_bytes: bytes):
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = """
Rasmdagi to'lov summasini top. Faqat asosiy to'lov summasini ol.
Karta raqami, vaqt, ID, telefon, balans, komissiya yoki boshqa raqamlarni olmang.
Agar summa Xitoy yuanida bo'lsa ham faqat sonni qaytar.
Javob faqat JSON bo'lsin: {"amount": 123.45, "currency": "CNY", "confidence": "high|medium|low"}
Agar topa olmasang: {"amount": null, "currency": null, "confidence": "low"}
"""
    res = await client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
            ],
        }],
    )
    text = res.output_text.strip()
    try:
        import json
        data = json.loads(text)
        amount = data.get("amount")
        if amount is None:
            return None, data
        return Decimal(str(amount)), data
    except Exception:
        m = re.search(r"[\d]+(?:[.,]\d+)?", text)
        if m:
            return Decimal(m.group(0).replace(",", ".")), {"raw": text}
        return None, {"raw": text}

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Assalomu alaykum!\n\n"
        "1) Avval kurs yozing: Kurs: 1750\n"
        "2) Keyin to'lov rasmlarini yuboring.\n"
        "3) Yakunlash uchun: /total\n\n"
        "Tozalash: /clear"
    )

@dp.message(F.text)
async def text_handler(message: Message):
    text = message.text or ""
    uid = message.from_user.id

    if text.startswith("/clear"):
        user_data[uid] = {"rate": None, "amounts": []}
        await message.answer("Tozalandi. Endi yangi hisob boshlashingiz mumkin.")
        return

    if text.startswith("/total"):
        rate = user_data[uid]["rate"]
        amounts = user_data[uid]["amounts"]
        if not amounts:
            await message.answer("Hali rasm yuborilmadi.")
            return
        total_yuan = sum(amounts, Decimal("0"))
        lines = [f"{i+1}-rasm: {fmt_num(a)} ¥" for i, a in enumerate(amounts)]
        msg = "\n".join(lines) + f"\n\nJami: {fmt_num(total_yuan)} ¥"
        if rate:
            som = (total_yuan * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            msg += f"\nKurs: {fmt_num(rate)}\nSo'mda: {fmt_num(som)} so'm"
        else:
            msg += "\n\nKurs yozilmagan. Masalan: Kurs: 1750"
        await message.answer(msg)
        return

    rate = parse_rate(text)
    if rate:
        user_data[uid]["rate"] = rate
        await message.answer(f"Kurs saqlandi: {fmt_num(rate)}\nEndi rasmlarni yuboring.")
        return

    await message.answer("Kurs yozing: Kurs: 1750\nYoki /total bosing.")


@dp.message(F.photo)
async def photo_handler(message: Message):
    uid = message.from_user.id
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    bio = BytesIO()
    await bot.download_file(file.file_path, bio)
    image_bytes = bio.getvalue()

    await message.answer("Rasm o'qilyapti...")

    try:
        amount, info = await extract_amount_from_image(image_bytes)
    except Exception as e:
        await message.answer(f"Rasmni o'qishda xato chiqdi:\n{str(e)[:500]}")
        return

    if amount is None:
        await message.answer("Summani topa olmadim. Rasm tiniqroq bo‘lsin.")
        return

    user_data[uid]["amounts"].append(amount)
    count = len(user_data[uid]["amounts"])

    await message.answer(
        f"{count}-rasm: {fmt_num(amount)} ¥ qo'shildi.\n"
        f"Jami hozircha: {fmt_num(sum(user_data[uid]['amounts'], Decimal('0')))} ¥\n\n"
        f"Yana rasm yuboring yoki /total bosing."
    )

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
